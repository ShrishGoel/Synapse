import asyncio
import json
import logging
import os
import re
import time
from html import escape as html_escape
from typing import Any, Literal, TypeVar
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from firecrawl import FirecrawlApp
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationError

from summarizer import SummarizerError, summarize_html


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
extension_preferences: dict[str, Any] = {
    "user_prompt": "Compare the items I've been looking at.",
    "enable_discovery": DISCOVERY_ENABLED_BY_DEFAULT,
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
    enable_discovery: bool | None = None
    previous_graph: dict[str, Any] | None = Field(default=None)


class DomTab(StrictModel):
    url: HttpUrl
    title: str = ""
    dom: str = Field(min_length=1)
    readable_text: str = ""
    readable_html: str = ""
    readable_extractor: str = ""


class SynthesizeFromDomRequest(StrictModel):
    user_prompt: str = Field(min_length=1)
    user_constraint: str | None = Field(default=None, min_length=1)
    tabs: list[DomTab] = Field(min_length=1)
    firecrawl_query_budget: int = Field(default=DEFAULT_FIRECRAWL_QUERY_BUDGET, ge=0, le=12)
    enable_discovery: bool | None = None
    previous_graph: dict[str, Any] | None = Field(default=None)


class SynthesizeFromExtensionRequest(StrictModel):
    user_prompt: str = Field(min_length=1)
    user_constraint: str | None = Field(default=None, min_length=1)
    firecrawl_query_budget: int = Field(default=DEFAULT_FIRECRAWL_QUERY_BUDGET, ge=0, le=12)
    max_tabs: int = Field(default=20, ge=1, le=50)
    enable_discovery: bool | None = None
    previous_graph: dict[str, Any] | None = Field(default=None)

class ExtensionSnapshot(StrictModel):
    url: HttpUrl
    title: str = ""
    dom: str = Field(min_length=1)
    readable_text: str = ""
    readable_html: str = ""
    readable_extractor: str = ""
    timestamp: int | None = None


class ExtensionPreferencesRequest(StrictModel):
    user_prompt: str = Field(min_length=1, max_length=400)
    enable_discovery: bool = DISCOVERY_ENABLED_BY_DEFAULT


class ComparisonRubric(StrictModel):
    domain: str = Field(
        description="A short, generic name for the category of items being researched (e.g., 'housing', 'products', 'software', 'locations').",
    )
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
    domain: str = ""
    rubric_fields: list[str] = Field(default_factory=list)
    nodes: list[ReactFlowNode]
    edges: list[ReactFlowEdge]


class SessionGraphNode(BaseModel):
    model_config = ConfigDict(strict=True)

    id: str
    type: str
    url: str = ""
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
    rubric_fields: list[str] = Field(default_factory=list)
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
    user_constraint: str = ""
    domain: str
    status: str
    rubric_fields: list[str] = Field(default_factory=list)
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


def _review_field_label() -> str:
    return "Review Consensus"


def _is_review_field_label(label: str) -> bool:
    normalized = _normalize_data_key(label)
    return any(
        token in normalized
        for token in ("review", "complaint", "feedback", "sentiment", "consensus")
    )


def _review_field_value_from_mapping(data: dict[str, Any]) -> str:
    if not isinstance(data, dict):
        return ""
    review_label = _review_field_label()
    direct_value = _stringify_data_value(
        _lookup_data_value(data, [review_label, "Review Sentiment", "User Feedback", "Common Complaints"])
    )
    if direct_value and direct_value != "Unknown":
        return direct_value
    for key, value in data.items():
        if not _is_review_field_label(str(key)):
            continue
        text = _stringify_data_value(value)
        if text and text != "Unknown":
            return text
    return ""


def _prioritize_review_metrics(metrics: list[dict[str, str]], limit: int = 6) -> list[dict[str, str]]:
    if not metrics:
        return []
    has_review_consensus = any(
        _normalize_data_key(_stringify_data_value(metric.get("label"))) == _normalize_data_key(_review_field_label())
        for metric in metrics
        if isinstance(metric, dict)
    )
    review_metrics = [
        metric for metric in metrics
        if _is_review_field_label(_stringify_data_value(metric.get("label")))
        and not (
            has_review_consensus
            and _normalize_data_key(_stringify_data_value(metric.get("label"))) == _normalize_data_key("Review Sentiment")
        )
    ]
    other_metrics = [
        metric for metric in metrics
        if not _is_review_field_label(_stringify_data_value(metric.get("label")))
    ]
    prioritized = [*review_metrics, *other_metrics]
    return prioritized[:limit]


def _ensure_review_field_in_rubric(rubric: ComparisonRubric) -> ComparisonRubric:
    fields = list(rubric.fields)
    review_label = _review_field_label()
    review_index = next(
        (index for index, field in enumerate(fields) if _normalize_data_key(field) == _normalize_data_key(review_label)),
        None,
    )
    if review_index is None:
        fields.insert(1 if len(fields) >= 1 else 0, review_label)
    else:
        existing = fields.pop(review_index)
        fields.insert(1 if len(fields) >= 1 else 0, existing)
    fields = fields[:12]
    return rubric.model_copy(update={"fields": fields})


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
    score = 0

    negative_tokens = ["firecrawl", "openrouter", "dashboard", "usage", "billing", "docs", "synapse"]
    if any(token in combined for token in negative_tokens):
        score -= 8
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        path = parsed.path.lower()
        # Penalize homepages
        if not path or path == "/":
            score -= 15
        # Penalize search and category indices
        elif any(path.startswith(p) for p in ["/s", "/search", "/category", "/c/", "/b/", "/browse", "/departments"]):
            score -= 10
        elif "search results" in title:
            score -= 10
    except Exception:
        pass

    if "/s?" in url or "search results" in combined:
        score -= 4

    prompt = user_prompt.lower()
    if "cooler" in prompt and "stand" in combined and all(token not in combined for token in ["cool", "cooler", "cooling"]):
        score -= 6

    for keyword in _prompt_keywords(user_prompt):
        if keyword in title:
            score += 5
        elif keyword in combined:
            score += 2
    if "amazon" in combined and "amazon" in user_prompt.lower():
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

    selected = [entry for entry, _, _ in scored[:max_tabs]]
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


def _best_dom_summary_input(tab: DomTab) -> str:
    if tab.readable_html.strip():
        title = tab.title.strip() or str(tab.url)
        return (
            "<html><head>"
            f"<title>{html_escape(title)}</title>"
            "</head><body><main>"
            f"{tab.readable_html}"
            "</main></body></html>"
        )
    return tab.dom


async def _dom_tab_to_active_tab(tab: DomTab) -> ActiveTab:
    summary = await asyncio.to_thread(summarize_html, _best_dom_summary_input(tab))
    normalized_summary = _render_dom_summary(tab, summary)
    return ActiveTab(url=tab.url, summary=normalized_summary)


def _fallback_dom_summary(tab: DomTab) -> str:
    source_text = tab.readable_text.strip() or tab.readable_html or tab.dom
    stripped = re.sub(r"<[^>]+>", " ", source_text)
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
    rubric = await _structured_llm_call(
        model_type=ComparisonRubric,
        schema_name="comparison_rubric",
        system_prompt=(
            "You are an expert research rubric designer. Infer the rubric from the seed items themselves "
            "and the user's query. Choose comparison fields that are natively meaningful for the actual "
            "item category shown in the captured tabs. Never mix fields from unrelated domains. Prefer "
            "concrete fields that are directly evidenced in the captured context, and include price when "
            "price or cost appears in the seed material or is central to the comparison. For consumer choices, "
            "recommendation-style comparisons, products, software, gear, services, or listings with public feedback, "
            "include Review Consensus as one of the rubric fields, and add other review-oriented fields such as Review Sentiment, User Feedback, "
            "or Common Complaints whenever the context can support it."
        ),
        user_prompt=(
            f"User query: {payload.user_prompt}\n"
            f"User constraint: {payload.user_constraint or 'None'}\n\n"
            f"Context (snapshots the user has gathered):\n{base_context}\n\n"
            "Generate a generic domain name, "
            "5-10 useful comparison fields (MUST be human-readable and Title Cased, e.g. 'Battery Life' not 'battery_life'), "
            "any inferred constraints, "
            "a default ordering philosophy, and common patterns to look for. "
            "Fields must fit the actual item category present in the context. Avoid category leakage "
            "from unrelated domains, and prefer labels that can be directly supported by the captured tabs. "
            "One of the comparison fields in the rubric MUST be exactly 'Review Consensus'. "
            "Keep 'Review Consensus' within the most important comparison fields so it appears in the main comparison table."
        ),
    )
    return _ensure_review_field_in_rubric(rubric)


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
            "items or recent discussion signals. Search only for fields that are actually present in the rubric "
            "or clearly implied by the user's query. Do not import fields from unrelated domains. Search for "
            "candidate listings that expose price, source URLs, rubric-relevant specs or fit signals, and "
            "independent review or complaint signals when the user asks about reviews or when the comparison is a "
            "consumer-choice decision where public feedback materially affects ranking."
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
    previous_graph: dict[str, Any] | None = None,
) -> ReactFlowGraphData:
    previous_graph_context = ""
    if previous_graph:
        # Simplify the previous graph for context to keep token usage low
        simplified_nodes = []
        for node in previous_graph.get("nodes", []):
            simplified_nodes.append({
                "id": node.get("id"),
                "title": node.get("title"),
                "summary": node.get("summary"),
                "url": node.get("source"), # SessionGraph uses 'source' for URL
                "metadata": node.get("metadata", {})
            })
        
        previous_graph_context = (
            "\n### PREVIOUS WORKSPACE STATE\n"
            "The following items are already in the user's graph. "
            "STRICT RULE: You must preserve these items. You may update their 'aiRank', 'summary', "
            "or 'attributes' if you have found more detailed information. "
            "Add newly discovered items as additional nodes.\n"
            f"{json.dumps({'nodes': simplified_nodes, 'edges': previous_graph.get('edges', [])})}\n\n"
        )
    
    graph = await _structured_llm_call(
        model_type=SynthesizedGraphData,
        schema_name="synthesized_graph_data",
        system_prompt=(
            "You synthesize browser-tab research into React Flow graph JSON. "
            "Each node must include top-level fields title, url, sourceType, aiRank, aiReason, summary, "
            "constraintViolated, constraintReason, and attributes. Use attributes for rubric facts as "
            "{label, value} pairs. CRITICAL: The label MUST be an exact match to one of the 'Rubric fields' provided. "
            "Do not invent your own attribute labels. "
            "Do not stuff product specs, listing facts, or rankings into constraintReason. "
            "constraintReason is only for the user constraint result and must be empty when no user constraint is provided. "
            "Create meaningful edges between related products, concepts, tradeoffs, or evidence. Keep the user-provided seed items "
            "in the graph and add newly found items as additional nodes rather than replacing the seed set. "
            f"Use at most {MAX_SYNTHESIZED_GRAPH_NODES} total nodes and at most {MAX_SYNTHESIZED_GRAPH_EDGES} edges, "
            "keeping all seed items and filling the remaining capacity with the strongest discovered items only. "
            "CRITICAL: NEVER create nodes for search directories, aggregators, or listicles (e.g., 'Arcadia Rental Search'). "
            "ONLY create nodes for INDIVIDUAL items being researched (e.g., a specific apartment, a specific product). "
            f"Keep each url under {MAX_SYNTHESIZED_URL_CHARS} characters and prefer canonical URLs without tracking, checkout, "
            "affiliate, or session parameters. sourceType is either seed or discovered. "
            "When external review context is present, express those findings through the rubric-field attributes "
            "instead of inventing extra labels, distinguish owner/reviewer sentiment from seller claims, and cite "
            "recent internet listing or discussion signals when possible. Prioritize review evidence and tradeoff "
            "signals in ranking, summaries, and attribute selection whenever credible review context exists."
        ),
        user_prompt=(
            f"User prompt:\n{user_prompt}\n\n"
            f"User constraint:\n{user_constraint or 'None'}\n\n"
            f"Rubric fields:\n{rubric.fields}\n\n"
            f"Inferred constraints:\n{rubric.inferred_constraints}\n\n"
            f"Seed patterns:\n{rubric.seed_patterns}\n\n"
            f"Default ordering:\n{rubric.default_ordering}\n\n"
            f"{previous_graph_context}"
            f"Collected context:\n{context}\n\n"
            "Return strict graph data with nodes and edges only. Each node must contain: "
            "id, type, title, url, sourceType, aiRank, aiReason, summary, constraintViolated, constraintReason, and attributes. "
            "Put the domain facts in attributes instead of embedding them into aiReason or constraintReason. "
            "Each attribute should be a short label/value pair. The label MUST EXACTLY match a string from the 'Rubric fields' list. "
            "If a rubric field is missing from the item, you may omit it or set value to 'Unknown'. "
            "If review evidence exists, make sure at least one review-oriented rubric field is populated for that node. "
            "Keep all seed items and previous graph nodes, then choose only the most relevant discovered items if there are more candidates than the graph can hold."
        ),
    )
    if isinstance(graph, ReactFlowGraphData):
        return graph
    review_label = _review_field_label()
    updated_nodes: list[SynthesizedNode] = []
    for node in graph.nodes:
        attributes = list(node.attributes)
        if not any(_normalize_data_key(attribute.label) == _normalize_data_key(review_label) for attribute in attributes):
            existing_review_value = ""
            for attribute in attributes:
                if _is_review_field_label(attribute.label):
                    existing_review_value = _stringify_data_value(attribute.value)
                    if existing_review_value and existing_review_value != "Unknown":
                        break
            attributes.append(
                SynthesizedAttribute(
                    label=review_label,
                    value=existing_review_value or "Unknown",
                )
            )
        updated_nodes.append(node.model_copy(update={"attributes": attributes[:18]}))
    graph = graph.model_copy(update={"nodes": updated_nodes})
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
    
    # Check for "free" first
    if "free" in text.lower():
        return 0.0

    # Try to match currencies explicitly
    for currency_pattern in [
        r"[\$€£]\s*(\d+(?:\.\d+)?)",
        r"(\d+(?:\.\d+)?)\s*(?:usd|dollars?|eur|euros?|gbp|pounds?|/mo|per month)",
    ]:
        matches = [float(match) for match in re.findall(currency_pattern, text, flags=re.IGNORECASE)]
        if matches:
            # If we see something like "$3,800 · $4,000", take the first one or average
            if len(matches) == 1:
                return matches[0]
            return sum(matches[:2]) / 2

    # Look for numeric patterns that look like prices (e.g. "3800")
    # Especially if they are at the end of a line or after a dot
    numbers = _extract_numeric_values(text)
    plausible = [number for number in numbers if number > 10] # Ignore tiny numbers
    if plausible:
        # If the original text had a '$', definitely use the numbers found
        if "$" in value:
            return plausible[0]
        # For housing, numbers in the 500-15000 range are likely prices
        housing_plausible = [n for n in plausible if 300 <= n <= 2500000]
        if housing_plausible:
            return housing_plausible[0]
        return plausible[0]

    return fallback


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


def _lookup_data_value_by_label_tokens(
    data: dict[str, Any],
    include_tokens: tuple[str, ...],
    exclude_tokens: tuple[str, ...] = (),
) -> Any:
    for key, value in data.items():
        if value in (None, ""):
            continue
        normalized_key = _normalize_data_key(str(key))
        if any(token in normalized_key for token in include_tokens) and not any(
            token in normalized_key for token in exclude_tokens
        ):
            return value
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
    cleaned = " ".join(value.replace("_", " ").replace("-", " ").split())
    if re.search(r"[\s()/]", value):
        return cleaned
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", cleaned)
    return " ".join(word.capitalize() for word in spaced.split())


def _is_internal_data_key(key: str) -> bool:
    return _normalize_data_key(key) in {
        "id",
        "title",
        "url",
        "source",
        "sourcetype",
        "sourceurl",
        "sourceurldisplay",
        "source_label",
        "sourcelabel",
        "status",
        "statuslabel",
        "kind",
        "kindlabel",
        "group",
        "summary",
        "description",
        "onesentencesummary",
        "excerpt",
        "aireason",
        "airank",
        "rank",
        "score",
        "combinedscore",
        "constraintviolated",
        "constraintreason",
        "rawdata",
        "attributes",
        "chips",
        "metrics",
        "locationlabel",
    }


def _hostname_label(url: str) -> str:
    try:
        from urllib.parse import urlparse

        hostname = urlparse(url).hostname or ""
        return hostname.replace("www.", "").upper() or "Captured"
    except Exception:
        return "Captured"


def _build_backend_metrics(raw_data: dict[str, Any]) -> list[dict[str, str]]:
    metrics: list[dict[str, str]] = []
    seen_labels: set[str] = set()
    for key, value in raw_data.items():
        if _is_internal_data_key(key):
            continue
        text = _stringify_data_value(value)
        if not text or text == "Unknown":
            continue
        label = _title_case_label(key)
        normalized_label = _normalize_data_key(label)
        if normalized_label in seen_labels:
            continue
        seen_labels.add(normalized_label)
        metrics.append({"label": label, "value": text})
        if len(metrics) >= 6:
            return _prioritize_review_metrics(metrics, 6)
    return _prioritize_review_metrics(metrics, 6)


def _build_backend_chips(raw_data: dict[str, Any], metrics: list[dict[str, str]], source_type: str) -> list[str]:
    chips: list[str] = []
    seen: set[str] = set()

    def add_chip(value: str) -> None:
        normalized = " ".join(value.split()).strip(" .,-")
        if not normalized:
            return
        lowered = normalized.lower()
        if lowered in {"captured", "ai found", "unknown"} or lowered in seen:
            return
        seen.add(lowered)
        chips.append(normalized)

    llm_chips = raw_data.get("chips")
    if isinstance(llm_chips, list):
        for chip in llm_chips:
            if isinstance(chip, str):
                add_chip(chip)
            if len(chips) >= 4:
                return chips

    for metric in metrics:
        if not isinstance(metric, dict):
            continue
        label = _stringify_data_value(metric.get("label"))
        value = _stringify_data_value(metric.get("value"))
        if not label or not value or value == "Unknown":
            continue
        add_chip(f"{label}: {value}")
        if len(chips) >= 4:
            return chips

    summary = _stringify_data_value(raw_data.get("summary"))
    ai_reason = _stringify_data_value(raw_data.get("aiReason"))
    for text in [summary, ai_reason]:
        if not text:
            continue
        for piece in re.split(r"[.;|]", text):
            cleaned = " ".join(piece.split())
            if len(cleaned) < 12:
                continue
            add_chip(cleaned[:72].rstrip(" ,"))
            if len(chips) >= 4:
                return chips

    add_chip("AI discovered" if source_type == "discovered" else "Captured")
    return chips[:4]


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

        attributes_list = []
        for attribute in node.attributes:
            if attribute.label and attribute.value:
                data[attribute.label] = attribute.value
                attributes_list.append({"label": attribute.label, "value": attribute.value})
        data["attributes"] = attributes_list

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
    for index, node in enumerate(payload.get("nodes", [])):
        raw_data = dict(node.get("data") or {})
        title = (
            _stringify_data_value(_lookup_data_value(raw_data, ["title", "Title", "name", "Name"]))
            or _first_interesting_data_string(raw_data)
            or f"Item {index + 1}"
        )
        url = _stringify_data_value(_lookup_data_value(raw_data, ["url", "URL", "Source URL", "sourceUrl", "source_url"]))
        source_type = _stringify_data_value(_lookup_data_value(raw_data, ["sourceType", "Source Type", "source_type"]), "seed").lower()
        source_type = "discovered" if source_type == "discovered" else "seed"
        source_label = _hostname_label(url) if url else _stringify_data_value(_lookup_data_value(raw_data, ["Source", "source"]), "Captured")
        kind_label = _stringify_data_value(_lookup_data_value(raw_data, ["kind", "Kind", "type", "Type", "category", "Category"]), "Item")
        price_usd = _parse_price_value(
            _lookup_data_value(raw_data, ["priceUsd"])
            or _lookup_data_value_by_label_tokens(raw_data, ("price", "cost", "rent"))
        )
        ai_rank = _parse_data_number(_lookup_data_value(raw_data, ["aiRank", "AI Rank", "rank"]), index + 1)
        combined_score = _parse_data_number(_lookup_data_value(raw_data, ["combinedScore", "Combined Score", "score"]), max(0, 100 - int(ai_rank) * 10))
        ai_reason = _stringify_data_value(_lookup_data_value(raw_data, ["aiReason", "AI Reason", "reason"]))
        summary = (
            _stringify_data_value(_lookup_data_value(raw_data, ["summary", "oneSentenceSummary", "description"]))
            or ai_reason
            or f"{title} is part of the current workspace graph."
        )
        status_label = _stringify_data_value(_lookup_data_value(raw_data, ["status", "Status"])) or ("enriched" if source_type == "discovered" else "captured")
        attributes_list = raw_data.get("attributes")
        if isinstance(attributes_list, list) and len(attributes_list) > 0:
            metrics = _prioritize_review_metrics(attributes_list, 6)
        else:
            metrics = _build_backend_metrics(raw_data)
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
            "priceUsd": price_usd,
            "combinedScore": combined_score,
            "aiRank": ai_rank,
            "aiReason": ai_reason,
            "sourceType": source_type,
            "constraintViolated": bool(_lookup_data_value(raw_data, ["constraintViolated", "Constraint Violated"])),
            "constraintReason": _stringify_data_value(_lookup_data_value(raw_data, ["constraintReason", "Constraint Reason"])),
            "metrics": metrics,
            "chips": chips,
            "rawData": raw_copy,
        }

    return ReactFlowGraphData.model_validate(payload)


def _normalized_url_key(value: str) -> str:
    raw = _stringify_data_value(value)
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except Exception:
        return raw.strip().lower().rstrip("/")

    hostname = (parsed.hostname or "").lower()
    if hostname.startswith("www."):
        hostname = hostname[4:]
    path = (parsed.path or "").rstrip("/")
    amazon_match = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})(?:[/?]|$)", path, flags=re.IGNORECASE)
    if "amazon." in hostname and amazon_match:
        return f"amazon:{amazon_match.group(1).lower()}"
    if "/ref=" in path:
        path = path.split("/ref=", 1)[0]
    return f"{hostname}{path}".lower()


def _reconcile_seed_nodes(graph: ReactFlowGraphData, active_tabs: list[ActiveTab]) -> ReactFlowGraphData:
    seed_url_keys = {
        _normalized_url_key(str(tab.url))
        for tab in active_tabs
        if _normalized_url_key(str(tab.url))
    }
    if not seed_url_keys:
        return graph

    payload = graph.model_dump()
    for node in payload.get("nodes", []):
        data = dict(node.get("data") or {})
        node_url = _stringify_data_value(data.get("url"))
        if _normalized_url_key(node_url) not in seed_url_keys:
            continue

        data["sourceType"] = "seed"
        status_label = _stringify_data_value(data.get("statusLabel"))
        if not status_label or status_label == "enriched":
            data["statusLabel"] = "captured"
        node["data"] = data

    return ReactFlowGraphData.model_validate(payload)


def _seed_node_id_for_tab(tab: ActiveTab, index: int) -> str:
    normalized = _normalized_url_key(str(tab.url))
    base = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    if not base:
        base = f"tab-{index + 1}"
    return f"seed-{base[:80]}"


def _ensure_seed_nodes_present(graph: ReactFlowGraphData, active_tabs: list[ActiveTab]) -> ReactFlowGraphData:
    payload = graph.model_dump()
    nodes = list(payload.get("nodes", []))
    existing_url_keys = {
        _normalized_url_key(_stringify_data_value((node.get("data") or {}).get("url")))
        for node in nodes
    }
    existing_url_keys.discard("")
    existing_ids = {str(node.get("id", "")) for node in nodes}
    next_rank = max(
        (
            int(_parse_data_number((node.get("data") or {}).get("aiRank"), 0))
            for node in nodes
        ),
        default=0,
    ) + 1

    for index, tab in enumerate(active_tabs):
        url = str(tab.url)
        url_key = _normalized_url_key(url)
        if not url_key or url_key in existing_url_keys:
            continue

        node_id = _seed_node_id_for_tab(tab, index)
        suffix = 2
        while node_id in existing_ids:
            node_id = f"{_seed_node_id_for_tab(tab, index)}-{suffix}"
            suffix += 1

        title = _title_from_active_tab_summary(tab)
        summary = tab.summary.strip() or f"Captured tab for {title}"
        nodes.append(
            {
                "id": node_id,
                "type": "research",
                "data": {
                    "title": title,
                    "url": url,
                    "sourceType": "seed",
                    "aiRank": float(next_rank),
                    "aiReason": "Captured tab preserved from the user's workspace.",
                    "summary": summary,
                    "constraintViolated": False,
                    "constraintReason": "",
                },
                "position": {"x": float(len(nodes) * 320), "y": 0.0},
            }
        )
        existing_ids.add(node_id)
        existing_url_keys.add(url_key)
        next_rank += 1

    payload["nodes"] = nodes
    return ReactFlowGraphData.model_validate(payload)


def _drop_discovered_nodes(graph: ReactFlowGraphData) -> ReactFlowGraphData:
    payload = graph.model_dump()
    kept_nodes = [
        node
        for node in payload.get("nodes", [])
        if _stringify_data_value((node.get("data") or {}).get("sourceType"), "seed").lower() != "discovered"
    ]
    kept_ids = {node.get("id") for node in kept_nodes}
    kept_edges = [
        edge
        for edge in payload.get("edges", [])
        if edge.get("source") in kept_ids and edge.get("target") in kept_ids
    ]
    payload["nodes"] = kept_nodes
    payload["edges"] = kept_edges
    return ReactFlowGraphData.model_validate(payload)





def _extract_price_constraint(user_constraint: str | None) -> tuple[str, float] | None:
    if not user_constraint:
        return None
    text = user_constraint.lower()
    max_patterns = [
        r"(under|below|less than(?:\s+or\s+equal\s+to)?|at most|up to|no more than|maximum|max|<=?)\s*\$?\s*(\d+(?:\.\d+)?)",
        r"\$?\s*(\d+(?:\.\d+)?)\s*(?:usd|dollars?)?\s*(?:or less|or below|or under)",
    ]
    for pattern in max_patterns:
        match = re.search(pattern, text)
        if match:
            return ("max", float(match.group(match.lastindex or 1)))

    min_patterns = [
        r"(over|above|more than(?:\s+or\s+equal\s+to)?|at least|no less than|minimum|min|>=?)\s*\$?\s*(\d+(?:\.\d+)?)",
        r"\$?\s*(\d+(?:\.\d+)?)\s*(?:usd|dollars?)?\s*(?:or more|or above|or over)",
    ]
    for pattern in min_patterns:
        match = re.search(pattern, text)
        if match:
            return ("min", float(match.group(match.lastindex or 1)))

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
    metrics = data.get("metrics")
    if isinstance(metrics, list):
        metric_bits: list[str] = []
        for metric in metrics[:2]:
            if not isinstance(metric, dict):
                continue
            label = _stringify_data_value(metric.get("label"))
            value = _stringify_data_value(metric.get("value"))
            if not value or value == "Unknown":
                continue
            metric_bits.append(f"{label}: {value}" if label else value)
        if metric_bits:
            return " | ".join(metric_bits[:2])

    price_usd = _parse_data_number(data.get("priceUsd"))
    if price_usd > 0:
        return f"${price_usd:,.0f}"

    summary = _stringify_data_value(data.get("summary"))
    if summary:
        return _debug_truncate(summary, 96)

    return _stringify_data_value(data.get("sourceLabel"), "Captured")


def _session_metadata_for_node(data: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    review_label = _review_field_label()
    review_consensus_present = bool(
        _review_field_value_from_mapping(data)
        or _review_field_value_from_mapping(data.get("rawData") if isinstance(data.get("rawData"), dict) else {})
    )
    for key in ["priceUsd", "combinedScore", "aiRank"]:
        value = data.get(key)
        if isinstance(value, (int, float)) and value not in (0, 0.0):
            metadata[key] = value

    for key in ["sourceType", "statusLabel", "kindLabel"]:
        value = _stringify_data_value(data.get(key))
        if value and value != "Unknown":
            metadata[key] = value

    metrics = data.get("metrics")
    if isinstance(metrics, list):
        sanitized_metrics = [
            {
                "label": _stringify_data_value(metric.get("label")),
                "value": _stringify_data_value(metric.get("value")),
            }
            for metric in metrics
            if isinstance(metric, dict)
            and _stringify_data_value(metric.get("value"))
            and _stringify_data_value(metric.get("value")) != "Unknown"
            and not (
                review_consensus_present
                and _normalize_data_key(_stringify_data_value(metric.get("label"))) == _normalize_data_key("Review Sentiment")
            )
        ]
        if sanitized_metrics:
            metadata["metrics"] = sanitized_metrics[:6]

    metadata["constraintViolated"] = bool(data.get("constraintViolated"))
    constraint_reason = _stringify_data_value(data.get("constraintReason"))
    if constraint_reason:
        metadata["constraintReason"] = constraint_reason

    raw_data = data.get("rawData")
    if isinstance(raw_data, dict):
        existing_keys = {_normalize_data_key(key) for key in metadata}
        for raw_key, raw_value in raw_data.items():
            if _is_internal_data_key(raw_key):
                continue
            normalized_key = _normalize_data_key(raw_key)
            if review_consensus_present and normalized_key == _normalize_data_key("Review Sentiment"):
                continue
            if normalized_key in existing_keys:
                continue
            if isinstance(raw_value, (int, float)) and raw_value not in (0, 0.0):
                metadata[str(raw_key)] = raw_value
                existing_keys.add(normalized_key)
                continue
            text = _stringify_data_value(raw_value)
            if not text or text == "Unknown":
                continue
            metadata[str(raw_key)] = text
            existing_keys.add(normalized_key)

    review_value = _review_field_value_from_mapping(data) or _review_field_value_from_mapping(
        data.get("rawData") if isinstance(data.get("rawData"), dict) else {}
    )
    metadata[review_label] = review_value or "Unknown"

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
                url=_stringify_data_value(data.get("url")),
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
    return SessionGraph(
        rubric_fields=list(graph.rubric_fields),
        nodes=session_nodes,
        edges=session_edges,
    )


def _rank_values(values: list[float], *, reverse: bool = False) -> dict[float, int]:
    unique_values = sorted(set(values), reverse=reverse)
    return {value: index + 1 for index, value in enumerate(unique_values)}


def _build_session_matrix(query: str, domain: str, graph: ReactFlowGraphData) -> SessionMatrix:
    option_nodes = list(graph.nodes)
    rubric_fields = graph.rubric_fields if hasattr(graph, "rubric_fields") and graph.rubric_fields else ["Details"]
    
    columns = [
        SessionMatrixColumn(key="score", label="Rank", type="text", highlight_best=True)
    ]
    
    # Generate dynamic columns from up to 5 rubric fields
    for i, field in enumerate(rubric_fields[:5]):
        col_type = "currency" if "price" in field.lower() or "cost" in field.lower() else "text"
        columns.append(SessionMatrixColumn(key=f"attr_{i}", label=field, type=col_type))
        
    columns.append(SessionMatrixColumn(key="notes", label="Notes", type="text"))
    
    rows: list[SessionMatrixRow] = []
    for node in option_nodes:
        data = dict(node.data)
        ai_rank = int(_parse_data_number(data.get("aiRank"), 0)) or None
        
        cells = {
            "score": SessionMatrixCell(value=ai_rank, display=f"#{ai_rank}" if ai_rank else "Unknown", rank=ai_rank),
        }
        
        for i, field in enumerate(rubric_fields[:5]):
            # Try to find the exact or normalized key in attributes or data
            val = ""
            # Search in the dynamic attributes array first
            for attr in data.get("attributes", []):
                if _normalize_data_key(attr.get("label", "")) == _normalize_data_key(field):
                    val = _stringify_data_value(attr.get("value"))
                    break
            
            # Fallback to direct keys
            if not val:
                val = _stringify_data_value(_lookup_data_value(data, [field, _normalize_data_key(field)]))
                
            cells[f"attr_{i}"] = SessionMatrixCell(value=None, display=val if val else "Unknown")
            
        risk_text = _stringify_data_value(data.get("constraintReason"))
        cells["notes"] = SessionMatrixCell(value=None, display=risk_text or "No issues")
        
        rows.append(SessionMatrixRow(node_id=node.id, cells=cells))

    # Keep a nice rubric name
    rubric_title = f"{domain.capitalize()} Comparison" if domain else "Research Comparison"
    return SessionMatrix(rubric=rubric_title, columns=columns, rows=rows)


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
    domain = graph.domain or "research"
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
        user_constraint=(user_constraint or "").strip(),
        domain=domain,
        status=overall_status,
        rubric_fields=list(graph.rubric_fields),
        graph=session_graph,
        matrix=session_matrix,
        digest=session_digest,
    )


def _apply_constraint_to_session(session: UnifiedSession, user_constraint: str | None = None) -> UnifiedSession:
    next_session = session.model_copy(deep=True)
    normalized_constraint = (user_constraint or "").strip()
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
    if normalized_constraint:
        theme_signals.append(f"constraint: {normalized_constraint}")
    next_session.digest.theme_signals = theme_signals
    next_session.user_constraint = normalized_constraint
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
    return graph


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
        "readable_text": payload.readable_text,
        "readable_html": payload.readable_html,
        "readable_extractor": payload.readable_extractor,
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
                "readable_text_chars": len(payload.readable_text),
                "readable_extractor": payload.readable_extractor,
                "timestamp": timestamp,
            }
        ),
        len(extension_history),
    )
    return {"status": "ok"}


@app.get("/api/v1/extension/preferences")
async def extension_preferences_view() -> dict[str, Any]:
    return extension_preferences


@app.post("/api/v1/extension/preferences")
async def extension_preferences_update(payload: ExtensionPreferencesRequest) -> dict[str, Any]:
    extension_preferences["user_prompt"] = payload.user_prompt.strip()
    extension_preferences["enable_discovery"] = bool(payload.enable_discovery)
    return extension_preferences


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
        "enable_discovery": bool(extension_preferences.get("enable_discovery", DISCOVERY_ENABLED_BY_DEFAULT)),
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
        effective_discovery = (
            bool(payload.enable_discovery)
            if payload.enable_discovery is not None
            else _should_investigate_further(payload.user_prompt)
        )
        effective_query_budget = payload.firecrawl_query_budget if effective_discovery else 0
        logger.debug(
            "Synthesize start prompt=%r constraint=%r active_tabs=%s budget=%s discovery=%s",
            _debug_truncate(payload.user_prompt, 160),
            _debug_truncate(payload.user_constraint, 120) if payload.user_constraint else "",
            len(payload.active_tabs),
            effective_query_budget,
            effective_discovery,
        )
        logger.debug(
            "Synthesize active tab previews=%s",
            _debug_json([_tab_debug_payload(tab) for tab in payload.active_tabs]),
        )
        context = _tabs_to_context(payload.active_tabs)
        query_budget_remaining = effective_query_budget
        allow_discovery = effective_discovery and query_budget_remaining > 0

        rubric = await _build_rubric(payload, context)
        logger.debug("Generated rubric fields=%s", rubric.fields)
        logger.debug("Inferred constraints=%s seed_patterns=%s", rubric.inferred_constraints, rubric.seed_patterns)

        review_queries = _review_search_queries(payload.active_tabs, payload.user_prompt, query_budget_remaining)
        if allow_discovery and review_queries:
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
            previous_graph=payload.previous_graph,
        )
        logger.debug("Raw graph snapshot=%s", _debug_json(_graph_debug_payload(raw_graph)))
        filtered_graph = _filter_graph_for_prompt(raw_graph, payload.user_prompt)
        if len(filtered_graph.nodes) != len(raw_graph.nodes):
            logger.debug(
                "Prompt filter adjusted graph before_nodes=%s after_nodes=%s",
                len(raw_graph.nodes),
                len(filtered_graph.nodes),
            )
        filtered_graph = _ensure_seed_nodes_present(filtered_graph, payload.active_tabs)
        graph = _canonicalize_graph_for_frontend(filtered_graph)
        graph = _reconcile_seed_nodes(graph, payload.active_tabs)
        if not effective_discovery:
            graph = _drop_discovered_nodes(graph)
        graph.domain = rubric.domain
        graph.rubric_fields = rubric.fields
        logger.debug("Canonical graph snapshot=%s", _debug_json(_graph_debug_payload(graph)))
        placeholder_like_nodes = [
            node.id
            for node in graph.nodes
            if re.fullmatch(r"Item \d+", node.data.title or "")
            or (
                not node.data.url
                and not _stringify_data_value(getattr(node.data, "summary", ""))
                and not isinstance(getattr(node.data, "metrics", None), list)
            )
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
            enable_discovery=payload.enable_discovery,
            previous_graph=payload.previous_graph,
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
            firecrawl_query_budget=payload.firecrawl_query_budget,
            enable_discovery=payload.enable_discovery,
            previous_graph=payload.previous_graph,
            tabs=[
                DomTab(
                    url=entry["url"],
                    title=str(entry.get("title", "")),
                    dom=str(entry.get("dom", "")),
                    readable_text=str(entry.get("readable_text", "")),
                    readable_html=str(entry.get("readable_html", "")),
                    readable_extractor=str(entry.get("readable_extractor", "")),
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
