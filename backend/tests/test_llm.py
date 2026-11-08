"""LLM features (requirements 3-5) through the public functions summarize, answer_question, extract_metadata.

`app.llm.OpenAI` is replaced with a fake, so no request leaves the process. Expected values come
from the README and requirements (providers, default models, summary sections, truncation limits),
not from the module's private constants.
"""

import inspect
import re
from types import SimpleNamespace

import httpx2
import openai
import pytest

from app import llm
from app.llm import LLMError, answer_question, extract_metadata, summarize
from app.models import ChatMessage, Paper


def completion(content, finish_reason="stop"):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish_reason)])


# Message shapes the OpenAI chat completions API accepts (https://platform.openai.com/docs/api-reference/chat/create)
MESSAGE_KEYS = {
    "system": {"role", "content", "name"},
    "developer": {"role", "content", "name"},
    "user": {"role", "content", "name"},
    "assistant": {"role", "content", "name", "refusal", "tool_calls", "audio", "function_call"},
    "tool": {"role", "content", "tool_call_id"},
}


def chat_request_problem(client: openai.OpenAI, kwargs: dict) -> str | None:
    """Why the chat completions API would reject this request with HTTP 400, or None if it's valid."""
    try:
        # The real SDK method raises TypeError for unknown or missing arguments
        inspect.signature(client.chat.completions.create).bind(**kwargs)
    except TypeError as e:
        return f"invalid arguments: {e}"
    if not isinstance(kwargs["model"], str) or not kwargs["model"]:
        return "model must be a non-empty string"
    messages = kwargs["messages"]
    if not isinstance(messages, list) or not messages:
        return "messages must be a non-empty array"
    for i, message in enumerate(messages):
        if not isinstance(message, dict):
            return f"messages[{i}] must be an object"
        role = message.get("role")
        if role not in MESSAGE_KEYS:
            return f"messages[{i}].role: invalid value {role!r}"
        extra = set(message) - MESSAGE_KEYS[role]
        if extra:
            return f"messages[{i}]: unrecognized keys {sorted(extra)}"
        if not isinstance(message.get("content"), (str, list)):
            return f"messages[{i}].content must be a string or an array of parts"
    temperature = kwargs.get("temperature")
    if temperature is not None and not (isinstance(temperature, (int, float)) and 0 <= temperature <= 2):
        return "temperature must be between 0 and 2"
    for key in ("max_tokens", "max_completion_tokens"):
        value = kwargs.get(key)
        if value is not None and not (isinstance(value, int) and value > 0):
            return f"{key} must be a positive integer"
    return None


def bad_request(message: str) -> openai.BadRequestError:
    request = httpx2.Request("POST", "https://llm.invalid/chat/completions")
    return openai.BadRequestError(message, response=httpx2.Response(400, request=request),
                                  body={"error": {"message": message}})


class FakeOpenAI:
    """Stands in for openai.OpenAI. Validates like the real service, replies with queued responses.

    The constructor builds a real SDK client from the same arguments (no request is made), so
    arguments the SDK rejects fail here too. create() rejects requests the chat completions API
    would answer with HTTP 400. Calls are recorded for assertions.
    """

    instances: list["FakeOpenAI"] = []
    replies: list = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.real = openai.OpenAI(**kwargs)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        FakeOpenAI.instances.append(self)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        problem = chat_request_problem(self.real, kwargs)
        if problem:
            raise bad_request(problem)
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
    # A stalled provider must not hang the request forever
    assert client.real.timeout is not None


def test_openrouter_needs_only_its_own_key(fake_llm, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    fake_llm.replies = [completion("summary")]

    summarize(make_paper())

    client = fake_llm.instances[0]
    assert client.kwargs["base_url"].startswith("https://openrouter.ai")
    assert client.kwargs["api_key"] == "or-key"
    assert client.calls[0]["model"] == "qwen/qwen3-235b-a22b-2507"
    # OpenRouter attributes requests to the app by this header (https://openrouter.ai/docs/app-attribution)
    assert client.real.default_headers.get("X-Title") == "AI Research Assistant"


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
    with pytest.raises(LLMError) as exc:
        summarize(make_paper())
    # Shown to the user as the error detail: names the bad value and the valid choices
    assert str(exc.value) == "Unknown LLM_PROVIDER 'skynet'. Use one of: genai, openrouter."
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
    # Each author name reaches the model intact (Q&A can answer "who wrote this?")
    for name in ("Ada Lovelace", "Alan Turing"):
        assert re.search(rf"(?<!\w){name}(?!\w)", prompt), name


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
    # The model is told the text is incomplete, so it doesn't present the excerpt as the whole paper
    assert "truncated" in prompt.lower()
    assert "None" not in prompt


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


def test_every_request_caps_the_output_length(genai):
    # Without a cap, reasoning models can run on and spend the user's credits (OpenRouter)
    genai.replies = [completion("summary"), completion("answer"), completion('{"title": "T"}')]

    summarize(make_paper())
    answer_question(make_paper(), [], "Q?")
    extract_metadata("text")

    for call in genai.all_calls():
        assert isinstance(call.get("max_tokens"), int) and call["max_tokens"] > 0


# --- the fake enforces the chat API's request rules --------------------------------------------


def valid_request(**overrides):
    return {"model": "m", "messages": [{"role": "user", "content": "hi"}], **overrides}


@pytest.mark.parametrize(
    "request_kwargs",
    [
        valid_request(messages=[{"ROLE": "user", "content": "hi"}]),
        valid_request(messages=[{"role": "USER", "content": "hi"}]),
        valid_request(messages=[{"role": "user", "content": "hi", "extra": 1}]),
        valid_request(messages=[{"role": "user"}]),
        valid_request(messages=[]),
        valid_request(model=""),
        valid_request(max_tokens=None, temperature=3),
        valid_request(max_tokens=0),
        valid_request(unknown_param=True),
    ],
)
def test_fake_llm_rejects_requests_the_api_would(genai, request_kwargs):
    genai.replies = [completion("never returned")]
    client = FakeOpenAI(api_key="k", base_url="https://llm.invalid")
    with pytest.raises(openai.BadRequestError):
        client.chat.completions.create(**request_kwargs)


def test_fake_llm_rejects_client_settings_the_sdk_would(fake_llm):
    with pytest.raises(TypeError):
        FakeOpenAI(api_key="k", base_url="https://llm.invalid", max_retries=None)


# --- retries and errors -----------------------------------------------------------------------


def test_null_responses_are_retried_after_waiting(genai, sleeps):
    # GenAI Studio may answer a rate-limited request with JSON null instead of an HTTP error
    genai.replies = [None, None, completion("finally")]

    assert summarize(make_paper()) == "finally"

    assert len(genai.all_calls()) == 3
    assert len(sleeps) == 2 and all(s > 0 for s in sleeps)


def test_persistent_null_responses_ask_the_user_to_try_again(genai, sleeps):
    genai.replies = [None] * 10

    with pytest.raises(LLMError) as exc:
        summarize(make_paper())

    assert str(exc.value) == "The LLM service is rate-limiting requests. Please try again in a minute."
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
    # Only the kind of error is shown, never the raw body
    assert str(exc.value) == "LLM request failed: APIError"


def test_connection_error_becomes_llm_error(genai):
    genai.replies = [openai.APIConnectionError(request=httpx2.Request("POST", "https://llm.invalid"))]
    with pytest.raises(LLMError) as exc:
        answer_question(make_paper(), [], "Q?")
    assert str(exc.value) == "LLM request failed: APIConnectionError"


@pytest.mark.parametrize("reply", [completion(""), completion(None), SimpleNamespace(choices=[])])
def test_empty_reply_is_an_error(genai, reply):
    genai.replies = [reply]
    with pytest.raises(LLMError) as exc:
        summarize(make_paper())
    assert str(exc.value) == "LLM returned an empty response"


def test_reply_cut_off_by_the_output_limit_is_an_error(genai):
    genai.replies = [completion(None, finish_reason="length")]
    with pytest.raises(LLMError) as exc:
        summarize(make_paper())
    assert str(exc.value) == "The LLM reached its output limit before answering. Please try again."


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


def test_extract_metadata_handles_pretty_printed_json_in_fences(genai):
    raw = 'Sure!\n```json\n{\n  "title": "Multi Line",\n  "authors": [\n    "Ada Lovelace"\n  ],\n  "year": 2020\n}\n```'
    genai.replies = [completion(raw)]
    assert extract_metadata("x") == {"title": "Multi Line", "authors": ["Ada Lovelace"], "year": 2020, "abstract": None}


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
