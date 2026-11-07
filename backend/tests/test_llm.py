"""LLM client: provider selection, retries, error sanitizing, prompts, and metadata parsing.

`app.llm.OpenAI` is replaced with a fake, so no request ever leaves the process.
"""

from types import SimpleNamespace

import httpx2
import openai
import pytest

from app import llm
from app.llm import LLMError
from app.models import ChatMessage, Paper


def completion(content, finish_reason="stop"):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish_reason)])


class FakeOpenAI:
    """Records constructor kwargs and create() calls; replies with queued responses or exceptions."""

    instances: list["FakeOpenAI"] = []
    replies: list = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        FakeOpenAI.instances.append(self)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        reply = FakeOpenAI.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


@pytest.fixture
def fake_llm(monkeypatch):
    """Configure the default provider with a dummy key and queue replies via the returned list."""
    FakeOpenAI.instances = []
    FakeOpenAI.replies = []
    monkeypatch.setattr(llm, "OpenAI", FakeOpenAI)
    monkeypatch.setenv("GENAI_API_KEY", "test-key")
    return FakeOpenAI


@pytest.fixture
def sleeps(monkeypatch):
    calls = []
    monkeypatch.setattr(llm.time, "sleep", calls.append)
    return calls


def api_error(body):
    return openai.APIError("request failed", httpx2.Request("POST", "https://llm.invalid"), body=body)


def make_paper(**fields):
    defaults = dict(source="upload", title="Paper Title", authors=["Ada Lovelace", "Alan Turing"], year=2023,
                    full_text="The full text.")
    return Paper(**{**defaults, **fields})


# --- provider and client ------------------------------------------------------------------------


def test_default_provider_is_genai():
    assert llm._provider() is llm.PROVIDERS["genai"]


@pytest.mark.parametrize("value", ["openrouter", "  OpenRouter "])
def test_provider_is_selected_from_env(monkeypatch, value):
    monkeypatch.setenv("LLM_PROVIDER", value)
    assert llm._provider() is llm.PROVIDERS["openrouter"]


def test_unknown_provider_raises(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "skynet")
    with pytest.raises(LLMError, match="Unknown LLM_PROVIDER 'skynet'"):
        llm._provider()


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.setattr(llm, "OpenAI", FakeOpenAI)
    with pytest.raises(LLMError, match="GENAI_API_KEY is not set"):
        llm._complete([{"role": "user", "content": "hi"}])


def test_client_is_configured_for_openrouter(monkeypatch, fake_llm):
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    fake_llm.replies = [completion("ok")]

    llm._complete([{"role": "user", "content": "hi"}])

    client = fake_llm.instances[0]
    assert client.kwargs["base_url"] == "https://openrouter.ai/api/v1"
    assert client.kwargs["api_key"] == "or-key"
    assert client.kwargs["default_headers"] == {"X-Title": "AI Research Assistant"}
    assert client.calls[0]["model"] == "qwen/qwen3-235b-a22b-2507"


# --- _complete ----------------------------------------------------------------------------------


def test_complete_returns_stripped_content_with_default_model(fake_llm):
    fake_llm.replies = [completion("  The answer.  \n")]
    messages = [{"role": "user", "content": "hi"}]

    assert llm._complete(messages) == "The answer."

    client = fake_llm.instances[0]
    assert client.kwargs["base_url"] == "https://genai.rcac.purdue.edu/api"
    assert client.kwargs["api_key"] == "test-key"
    call = client.calls[0]
    assert call["model"] == "gpt-oss:120b"
    assert call["messages"] == messages
    assert call["max_tokens"] == llm.MAX_OUTPUT_TOKENS


def test_complete_uses_model_override(monkeypatch, fake_llm):
    monkeypatch.setenv("GENAI_MODEL", "llama3.3:70b")
    fake_llm.replies = [completion("ok")]
    llm._complete([])
    assert fake_llm.instances[0].calls[0]["model"] == "llama3.3:70b"


def test_complete_retries_null_responses_then_succeeds(fake_llm, sleeps):
    fake_llm.replies = [None, None, completion("finally")]
    assert llm._complete([]) == "finally"
    assert sleeps == [5, 15]


def test_complete_gives_up_after_all_null_retries(fake_llm, sleeps):
    fake_llm.replies = [None, None, None, None]
    with pytest.raises(LLMError, match="rate-limiting"):
        llm._complete([])
    assert sleeps == list(llm.NULL_RESPONSE_RETRY_DELAYS)
    assert len(fake_llm.instances[0].calls) == 4


def test_complete_surfaces_only_the_provider_message(fake_llm):
    body = {"error": {"message": "Insufficient credits", "metadata": {"user_id": "user_secret123"}}}
    fake_llm.replies = [api_error(body)]

    with pytest.raises(LLMError) as exc:
        llm._complete([])

    assert str(exc.value) == "LLM request failed: Insufficient credits"
    assert "user_secret123" not in str(exc.value)


def test_complete_handles_flat_error_body(fake_llm):
    fake_llm.replies = [api_error({"message": "Model not found"})]
    with pytest.raises(LLMError, match="LLM request failed: Model not found"):
        llm._complete([])


@pytest.mark.parametrize("body", [None, "raw text with org_abc123", {"error": "just a string org_abc123"}])
def test_complete_falls_back_to_error_type_name(fake_llm, body):
    fake_llm.replies = [api_error(body)]
    with pytest.raises(LLMError) as exc:
        llm._complete([])
    assert str(exc.value) == "LLM request failed: APIError"


def test_complete_handles_connection_error(fake_llm):
    fake_llm.replies = [openai.APIConnectionError(request=httpx2.Request("POST", "https://llm.invalid"))]
    with pytest.raises(LLMError, match="LLM request failed: APIConnectionError"):
        llm._complete([])


def test_complete_empty_content_raises(fake_llm):
    fake_llm.replies = [completion("")]
    with pytest.raises(LLMError, match="empty response"):
        llm._complete([])


def test_complete_no_choices_raises(fake_llm):
    fake_llm.replies = [SimpleNamespace(choices=[])]
    with pytest.raises(LLMError, match="empty response"):
        llm._complete([])


def test_complete_reports_output_limit(fake_llm):
    fake_llm.replies = [completion(None, finish_reason="length")]
    with pytest.raises(LLMError, match="output limit"):
        llm._complete([])


# --- prompts ------------------------------------------------------------------------------------


def test_paper_context_includes_metadata_and_text():
    ctx = llm._paper_context(make_paper())
    assert "Title: Paper Title" in ctx
    assert "Authors: Ada Lovelace, Alan Turing" in ctx
    assert "Year: 2023" in ctx
    assert "--- FULL TEXT ---\nThe full text.\n--- END OF PAPER ---" in ctx
    assert "truncated" not in ctx


def test_paper_context_unknown_authors_and_year():
    ctx = llm._paper_context(make_paper(authors=[], year=None, full_text=None))
    assert "Authors: Unknown" in ctx
    assert "Year: Unknown" in ctx


@pytest.mark.parametrize("provider", ["genai", "openrouter"])
def test_paper_context_truncates_to_provider_limit(monkeypatch, provider):
    monkeypatch.setenv("LLM_PROVIDER", provider)
    limit = llm.PROVIDERS[provider].max_paper_chars
    ctx = llm._paper_context(make_paper(full_text="a" * limit + "OVERFLOW"))
    assert "a" * limit in ctx
    assert "OVERFLOW" not in ctx
    assert "[Paper text truncated due to length.]" in ctx


def test_paper_context_keeps_text_exactly_at_limit():
    limit = llm.PROVIDERS["genai"].max_paper_chars
    ctx = llm._paper_context(make_paper(full_text="b" * limit))
    assert "truncated" not in ctx


def test_summarize_sends_full_text_in_prompt(fake_llm):
    fake_llm.replies = [completion("## TL;DR\nGood paper.")]

    assert llm.summarize(make_paper(full_text="UNIQUE BODY TEXT")) == "## TL;DR\nGood paper."

    messages = fake_llm.instances[0].calls[0]["messages"]
    assert len(messages) == 1 and messages[0]["role"] == "user"
    assert "UNIQUE BODY TEXT" in messages[0]["content"]
    assert "## Key Results" in messages[0]["content"]


def test_answer_question_builds_system_history_and_question(fake_llm):
    fake_llm.replies = [completion("It uses CIFAR-10.")]
    history = [ChatMessage(paper_id=1, role="user", content="Q1"), ChatMessage(paper_id=1, role="assistant", content="A1")]

    assert llm.answer_question(make_paper(), history, "What dataset?") == "It uses CIFAR-10."

    messages = fake_llm.instances[0].calls[0]["messages"]
    assert messages[0]["role"] == "system" and "The full text." in messages[0]["content"]
    assert messages[1:] == [
        {"role": "user", "content": "Q1"},
        {"role": "assistant", "content": "A1"},
        {"role": "user", "content": "What dataset?"},
    ]


def test_answer_question_keeps_only_recent_history(fake_llm):
    fake_llm.replies = [completion("ok")]
    history = [ChatMessage(paper_id=1, role="user" if i % 2 == 0 else "assistant", content=f"m{i}") for i in range(15)]

    llm.answer_question(make_paper(), history, "latest?")

    messages = fake_llm.instances[0].calls[0]["messages"]
    sent_history = [m["content"] for m in messages[1:-1]]
    assert sent_history == [f"m{i}" for i in range(5, 15)]


# --- extract_metadata ---------------------------------------------------------------------------


def test_extract_metadata_parses_clean_json(fake_llm):
    fake_llm.replies = [completion('{"title": " Title ", "authors": [" Ada ", "", 3, "Alan"], "year": "2022", '
                                   '"abstract": " Abs. "}')]

    assert llm.extract_metadata("paper text") == {
        "title": "Title", "authors": ["Ada", "Alan"], "year": 2022, "abstract": "Abs."}


def test_extract_metadata_handles_code_fences_and_prose(fake_llm):
    fake_llm.replies = [completion('Here you go:\n```json\n{"title": "T", "authors": [], "year": null, '
                                   '"abstract": null}\n```')]
    assert llm.extract_metadata("x") == {"title": "T", "authors": [], "year": None, "abstract": None}


def test_extract_metadata_sends_only_the_beginning_of_the_text(fake_llm):
    fake_llm.replies = [completion('{"title": "T"}')]
    llm.extract_metadata("s" * 12_000 + "TAIL")
    prompt = fake_llm.instances[0].calls[0]["messages"][0]["content"]
    assert "s" * 12_000 in prompt and "TAIL" not in prompt


def test_extract_metadata_defaults_missing_fields(fake_llm):
    fake_llm.replies = [completion('{"title": "T", "abstract": "   "}')]
    assert llm.extract_metadata("x") == {"title": "T", "authors": [], "year": None, "abstract": None}


def test_extract_metadata_bad_year_becomes_none(fake_llm):
    fake_llm.replies = [completion('{"title": "T", "year": "circa 2020"}')]
    assert llm.extract_metadata("x")["year"] is None


def test_extract_metadata_invalid_json_raises(fake_llm):
    fake_llm.replies = [completion("I could not find any metadata.")]
    with pytest.raises(LLMError, match="invalid JSON"):
        llm.extract_metadata("x")


@pytest.mark.parametrize("title", ['""', '"   "', "null", "42"])
def test_extract_metadata_requires_title(fake_llm, title):
    fake_llm.replies = [completion(f'{{"title": {title}, "authors": ["A"]}}')]
    with pytest.raises(LLMError, match="did not return a paper title"):
        llm.extract_metadata("x")


@pytest.mark.parametrize("raw", ["null", '["Title", "Ada"]', '"just a string"'])
def test_extract_metadata_non_object_json_raises_llm_error(fake_llm, raw):
    # The docstring promises LLMError for unusable output; upload_paper relies on that to fall back
    fake_llm.replies = [completion(raw)]
    with pytest.raises(LLMError):
        llm.extract_metadata("x")


def test_extract_metadata_authors_string_is_not_split_into_characters(fake_llm):
    fake_llm.replies = [completion('{"title": "T", "authors": "Ada Lovelace, Alan Turing"}')]
    authors = llm.extract_metadata("x")["authors"]
    assert all(len(a) > 1 for a in authors), f"authors were split into characters: {authors}"


def test_extract_metadata_propagates_llm_errors(fake_llm):
    fake_llm.replies = [api_error({"error": {"message": "down"}})]
    with pytest.raises(LLMError, match="down"):
        llm.extract_metadata("x")
