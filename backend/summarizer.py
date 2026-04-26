"""Standalone HTML summarizer using OpenRouter."""

from __future__ import annotations

import json
import os
import re
from html import unescape
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


OPENROUTER_CHAT_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_ENV_PATH = Path(__file__).with_name(".env")
DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_MAX_HTML_CHARS = int(os.getenv("SUMMARIZER_MAX_HTML_CHARS", "120000"))
DEFAULT_MAX_SEMANTIC_TEXT_CHARS = int(os.getenv("SUMMARIZER_MAX_SEMANTIC_TEXT_CHARS", "50000"))
SUMMARY_JSON_SCHEMA = {
    "page_type": "string",
    "title": "string",
    "primary_subject": "string",
    "primary_subject_type": "string",
    "one_sentence_summary": "string",
    "key_points": ["string"],
    "entities": [
        {
            "name": "string",
            "type": "string",
            "evidence": "string",
        }
    ],
    "facts": [
        {
            "label": "string",
            "value": "string",
            "evidence": "string",
        }
    ],
}


class SummarizerError(RuntimeError):
    """Raised when HTML summarization cannot be completed."""


COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
STRIP_BLOCK_RE = re.compile(
    r"<(script|style|noscript|svg|iframe|canvas|template)[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")


def _normalize_text(value: str) -> str:
    without_tags = TAG_RE.sub(" ", value)
    plain = unescape(without_tags)
    return WHITESPACE_RE.sub(" ", plain).strip()


def _extract_semantic_text(html: str, max_chars: int) -> str:
    snippets: list[str] = []
    patterns = [
        r"<title[^>]*>(.*?)</title>",
        r"<meta[^>]+name=[\"']description[\"'][^>]+content=[\"'](.*?)[\"'][^>]*>",
        r"<h[1-3][^>]*>(.*?)</h[1-3]>",
        r"<p[^>]*>(.*?)</p>",
        r"<li[^>]*>(.*?)</li>",
    ]
    for pattern in patterns:
        matches = re.findall(pattern, html, flags=re.IGNORECASE | re.DOTALL)
        for match in matches:
            text = _normalize_text(str(match))
            if text:
                snippets.append(text)

    joined = "\n".join(snippets)
    if len(joined) > max_chars:
        return joined[:max_chars]
    return joined


def _prepare_html_for_llm(html: str) -> tuple[str, str]:
    cleaned = COMMENT_RE.sub(" ", html)
    cleaned = STRIP_BLOCK_RE.sub(" ", cleaned)
    cleaned = WHITESPACE_RE.sub(" ", cleaned).strip()
    compact_html = cleaned[:DEFAULT_MAX_HTML_CHARS]
    semantic_text = _extract_semantic_text(cleaned, DEFAULT_MAX_SEMANTIC_TEXT_CHARS)
    return compact_html, semantic_text


def load_env(env_path: str | Path = DEFAULT_ENV_PATH) -> None:
    """Load simple KEY=VALUE pairs from a .env file without extra dependencies."""
    path = Path(env_path)

    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if key and key not in os.environ:
            os.environ[key] = value


def summarize_html(
    html: str,
    *,
    env_path: str | Path = DEFAULT_ENV_PATH,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Summarize an HTML document with the OpenRouter model from .env.

    Args:
        html: Raw HTML to summarize.
        env_path: Optional .env path. Defaults to backend/.env.
        timeout_seconds: HTTP request timeout.

    Returns:
        A validated JSON-compatible dict summarizing only the supplied HTML.
    """
    if not html or not html.strip():
        raise ValueError("html must be a non-empty string")

    load_env(env_path)

    api_key = os.getenv("OPENROUTER_API_KEY")
    model = os.getenv("OPENROUTER_MODEL")

    if not api_key:
        raise SummarizerError("Missing OPENROUTER_API_KEY in environment or .env")

    if not model:
        raise SummarizerError("Missing OPENROUTER_MODEL in environment or .env")

    compact_html, semantic_text = _prepare_html_for_llm(html)

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an HTML-only summarizer for a browser context graph. "
                    "Use only the supplied HTML. Do not use outside knowledge, prior "
                    "conversation, URL assumptions, hidden reasoning, or guesses that "
                    "are not grounded in the HTML. Ignore scripts, styles, analytics, "
                    "tracking markup, SVG paths, repeated navigation, and boilerplate. "
                    "Prefer the page's main subject over related products, recommendation "
                    "carousels, search results, accessories, sponsored blocks, and "
                    "'customers also bought' sections. For marketplace or product pages, "
                    "identify the single primary product that the page is mainly about, "
                    "using title, h1, semantic metadata, and nearby product facts first. "
                    "Return only valid JSON. No markdown. No commentary."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Extract a compact semantic summary from this HTML for a future "
                    "graph canvas and comparison/rubric engine.\n\n"
                    "Return exactly this JSON shape:\n"
                    f"{json.dumps(SUMMARY_JSON_SCHEMA, indent=2)}\n\n"
                    "Rules:\n"
                    "- Use empty arrays when a field is not present in the HTML.\n"
                    "- Use an empty string when a scalar field is not present.\n"
                    "- 'primary_subject' should be the main item, listing, or topic of this page, not a recommended or related item.\n"
                    "- 'primary_subject_type' should be a short label such as product, listing, article, forum thread, review page, or comparison page.\n"
                    "- Keep key_points to 3-7 short strings.\n"
                    "- Keep entities and facts grounded in visible or semantic HTML "
                    "text.\n\n"
                    "Semantic text excerpt:\n"
                    f"{semantic_text}\n\n"
                    "HTML (cleaned and truncated for token safety):\n"
                    f"{compact_html}"
                ),
            },
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }

    response = _post_json(
        OPENROUTER_CHAT_COMPLETIONS_URL,
        payload,
        api_key=api_key,
        timeout_seconds=timeout_seconds,
    )

    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise SummarizerError(f"Unexpected OpenRouter response: {response}") from exc

    if not isinstance(content, str) or not content.strip():
        raise SummarizerError("OpenRouter returned an empty summary")

    try:
        summary = json.loads(content)
    except json.JSONDecodeError as exc:
        raise SummarizerError(f"OpenRouter returned invalid summary JSON: {content}") from exc

    _validate_summary(summary)
    return summary


def _validate_summary(summary: Any) -> None:
    if not isinstance(summary, dict):
        raise SummarizerError("Summary must be a JSON object")

    expected_fields = {
        "page_type": str,
        "title": str,
        "primary_subject": str,
        "primary_subject_type": str,
        "one_sentence_summary": str,
        "key_points": list,
        "entities": list,
        "facts": list,
    }

    for field, expected_type in expected_fields.items():
        if field not in summary:
            raise SummarizerError(f"Summary missing required field: {field}")
        if not isinstance(summary[field], expected_type):
            raise SummarizerError(f"Summary field has wrong type: {field}")

    _validate_string_list(summary, "key_points")
    _validate_object_list(summary, "entities", {"name", "type", "evidence"})
    _validate_object_list(summary, "facts", {"label", "value", "evidence"})


def _validate_string_list(summary: dict[str, Any], field: str) -> None:
    if not all(isinstance(item, str) for item in summary[field]):
        raise SummarizerError(f"Summary field must contain only strings: {field}")


def _validate_object_list(
    summary: dict[str, Any],
    field: str,
    required_keys: set[str],
) -> None:
    for index, item in enumerate(summary[field]):
        if not isinstance(item, dict):
            raise SummarizerError(f"Summary field must contain objects: {field}")

        missing_keys = required_keys - item.keys()
        if missing_keys:
            missing = ", ".join(sorted(missing_keys))
            raise SummarizerError(f"Summary {field}[{index}] missing keys: {missing}")

        for key in required_keys:
            if not isinstance(item[key], str):
                raise SummarizerError(f"Summary {field}[{index}].{key} must be a string")


def _post_json(
    url: str,
    payload: dict[str, Any],
    *,
    api_key: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost",
            "X-Title": "Synapse HTML Summarizer",
        },
    )

    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            response_body = response.read().decode("utf-8")
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise SummarizerError(
            f"OpenRouter request failed with HTTP {exc.code}: {error_body}"
        ) from exc
    except URLError as exc:
        raise SummarizerError(f"OpenRouter request failed: {exc.reason}") from exc

    try:
        return json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise SummarizerError(f"OpenRouter returned invalid JSON: {response_body}") from exc
