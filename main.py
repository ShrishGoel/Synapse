import asyncio
import json
import logging
import os
import re
import time
from typing import Any, Literal, TypeVar

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from firecrawl import FirecrawlApp
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationError

from backend.summarizer import SummarizerError, summarize_html


def _parse_log_level(value: str | None) -> int:
    raw = str(value or "WARNING").strip().upper()
    return getattr(logging, raw, logging.WARNING)


logging.basicConfig(
    level=_parse_log_level(os.getenv("SYNAPSE_LOG_LEVEL")),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("tabgraph")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)


def _load_local_env() -> None:
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(env_path):
        return

    with open(env_path, "r", encoding="utf-8") as env_file:
        for line in env_file:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue

            key, value = stripped.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


_load_local_env()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "dummy-openrouter-api-key")
FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY", "dummy-firecrawl-api-key")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
MAX_EVALUATION_PASSES = int(os.getenv("MAX_EVALUATION_PASSES", "1"))
DEFAULT_FIRECRAWL_QUERY_BUDGET = int(os.getenv("FIRECRAWL_QUERY_BUDGET", "4"))
FIRECRAWL_RESULTS_PER_QUERY = int(os.getenv("FIRECRAWL_RESULTS_PER_QUERY", "4"))
MAX_EXTENSION_HISTORY_TABS = int(os.getenv("MAX_EXTENSION_HISTORY_TABS", "6"))
MAX_EXTENSION_HISTORY_DOM_CHARS = int(os.getenv("MAX_EXTENSION_HISTORY_DOM_CHARS", "220000"))
DEBUG_VALUE_PREVIEW_CHARS = int(os.getenv("SYNAPSE_DEBUG_VALUE_PREVIEW_CHARS", "180"))
MAX_STRUCTURED_LLM_ATTEMPTS = int(os.getenv("MAX_STRUCTURED_LLM_ATTEMPTS", "2"))
MAX_SYNTHESIZED_GRAPH_NODES = int(os.getenv("MAX_SYNTHESIZED_GRAPH_NODES", "12"))
MAX_SYNTHESIZED_GRAPH_EDGES = int(os.getenv("MAX_SYNTHESIZED_GRAPH_EDGES", "20"))
MAX_SYNTHESIZED_URL_CHARS = int(os.getenv("MAX_SYNTHESIZED_URL_CHARS", "500"))
DISCOVERY_ENABLED_BY_DEFAULT = os.getenv("SYNAPSE_ENABLE_DISCOVERY", "0").strip() == "1"


llm_client = AsyncOpenAI(
    api_key=OPENROUTER_API_KEY,
    base_url="https://openrouter.ai/api/v1",
)
firecrawl_client = FirecrawlApp(api_key=FIRECRAWL_API_KEY)

app = FastAPI(title="TabGraph Backend", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def force_cors_headers(request, call_next):
    response = await call_next(request)
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,PUT,DELETE,OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "*"
    return response

EXTENSION_RETENTION_SECONDS = 30 * 60
extension_history: dict[str, dict[str, Any]] = {}
extension_preferences: dict[str, str] = {
    "user_prompt": "Graph the rentals I've been looking at.",
    "enable_discovery": "false",
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ActiveTab(StrictModel):
    url: HttpUrl
    summary: str = Field(min_length=1)


class SynthesizeRequest(StrictModel):
    user_prompt: str = Field(min_length=1)
    user_constraint: str | None = Field(default=None, min_length=1)
    active_tabs: list[ActiveTab] = Field(min_length=1)
    firecrawl_query_budget: int = Field(default=DEFAULT_FIRECRAWL_QUERY_BUDGET, ge=0, le=12)


class DomTab(StrictModel):
    url: HttpUrl
    title: str = ""
    dom: str = Field(min_length=1)


class SynthesizeFromDomRequest(StrictModel):
    user_prompt: str = Field(min_length=1)
    user_constraint: str | None = Field(default=None, min_length=1)
    tabs: list[DomTab] = Field(min_length=1)
    firecrawl_query_budget: int = Field(default=DEFAULT_FIRECRAWL_QUERY_BUDGET, ge=0, le=12)


class SynthesizeFromExtensionRequest(StrictModel):
    user_prompt: str = Field(min_length=1)
    user_constraint: str | None = Field(default=None, min_length=1)
    firecrawl_query_budget: int = Field(default=DEFAULT_FIRECRAWL_QUERY_BUDGET, ge=0, le=12)
    max_tabs: int = Field(default=20, ge=1, le=50)

class ExtensionSnapshot(StrictModel):
    url: HttpUrl
    title: str = ""
    dom: str = Field(min_length=1)
    timestamp: int | None = None


class ExtensionPreferencesRequest(StrictModel):
    user_prompt: str | None = Field(default=None, min_length=1, max_length=400)
    enable_discovery: bool | None = None


class ComparisonRubric(StrictModel):
    fields: list[str] = Field(
        min_length=2,
        max_length=12,
        description="Comparison fields needed to answer the user's prompt.",
    )
    inferred_constraints: list[str] = Field(
        max_length=8,
        description="Explicit or strongly implied constraints inferred from the user's interests.",
    )
    default_ordering: str = Field(
        description="Recommended default ordering for the final graph, such as performance-per-dollar.",
    )
    seed_patterns: list[str] = Field(
        max_length=8,
        description="Patterns observed specifically from the user-provided seed items.",
    )
    x_axis_label: str = Field(description="Human-readable label for the chart's x axis.")
    x_axis_score_field: str = Field(description="Exact node field name for the x-axis 0-100 score.")
    x_axis_low: str = Field(description="What the low end of the x axis means.")
    x_axis_high: str = Field(description="What the high end of the x axis means.")
    y_axis_label: str = Field(description="Human-readable label for the chart's y axis.")
    y_axis_score_field: str = Field(description="Exact node field name for the y-axis 0-100 score.")
    y_axis_low: str = Field(description="What the low end of the y axis means.")
    y_axis_high: str = Field(description="What the high end of the y axis means.")


class EvaluationState(StrictModel):
    is_complete: bool
    should_search_more: bool
    missing_fields: list[str] = Field(
        description="Rubric fields that are still unknown or weakly supported.",
    )
    search_queries: list[str] = Field(
        max_length=3,
        description="Targeted Firecrawl search queries. Keep this small to conserve credits.",
    )
    stop_reason: str = Field(description="Why the search loop should stop or continue.")


class GraphPosition(StrictModel):
    x: float
    y: float


class ReactFlowNodeData(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)
    constraintViolated: bool
    constraintReason: str


class ReactFlowNode(StrictModel):
    id: str
    type: str
    data: ReactFlowNodeData = Field(
        description="Dictionary containing the rubric fields and synthesized values.",
    )
    position: GraphPosition


class ReactFlowEdge(StrictModel):
    id: str
    source: str
    target: str


class ReactFlowGraphData(StrictModel):
    nodes: list[ReactFlowNode]
    edges: list[ReactFlowEdge]


class SessionGraphNode(BaseModel):
    model_config = ConfigDict(strict=True)

    id: str
    type: str
    source: str
    title: str
    subtitle: str
    status: str
    group: str
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    summary: str


class SessionGraphEdge(StrictModel):
    id: str
    from_: str = Field(alias="from")
    to: str
    label: str
    strength: float = Field(ge=0, le=1)


class SessionGraph(StrictModel):
    nodes: list[SessionGraphNode]
    edges: list[SessionGraphEdge]


class SessionMatrixColumn(BaseModel):
    model_config = ConfigDict(strict=True)

    key: str
    label: str
    type: str
    highlight_best: bool = False


class SessionMatrixCell(BaseModel):
    model_config = ConfigDict(strict=True)

    value: Any = None
    display: str
    rank: int | None = None
    sentiment: str | None = None


class SessionMatrixRow(BaseModel):
    model_config = ConfigDict(strict=True)

    node_id: str
    cells: dict[str, SessionMatrixCell]


class SessionMatrix(StrictModel):
    rubric: str
    columns: list[SessionMatrixColumn]
    rows: list[SessionMatrixRow]


class SessionDigestSignal(StrictModel):
    label: str
    kind: str


class SessionDigestEntry(BaseModel):
    model_config = ConfigDict(strict=True)

    node_id: str
    relevance: float
    summary: str
    signals: list[SessionDigestSignal] = Field(default_factory=list)
    source_note: str


class SessionDigestStats(StrictModel):
    total: int
    ready: int
    extracting: int
    pending: int


class SessionDigest(StrictModel):
    theme: str
    theme_signals: list[str]
    stats: SessionDigestStats
    entries: list[SessionDigestEntry]


class UnifiedSession(StrictModel):
    session_id: str
    query: str
    domain: str
    status: str
    graph: SessionGraph
    matrix: SessionMatrix
    digest: SessionDigest


class SessionConstraintRequest(StrictModel):
    session: UnifiedSession
    user_constraint: str | None = Field(default=None, min_length=1)


class SynthesizedAttribute(StrictModel):
    label: str = Field(min_length=1, max_length=80)
    value: str = Field(max_length=500)


class SynthesizedNode(StrictModel):
    id: str
    type: str = "research"
    title: str = Field(min_length=1, max_length=200)
    url: str = Field(default="", max_length=MAX_SYNTHESIZED_URL_CHARS)
    sourceType: Literal["seed", "discovered"]
    aiRank: float
    aiReason: str = Field(min_length=1, max_length=700)
    summary: str = Field(min_length=1, max_length=900)
    constraintViolated: bool
    constraintReason: str = Field(max_length=120)
    attributes: list[SynthesizedAttribute] = Field(default_factory=list, max_length=18)
    chips: list[str] = Field(default_factory=list, max_length=6)
    sourceLabel: str = ""
    kindLabel: str = ""
    statusLabel: str = ""


class SynthesizedGraphData(StrictModel):
    nodes: list[SynthesizedNode] = Field(min_length=1, max_length=MAX_SYNTHESIZED_GRAPH_NODES)
    edges: list[ReactFlowEdge] = Field(default_factory=list, max_length=MAX_SYNTHESIZED_GRAPH_EDGES)


StructuredModel = TypeVar("StructuredModel", bound=BaseModel)


def _debug_truncate(value: Any, limit: int = DEBUG_VALUE_PREVIEW_CHARS) -> str:
    text = str(value).replace("\n", " ").replace("\r", " ").strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...<{len(text)} chars>"


def _debug_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=True)
    except TypeError:
        return _debug_truncate(value, limit=400)


def _should_investigate_further(user_prompt: str) -> bool:
    prompt = user_prompt.lower()
    if DISCOVERY_ENABLED_BY_DEFAULT:
        return True
    investigation_tokens = (
        "review",
        "reviews",
        "investigate",
        "investigation",
        "external",
        "reddit",
        "forum",
        "forums",
        "benchmark",
        "benchmarks",
        "youtube",
        "look up",
        "research more",
        "dig deeper",
        "pros and cons",
    )
    return any(token in prompt for token in investigation_tokens)


def _wants_external_reviews(user_prompt: str) -> bool:
    prompt = user_prompt.lower()
    review_tokens = (
        "review",
        "reviews",
        "external",
        "reddit",
        "forum",
        "forums",
        "complaint",
        "complaints",
        "user feedback",
        "real world",
        "real-world",
        "owner",
        "owners",
    )
    return any(token in prompt for token in review_tokens)


def _extension_pref_enabled(key: str, default: bool = False) -> bool:
    raw = str(extension_preferences.get(key, "true" if default else "false")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _extension_firecrawl_budget() -> int:
    return DEFAULT_FIRECRAWL_QUERY_BUDGET if _extension_pref_enabled("enable_discovery") else 0


def _rubric_axis_config_for_domain(domain: str) -> dict[str, str]:
    if domain == "housing":
        return {
            "x_axis_label": "Cost efficiency",
            "x_axis_score_field": "Cost Efficiency Score",
            "x_axis_low": "Expensive for the fit",
            "x_axis_high": "Efficient for the fit",
            "y_axis_label": "Fit confidence",
            "y_axis_score_field": "Fit Confidence Score",
            "y_axis_low": "Weak match",
            "y_axis_high": "Strong match",
        }

    if domain == "products":
        return {
            "x_axis_label": "Value for money",
            "x_axis_score_field": "Value Score",
            "x_axis_low": "Poor value",
            "x_axis_high": "Strong value",
            "y_axis_label": "Cooling confidence",
            "y_axis_score_field": "Cooling Confidence Score",
            "y_axis_low": "Weak cooling",
            "y_axis_high": "Strong cooling",
        }

    return {
        "x_axis_label": "Evidence strength",
        "x_axis_score_field": "Evidence Strength Score",
        "x_axis_low": "Thin evidence",
        "x_axis_high": "Strong evidence",
        "y_axis_label": "Prompt fit",
        "y_axis_score_field": "Prompt Fit Score",
        "y_axis_low": "Loose fit",
        "y_axis_high": "Strong fit",
    }


def _supports_response_format_for_schema(schema_name: str) -> bool:
    model_name = OPENROUTER_MODEL.lower()
    if schema_name != "synthesized_graph_data":
        return True
    incompatible_tokens = ("google/", "gemini", "google ai studio")
    return not any(token in model_name for token in incompatible_tokens)


def _schema_response_format(model: type[BaseModel], name: str) -> dict[str, Any]:
    schema = model.model_json_schema()
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": True,
            "schema": schema,
        },
    }


def _plain_json_instruction(model: type[BaseModel], name: str) -> str:
    schema = json.dumps(model.model_json_schema(), ensure_ascii=True)
    return (
        f"Return only valid JSON for schema '{name}'. "
        "Do not wrap it in markdown. "
        f"JSON schema: {schema}"
    )


def _tabs_to_context(active_tabs: list[ActiveTab]) -> str:
    chunks = []
    for index, tab in enumerate(active_tabs, start=1):
        chunks.append(f"Tab {index}\nURL: {tab.url}\nSummary: {tab.summary}")
    return "\n\n".join(chunks)


def _tab_debug_payload(tab: ActiveTab | DomTab) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "url": str(tab.url),
    }
    if isinstance(tab, DomTab):
        payload["title"] = _debug_truncate(tab.title or str(tab.url), 80)
        payload["dom_chars"] = len(tab.dom)
    else:
        payload["summary"] = _debug_truncate(tab.summary, 220)
    return payload


def _prompt_keywords(user_prompt: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", user_prompt.lower())
    stopwords = {
        "the", "and", "for", "with", "that", "this", "from", "have", "been", "looking",
        "graph", "show", "find", "more", "like", "what", "into", "your", "their",
        "near", "under", "over", "than", "into", "about", "pages", "page",
    }
    keywords: list[str] = []
    for word in words:
        if len(word) < 3 or word in stopwords:
            continue
        if word not in keywords:
            keywords.append(word)
    return keywords[:12]


def _entry_relevance_score(entry: dict[str, Any], user_prompt: str) -> int:
    title = str(entry.get("title", "")).lower()
    url = str(entry.get("url", "")).lower()
    combined = f"{title} {url}"
    prompt = user_prompt.lower()
    score = 0

    negative_tokens = ["firecrawl", "openrouter", "dashboard", "usage", "billing", "docs", "synapse"]
    if any(token in combined for token in negative_tokens):
        score -= 8
    if "/s?" in url or "search results" in combined:
        score -= 4
    if "cooler" in prompt and "stand" in combined and all(token not in combined for token in ["cool", "cooler", "cooling"]):
        score -= 6

    for keyword in _prompt_keywords(user_prompt):
        if keyword in title:
            score += 5
        elif keyword in combined:
            score += 2
    if "amazon" in combined and "amazon" in user_prompt.lower():
        score += 2
    if "zillow" in combined and "zillow" in user_prompt.lower():
        score += 2
    if "apartments" in combined and "rental" in user_prompt.lower():
        score += 2
    score -= min(int(entry.get("dom", "") and len(str(entry.get("dom", ""))) / 100000), 3)
    return score


def _select_relevant_extension_entries(
    entries: list[dict[str, Any]],
    user_prompt: str,
    max_tabs: int,
) -> list[dict[str, Any]]:
    filtered_entries = [
        entry
        for entry in entries
        if "localhost" not in str(entry.get("url", "")).lower()
        and "127.0.0.1" not in str(entry.get("url", "")).lower()
    ]
    if not filtered_entries:
        filtered_entries = entries

    scored_entries = [
        (
            entry,
            _entry_relevance_score(entry, user_prompt),
            int(entry.get("timestamp", 0)),
        )
        for entry in filtered_entries
    ]
    scored = sorted(
        scored_entries,
        key=lambda item: (item[1], item[2]),
        reverse=True,
    )
    if any(score > 0 for _, score, _ in scored):
        scored = [item for item in scored if item[1] > 0]

    selected: list[dict[str, Any]] = []
    total_dom_chars = 0
    for entry, _, _ in scored:
        dom = str(entry.get("dom", ""))
        dom_len = len(dom)
        if selected and total_dom_chars + dom_len > MAX_EXTENSION_HISTORY_DOM_CHARS:
            continue
        selected.append(entry)
        total_dom_chars += dom_len
        if len(selected) >= max_tabs:
            break

    if selected:
        return selected

    return filtered_entries[:max_tabs]


def _prune_extension_history(now_seconds: int | None = None) -> None:
    now = now_seconds if now_seconds is not None else int(time.time())
    cutoff = now - EXTENSION_RETENTION_SECONDS
    stale_keys = [url for url, entry in extension_history.items() if int(entry.get("timestamp", 0)) < cutoff]
    for key in stale_keys:
        extension_history.pop(key, None)


def _render_dom_summary(tab: DomTab, summary: dict[str, Any]) -> str:
    key_points = summary.get("key_points", [])
    facts = summary.get("facts", [])
    entities = summary.get("entities", [])
    one_sentence = str(summary.get("one_sentence_summary", "")).strip()
    page_type = str(summary.get("page_type", "")).strip()
    inferred_title = str(summary.get("title", "")).strip() or tab.title or str(tab.url)

    lines = [
        f"Title: {inferred_title}",
        f"Page type: {page_type or 'Unknown'}",
        f"Summary: {one_sentence or 'No summary extracted.'}",
    ]

    if key_points:
        points = "; ".join(str(point) for point in key_points[:6])
        lines.append(f"Key points: {points}")

    if entities:
        entity_bits = []
        for entity in entities[:6]:
            if isinstance(entity, dict):
                name = str(entity.get("name", "")).strip()
                entity_type = str(entity.get("type", "")).strip()
                if name:
                    entity_bits.append(f"{name} ({entity_type or 'entity'})")
        if entity_bits:
            lines.append(f"Entities: {', '.join(entity_bits)}")

    if facts:
        fact_bits = []
        for fact in facts[:8]:
            if isinstance(fact, dict):
                label = str(fact.get("label", "")).strip()
                value = str(fact.get("value", "")).strip()
                if label and value:
                    fact_bits.append(f"{label}: {value}")
        if fact_bits:
            lines.append(f"Facts: {'; '.join(fact_bits)}")

    return " | ".join(lines)


async def _dom_tab_to_active_tab(tab: DomTab) -> ActiveTab:
    summary = await asyncio.to_thread(summarize_html, tab.dom)
    normalized_summary = _render_dom_summary(tab, summary)
    return ActiveTab(url=tab.url, summary=normalized_summary)


def _fallback_dom_summary(tab: DomTab) -> str:
    stripped = re.sub(r"<[^>]+>", " ", tab.dom)
    collapsed = re.sub(r"\s+", " ", stripped).strip()
    excerpt = collapsed[:1000]
    title = tab.title or str(tab.url)
    return f"Title: {title} | Page type: Unknown | Summary: {excerpt or 'Unable to summarize page.'}"


async def _safe_dom_tab_to_active_tab(tab: DomTab) -> ActiveTab:
    try:
        active_tab = await _dom_tab_to_active_tab(tab)
        logger.debug(
            "DOM tab normalized payload=%s",
            _debug_json(
                {
                    **_tab_debug_payload(tab),
                    "summary": _debug_truncate(active_tab.summary, 260),
                }
            ),
        )
        return active_tab
    except SummarizerError as exc:
        logger.warning("DOM summarizer failed for %s; using fallback summary. error=%s", tab.url, exc)
        fallback = ActiveTab(url=tab.url, summary=_fallback_dom_summary(tab))
        logger.debug(
            "DOM tab fallback payload=%s",
            _debug_json(
                {
                    **_tab_debug_payload(tab),
                    "summary": _debug_truncate(fallback.summary, 260),
                }
            ),
        )
        return fallback


async def _structured_llm_call(
    *,
    model_type: type[StructuredModel],
    schema_name: str,
    system_prompt: str,
    user_prompt: str,
) -> StructuredModel:
    base_system_prompt = system_prompt.strip()
    base_user_prompt = user_prompt.strip()
    last_error: Exception | None = None
    last_content = ""
    supports_response_format = _supports_response_format_for_schema(schema_name)

    for attempt in range(1, MAX_STRUCTURED_LLM_ATTEMPTS + 1):
        attempt_system_prompt = base_system_prompt
        attempt_user_prompt = base_user_prompt
        if attempt > 1:
            attempt_system_prompt = (
                f"{base_system_prompt} "
                "The previous attempt returned malformed or oversized JSON. "
                "Return only compact valid JSON that fully matches the schema. "
                "Keep strings concise, prefer canonical URLs without tracking or session parameters, "
                "and drop low-priority discovered items before truncating required fields."
            )
            attempt_user_prompt = (
                f"{base_user_prompt}\n\n"
                "Retry instructions:\n"
                "- Return valid JSON only.\n"
                "- If the answer would be too large, keep all seed items and only the strongest discovered items.\n"
                "- Use concise summaries and reasons.\n"
                "- Prefer canonical URLs and remove tracking, affiliate, checkout, and session query parameters."
            )
        if not supports_response_format:
            attempt_system_prompt = (
                f"{attempt_system_prompt} "
                "The provider does not support structured response formats for this request. "
                "You must output raw JSON text only, with no markdown fences or commentary."
            )
            attempt_user_prompt = (
                f"{attempt_user_prompt}\n\n"
                f"{_plain_json_instruction(model_type, schema_name)}"
            )

        logger.debug(
            "LLM call start schema=%s attempt=%s/%s response_format=%s system_chars=%s user_chars=%s user_preview=%r",
            schema_name,
            attempt,
            MAX_STRUCTURED_LLM_ATTEMPTS,
            supports_response_format,
            len(attempt_system_prompt),
            len(attempt_user_prompt),
            _debug_truncate(attempt_user_prompt, 220),
        )
        request_kwargs: dict[str, Any] = {
            "model": OPENROUTER_MODEL,
            "messages": [
                {"role": "system", "content": attempt_system_prompt},
                {"role": "user", "content": attempt_user_prompt},
            ],
            "temperature": 0.2,
        }
        if supports_response_format:
            request_kwargs["response_format"] = _schema_response_format(model_type, schema_name)

        try:
            response = await llm_client.chat.completions.create(**request_kwargs)
        except Exception as exc:
            last_error = exc
            error_text = str(exc)
            unsupported_response_format = supports_response_format and any(
                token in error_text.lower()
                for token in ["invalid_argument", "invalid argument", "google ai studio", "response_format", "json_schema"]
            )
            logger.warning(
                "LLM provider call failed schema=%s attempt=%s/%s response_format=%s error=%s",
                schema_name,
                attempt,
                MAX_STRUCTURED_LLM_ATTEMPTS,
                supports_response_format,
                exc,
            )
            if unsupported_response_format:
                supports_response_format = False
                continue
            if attempt < MAX_STRUCTURED_LLM_ATTEMPTS:
                continue
            raise

        content = response.choices[0].message.content
        if not content:
            last_error = ValueError(f"LLM returned empty content for {schema_name}")
            logger.warning(
                "LLM call empty schema=%s attempt=%s/%s",
                schema_name,
                attempt,
                MAX_STRUCTURED_LLM_ATTEMPTS,
            )
            if attempt < MAX_STRUCTURED_LLM_ATTEMPTS:
                continue
            raise last_error

        last_content = content
        logger.debug(
            "LLM call success schema=%s attempt=%s/%s content_chars=%s content_preview=%r",
            schema_name,
            attempt,
            MAX_STRUCTURED_LLM_ATTEMPTS,
            len(content),
            _debug_truncate(content, 220),
        )

        try:
            parsed = json.loads(content)
            return model_type.model_validate(parsed)
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = exc
            logger.warning(
                "LLM structured parse failed schema=%s attempt=%s/%s error=%s content_preview=%r",
                schema_name,
                attempt,
                MAX_STRUCTURED_LLM_ATTEMPTS,
                exc,
                _debug_truncate(content, 400),
            )
            if attempt < MAX_STRUCTURED_LLM_ATTEMPTS:
                continue

    preview = _debug_truncate(last_content, 1200)
    raise ValueError(
        f"LLM returned invalid structured output for {schema_name}: {type(last_error).__name__}: "
        f"{last_error}. content_preview={preview}"
    ) from last_error


async def _build_rubric(payload: SynthesizeRequest, base_context: str) -> ComparisonRubric:
    domain = _infer_session_domain(payload.user_prompt, None)
    axis_config = _rubric_axis_config_for_domain(domain)
    inferred_constraints: list[str] = []
    if payload.user_constraint:
        inferred_constraints.append(payload.user_constraint.strip())

    if domain == "housing":
        return ComparisonRubric(
            fields=[
                "Price USD",
                "Distance to Anchor",
                "Bedrooms",
                "Bathrooms",
                "Square Feet",
                "Rental Type",
                "Neighborhood",
                "Lease Term",
                "Source URL",
            ],
            inferred_constraints=inferred_constraints,
            default_ordering="Best housing fit with price awareness",
            seed_patterns=[
                "Emphasis on affordability and commute fit",
                "Compare concrete listing facts instead of marketing copy",
                "Preserve captured listings as the primary decision set",
            ],
            **axis_config,
        )

    if domain == "products":
        return ComparisonRubric(
            fields=[
                "Price USD",
                "External Review Consensus",
                "Common Complaints",
                "Noise Level (dB)",
                "Cooling Performance",
                "Fan Speed (RPM)",
                "Fan Type & Count",
                "Laptop Size Compatibility",
                "USB Hub/Connectivity",
                "Dust Protection",
                "Source URL",
            ],
            inferred_constraints=inferred_constraints,
            default_ordering="Best cooling performance among constraint matches, then price",
            seed_patterns=[
                "Prioritize concrete performance specs over generic copy",
                "Separate seller claims from external owner or reviewer sentiment",
                "Preserve the currently captured product set as the main comparison board",
                "Highlight budget, noise, and cooling tradeoffs explicitly",
            ],
            **axis_config,
        )

    return ComparisonRubric(
        fields=[
            "Price USD",
            "Key Differentiator",
            "Strengths",
            "Tradeoffs",
            "Source URL",
        ],
        inferred_constraints=inferred_constraints,
        default_ordering="Most relevant options first",
        seed_patterns=[
            "Prefer concise comparisons grounded in captured evidence",
            "Use discovery only when the prompt explicitly asks for broader investigation",
        ],
        **axis_config,
    )


async def _evaluate_context(
    *,
    user_prompt: str,
    rubric: ComparisonRubric,
    context: str,
    remaining_query_budget: int,
) -> EvaluationState:
    return await _structured_llm_call(
        model_type=EvaluationState,
        schema_name="evaluation_state",
        system_prompt=(
            "You evaluate whether the collected context is enough to synthesize a strict "
            "React Flow graph. If information is missing, provide only the smallest number of specific "
            "web search queries. Do not request searches for information already present. "
            "Minimize Firecrawl credit usage. Search only when likely to add materially new qualifying "
            "items or recent discussion signals. Search for candidate listings that expose price, "
            "distance or neighborhood fit, rental or product type, source URLs, and independent review "
            "or complaint signals when the user asks about reviews."
        ),
        user_prompt=(
            f"User prompt:\n{user_prompt}\n\n"
            f"Rubric fields:\n{rubric.fields}\n\n"
            f"Inferred constraints:\n{rubric.inferred_constraints}\n\n"
            f"Seed patterns:\n{rubric.seed_patterns}\n\n"
            f"Default ordering:\n{rubric.default_ordering}\n\n"
            f"Remaining Firecrawl query budget:\n{remaining_query_budget}\n\n"
            f"Known context:\n{context}\n\n"
            "Decide whether the context is complete. If incomplete, identify missing "
            "fields and generate no more than three targeted search queries. Set "
            "should_search_more=false when the remaining budget is low or the known context already "
            "contains enough credible candidates."
        ),
    )


def _extract_firecrawl_markdown(result: Any) -> str:
    if result is None:
        return "No search results returned."

    if isinstance(result, str):
        return result

    if isinstance(result, dict):
        if isinstance(result.get("markdown"), str):
            return result["markdown"]
        if isinstance(result.get("data"), list):
            rendered = []
            for item in result["data"]:
                if isinstance(item, dict):
                    title = item.get("title") or item.get("url") or "Result"
                    markdown = item.get("markdown") or item.get("description") or item.get("content") or ""
                    rendered.append(f"### {title}\n{markdown}")
                else:
                    rendered.append(str(item))
            return "\n\n".join(rendered) if rendered else "No search results returned."
        return json.dumps(result, ensure_ascii=True)

    data = getattr(result, "data", None)
    if isinstance(data, list):
        return _extract_firecrawl_markdown({"data": data})

    return str(result)


async def _run_firecrawl_search(query: str) -> str:
    try:
        logger.debug("Firecrawl search start query=%r limit=%s", query, FIRECRAWL_RESULTS_PER_QUERY)
        result = await asyncio.wait_for(
            asyncio.to_thread(firecrawl_client.search, query, limit=FIRECRAWL_RESULTS_PER_QUERY),
            timeout=20,
        )
        markdown = _extract_firecrawl_markdown(result)
        logger.debug(
            "Firecrawl search success query=%r markdown_chars=%s markdown_preview=%r",
            query,
            len(markdown),
            _debug_truncate(markdown, 220),
        )
        return f"Search query: {query}\n{markdown}"
    except Exception as exc:
        logger.exception("Firecrawl search failed for query=%r", query)
        return f"Search query: {query}\nSearch failed: {type(exc).__name__}: {exc}"


async def _synthesize_graph(
    *,
    user_prompt: str,
    user_constraint: str | None = None,
    rubric: ComparisonRubric,
    context: str,
) -> ReactFlowGraphData:
    graph = await _structured_llm_call(
        model_type=SynthesizedGraphData,
        schema_name="synthesized_graph_data",
        system_prompt=(
            "You synthesize browser-tab research into React Flow graph JSON. "
            "Each node must include top-level fields title, url, sourceType, aiRank, aiReason, summary, "
            "constraintViolated, constraintReason, and attributes. Use attributes for rubric facts as "
            "{label, value} pairs, using exact rubric field names whenever possible. "
            f"Every node must also include two quantifiable rubric scores on a 0-100 scale in attributes named "
            f"'{rubric.x_axis_score_field}' and '{rubric.y_axis_score_field}'. "
            f"'{rubric.x_axis_score_field}' measures {rubric.x_axis_label.lower()} where 0 means "
            f"'{rubric.x_axis_low}' and 100 means '{rubric.x_axis_high}'. "
            f"'{rubric.y_axis_score_field}' measures {rubric.y_axis_label.lower()} where 0 means "
            f"'{rubric.y_axis_low}' and 100 means '{rubric.y_axis_high}'. "
            "Do not stuff product specs, listing facts, or rankings into constraintReason. "
            "constraintReason is only for the user constraint result and must be empty when no user constraint is provided. "
            "Create meaningful edges between related products, concepts, tradeoffs, or evidence. Keep the user-provided seed items "
            "in the graph and add newly found items as additional nodes rather than replacing the seed set. "
            f"Use at most {MAX_SYNTHESIZED_GRAPH_NODES} total nodes and at most {MAX_SYNTHESIZED_GRAPH_EDGES} edges, "
            "keeping all seed items and filling the remaining capacity with the strongest discovered items only. "
            f"Keep each url under {MAX_SYNTHESIZED_URL_CHARS} characters and prefer canonical URLs without tracking, checkout, "
            "affiliate, or session parameters. sourceType is either seed or discovered. "
            "When external review context is present, include External Review Consensus and Common Complaints "
            "as attributes, distinguish owner/reviewer sentiment from seller claims, and cite recent internet "
            "listing or discussion signals when possible."
        ),
        user_prompt=(
            f"User prompt:\n{user_prompt}\n\n"
            f"User constraint:\n{user_constraint or 'None'}\n\n"
            f"Rubric fields:\n{rubric.fields}\n\n"
            f"Inferred constraints:\n{rubric.inferred_constraints}\n\n"
            f"Seed patterns:\n{rubric.seed_patterns}\n\n"
            f"Default ordering:\n{rubric.default_ordering}\n\n"
            f"Chart rubric X axis:\n{rubric.x_axis_label} via attribute '{rubric.x_axis_score_field}' "
            f"on a 0-100 scale ({rubric.x_axis_low} -> {rubric.x_axis_high})\n\n"
            f"Chart rubric Y axis:\n{rubric.y_axis_label} via attribute '{rubric.y_axis_score_field}' "
            f"on a 0-100 scale ({rubric.y_axis_low} -> {rubric.y_axis_high})\n\n"
            f"Collected context:\n{context}\n\n"
            "Return strict graph data with nodes and edges only. Each node must contain: "
            "id, type, title, url, sourceType, aiRank, aiReason, summary, constraintViolated, constraintReason, and attributes. "
            "Put the domain facts in attributes instead of embedding them into aiReason or constraintReason. "
            "Each attribute should be a short label/value pair. Use exact rubric field names for labels whenever possible. "
            "For review prompts, preserve review/forum evidence in attributes such as External Review Consensus, "
            "Common Complaints, Reddit Consensus, Review Summary, or Owner Feedback. "
            "Keep all seed items, then choose only the most relevant discovered items if there are more candidates than the graph can hold."
        ),
    )        
    if isinstance(graph, ReactFlowGraphData):
        return graph
    return _synthesized_graph_to_react_flow(graph)


def _normalize_data_key(key: str) -> str:
    return key.lower().replace(" ", "").replace("_", "").replace("-", "").replace("/", "").replace("(", "").replace(")", "").replace(":", "")


def _stringify_data_value(value: Any, fallback: str = "") -> str:
    if isinstance(value, str):
        return value.strip() or fallback
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, int | float):
        return str(value)
    return fallback


def _parse_data_number(value: Any, fallback: float = 0) -> float:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"-?\d+(?:\.\d+)?", value.replace(",", ""))
        if match:
            return float(match.group(0))
    return fallback


def _extract_numeric_values(value: Any) -> list[float]:
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, str):
        normalized = value.replace(",", "").replace("–", "-").replace("—", "-")
        return [float(match) for match in re.findall(r"\d+(?:\.\d+)?", normalized)]
    return []


def _parse_range_midpoint(value: Any, fallback: float = 0) -> float:
    numbers = _extract_numeric_values(value)
    if not numbers:
        return fallback
    if len(numbers) == 1:
        return numbers[0]
    return sum(numbers[:2]) / 2


def _parse_price_value(value: Any, fallback: float = 0) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return fallback

    text = value.replace(",", "").strip()
    money_matches = [
        float(match)
        for match in re.findall(r"\$\s*(\d+(?:\.\d+)?)", text)
    ]
    if not money_matches:
        money_matches = [
            float(match)
            for match in re.findall(r"(\d+(?:\.\d+)?)\s*(?:usd|dollars?)", text, flags=re.IGNORECASE)
        ]
    if money_matches:
        if len(money_matches) == 1:
            return money_matches[0]
        return sum(money_matches[:2]) / 2

    lowered = text.lower()
    if "price" in lowered or "cost" in lowered:
        numbers = _extract_numeric_values(text)
        plausible = [number for number in numbers if 0 < number <= 100000]
        if plausible:
            if len(plausible) == 1:
                return plausible[0]
            return sum(plausible[:2]) / 2
    return fallback


def _derive_review_sentiment(raw_data: dict[str, Any], summary: str, ai_reason: str) -> tuple[str, str]:
    review_blob_parts: list[str] = [summary, ai_reason]
    for key in [
        "Review Summary",
        "Reviews",
        "Customer Reviews",
        "Sentiment",
        "Discussion Summary",
        "Reddit Consensus",
    ]:
        value = _stringify_data_value(raw_data.get(key))
        if value:
            review_blob_parts.append(value)

    blob = " ".join(part for part in review_blob_parts if part).lower()
    if not blob:
        return ("unknown", "Unknown")

    positive_hits = sum(
        blob.count(token)
        for token in [
            "positive",
            "well-reviewed",
            "recommended",
            "popular",
            "praised",
            "great",
            "excellent",
            "quiet",
            "best",
            "reliable",
        ]
    )
    negative_hits = sum(
        blob.count(token)
        for token in [
            "negative",
            "complaint",
            "complaints",
            "loud",
            "noisy",
            "issues",
            "problem",
            "problems",
            "weak",
            "expensive",
            "criticized",
        ]
    )
    if positive_hits > negative_hits:
        return ("positive", "Generally positive")
    if negative_hits > positive_hits:
        return ("negative", "Generally mixed / negative")
    return ("neutral", "Mixed / unclear")


def _lookup_data_value(data: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value

    normalized_items = {
        _normalize_data_key(key): value
        for key, value in data.items()
        if value not in (None, "")
    }
    for key in keys:
        normalized_key = _normalize_data_key(key)
        if normalized_key in normalized_items:
            return normalized_items[normalized_key]
    return None


def _first_interesting_data_string(data: dict[str, Any]) -> str:
    ignore = {
        "source type",
        "constraint reason",
        "ai reason",
        "summary",
        "description",
        "status",
        "sourcelabel",
        "statuslabel",
    }
    for key, value in data.items():
        if key.lower() in ignore:
            continue
        text = _stringify_data_value(value)
        if 3 <= len(text) <= 120 and re.search(r"[A-Za-z]", text):
            return text
    return ""


def _title_case_label(value: str) -> str:
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", value.replace("_", " ").replace("-", " "))
    return " ".join(word.capitalize() for word in spaced.split())


def _hostname_label(url: str) -> str:
    try:
        from urllib.parse import urlparse

        hostname = urlparse(url).hostname or ""
        return hostname.replace("www.", "").upper() or "Captured"
    except Exception:
        return "Captured"


def _build_backend_metrics(
    raw_data: dict[str, Any],
    price_usd: float,
    distance_miles: float,
    bedrooms: float,
    bathrooms: float,
    square_feet: float,
    rental_type: str,
) -> list[dict[str, str]]:
    ordered_keys = [
        "Price USD",
        "Price",
        "Price Range",
        "External Review Consensus",
        "Common Complaints",
        "Review Summary",
        "Reddit Consensus",
        "Owner Feedback",
        "Cooling Performance",
        "Cooling Performance Rating",
        "Fan Speed (RPM)",
        "Max Fan Speed (RPM)",
        "Noise Level (dB)",
        "Number of Fans",
        "Fan Configuration",
        "Fan Type & Count",
        "Cooling Mechanism",
        "Cooling Method",
        "Laptop Size Compatibility",
        "Compatible Laptop Sizes",
        "USB Hub/Ports",
        "USB Hub Ports",
        "RGB Lighting",
        "Dust Filtration",
        "Dust Protection",
        "Distance to Anchor",
        "Neighborhood",
        "Rental Type",
        "Bedrooms",
        "Bathrooms",
        "Square Feet",
    ]

    metrics: list[dict[str, str]] = []
    for key in ordered_keys:
        value = _lookup_data_value(raw_data, [key])
        text = _stringify_data_value(value)
        if not text:
            continue
        metrics.append({"label": _title_case_label(key), "value": text})
        if len(metrics) >= 6:
            return metrics

    if not metrics:
        generic_metrics = [
            {"label": _title_case_label(key), "value": _stringify_data_value(value)}
            for key, value in raw_data.items()
            if _stringify_data_value(value)
            and _normalize_data_key(key)
            not in {
                "title", "url", "sourceurl", "sourcetype", "airank", "aireason", "constraintviolated", "constraintreason",
            }
        ]
        if generic_metrics:
            return generic_metrics[:6]

        metrics = [
            {"label": "Price", "value": f"${price_usd:g}" if price_usd else "$0"},
            {"label": "Signal", "value": f"{distance_miles:g}" if distance_miles else "0"},
            {"label": "Type", "value": rental_type or "Unknown"},
            {"label": "Shape", "value": f"{bedrooms:g}/{bathrooms:g}/{square_feet:g}"},
        ]

    return metrics[:6]


def _build_backend_chips(raw_data: dict[str, Any], metrics: list[dict[str, str]], source_type: str) -> list[str]:
    preferred_keys = [
        "Cooling Mechanism",
        "Cooling Method",
        "Build Material",
        "Power Source",
        "Neighborhood",
        "Lease Term",
        "RGB Lighting",
        "Dust Filtration",
        "Dust Protection",
        "Noise Control Features",
        "External Review Consensus",
        "Common Complaints",
        "Reddit Consensus",
    ]
    chips: list[str] = []
    for key in preferred_keys:
        value = _stringify_data_value(_lookup_data_value(raw_data, [key]))
        if value:
            chips.append(f"{_title_case_label(key)} {value}")
        if len(chips) >= 4:
            return chips

    for metric in metrics:
        chips.append(f"{metric['label']} {metric['value']}")
        if len(chips) >= 4:
            return chips

    chips.append("AI found" if source_type == "discovered" else "Captured")
    return chips[:4]


def _derive_noise_display(raw_data: dict[str, Any], summary: str, ai_reason: str) -> str:
    explicit = _stringify_data_value(
        _lookup_data_value(raw_data, ["noiseLevelDb", "Noise Level (dB)", "Noise Level", "Noise"])
    )
    if explicit:
        return explicit

    combined = f"{summary} {ai_reason} {' '.join(str(value) for value in raw_data.values())}".lower()
    if "quiet" in combined:
        return "Quiet"
    if "loud" in combined or "noisy" in combined:
        return "Loud"
    if "improved noise" in combined or "noise profile" in combined:
        return "Moderate"
    return "Unknown"


def _derive_cooling_performance(raw_data: dict[str, Any], fan_speed_rpm: float, ai_reason: str) -> tuple[str, float]:
    explicit = _stringify_data_value(
        _lookup_data_value(raw_data, ["coolingPerformance", "Cooling Performance", "Cooling Performance Rating"])
    )
    if explicit:
        return explicit, _parse_range_midpoint(explicit, fan_speed_rpm)

    combined = f"{ai_reason} {' '.join(str(value) for value in raw_data.values())}".lower()
    if fan_speed_rpm >= 4200 or "maximum thermal" in combined or "industrial-grade" in combined:
        return "Very high", max(fan_speed_rpm, 95)
    if fan_speed_rpm >= 2600 or "premium performance" in combined or "stronger cooling" in combined:
        return "High", max(fan_speed_rpm, 82)
    if fan_speed_rpm >= 1400 or "balanced cooling" in combined:
        return "Moderate", max(fan_speed_rpm, 68)
    if fan_speed_rpm > 0:
        return "Basic", fan_speed_rpm
    return "Unknown", 0


def _graph_debug_payload(graph: ReactFlowGraphData) -> list[dict[str, Any]]:
    debug_nodes: list[dict[str, Any]] = []
    for index, node in enumerate(graph.nodes):
        data = dict(node.data)
        metrics = data.get("metrics")
        chips = data.get("chips")
        debug_nodes.append(
            {
                "index": index,
                "id": node.id,
                "type": node.type,
                "title": _debug_truncate(
                    _lookup_data_value(data, ["title", "Title", "name", "Name", "Product Name", "productName"])
                    or _first_interesting_data_string(data)
                    or "",
                    80,
                ),
                "sourceType": _stringify_data_value(_lookup_data_value(data, ["sourceType", "Source Type", "source_type"])),
                "url": _debug_truncate(
                    _lookup_data_value(data, ["url", "URL", "Source URL", "sourceUrl", "source_url"]) or "",
                    80,
                ),
                "keys": sorted(data.keys()),
                "sample_values": {
                    key: _debug_truncate(value, 90)
                    for key, value in list(data.items())[:8]
                },
                "metrics": metrics[:4] if isinstance(metrics, list) else None,
                "chips": chips[:4] if isinstance(chips, list) else None,
            }
        )
    return debug_nodes


def _extension_entry_debug_payload(entry: dict[str, Any], user_prompt: str) -> dict[str, Any]:
    return {
        "title": _debug_truncate(entry.get("title", ""), 70),
        "url": _debug_truncate(entry.get("url", ""), 90),
        "score": _entry_relevance_score(entry, user_prompt),
        "timestamp": int(entry.get("timestamp", 0)),
        "dom_chars": len(str(entry.get("dom", ""))),
    }


def _synthesized_graph_to_react_flow(graph: SynthesizedGraphData) -> ReactFlowGraphData:
    nodes: list[ReactFlowNode] = []
    for index, node in enumerate(graph.nodes):
        data: dict[str, Any] = {
            "title": node.title,
            "url": node.url,
            "sourceType": node.sourceType,
            "aiRank": node.aiRank,
            "aiReason": node.aiReason,
            "summary": node.summary,
            "constraintViolated": node.constraintViolated,
            "constraintReason": node.constraintReason,
        }
        if node.sourceLabel:
            data["sourceLabel"] = node.sourceLabel
        if node.kindLabel:
            data["kindLabel"] = node.kindLabel
        if node.statusLabel:
            data["statusLabel"] = node.statusLabel
        if node.chips:
            data["chips"] = list(node.chips)

        for attribute in node.attributes:
            if attribute.label and attribute.value:
                data[attribute.label] = attribute.value

        nodes.append(
            ReactFlowNode(
                id=node.id,
                type=node.type or "research",
                data=data,
                position=GraphPosition(x=float(index * 320), y=0.0),
            )
        )

    return ReactFlowGraphData(nodes=nodes, edges=graph.edges)


def _canonicalize_graph_for_frontend(graph: ReactFlowGraphData) -> ReactFlowGraphData:
    payload = graph.model_dump()
    domain = _infer_session_domain("", graph)
    axis_config = _rubric_axis_config_for_domain(domain)
    for index, node in enumerate(payload.get("nodes", [])):
        raw_data = dict(node.get("data") or {})
        brand = _stringify_data_value(_lookup_data_value(raw_data, ["Brand", "brand"]))
        model = _stringify_data_value(_lookup_data_value(raw_data, ["Model", "model"]))
        title = (
            _stringify_data_value(_lookup_data_value(raw_data, ["title", "Title", "name", "Name", "Product Name", "productName"]))
            or " ".join(bit for bit in [brand, model] if bit).strip()
            or _first_interesting_data_string(raw_data)
            or f"Item {index + 1}"
        )
        url = _stringify_data_value(_lookup_data_value(raw_data, ["url", "URL", "Source URL", "sourceUrl", "source_url"]))
        source_type = _stringify_data_value(_lookup_data_value(raw_data, ["sourceType", "Source Type", "source_type"]), "seed").lower()
        source_type = "discovered" if source_type == "discovered" else "seed"
        source_label = _hostname_label(url) if url else _stringify_data_value(_lookup_data_value(raw_data, ["Source", "source", "Brand"]), "Captured")
        location_label = _stringify_data_value(_lookup_data_value(raw_data, ["locationLabel", "Neighborhood", "Location", "Brand", "Platform"]), "Unknown")
        kind_label = _stringify_data_value(_lookup_data_value(raw_data, ["kind", "Type", "Rental Type", "Cooling Method", "Cooling Mechanism", "Product Type"]), "Item")
        price_usd = _parse_price_value(_lookup_data_value(raw_data, ["priceUsd", "Price USD", "Price", "Price Range"]))
        distance_miles = _parse_data_number(_lookup_data_value(raw_data, ["distanceMiles", "Distance to Anchor", "Noise Level", "Noise Level (dB)"]))
        bedrooms = _parse_data_number(_lookup_data_value(raw_data, ["bedrooms", "Bedrooms", "Number of Fans", "Fan Count", "Fan Type/Count", "Fan Type & Count"]))
        bathrooms = _parse_data_number(_lookup_data_value(raw_data, ["bathrooms", "Bathrooms", "Adjustable Height Levels", "Modes"]))
        square_feet = _parse_data_number(_lookup_data_value(raw_data, ["squareFeet", "Square Feet", "Fan Speed (RPM)", "Max Fan Speed (RPM)", "Airflow", "Cooling Performance Rating"]))
        rental_type = _stringify_data_value(_lookup_data_value(raw_data, ["rentalType", "Rental Type", "Cooling Method", "Cooling Mechanism", "Maximum Laptop Size Supported"]), "Unknown")
        ai_rank = _parse_data_number(_lookup_data_value(raw_data, ["aiRank", "AI Rank", "rank"]), index + 1)
        combined_score = _parse_data_number(_lookup_data_value(raw_data, ["combinedScore", "Combined Score", "score"]), max(0, 100 - int(ai_rank) * 10))
        x_axis_score = _parse_data_number(
            _lookup_data_value(raw_data, [axis_config["x_axis_score_field"], "xAxisScore", "X Axis Score"]),
            combined_score,
        )
        y_axis_score = _parse_data_number(
            _lookup_data_value(raw_data, [axis_config["y_axis_score_field"], "yAxisScore", "Y Axis Score"]),
            combined_score,
        )
        ai_reason = _stringify_data_value(_lookup_data_value(raw_data, ["aiReason", "AI Reason", "reason"]))
        summary = (
            _stringify_data_value(_lookup_data_value(raw_data, ["summary", "oneSentenceSummary", "description"]))
            or ai_reason
            or f"{title} is part of the current workspace graph."
        )
        review_sentiment, review_sentiment_label = _derive_review_sentiment(raw_data, summary, ai_reason)
        fan_speed_rpm = _parse_data_number(_lookup_data_value(raw_data, ["Fan Speed (RPM)", "Max Fan Speed (RPM)", "fanSpeedRpm", "maxFanSpeedRpm"]))
        noise_display = _derive_noise_display(raw_data, summary, ai_reason)
        noise_level_db = _parse_range_midpoint(_lookup_data_value(raw_data, ["noiseLevelDb", "Noise Level (dB)", "Noise Level"]), 0)
        cooling_performance, cooling_performance_score = _derive_cooling_performance(raw_data, fan_speed_rpm, ai_reason)
        status_label = _stringify_data_value(_lookup_data_value(raw_data, ["status", "Status"])) or ("enriched" if source_type == "discovered" else "captured")
        metrics = _build_backend_metrics(raw_data, price_usd, distance_miles, bedrooms, bathrooms, square_feet, rental_type)
        chips = _build_backend_chips(raw_data, metrics, source_type)
        raw_copy = dict(raw_data)

        node["data"] = {
            **raw_data,
            "title": title,
            "url": url,
            "sourceLabel": source_label,
            "statusLabel": status_label,
            "kindLabel": kind_label,
            "summary": summary,
            "locationLabel": location_label,
            "priceUsd": price_usd,
            "distanceMiles": distance_miles,
            "fanSpeedRpm": fan_speed_rpm,
            "noiseLevelDb": noise_level_db,
            "noiseDisplay": noise_display,
            "coolingPerformance": cooling_performance,
            "coolingPerformanceScore": cooling_performance_score,
            "bedrooms": bedrooms,
            "bathrooms": bathrooms,
            "squareFeet": square_feet,
            "rentalType": rental_type,
            "combinedScore": combined_score,
            "xAxisScore": max(0, min(100, x_axis_score)),
            "yAxisScore": max(0, min(100, y_axis_score)),
            "xAxisLabel": axis_config["x_axis_label"],
            "xAxisLow": axis_config["x_axis_low"],
            "xAxisHigh": axis_config["x_axis_high"],
            "yAxisLabel": axis_config["y_axis_label"],
            "yAxisLow": axis_config["y_axis_low"],
            "yAxisHigh": axis_config["y_axis_high"],
            "aiRank": ai_rank,
            "aiReason": ai_reason,
            "reviewSentiment": review_sentiment,
            "reviewSentimentLabel": review_sentiment_label,
            "sourceType": source_type,
            "constraintViolated": bool(_lookup_data_value(raw_data, ["constraintViolated", "Constraint Violated"])),
            "constraintReason": _stringify_data_value(_lookup_data_value(raw_data, ["constraintReason", "Constraint Reason"])),
            "metrics": metrics,
            "chips": chips,
            "rawData": raw_copy,
        }

    return ReactFlowGraphData.model_validate(payload)


def _infer_session_domain(query: str, graph: ReactFlowGraphData | None) -> str:
    prompt = query.lower()
    blob = ""
    if graph is not None:
        blob = " ".join(
            " ".join(str(value) for value in dict(node.data).values())
            for node in graph.nodes
        ).lower()
    combined = f"{prompt} {blob}"
    if any(token in combined for token in ["apartment", "rental", "rent", "landlord", "studio", "bedroom", "lease", "housing"]):
        return "housing"
    if any(token in combined for token in ["cooler", "cooling pad", "laptop cooler", "rpm", "fan", "gaming laptop"]):
        return "products"
    return "research"


def _extract_price_constraint(user_constraint: str | None) -> tuple[str, float] | None:
    if not user_constraint:
        return None
    text = user_constraint.lower()
    match = re.search(r"(under|below|less than|<=?)\s*\$?\s*(\d+(?:\.\d+)?)", text)
    if match:
        return ("max", float(match.group(2)))
    match = re.search(r"(over|above|more than|>=?)\s*\$?\s*(\d+(?:\.\d+)?)", text)
    if match:
        return ("min", float(match.group(2)))
    return None


def _apply_constraint_to_data_dict(data: dict[str, Any], user_constraint: str | None) -> dict[str, Any]:
    next_data = dict(data)
    price_constraint = _extract_price_constraint(user_constraint)
    if not price_constraint:
        next_data["constraintViolated"] = False
        next_data["constraintReason"] = ""
        return next_data

    mode, threshold = price_constraint
    price_value = _parse_price_value(_lookup_data_value(next_data, ["priceUsd", "Price USD", "Price", "Price Range"]), 0)
    violated = False
    if mode == "max" and price_value > 0:
        violated = price_value > threshold
    elif mode == "min" and price_value > 0:
        violated = price_value < threshold

    if violated:
        existing_reason = _stringify_data_value(next_data.get("constraintReason"))
        next_data["constraintViolated"] = True
        next_data["constraintReason"] = existing_reason or (
            f"Above budget cap of ${threshold:,.0f}" if mode == "max" else f"Below minimum budget of ${threshold:,.0f}"
        )
        combined_score = _parse_data_number(next_data.get("combinedScore"), 0)
        if combined_score > 0:
            next_data["combinedScore"] = max(0, combined_score - 35)
        return next_data

    next_data["constraintViolated"] = False
    next_data["constraintReason"] = ""
    return next_data


def _apply_deterministic_constraints(graph: ReactFlowGraphData, user_constraint: str | None) -> ReactFlowGraphData:
    payload = graph.model_dump()
    for node in payload.get("nodes", []):
        node["data"] = _apply_constraint_to_data_dict(dict(node.get("data") or {}), user_constraint)

    return ReactFlowGraphData.model_validate(payload)


def _session_status_from_data(data: dict[str, Any]) -> str:
    if bool(data.get("constraintViolated")):
        return "flagged"
    return "ready"


def _session_group_for_node(node: ReactFlowNode) -> str:
    data = dict(node.data)
    source_type = _stringify_data_value(data.get("sourceType"), "seed")
    source_label = _stringify_data_value(data.get("sourceLabel")).lower()
    title = _stringify_data_value(data.get("title")).lower()
    kind_label = _stringify_data_value(data.get("kindLabel")).lower()
    if any(token in source_label for token in ["reddit", "forum"]) or any(token in title for token in ["consensus", "review", "feedback"]) or "review" in kind_label:
        return "reviews"
    return "options" if source_type == "seed" else "context"


def _session_type_for_node(node: ReactFlowNode) -> str:
    data = dict(node.data)
    source_label = _stringify_data_value(data.get("sourceLabel")).lower()
    title = _stringify_data_value(data.get("title")).lower()
    kind_label = _stringify_data_value(data.get("kindLabel"), node.type or "item").lower().replace(" ", "_")
    if any(token in source_label for token in ["reddit", "forum"]) or any(token in title for token in ["consensus", "review", "feedback"]) or "review" in kind_label:
        return "review"
    return kind_label or "item"


def _session_subtitle_for_node(data: dict[str, Any]) -> str:
    source_label = _stringify_data_value(data.get("sourceLabel")).lower()
    title = _stringify_data_value(data.get("title")).lower()
    if any(token in source_label for token in ["reddit", "forum"]) or any(token in title for token in ["consensus", "review", "feedback"]):
        review_sentiment = _stringify_data_value(data.get("reviewSentimentLabel"))
        summary = _stringify_data_value(data.get("summary"))
        if review_sentiment and review_sentiment != "Unknown":
            return review_sentiment
        if summary:
            return _debug_truncate(summary, 96)
        return _stringify_data_value(data.get("sourceLabel"), "External review")

    metric_bits: list[str] = []
    x_axis_score = _parse_data_number(data.get("xAxisScore"))
    y_axis_score = _parse_data_number(data.get("yAxisScore"))
    if x_axis_score > 0:
        metric_bits.append(f"X {int(round(x_axis_score))}")
    if y_axis_score > 0:
        metric_bits.append(f"Y {int(round(y_axis_score))}")
    price_usd = _parse_data_number(data.get("priceUsd"))
    if price_usd > 0:
        metric_bits.append(f"${price_usd:,.0f}")

    noise_display = _stringify_data_value(data.get("noiseDisplay"))
    cooling_performance = _stringify_data_value(data.get("coolingPerformance"))
    fan_speed_rpm = _parse_data_number(data.get("fanSpeedRpm"))
    if fan_speed_rpm > 0:
        metric_bits.append(f"{fan_speed_rpm:g} RPM")
    if noise_display and noise_display != "Unknown":
        metric_bits.append(f"Noise {noise_display}")
    if cooling_performance and cooling_performance != "Unknown":
        metric_bits.append(f"Cooling {cooling_performance}")

    distance_miles = _parse_data_number(data.get("distanceMiles"))
    if distance_miles > 0:
        metric_bits.append(f"{distance_miles:g} mi")

    rental_type = _stringify_data_value(data.get("rentalType"))
    if rental_type and rental_type != "Unknown":
        metric_bits.insert(0, rental_type)

    metrics = data.get("metrics")
    if not metric_bits and isinstance(metrics, list):
        metric_bits = [
            _stringify_data_value(metric.get("value"))
            for metric in metrics[:2]
            if isinstance(metric, dict) and _stringify_data_value(metric.get("value"))
        ]

    return " · ".join(metric_bits[:3]) or _stringify_data_value(data.get("sourceLabel"), "Captured")


def _session_metadata_for_node(data: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for key in [
        "priceUsd",
        "distanceMiles",
        "bedrooms",
        "bathrooms",
        "squareFeet",
        "combinedScore",
        "xAxisScore",
        "yAxisScore",
        "aiRank",
        "fanSpeedRpm",
        "noiseLevelDb",
        "coolingPerformanceScore",
    ]:
        value = data.get(key)
        if isinstance(value, (int, float)) and value not in (0, 0.0):
            metadata[key] = value

    for key in [
        "rentalType",
        "locationLabel",
        "sourceType",
        "statusLabel",
        "kindLabel",
        "noiseDisplay",
        "coolingPerformance",
        "reviewSentiment",
        "reviewSentimentLabel",
        "xAxisLabel",
        "xAxisLow",
        "xAxisHigh",
        "yAxisLabel",
        "yAxisLow",
        "yAxisHigh",
    ]:
        value = _stringify_data_value(data.get(key))
        if value and value != "Unknown":
            metadata[key] = value
    metadata["constraintViolated"] = bool(data.get("constraintViolated"))
    constraint_reason = _stringify_data_value(data.get("constraintReason"))
    if constraint_reason:
        metadata["constraintReason"] = constraint_reason

    raw_data = data.get("rawData")
    if isinstance(raw_data, dict):
        for raw_key in [
            "Brand",
            "Model",
            "Laptop Size Compatibility",
            "Compatible Laptop Sizes",
            "Cooling Mechanism",
            "Noise Level (dB)",
            "Price Range",
            "USB Hub/Connectivity",
        ]:
            raw_value = _stringify_data_value(raw_data.get(raw_key))
            if raw_value:
                metadata[_normalize_data_key(raw_key)] = raw_value

    return metadata


def _build_session_graph(graph: ReactFlowGraphData) -> SessionGraph:
    session_nodes: list[SessionGraphNode] = []
    for node in graph.nodes:
        data = dict(node.data)
        status = _session_status_from_data(data)
        tags = [str(chip) for chip in list(data.get("chips") or [])[:4]]
        if bool(data.get("constraintViolated")):
            tags = ["Constraint mismatch", *tags][:4]
        session_nodes.append(
            SessionGraphNode(
                id=node.id,
                type=_session_type_for_node(node),
                source=_stringify_data_value(data.get("sourceLabel"), "Captured"),
                title=_stringify_data_value(data.get("title"), node.id),
                subtitle=_session_subtitle_for_node(data),
                status=status,
                group=_session_group_for_node(node),
                tags=tags,
                metadata=_session_metadata_for_node(data),
                summary=_stringify_data_value(data.get("summary"), "No summary available."),
            )
        )

    session_edges = [
        SessionGraphEdge(
            id=edge.id,
            **{
                "from": edge.source,
                "to": edge.target,
                "label": "related_to",
                "strength": 0.8,
            },
        )
        for edge in graph.edges
    ]
    return SessionGraph(nodes=session_nodes, edges=session_edges)


def _rank_values(values: list[float], *, reverse: bool = False) -> dict[float, int]:
    unique_values = sorted(set(values), reverse=reverse)
    return {value: index + 1 for index, value in enumerate(unique_values)}


def _build_session_matrix(query: str, domain: str, graph: ReactFlowGraphData) -> SessionMatrix:
    option_nodes = list(graph.nodes)

    if domain == "housing":
        columns = [
            SessionMatrixColumn(key="price", label="Monthly Cost", type="currency", highlight_best=True),
            SessionMatrixColumn(key="commute", label="Distance", type="text"),
            SessionMatrixColumn(key="trust", label="Trust Signal", type="sentiment"),
            SessionMatrixColumn(key="friction", label="Friction", type="text"),
        ]
    elif domain == "products":
        columns = [
            SessionMatrixColumn(key="price", label="Price", type="currency", highlight_best=True),
            SessionMatrixColumn(key="noise", label="Noise", type="text"),
            SessionMatrixColumn(key="cooling", label="Cooling", type="text"),
            SessionMatrixColumn(key="reviews", label="Reviews", type="sentiment"),
            SessionMatrixColumn(key="aiChoice", label="AI Choice", type="text"),
        ]
    else:
        columns = [
            SessionMatrixColumn(key="score", label="Rank", type="text", highlight_best=True),
            SessionMatrixColumn(key="source", label="Source", type="text"),
            SessionMatrixColumn(key="signal", label="Signal", type="text"),
            SessionMatrixColumn(key="notes", label="Notes", type="text"),
        ]

    price_values = [
        _parse_data_number(dict(node.data).get("priceUsd"))
        for node in option_nodes
        if _parse_data_number(dict(node.data).get("priceUsd")) > 0
    ]
    price_ranks = _rank_values(price_values) if price_values else {}

    rows: list[SessionMatrixRow] = []
    for node in option_nodes:
        data = dict(node.data)
        price_usd = _parse_data_number(data.get("priceUsd"))
        distance_miles = _parse_data_number(data.get("distanceMiles"))
        summary = _stringify_data_value(data.get("summary"))
        chips = [str(chip) for chip in list(data.get("chips") or [])]
        risk_text = _stringify_data_value(data.get("constraintReason")) or (chips[-1] if chips else "Low context")

        if domain == "housing":
            trust_sentiment = "negative" if data.get("constraintViolated") else "neutral"
            trust_display = "Constraint mismatch" if data.get("constraintViolated") else "Within target"
            cells = {
                "price": SessionMatrixCell(
                    value=price_usd or None,
                    display=f"${price_usd:,.0f}" if price_usd > 0 else "Unknown",
                    rank=price_ranks.get(price_usd),
                ),
                "commute": SessionMatrixCell(
                    value=distance_miles or None,
                    display=f"{distance_miles:g} mi" if distance_miles > 0 else "Unknown",
                ),
                "trust": SessionMatrixCell(
                    value=None if trust_sentiment == "neutral" else -1,
                    display=trust_display,
                    sentiment=trust_sentiment,
                ),
                "friction": SessionMatrixCell(
                    value=None,
                    display=risk_text,
                ),
            }
        elif domain == "products":
            performance_display = _stringify_data_value(data.get("coolingPerformance")) or next(
                (
                    f"{metric['label']} {metric['value']}"
                    for metric in list(data.get("metrics") or [])
                    if isinstance(metric, dict) and any(token in str(metric.get("label", "")).lower() for token in ["cooling", "performance", "fan", "speed"])
                ),
                "Unknown",
            )
            noise_display = _stringify_data_value(data.get("noiseDisplay")) or next(
                (
                    f"{metric['label']} {metric['value']}"
                    for metric in list(data.get("metrics") or [])
                    if isinstance(metric, dict) and "noise" in str(metric.get("label", "")).lower()
                ),
                "Unknown",
            )
            ai_choice_display = f"AI #{int(_parse_data_number(data.get('aiRank'), 0))}" if _parse_data_number(data.get("aiRank"), 0) > 0 else "Unranked"
            review_sentiment = _stringify_data_value(data.get("reviewSentiment"), "unknown").lower()
            review_display = _stringify_data_value(data.get("reviewSentimentLabel"), "Unknown")
            cells = {
                "price": SessionMatrixCell(
                    value=price_usd or None,
                    display=f"${price_usd:,.0f}" if price_usd > 0 else "Unknown",
                    rank=price_ranks.get(price_usd),
                ),
                "noise": SessionMatrixCell(value=data.get("noiseLevelDb"), display=noise_display),
                "cooling": SessionMatrixCell(value=data.get("coolingPerformanceScore"), display=performance_display),
                "reviews": SessionMatrixCell(value=None, display=review_display, sentiment=review_sentiment),
                "aiChoice": SessionMatrixCell(value=data.get("aiRank"), display=ai_choice_display, rank=int(_parse_data_number(data.get("aiRank"), 0)) or None),
            }
        else:
            ai_rank = int(_parse_data_number(data.get("aiRank"), 0)) or None
            signal_display = chips[0] if chips else summary[:60] or "Unknown"
            cells = {
                "score": SessionMatrixCell(value=ai_rank, display=f"#{ai_rank}" if ai_rank else "Unknown", rank=ai_rank),
                "source": SessionMatrixCell(value=None, display=_stringify_data_value(data.get("sourceLabel"), "Captured")),
                "signal": SessionMatrixCell(value=None, display=signal_display),
                "notes": SessionMatrixCell(value=None, display=risk_text),
            }

        rows.append(SessionMatrixRow(node_id=node.id, cells=cells))

    rubric = "Housing Comparison" if domain == "housing" else "Product Comparison" if domain == "products" else "Research Comparison"
    return SessionMatrix(rubric=rubric, columns=columns, rows=rows)


def _build_session_digest(query: str, domain: str, graph: ReactFlowGraphData) -> SessionDigest:
    theme_signals = _prompt_keywords(query)[:4]
    entries: list[SessionDigestEntry] = []
    ready = flagged = 0

    sorted_nodes = sorted(
        graph.nodes,
        key=lambda node: (
            -int(bool(dict(node.data).get("constraintViolated"))),
            _parse_data_number(dict(node.data).get("combinedScore"), 0),
            -_parse_data_number(dict(node.data).get("aiRank"), 999),
        ),
        reverse=True,
    )

    for node in sorted_nodes:
        data = dict(node.data)
        status = _session_status_from_data(data)
        if status == "flagged":
            flagged += 1
        else:
            ready += 1

        signals = [
            SessionDigestSignal(label=str(chip), kind="signal")
            for chip in list(data.get("chips") or [])[:4]
        ]
        relevance = _parse_data_number(data.get("combinedScore"), 0) / 100
        if relevance <= 0:
            ai_rank = _parse_data_number(data.get("aiRank"), 1)
            relevance = max(0.2, 1 - (max(ai_rank, 1) - 1) * 0.15)

        entries.append(
            SessionDigestEntry(
                node_id=node.id,
                relevance=min(max(relevance, 0), 1),
                summary=_stringify_data_value(data.get("summary"), "No summary available."),
                signals=signals,
                source_note="Constraint mismatch" if data.get("constraintViolated") else _stringify_data_value(data.get("sourceLabel"), "Captured"),
            )
        )

    theme = "Housing comparison" if domain == "housing" else "Product comparison" if domain == "products" else "Research digest"
    return SessionDigest(
        theme=theme,
        theme_signals=theme_signals or [domain],
        stats=SessionDigestStats(
            total=len(graph.nodes),
            ready=ready,
            extracting=0,
            pending=flagged,
        ),
        entries=entries,
    )


def _graph_to_unified_session(graph: ReactFlowGraphData, query: str, user_constraint: str | None = None) -> UnifiedSession:
    constrained_graph = _apply_deterministic_constraints(graph, user_constraint)
    domain = _infer_session_domain(query, constrained_graph)
    session_graph = _build_session_graph(constrained_graph)
    session_matrix = _build_session_matrix(query, domain, constrained_graph)
    session_digest = _build_session_digest(query, domain, constrained_graph)
    overall_status = "pending" if session_digest.stats.pending else "ready"
    theme_signals = list(session_digest.theme_signals)
    if user_constraint:
        theme_signals = [*theme_signals, f"constraint: {user_constraint.strip()}"]
        session_digest = session_digest.model_copy(update={"theme_signals": theme_signals})
    return UnifiedSession(
        session_id=f"ses_{int(time.time())}",
        query=query,
        domain=domain,
        status=overall_status,
        graph=session_graph,
        matrix=session_matrix,
        digest=session_digest,
    )


def _apply_constraint_to_session(session: UnifiedSession, user_constraint: str | None = None) -> UnifiedSession:
    next_session = session.model_copy(deep=True)
    ready = flagged = 0

    for node in next_session.graph.nodes:
        metadata = _apply_constraint_to_data_dict(dict(node.metadata or {}), user_constraint)
        node.metadata = metadata
        node.status = _session_status_from_data(metadata)
        node.subtitle = _session_subtitle_for_node(metadata)
        if node.status == "flagged":
            flagged += 1
        else:
            ready += 1

    next_session.graph.nodes.sort(
        key=lambda node: (
            int(bool(node.metadata.get("constraintViolated"))),
            _parse_data_number(node.metadata.get("aiRank"), 999),
        )
    )

    node_map = {node.id: node for node in next_session.graph.nodes}
    def _row_sort_key(row: SessionMatrixRow) -> tuple[int, float]:
        node = node_map.get(row.node_id)
        metadata = node.metadata if node else {}
        return (
            int(bool(metadata.get("constraintViolated"))),
            _parse_data_number(metadata.get("aiRank"), 999),
        )

    next_session.matrix.rows.sort(key=_row_sort_key)

    for entry in next_session.digest.entries:
        node = node_map.get(entry.node_id)
        if not node:
            continue
        if bool(node.metadata.get("constraintViolated")):
            entry.source_note = "Constraint mismatch"
            entry.relevance = max(0.05, entry.relevance * 0.55)
        else:
            entry.source_note = _stringify_data_value(node.metadata.get("sourceLabel"), "Captured")

    next_session.digest.entries.sort(
        key=lambda entry: (
            int(bool(node_map.get(entry.node_id).metadata.get("constraintViolated"))) if node_map.get(entry.node_id) else 1,
            -entry.relevance,
        )
    )
    next_session.digest.stats = SessionDigestStats(
        total=len(next_session.graph.nodes),
        ready=ready,
        extracting=0,
        pending=flagged,
    )
    theme_signals = [signal for signal in next_session.digest.theme_signals if not signal.startswith("constraint:")]
    if user_constraint:
        theme_signals.append(f"constraint: {user_constraint.strip()}")
    next_session.digest.theme_signals = theme_signals
    next_session.status = "pending" if flagged else "ready"
    return next_session


def _is_degenerate_query(query: str) -> bool:
    normalized = " ".join(query.lower().split())
    if not normalized or len(normalized) > 160:
        return True
    parts = normalized.split("/")
    if len(parts) >= 3 and len(set(parts)) == 1:
        return True
    if len(parts) > 8 and len(set(parts)) <= 2:
        return True
    words = normalized.split()
    if len(words) > 8 and len(set(words)) <= 2:
        return True
    return False


def _sanitize_search_queries(queries: list[str], budget: int) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for query in queries:
        normalized = " ".join(str(query).split())
        if normalized in seen or _is_degenerate_query(normalized):
            continue
        seen.add(normalized)
        cleaned.append(normalized)
        if len(cleaned) >= budget:
            break
    return cleaned


def _title_from_active_tab_summary(tab: ActiveTab) -> str:
    summary = tab.summary
    match = re.search(r"Title:\s*([^|]+)", summary)
    if match:
        return match.group(1).strip()
    return str(tab.url).rsplit("/", 1)[-1].replace("-", " ").strip() or str(tab.url)


def _review_search_queries(active_tabs: list[ActiveTab], user_prompt: str, budget: int) -> list[str]:
    if budget <= 0 or not _wants_external_reviews(user_prompt):
        return []

    prompt_keywords = " ".join(_prompt_keywords(user_prompt)[:5])
    candidates: list[str] = []
    for tab in active_tabs[:4]:
        title = _title_from_active_tab_summary(tab)
        title = re.sub(r"\s+", " ", title).strip()
        title = re.sub(r"\b(Amazon\.com|Walmart\.com|Best Buy|eBay)\b", "", title, flags=re.IGNORECASE).strip(" -:|")
        if len(title) > 90:
            title = title[:90].rsplit(" ", 1)[0]
        if title:
            candidates.append(f"{title} reviews complaints reddit forum")

    if prompt_keywords:
        candidates.append(f"{prompt_keywords} best reviews complaints reddit forum")
        candidates.append(f"{prompt_keywords} external reviews owner feedback")

    return _sanitize_search_queries(candidates, budget)


def _filter_graph_for_prompt(graph: ReactFlowGraphData, user_prompt: str) -> ReactFlowGraphData:
    prompt = user_prompt.lower()
    wants_active_cooler = any(token in prompt for token in ["cooler", "cooling pad", "cooling pads", "laptop cooler"])
    if not wants_active_cooler:
        return graph

    kept_nodes: list[ReactFlowNode] = []
    for node in graph.nodes:
        data = dict(node.data)
        blob = " ".join(str(value) for value in data.values()).lower()
        source_type = str(data.get("sourceType", "")).lower()
        node_id = str(node.id).lower()
        title = str(data.get("title", "")).lower()
        url = str(data.get("url", "")).lower()
        mentions_passive = "passive" in blob or "stand" in blob
        missing_active_signals = all(token not in blob for token in ["fan", "rpm", "cfm", "turbo"])
        looks_like_placeholder = (
            re.fullmatch(r"item \d+", str(data.get("title", "")).strip(), re.IGNORECASE) is not None
            or "concept" in node_id
            or "concept" in title
            or "technology" in title
            or "explainer" in title
        )
        has_product_signal = any(
            token in blob for token in ["price", "$", "rpm", "fan", "noise", "usb", "inch", "cooling"]
        )
        if source_type == "discovered" and mentions_passive and missing_active_signals:
            continue
        if source_type == "discovered" and looks_like_placeholder and not has_product_signal:
            continue
        if source_type == "discovered" and not url:
            continue
        kept_nodes.append(node)

    kept_ids = {node.id for node in kept_nodes}
    kept_edges = [edge for edge in graph.edges if edge.source in kept_ids and edge.target in kept_ids]
    return ReactFlowGraphData(nodes=kept_nodes, edges=kept_edges)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}

@app.post("/api/v1/extension/snapshot")
async def extension_snapshot(payload: ExtensionSnapshot) -> dict[str, str]:
    timestamp = payload.timestamp or int(time.time())
    extension_history[str(payload.url)] = {
        "url": str(payload.url),
        "title": payload.title,
        "dom": payload.dom,
        "timestamp": timestamp,
    }
    _prune_extension_history(timestamp)
    logger.debug(
        "Extension snapshot stored payload=%s history_size=%s",
        _debug_json(
            {
                "url": str(payload.url),
                "title": _debug_truncate(payload.title, 80),
                "dom_chars": len(payload.dom),
                "timestamp": timestamp,
            }
        ),
        len(extension_history),
    )
    return {"status": "ok"}


@app.get("/api/v1/extension/preferences")
async def extension_preferences_view() -> dict[str, Any]:
    return {
        "user_prompt": extension_preferences.get("user_prompt", ""),
        "enable_discovery": _extension_pref_enabled("enable_discovery"),
    }


@app.post("/api/v1/extension/preferences")
async def extension_preferences_update(payload: ExtensionPreferencesRequest) -> dict[str, Any]:
    if payload.user_prompt is not None:
        extension_preferences["user_prompt"] = payload.user_prompt.strip()
    if payload.enable_discovery is not None:
        extension_preferences["enable_discovery"] = "true" if payload.enable_discovery else "false"
    return {
        "user_prompt": extension_preferences.get("user_prompt", ""),
        "enable_discovery": _extension_pref_enabled("enable_discovery"),
    }


@app.get("/api/v1/extension/history")
async def extension_history_view() -> dict[str, list[dict[str, Any]]]:
    _prune_extension_history()
    tabs = sorted(extension_history.values(), key=lambda entry: int(entry.get("timestamp", 0)), reverse=True)
    return {"tabs": tabs}


@app.get("/api/v1/extension/history/stats")
async def extension_history_stats() -> dict[str, Any]:
    _prune_extension_history()
    tabs = sorted(extension_history.values(), key=lambda entry: int(entry.get("timestamp", 0)), reverse=True)
    return {
        "count": len(tabs),
        "user_prompt": extension_preferences.get("user_prompt", ""),
        "enable_discovery": _extension_pref_enabled("enable_discovery"),
        "recent": [
            {
                "url": entry.get("url", ""),
                "title": entry.get("title", ""),
                "timestamp": entry.get("timestamp", 0),
            }
            for entry in tabs[:5]
        ],
    }


@app.post("/api/v1/synthesize", response_model=ReactFlowGraphData)
async def synthesize(payload: SynthesizeRequest) -> ReactFlowGraphData:
    try:
        logger.debug(
            "Synthesize start prompt=%r constraint=%r active_tabs=%s budget=%s",
            _debug_truncate(payload.user_prompt, 160),
            _debug_truncate(payload.user_constraint, 120) if payload.user_constraint else "",
            len(payload.active_tabs),
            payload.firecrawl_query_budget,
        )
        logger.debug(
            "Synthesize active tab previews=%s",
            _debug_json([_tab_debug_payload(tab) for tab in payload.active_tabs]),
        )
        context = _tabs_to_context(payload.active_tabs)
        query_budget_remaining = payload.firecrawl_query_budget
        allow_discovery = _should_investigate_further(payload.user_prompt) and query_budget_remaining > 0

        rubric = await _build_rubric(payload, context)
        logger.debug("Generated rubric fields=%s", rubric.fields)
        logger.debug("Inferred constraints=%s seed_patterns=%s", rubric.inferred_constraints, rubric.seed_patterns)

        review_queries = _review_search_queries(payload.active_tabs, payload.user_prompt, query_budget_remaining)
        if review_queries:
            logger.debug("Running review-focused discovery queries=%s", review_queries)
            review_results = await asyncio.gather(
                *[_run_firecrawl_search(query) for query in review_queries],
                return_exceptions=False,
            )
            query_budget_remaining -= len(review_queries)
            context += (
                "\n\n--- External Review Signals ---\n"
                "These results are for independent reviews, owner feedback, forum threads, complaints, "
                "and consensus signals. Prefer them for review sentiment and tradeoffs; keep seller pages "
                "as seed/product fact sources.\n"
                f"Queries used: {len(review_queries)}\n"
                f"Remaining query budget: {query_budget_remaining}\n\n"
                + "\n\n".join(review_results)
            )
            allow_discovery = allow_discovery and query_budget_remaining > 0

        evaluation_passes = 0
        while allow_discovery and evaluation_passes < MAX_EVALUATION_PASSES:
            evaluation = await _evaluate_context(
                user_prompt=payload.user_prompt,
                rubric=rubric,
                context=context,
                remaining_query_budget=query_budget_remaining,
            )

            logger.debug(
                "EvalPass=%s is_complete=%s should_search_more=%s budget_remaining=%s missing_fields=%s search_queries=%s stop_reason=%s",
                evaluation_passes + 1,
                evaluation.is_complete,
                evaluation.should_search_more,
                query_budget_remaining,
                evaluation.missing_fields,
                evaluation.search_queries,
                evaluation.stop_reason,
            )

            if evaluation.is_complete:
                break

            if not evaluation.should_search_more:
                break

            if query_budget_remaining <= 0:
                logger.debug("Firecrawl query budget exhausted; ending discovery loop.")
                break

            queries = _sanitize_search_queries(evaluation.search_queries, query_budget_remaining)
            if not queries:
                logger.debug("No usable search queries generated; ending fill-in loop.")
                break

            search_results = await asyncio.gather(
                *[_run_firecrawl_search(query) for query in queries],
                return_exceptions=False,
            )
            query_budget_remaining -= len(queries)
            context += (
                "\n\n--- Firecrawl Search Results ---\n"
                f"Queries used this pass: {len(queries)}\n"
                f"Remaining query budget: {query_budget_remaining}\n\n"
                + "\n\n".join(search_results)
            )
            evaluation_passes += 1
            break

        raw_graph = await _synthesize_graph(
            user_prompt=payload.user_prompt,
            user_constraint=payload.user_constraint,
            rubric=rubric,
            context=context,
        )
        logger.debug("Raw graph snapshot=%s", _debug_json(_graph_debug_payload(raw_graph)))
        filtered_graph = _filter_graph_for_prompt(raw_graph, payload.user_prompt)
        if len(filtered_graph.nodes) != len(raw_graph.nodes):
            logger.debug(
                "Prompt filter adjusted graph before_nodes=%s after_nodes=%s",
                len(raw_graph.nodes),
                len(filtered_graph.nodes),
            )
        graph = _canonicalize_graph_for_frontend(filtered_graph)
        logger.debug("Canonical graph snapshot=%s", _debug_json(_graph_debug_payload(graph)))
        placeholder_like_nodes = [
            node.id
            for node in graph.nodes
            if re.fullmatch(r"Item \d+", node.data.title or "")
            or (not node.data.url and node.data.priceUsd == 0 and node.data.rentalType == "Unknown")
        ]
        if placeholder_like_nodes:
            logger.warning(
                "Canonical graph contains placeholder-like nodes=%s total_nodes=%s",
                placeholder_like_nodes,
                len(graph.nodes),
            )
        logger.debug("Synthesized graph nodes=%s edges=%s", len(graph.nodes), len(graph.edges))
        return graph
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Synthesis failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/v1/synthesize-from-dom", response_model=ReactFlowGraphData)
async def synthesize_from_dom(payload: SynthesizeFromDomRequest) -> ReactFlowGraphData:
    try:
        logger.debug(
            "Synthesize from DOM start prompt=%r constraint=%r tabs=%s budget=%s dom_tabs=%s",
            _debug_truncate(payload.user_prompt, 160),
            _debug_truncate(payload.user_constraint, 120) if payload.user_constraint else "",
            len(payload.tabs),
            payload.firecrawl_query_budget,
            _debug_json([_tab_debug_payload(tab) for tab in payload.tabs]),
        )
        active_tabs = await asyncio.gather(*[_safe_dom_tab_to_active_tab(tab) for tab in payload.tabs])
        synthesize_payload = SynthesizeRequest(
            user_prompt=payload.user_prompt,
            user_constraint=payload.user_constraint,
            active_tabs=active_tabs,
            firecrawl_query_budget=payload.firecrawl_query_budget,
        )
        return await synthesize(synthesize_payload)
    except SummarizerError as exc:
        logger.exception("DOM summarization failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("DOM pipeline failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/v1/synthesize-from-extension-history", response_model=ReactFlowGraphData)
async def synthesize_from_extension_history(payload: SynthesizeFromExtensionRequest) -> ReactFlowGraphData:
    _prune_extension_history()
    tabs = sorted(extension_history.values(), key=lambda entry: int(entry.get("timestamp", 0)), reverse=True)
    scored_entries = sorted(
        [_extension_entry_debug_payload(entry, payload.user_prompt) for entry in tabs],
        key=lambda entry: (int(entry["score"]), int(entry["timestamp"])),
        reverse=True,
    )
    logger.debug(
        "Extension history synthesis start prompt=%r constraint=%r total_tabs=%s max_tabs=%s budget=%s ranked_candidates=%s",
        _debug_truncate(payload.user_prompt, 160),
        _debug_truncate(payload.user_constraint, 120) if payload.user_constraint else "",
        len(tabs),
        payload.max_tabs,
        payload.firecrawl_query_budget,
        _debug_json(scored_entries[:10]),
    )
    selected = _select_relevant_extension_entries(
        tabs,
        payload.user_prompt,
        min(payload.max_tabs, MAX_EXTENSION_HISTORY_TABS),
    )
    logger.debug(
        "Extension history selected entries=%s",
        _debug_json([_extension_entry_debug_payload(entry, payload.user_prompt) for entry in selected]),
    )

    if not selected:
        raise HTTPException(status_code=400, detail="No extension DOM history available")

    try:
        dom_payload = SynthesizeFromDomRequest(
            user_prompt=payload.user_prompt,
            user_constraint=payload.user_constraint,
            firecrawl_query_budget=(
                payload.firecrawl_query_budget
                if "firecrawl_query_budget" in payload.model_fields_set
                else _extension_firecrawl_budget()
            ),
            tabs=[
                DomTab(
                    url=entry["url"],
                    title=str(entry.get("title", "")),
                    dom=str(entry.get("dom", "")),
                )
                for entry in selected
            ],
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid extension history data: {exc}") from exc

    return await synthesize_from_dom(dom_payload)


@app.post("/api/v1/session/synthesize", response_model=UnifiedSession)
async def synthesize_session(payload: SynthesizeRequest) -> UnifiedSession:
    graph = await synthesize(payload)
    return _graph_to_unified_session(graph, payload.user_prompt, payload.user_constraint)


@app.post("/api/v1/session/synthesize-from-dom", response_model=UnifiedSession)
async def synthesize_session_from_dom(payload: SynthesizeFromDomRequest) -> UnifiedSession:
    graph = await synthesize_from_dom(payload)
    return _graph_to_unified_session(graph, payload.user_prompt, payload.user_constraint)


@app.post("/api/v1/session/synthesize-from-extension-history", response_model=UnifiedSession)
async def synthesize_session_from_extension_history(payload: SynthesizeFromExtensionRequest) -> UnifiedSession:
    graph = await synthesize_from_extension_history(payload)
    return _graph_to_unified_session(graph, payload.user_prompt, payload.user_constraint)


@app.post("/api/v1/session/apply-constraint", response_model=UnifiedSession)
async def apply_session_constraint(payload: SessionConstraintRequest) -> UnifiedSession:
    try:
        return _apply_constraint_to_session(payload.session, payload.user_constraint)
    except Exception as exc:
        logger.exception("Session constraint application failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
