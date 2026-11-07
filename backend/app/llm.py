"""LLM access through OpenAI-compatible APIs (Purdue GenAI Studio or OpenRouter)."""

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import openai
from dotenv import load_dotenv
from openai import OpenAI

from .models import ChatMessage, Paper

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


@dataclass(frozen=True)
class Provider:
    base_url: str
    key_env: str
    model_env: str
    default_model: str
    max_paper_chars: int
    headers: dict[str, str] = field(default_factory=dict)


PROVIDERS = {
    "genai": Provider(
        base_url="https://genai.rcac.purdue.edu/api",
        key_env="GENAI_API_KEY",
        model_env="GENAI_MODEL",
        default_model="gpt-oss:120b",
        # ~43k tokens; leaves room for chat history and the response in the 65,536-token context
        max_paper_chars=160_000,
    ),
    "openrouter": Provider(
        base_url="https://openrouter.ai/api/v1",
        key_env="OPENROUTER_API_KEY",
        model_env="OPENROUTER_MODEL",
        default_model="qwen/qwen3-235b-a22b-2507",
        # ~50k tokens; well within the default model's context
        max_paper_chars=200_000,
        headers={"X-Title": "AI Research Assistant"},
    ),
}
DEFAULT_PROVIDER = "genai"
MAX_HISTORY_MESSAGES = 10
# Reasoning counts toward this limit on models that output it, and can use well over 1,000 tokens
MAX_OUTPUT_TOKENS = 4000
REQUEST_TIMEOUT_SECONDS = 180
# GenAI Studio may answer with a JSON null instead of an HTTP error when rate limited (20 requests/minute)
NULL_RESPONSE_RETRY_DELAYS = (5, 15, 30)


class LLMError(Exception):
    pass


def _provider() -> Provider:
    name = (os.getenv("LLM_PROVIDER") or DEFAULT_PROVIDER).strip().lower()
    provider = PROVIDERS.get(name)
    if provider is None:
        raise LLMError(f"Unknown LLM_PROVIDER '{name}'. Use one of: {', '.join(PROVIDERS)}.")
    return provider


def _client(provider: Provider) -> OpenAI:
    api_key = os.getenv(provider.key_env)
    if not api_key:
        raise LLMError(f"{provider.key_env} is not set. Add it to backend/.env.")
    return OpenAI(
        base_url=provider.base_url,
        api_key=api_key,
        default_headers=provider.headers,
        timeout=REQUEST_TIMEOUT_SECONDS,
        max_retries=1,
    )


def _complete(messages: list[dict]) -> str:
    provider = _provider()
    client = _client(provider)
    for delay in (*NULL_RESPONSE_RETRY_DELAYS, None):
        try:
            resp = client.chat.completions.create(
                model=os.getenv(provider.model_env) or provider.default_model,
                messages=messages,
                temperature=0.2,
                max_tokens=MAX_OUTPUT_TOKENS,
            )
        except openai.APIError as e:
            # Surface only the provider's human-readable message, not the raw body (it includes account IDs)
            body = e.body if isinstance(e.body, dict) else {}
            error = body.get("error", body)
            detail = error.get("message") if isinstance(error, dict) else None
            raise LLMError(f"LLM request failed: {detail or type(e).__name__}") from e
        if resp is not None:
            break
        if delay is None:
            raise LLMError("The LLM service is rate-limiting requests. Please try again in a minute.")
        time.sleep(delay)
    content = resp.choices[0].message.content if resp.choices else None
    if not content:
        if resp.choices and resp.choices[0].finish_reason == "length":
            raise LLMError("The LLM reached its output limit before answering. Please try again.")
        raise LLMError("LLM returned an empty response")
    return content.strip()


def _paper_context(paper: Paper) -> str:
    text = paper.full_text or ""
    note = ""
    max_chars = _provider().max_paper_chars
    if len(text) > max_chars:
        text = text[:max_chars]
        note = "\n[Paper text truncated due to length.]"
    authors = ", ".join(paper.authors) or "Unknown"
    return (
        f"Title: {paper.title}\n"
        f"Authors: {authors}\n"
        f"Year: {paper.year or 'Unknown'}\n\n"
        f"--- FULL TEXT ---\n{text}{note}\n--- END OF PAPER ---"
    )


SUMMARY_PROMPT = """You are a research assistant. Summarize the paper below for a researcher \
who has not read it. Base the summary on the full text, not just the abstract.

Use Markdown with exactly these sections:
## TL;DR
One or two sentences.
## Problem
What problem the paper addresses and why it matters.
## Approach
The main idea and how the method works.
## Key Results
The most important findings, with numbers where the paper gives them.
## Limitations
Limitations stated by the authors or evident from the paper.

Be concise (about 300-450 words). Do not invent details that are not in the paper.

{paper}"""


def summarize(paper: Paper) -> str:
    return _complete([{"role": "user", "content": SUMMARY_PROMPT.format(paper=_paper_context(paper))}])


QA_SYSTEM_PROMPT = """You are a research assistant answering questions about one paper. \
Answer using the paper's content below. Quote or cite section names when helpful. \
If the paper does not contain the answer, say so plainly instead of guessing; you may then \
add general background knowledge, clearly labeled as not coming from the paper. \
Answer in concise Markdown.

{paper}"""


def answer_question(paper: Paper, history: list[ChatMessage], question: str) -> str:
    messages = [{"role": "system", "content": QA_SYSTEM_PROMPT.format(paper=_paper_context(paper))}]
    messages += [{"role": m.role, "content": m.content} for m in history[-MAX_HISTORY_MESSAGES:]]
    messages.append({"role": "user", "content": question})
    return _complete(messages)


METADATA_PROMPT = """Extract bibliographic metadata from the beginning of a research paper.

Return ONLY a JSON object with these keys:
- "title": the paper title (string)
- "authors": list of author full names in order (strings only, no affiliations or emails)
- "year": publication year as an integer, or null if it cannot be determined
- "abstract": the paper's abstract copied verbatim (string), or null if there is none

Paper text:
{text}"""


def extract_metadata(text: str) -> dict:
    """Ask the LLM for title/authors/year/abstract; raises LLMError if the output is unusable."""
    raw = _complete([{"role": "user", "content": METADATA_PROMPT.format(text=text[:12_000])}])
    # Models sometimes wrap JSON in ```json fences or add prose around it
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    try:
        data = json.loads(match.group(0) if match else raw)
    except json.JSONDecodeError as e:
        raise LLMError("LLM returned invalid JSON for paper metadata") from e

    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        raise LLMError("LLM did not return a paper title")
    authors = data.get("authors") or []
    year = data.get("year")
    abstract = data.get("abstract")
    try:
        year = int(year) if year is not None else None
    except (TypeError, ValueError):
        year = None
    return {
        "title": title.strip(),
        "authors": [a.strip() for a in authors if isinstance(a, str) and a.strip()],
        "year": year,
        "abstract": abstract.strip() if isinstance(abstract, str) and abstract.strip() else None,
    }
