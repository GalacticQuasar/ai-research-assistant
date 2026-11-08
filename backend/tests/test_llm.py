"""LLM features (requirements 3-5) through the public functions summarize, answer_question, extract_metadata.

`app.llm.OpenAI` is replaced with a fake, so no request leaves the process. Expected values come
from the README and requirements (providers, default models, summary sections, truncation limits),
not from the module's private constants.
"""

from types import SimpleNamespace

import httpx2
import openai
import pytest

from app import llm
from app.llm import LLMError, answer_question, extract_metadata, summarize
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

    @classmethod
    def all_calls(cls):
        return [call for instance in cls.instances for call in instance.calls]


@pytest.fixture
def fake_llm(monkeypatch):
    """Replace the OpenAI client; queue replies on the returned class. No API key is set."""
    FakeOpenAI.instances = []
    FakeOpenAI.replies = []
    monkeypatch.setattr(llm, "OpenAI", FakeOpenAI)
    return FakeOpenAI


@pytest.fixture
def genai(fake_llm, monkeypatch):
    """The default setup from the README: only GENAI_API_KEY in backend/.env."""
    monkeypatch.setenv("GENAI_API_KEY", "test-key")
    return fake_llm


@pytest.fixture
def sleeps(monkeypatch):
    calls = []
    monkeypatch.setattr(llm.time, "sleep", calls.append)
    return calls


def api_error(body):
    return openai.APIError("request failed", httpx2.Request("POST", "https://llm.invalid"), body=body)


def make_paper(**fields):
    defaults = dict(source="upload", title="Paper Title", authors=["Ada Lovelace", "Alan Turing"], year=2023,
                    abstract="Short abstract.", full_text="The full text.")
    return Paper(**{**defaults, **fields})


def sent_messages(fake):
    return fake.all_calls()[0]["messages"]


def prompt_text(fake):
    return "\n".join(m["content"] for m in sent_messages(fake))


# --- provider configuration (README "Configure the API key") ---------------------------------


def test_default_provider_is_genai_studio_with_its_default_model(genai):
    genai.replies = [completion("summary")]

    summarize(make_paper())

    client = genai.instances[0]
    assert client.kwargs["base_url"].startswith("https://genai.rcac.purdue.edu")
    assert client.kwargs["api_key"] == "test-key"
    assert client.calls[0]["model"] == "gpt-oss:120b"


def test_openrouter_needs_only_its_own_key(fake_llm, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    fake_llm.replies = [completion("summary")]

    summarize(make_paper())

    client = fake_llm.instances[0]
    assert client.kwargs["base_url"].startswith("https://openrouter.ai")
    assert client.kwargs["api_key"] == "or-key"
    assert client.calls[0]["model"] == "qwen/qwen3-235b-a22b-2507"


@pytest.mark.parametrize(
    ("provider", "key_env", "model_env"),
    [("genai", "GENAI_API_KEY", "GENAI_MODEL"), ("openrouter", "OPENROUTER_API_KEY", "OPENROUTER_MODEL")],
)
def test_model_can_be_overridden(fake_llm, monkeypatch, provider, key_env, model_env):
    monkeypatch.setenv("LLM_PROVIDER", provider)
    monkeypatch.setenv(key_env, "k")
    monkeypatch.setenv(model_env, "some/other-model")
    fake_llm.replies = [completion("ok")]

    summarize(make_paper())

    assert fake_llm.all_calls()[0]["model"] == "some/other-model"


def test_unknown_provider_is_reported(fake_llm, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "skynet")
    with pytest.raises(LLMError, match="skynet"):
        summarize(make_paper())
    assert fake_llm.instances == []


def test_missing_api_key_names_the_variable_to_set(fake_llm):
    with pytest.raises(LLMError, match="GENAI_API_KEY"):
        summarize(make_paper())
    assert fake_llm.instances == []


# --- summarize (requirement 4) --------------------------------------------------------------------


def test_summary_is_based_on_the_full_text(genai):
    genai.replies = [completion("  ## TL;DR\nGood paper.  \n")]
    paper = make_paper(full_text="Intro.\n\n2 Method\nWe train on ImageNet with a sparse kernel.")

    assert summarize(paper) == "## TL;DR\nGood paper."

    prompt = prompt_text(genai)
    assert "We train on ImageNet with a sparse kernel." in prompt  # body text, not only the abstract
    assert "Paper Title" in prompt


def test_summary_asks_for_the_documented_sections(genai):
    genai.replies = [completion("ok")]
    summarize(make_paper())
    prompt = prompt_text(genai).lower()
    for section in ("tl;dr", "problem", "approach", "key results", "limitations"):
        assert section in prompt


@pytest.mark.parametrize("provider", ["genai", "openrouter"])
def test_typical_paper_is_sent_in_full(fake_llm, monkeypatch, provider):
    # ~25k tokens, well under the README's ~43k (GenAI Studio) / ~50k (OpenRouter) limits
    monkeypatch.setenv("LLM_PROVIDER", provider)
    monkeypatch.setenv("GENAI_API_KEY" if provider == "genai" else "OPENROUTER_API_KEY", "k")
    fake_llm.replies = [completion("ok")]
    text = "x" * 100_000 + " CONCLUSION"

    summarize(make_paper(full_text=text))

    assert text in prompt_text(fake_llm)


@pytest.mark.parametrize("provider", ["genai", "openrouter"])
def test_very_long_paper_is_truncated(fake_llm, monkeypatch, provider):
    # README: text beyond about 43k tokens (GenAI Studio) or 50k (OpenRouter) is truncated
    monkeypatch.setenv("LLM_PROVIDER", provider)
    monkeypatch.setenv("GENAI_API_KEY" if provider == "genai" else "OPENROUTER_API_KEY", "k")
    fake_llm.replies = [completion("ok")]

    summarize(make_paper(full_text="BEGINNING " + "y" * 2_000_000 + " TAIL"))

    prompt = prompt_text(fake_llm)
    assert "BEGINNING" in prompt
    assert "TAIL" not in prompt
    assert len(prompt) < 250_000  # roughly 60k tokens at ~4 characters per token


def test_summary_of_paper_with_unknown_metadata(genai):
    genai.replies = [completion("ok")]
    summarize(make_paper(authors=[], year=None))
    assert "None" not in prompt_text(genai)


# --- answer_question (requirement 5) ----------------------------------------------------------


def test_question_is_answered_from_the_paper(genai):
    genai.replies = [completion("It uses CIFAR-10.")]
    history = [ChatMessage(paper_id=1, role="user", content="Q1"), ChatMessage(paper_id=1, role="assistant", content="A1")]

    answer = answer_question(make_paper(full_text="We evaluate on CIFAR-10."), history, "What dataset?")

    assert answer == "It uses CIFAR-10."
    messages = sent_messages(genai)
    assert "We evaluate on CIFAR-10." in messages[0]["content"]
    # Follow-ups see the conversation so far, in order, then the new question
    assert messages[1:] == [
        {"role": "user", "content": "Q1"},
        {"role": "assistant", "content": "A1"},
        {"role": "user", "content": "What dataset?"},
    ]


def test_long_conversations_keep_the_most_recent_messages(genai):
    genai.replies = [completion("ok")]
    history = [ChatMessage(paper_id=1, role="user" if i % 2 == 0 else "assistant", content=f"m{i}") for i in range(40)]

    answer_question(make_paper(), history, "latest?")

    sent = [m["content"] for m in sent_messages(genai)[1:-1]]
    all_contents = [m.content for m in history]
    assert sent, "some history must be sent"
    assert sent == all_contents[-len(sent):]  # a recent, contiguous, ordered window
    assert sent_messages(genai)[-1] == {"role": "user", "content": "latest?"}


# --- retries and errors -----------------------------------------------------------------------


def test_null_responses_are_retried_after_waiting(genai, sleeps):
    # GenAI Studio may answer a rate-limited request with JSON null instead of an HTTP error
    genai.replies = [None, None, completion("finally")]

    assert summarize(make_paper()) == "finally"

    assert len(genai.all_calls()) == 3
    assert len(sleeps) == 2 and all(s > 0 for s in sleeps)


def test_persistent_null_responses_ask_the_user_to_try_again(genai, sleeps):
    genai.replies = [None] * 10

    with pytest.raises(LLMError, match="try again in a minute"):
        summarize(make_paper())

    attempts = len(genai.all_calls())
    assert attempts > 1
    assert len(sleeps) == attempts - 1


def test_error_shows_provider_message_without_account_details(genai):
    body = {"error": {"message": "Insufficient credits", "metadata": {"user_id": "user_secret123"}}}
    genai.replies = [api_error(body)]

    with pytest.raises(LLMError) as exc:
        summarize(make_paper())

    assert "Insufficient credits" in str(exc.value)
    assert "user_secret123" not in str(exc.value)


def test_error_with_flat_body_shows_its_message(genai):
    genai.replies = [api_error({"message": "Model not found"})]
    with pytest.raises(LLMError, match="Model not found"):
        summarize(make_paper())


@pytest.mark.parametrize("body", [None, "raw text with org_abc123", {"error": "plain string org_abc123"},
                                  {"error": {"code": 500, "org": "org_abc123"}}])
def test_unstructured_error_bodies_are_never_shown(genai, body):
    genai.replies = [api_error(body)]
    with pytest.raises(LLMError) as exc:
        summarize(make_paper())
    assert "LLM request failed" in str(exc.value)
    assert "org_abc123" not in str(exc.value)


def test_connection_error_becomes_llm_error(genai):
    genai.replies = [openai.APIConnectionError(request=httpx2.Request("POST", "https://llm.invalid"))]
    with pytest.raises(LLMError, match="LLM request failed"):
        answer_question(make_paper(), [], "Q?")


@pytest.mark.parametrize("reply", [completion(""), completion(None), SimpleNamespace(choices=[])])
def test_empty_reply_is_an_error(genai, reply):
    genai.replies = [reply]
    with pytest.raises(LLMError):
        summarize(make_paper())


def test_reply_cut_off_by_the_output_limit_is_an_error(genai):
    genai.replies = [completion(None, finish_reason="length")]
    with pytest.raises(LLMError, match="output limit"):
        summarize(make_paper())


# --- extract_metadata (requirement 3) -----------------------------------------------------------


def test_extract_metadata_parses_and_cleans_json(genai):
    genai.replies = [completion('{"title": " Title ", "authors": [" Ada ", "", 3, "Alan"], "year": "2022", '
                                '"abstract": " Abs. "}')]

    assert extract_metadata("paper text") == {"title": "Title", "authors": ["Ada", "Alan"], "year": 2022,
                                              "abstract": "Abs."}
    assert "paper text" in prompt_text(genai)


def test_extract_metadata_handles_code_fences_and_prose(genai):
    genai.replies = [completion('Here you go:\n```json\n{"title": "T", "authors": [], "year": null, '
                                '"abstract": null}\n```')]
    assert extract_metadata("x") == {"title": "T", "authors": [], "year": None, "abstract": None}


def test_extract_metadata_defaults_missing_fields(genai):
    genai.replies = [completion('{"title": "T", "abstract": "   "}')]
    assert extract_metadata("x") == {"title": "T", "authors": [], "year": None, "abstract": None}


def test_extract_metadata_bad_year_becomes_none(genai):
    genai.replies = [completion('{"title": "T", "year": "circa 2020"}')]
    assert extract_metadata("x")["year"] is None


def test_extract_metadata_invalid_json_raises(genai):
    genai.replies = [completion("I could not find any metadata.")]
    with pytest.raises(LLMError):
        extract_metadata("x")


@pytest.mark.parametrize("title", ['""', '"   "', "null", "42"])
def test_extract_metadata_requires_title(genai, title):
    genai.replies = [completion(f'{{"title": {title}, "authors": ["A"]}}')]
    with pytest.raises(LLMError):
        extract_metadata("x")


@pytest.mark.parametrize("raw", ["null", '["Title", "Ada"]', '"just a string"'])
def test_extract_metadata_non_object_json_raises_llm_error(genai, raw):
    # The docstring promises LLMError for unusable output; upload_paper relies on that to fall back
    genai.replies = [completion(raw)]
    with pytest.raises(LLMError):
        extract_metadata("x")


def test_extract_metadata_authors_string_is_not_split_into_characters(genai):
    genai.replies = [completion('{"title": "T", "authors": "Ada Lovelace, Alan Turing"}')]
    authors = extract_metadata("x")["authors"]
    assert all(len(a) > 1 for a in authors), f"authors were split into characters: {authors}"


def test_extract_metadata_propagates_llm_errors(genai):
    genai.replies = [api_error({"error": {"message": "down"}})]
    with pytest.raises(LLMError, match="down"):
        extract_metadata("x")
