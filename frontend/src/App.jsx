import { Fragment, useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  BookText,
  ExternalLink,
  LayoutGrid,
  Loader2,
  Search,
  SlidersHorizontal,
  Sparkles,
  Table2,
  X,
} from "lucide-react";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8010";
const DEFAULT_QUERY = "Compare the laptop coolers";
const BOARD_WIDTH = 1400;
const BOARD_MIN_HEIGHT = 780;
const NODE_WIDTH = 252;
const NODE_HEIGHT = 212;
const NODE_GAP = 48;
const BOARD_PADDING = 56;
const BOARD_MAX_COLUMNS = 4;
const SORT_OPTIONS = [
  { value: "ai", label: "AI best choice" },
  { value: "price", label: "Price" },
  { value: "noise", label: "Noise" },
];

const TYPE_CLR = {
  listing: "#d0ab67",
  review: "#c98673",
  location: "#8ca38f",
  enrichment: "#b09a74",
  reference: "#b9aa86",
  item: "#93a696",
  laptop_cooler: "#93a696",
};

const SENT_CLR = {
  positive: "#9eb785",
  neutral: "#aaa08d",
  negative: "#c98673",
  unknown: "#c7ac6b",
};

const STAT_CLR = {
  ready: "#9eb785",
  flagged: "#c98673",
  pending: "#aaa08d",
};

const EDGE_CLR = {
  mentions: "rgba(201, 134, 115, 0.34)",
  competes_with: "rgba(208, 171, 103, 0.2)",
  located_in: "rgba(236, 228, 214, 0.08)",
  enriches: "rgba(147, 166, 150, 0.18)",
  related_to: "rgba(236, 228, 214, 0.12)",
};

const pillStyle = {
  padding: "6px 10px",
  borderRadius: 999,
  fontSize: 11,
  background: "rgba(245, 238, 226, 0.035)",
  color: "#d7d1c6",
  border: "1px solid rgba(236, 228, 214, 0.08)",
};

function Badge({ sentiment, children }) {
  return (
    <span
      style={{
        ...pillStyle,
        color: SENT_CLR[sentiment] || SENT_CLR.unknown,
        background:
          sentiment === "positive"
            ? "rgba(158, 183, 133, 0.14)"
            : sentiment === "negative"
              ? "rgba(201, 134, 115, 0.14)"
              : sentiment === "neutral"
                ? "rgba(170, 160, 141, 0.12)"
                : "rgba(199, 172, 107, 0.14)",
      }}
    >
      {children}
    </span>
  );
}

function normalizeType(value) {
  return String(value || "item")
    .trim()
    .toLowerCase()
    .replace(/\s+/g, "_");
}

function numberOrNull(value) {
  return typeof value === "number" && Number.isFinite(value) && value > 0
    ? value
    : null;
}

function metaNumber(node, key) {
  const value = node?.metadata?.[key];
  return numberOrNull(typeof value === "string" ? Number(value) : value);
}

function metaText(node, key, fallback = "Unknown") {
  const value = node?.metadata?.[key];
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

function reviewMetricForNode(node) {
  const metrics = node?.metadata?.metrics || [];
  const metricMatch = metrics.find((metric) =>
    /review|consensus|feedback|complaint/i.test(String(metric?.label || "")),
  );
  if (metricMatch) {
    return metricMatch;
  }
  const reviewConsensus = metaText(node, "Review Consensus", "");
  if (reviewConsensus) {
    return { label: "Review Consensus", value: reviewConsensus };
  }
  return null;
}

function subtitleWithoutDuplicateReview(node, reviewMetric) {
  const subtitle = String(node?.subtitle || "").trim();
  if (!subtitle || !reviewMetric?.value) {
    return subtitle;
  }
  const normalizedReviewLabel = String(reviewMetric.label || "").trim().toLowerCase();
  const normalizedReviewValue = String(reviewMetric.value || "").trim().toLowerCase();
  const cleanedParts = subtitle
    .split("|")
    .map((part) => part.trim())
    .filter((part) => {
      const normalizedPart = part.replace(/\s+/g, " ").trim().toLowerCase();
      if (!normalizedPart) {
        return false;
      }
      if (normalizedReviewLabel && normalizedPart.includes(normalizedReviewLabel)) {
        return false;
      }
      if (normalizedReviewValue && normalizedPart.includes(normalizedReviewValue)) {
        return false;
      }
      return !/review|consensus|feedback|complaint/i.test(part);
    });
  return cleanedParts.join(" | ");
}

function cardMetricsForNode(node, limit = 3) {
  const metrics = Array.isArray(node?.metadata?.metrics) ? node.metadata.metrics : [];
  if (!metrics.length) {
    return [];
  }
  const reviewMetric = reviewMetricForNode(node);
  const filtered = reviewMetric
    ? metrics.filter(
        (metric) =>
          String(metric?.label || "").trim() !== String(reviewMetric.label || "").trim(),
      )
    : metrics;
  return filtered.slice(0, limit);
}

function isViolated(node) {
  return Boolean(node?.metadata?.constraintViolated);
}

function isDiscoveredNode(node) {
  return node?.metadata?.sourceType === "discovered";
}

function isSeedNode(node) {
  return !isDiscoveredNode(node);
}

function isExternalReviewNode(node) {
  const source = String(node?.source || "").toLowerCase();
  const title = String(node?.title || "").toLowerCase();
  const type = normalizeType(node?.type);
  const group = String(node?.group || "").toLowerCase();
  const sourceType = String(node?.metadata?.sourceType || "").toLowerCase();

  return (
    type === "review" ||
    group === "reviews" ||
    source.includes("reddit") ||
    source.includes("forum") ||
    source.includes("youtube") ||
    (sourceType === "discovered" &&
      (title.includes("consensus") ||
        title.includes("owner feedback") ||
        title.includes("review") ||
        title.includes("complaint")))
  );
}

function getNodeSortValue(node, sortMode) {
  if (sortMode === "price") {
    return metaNumber(node, "priceUsd") ?? Number.POSITIVE_INFINITY;
  }
  if (sortMode === "noise") {
    return metaNumber(node, "noiseLevelDb") ?? Number.POSITIVE_INFINITY;
  }
  return metaNumber(node, "aiRank") ?? Number.POSITIVE_INFINITY;
}

function nodeXForDomain(node, domain) {
  return metaNumber(node, "combinedScore") ?? Number.POSITIVE_INFINITY;
}

function nodeYForDomain(node, domain) {
  return metaNumber(node, "aiRank") ?? Number.POSITIVE_INFINITY;
}

function axisMetaForDomain(domain) {
  return {
    x: { label: "Relevance score", low: "Lower", high: "Higher" },
    y: { label: "AI Rank", low: "Worse", high: "Better" },
  };
}

function boardColumnsFor(nodesLength, viewportWidth) {
  if (nodesLength <= 1) {
    return 1;
  }
  // Allow the board to grow wide instead of clamping it to the viewport width.
  // For small node counts, don't force too many columns.
  const ideal = Math.ceil(Math.sqrt(nodesLength));
  return Math.max(2, ideal);
}

function boardWidthFor(columns, viewportWidth, hasDetail) {
  const sidebarWidth = hasDetail ? 360 : 0;
  // Account for shell and stage padding
  const totalShellPadding = 56; // 28px * 2
  const totalStagePadding = 44; // 22px * 2
  const availableWidth = viewportWidth - sidebarWidth - totalShellPadding - totalStagePadding;

  const naturalWidth =
    BOARD_PADDING * 2 +
    columns * NODE_WIDTH +
    Math.max(0, columns - 1) * NODE_GAP;

  return Math.max(availableWidth, naturalWidth, 1000);
}

function boardHeightFor(nodesLength, columns) {
  const rows = Math.max(1, Math.ceil(nodesLength / columns));
  const scatterHeight =
    BOARD_PADDING * 2 + rows * (NODE_HEIGHT + NODE_GAP * 1.5);
  return Math.max(BOARD_MIN_HEIGHT, scatterHeight);
}

function computeLayout(nodes, viewportWidth, boardWidth, boardHeight) {
  const positions = {};
  if (!nodes.length) {
    return positions;
  }

  const rankedNodes = [...nodes];
  const priceValues = rankedNodes
    .map((node) => metaNumber(node, "priceUsd"))
    .filter((value) => value !== null);
  const scoreValues = rankedNodes
    .map((node) => metaNumber(node, "combinedScore"))
    .filter((value) => value !== null);
  const aiRanks = rankedNodes
    .map((node) => metaNumber(node, "aiRank"))
    .filter((value) => value !== null);

  const priceMin = priceValues.length ? Math.min(...priceValues) : 0;
  const priceMax = priceValues.length ? Math.max(...priceValues) : 0;
  const scoreMin = scoreValues.length ? Math.min(...scoreValues) : 0;
  const scoreMax = scoreValues.length ? Math.max(...scoreValues) : 0;
  const rankMin = aiRanks.length ? Math.min(...aiRanks) : 1;
  const rankMax = aiRanks.length ? Math.max(...aiRanks) : 1;
  const xRange = Math.max(0, boardWidth - NODE_WIDTH - BOARD_PADDING * 2);
  const yRange = Math.max(0, boardHeight - NODE_HEIGHT - BOARD_PADDING * 2);
  const targetPositions = {};

  rankedNodes.forEach((node) => {
    const rank = metaNumber(node, "aiRank") ?? rankMax;
    const combinedScore = metaNumber(node, "combinedScore") ?? scoreMin;
    const price = metaNumber(node, "priceUsd");
    const rankNormalized =
      rankMax > rankMin ? 1 - (rank - rankMin) / (rankMax - rankMin) : 1;
    const fitNormalized =
      scoreMax > scoreMin
        ? (combinedScore - scoreMin) / (scoreMax - scoreMin)
        : 0.65;
    const pricePenalty =
      price !== null && priceMax > priceMin
        ? (price - priceMin) / (priceMax - priceMin)
        : 0.2;
    const xNormalized = Math.max(
      0,
      Math.min(1, rankNormalized * 0.8 + (1 - pricePenalty) * 0.2),
    );
    const yNormalizedBase = Math.max(0, Math.min(1, fitNormalized));
    const yNormalized = isViolated(node)
      ? Math.min(0.18, yNormalizedBase * 0.35)
      : Math.max(0.12, yNormalizedBase);

    targetPositions[node.id] = {
      x: BOARD_PADDING + xNormalized * xRange,
      y: BOARD_PADDING + (1 - yNormalized) * yRange,
    };
    positions[node.id] = { ...targetPositions[node.id] };
  });

  const orderedIds = rankedNodes
    .sort((left, right) => {
      const leftScore = metaNumber(left, "combinedScore") ?? scoreMin;
      const rightScore = metaNumber(right, "combinedScore") ?? scoreMin;
      if (leftScore !== rightScore) {
        return rightScore - leftScore;
      }
      return (
        (metaNumber(left, "aiRank") ?? 999) - (metaNumber(right, "aiRank") ?? 999)
      );
    })
    .map((node) => node.id);

  const minDx = NODE_WIDTH + NODE_GAP;
  const minDy = NODE_HEIGHT + NODE_GAP;

  for (let iteration = 0; iteration < 200; iteration += 1) {
    let anyOverlap = false;

    for (let index = 0; index < orderedIds.length; index += 1) {
      const leftId = orderedIds[index];
      const leftPosition = positions[leftId];
      if (!leftPosition) {
        continue;
      }

      for (
        let nextIndex = index + 1;
        nextIndex < orderedIds.length;
        nextIndex += 1
      ) {
        const rightId = orderedIds[nextIndex];
        const rightPosition = positions[rightId];
        if (!rightPosition) {
          continue;
        }

        const dx = rightPosition.x - leftPosition.x;
        const dy = rightPosition.y - leftPosition.y;
        const overlapX = minDx - Math.abs(dx);
        const overlapY = minDy - Math.abs(dy);

        if (overlapX > 0 && overlapY > 0) {
          anyOverlap = true;
          const pushX = overlapX * 0.52 * (dx >= 0 ? 1 : -1);
          const pushY = overlapY * 0.52 * (dy >= 0 ? 1 : -1);
          leftPosition.x = Math.max(
            BOARD_PADDING,
            Math.min(BOARD_PADDING + xRange, leftPosition.x - pushX),
          );
          rightPosition.x = Math.max(
            BOARD_PADDING,
            Math.min(BOARD_PADDING + xRange, rightPosition.x + pushX),
          );
          leftPosition.y = Math.max(
            BOARD_PADDING,
            Math.min(BOARD_PADDING + yRange, leftPosition.y - pushY),
          );
          rightPosition.y = Math.max(
            BOARD_PADDING,
            Math.min(BOARD_PADDING + yRange, rightPosition.y + pushY),
          );
        }
      }
    }

    orderedIds.forEach((id) => {
      positions[id].x = Math.max(
        BOARD_PADDING,
        Math.min(
          BOARD_PADDING + xRange,
          positions[id].x * 0.92 + targetPositions[id].x * 0.08,
        ),
      );
      positions[id].y = Math.max(
        BOARD_PADDING,
        Math.min(
          BOARD_PADDING + yRange,
          positions[id].y * 0.92 + targetPositions[id].y * 0.08,
        ),
      );
    });

    if (!anyOverlap) {
      break;
    }
  }

  return positions;
}

async function fetchSession(query, constraint, enableDiscovery, previousSession) {
  const statsResponse = await fetch(`${API_BASE}/api/v1/extension/history/stats`);
  if (!statsResponse.ok) {
    throw new Error(
      `Failed to load extension history stats: ${statsResponse.status}`,
    );
  }

  const stats = await statsResponse.json();
  if (Number(stats.count ?? 0) <= 0) {
    throw new Error(
      "No synced extension snapshots found. Open the extension, capture a page, and click Graph again.",
    );
  }

  const bodyPayload = {
    user_prompt: query,
    user_constraint: constraint || null,
    firecrawl_query_budget: enableDiscovery ? 4 : 0,
    max_tabs: 20,
    enable_discovery: Boolean(enableDiscovery),
  };
  if (enableDiscovery && previousSession && previousSession.graph) {
    bodyPayload.previous_graph = previousSession.graph;
  }

  const response = await fetch(
    `${API_BASE}/api/v1/session/synthesize-from-extension-history`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(bodyPayload),
    },
  );

  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Session synthesis failed: ${response.status}`);
  }

  return response.json();
}

async function applyConstraintToSession(session, constraint) {
  const response = await fetch(`${API_BASE}/api/v1/session/apply-constraint`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session,
      user_constraint: constraint || null,
    }),
  });

  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Constraint update failed: ${response.status}`);
  }

  return response.json();
}

function LoadingOverlay({ message }) {
  return (
    <motion.div
      className="loading-overlay"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
    >
      <div className="loading-content">
        <Loader2 size={48} strokeWidth={1.5} className="spin-icon" />
        <h2>{message || "Synthesizing research..."}</h2>
        <p>Aggregating signals from your captured tabs and discovered context.</p>
        <div className="loading-progress-bar">
          <motion.div
            className="loading-progress-fill"
            initial={{ width: "0%" }}
            animate={{ width: "95%" }}
            transition={{ duration: 15, ease: "linear" }}
          />
        </div>
      </div>
    </motion.div>
  );
}

function BoardCard({ node, active, onSelect }) {
  const color = TYPE_CLR[normalizeType(node.type)] || TYPE_CLR.item;
  const violated = isViolated(node);
  const metrics = cardMetricsForNode(node, 3);
  const reviewMetric = reviewMetricForNode(node);
  const subtitle = subtitleWithoutDuplicateReview(node, reviewMetric);

  return (
    <motion.button
      type="button"
      className={`board-node-card${active ? " is-active" : ""}${
        violated ? " is-flagged" : ""
      }`}
      initial={{ opacity: 0, scale: 0.92, y: 18 }}
      animate={{ opacity: violated ? 0.88 : 1, scale: 1, y: 0 }}
      transition={{ type: "spring", stiffness: 220, damping: 26 }}
      onClick={() => onSelect((prev) => (prev === node.id ? null : node.id))}
      style={{ "--accent": color, width: NODE_WIDTH, minHeight: NODE_HEIGHT }}
    >
      <div className="board-node-top">
        <span className="board-node-source">{node.source}</span>
        <span
          className="board-node-dot"
          style={{ background: STAT_CLR[node.status] || "#8f8779" }}
        />
        <span className="board-node-state">{violated ? "flagged" : "fit"}</span>
      </div>

      <div className="board-node-title">{node.title}</div>
      {subtitle ? <div className="board-node-subtitle">{subtitle}</div> : null}

      {reviewMetric ? (
        <div className="board-node-review-consensus">
          {reviewMetric.label}: {reviewMetric.value}
        </div>
      ) : null}

      {violated && node.metadata?.constraintReason ? (
        <div className="board-node-warning">{node.metadata.constraintReason}</div>
      ) : null}

      {metrics.length > 0 && (
        <div className="board-node-metrics">
          {metrics.map((m, i) => (
            <div key={i} className="metric-pair">
              <div className="metric-label">{m.label}</div>
              <div className="metric-value">{m.value}</div>
            </div>
          ))}
        </div>
      )}
    </motion.button>
  );
}

function DigestCard({ entry, node, active, onSelect, onSwap, index }) {
  const color = TYPE_CLR[normalizeType(node?.type)] || TYPE_CLR.item;
  const violated = isViolated(node);
  const aiRank = metaNumber(node, "aiRank");
  const metrics = cardMetricsForNode(node, 3);
  const reviewMetric = reviewMetricForNode(node);

  return (
    <motion.div
      layout
      className={`digest-card${active ? " is-active" : ""}${
        violated ? " is-flagged" : ""
      }`}
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.03 }}
      draggable
      onDragStart={(event) => {
        event.dataTransfer.setData("text/plain", entry.node_id);
        event.dataTransfer.effectAllowed = "move";
      }}
      onDragOver={(event) => {
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
      }}
      onDrop={(event) => {
        event.preventDefault();
        const sourceId = event.dataTransfer.getData("text/plain");
        onSwap(sourceId, entry.node_id);
      }}
      onClick={() => onSelect((prev) => (prev === entry.node_id ? null : entry.node_id))}
      style={{
        "--accent": color,
        opacity: entry.relevance < 0.4 ? 0.72 : 1,
      }}
    >
      <div className="digest-topline">
        <span className="source-pill" style={{ "--accent": color }}>
          {node?.source || "Captured"}
        </span>
        <span className={`status-pill${violated ? " is-flagged" : ""}`}>
          {violated ? "flagged" : node?.status || "ready"}
        </span>
        <span className="relevance-pill">
          {aiRank ? `AI #${aiRank}` : `${Math.round(entry.relevance * 100)}%`}
        </span>
      </div>

      <div className="digest-title">{node?.title || entry.node_id}</div>
      <div className="digest-summary">{entry.summary}</div>

      {reviewMetric ? (
        <div className="digest-review-consensus">
          {reviewMetric.label}: {reviewMetric.value}
        </div>
      ) : null}

      {metrics.length > 0 && (
        <div className="digest-metrics">
          {metrics.map((m, i) => (
            <div key={i} className="metric-pair">
              <div className="metric-label">{m.label}</div>
              <div className="metric-value">{m.value}</div>
            </div>
          ))}
        </div>
      )}

      <div className="digest-signal-row">
        {violated && node?.metadata?.constraintReason ? (
          <span
            style={{
              ...pillStyle,
              color: "#e1b3a8",
              background: "rgba(201, 134, 115, 0.12)",
              border: "1px solid rgba(201, 134, 115, 0.24)",
              maxWidth: 200,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
            title={node.metadata.constraintReason}
          >
            {node.metadata.constraintReason}
          </span>
        ) : null}
        {entry.signals.slice(0, 2).map((signal) => (
          <span
            key={`${entry.node_id}-${signal.label}`}
            style={{
              ...pillStyle,
              maxWidth: 160,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
            title={signal.label}
          >
            {signal.label.length > 28 ? `${signal.label.slice(0, 26)}…` : signal.label}
          </span>
        ))}
        {entry.signals.length > 2 ? (
          <span style={{ ...pillStyle, color: "#8c8478" }}>+{entry.signals.length - 2}</span>
        ) : null}
      </div>
    </motion.div>
  );
}

export default function App() {
  const [selected, setSelected] = useState(null);
  const [query, setQuery] = useState(DEFAULT_QUERY);
  const [constraint, setConstraint] = useState("");
  const [view, setView] = useState("board");
  const [sortMode, setSortMode] = useState("ai");
  const [showDiscovered, setShowDiscovered] = useState(true);
  const [session, setSession] = useState(null);
  const [positions, setPositions] = useState({});
  const [digestOrder, setDigestOrder] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");
  const [enableDiscovery, setEnableDiscovery] = useState(true);
  const [viewportWidth, setViewportWidth] = useState(() =>
    typeof window === "undefined" ? 1280 : window.innerWidth,
  );

  const data = session;
  const nodeMap = useMemo(
    () => Object.fromEntries((data?.graph?.nodes || []).map((node) => [node.id, node])),
    [data],
  );
  const selectedNode = selected ? nodeMap[selected] : null;
  const selectedMetadataEntries = useMemo(() => {
    if (!selectedNode?.metadata) {
      return [];
    }
    const hasReviewConsensus =
      typeof selectedNode.metadata["Review Consensus"] === "string" &&
      selectedNode.metadata["Review Consensus"].trim();
    return Object.entries(selectedNode.metadata).filter(
      ([key, value]) =>
        key !== "metrics" &&
        !(hasReviewConsensus && key === "Review Sentiment") &&
        (typeof value === "string" ||
          typeof value === "number" ||
          typeof value === "boolean"),
    );
  }, [selectedNode]);
  const boardAxes = useMemo(() => axisMetaForDomain(data?.domain), [data]);

  const allNodes = useMemo(() => data?.graph?.nodes || [], [data]);
  const visibleNodes = useMemo(() => {
    if (showDiscovered) {
      return allNodes;
    }
    return allNodes.filter((node) => !isDiscoveredNode(node));
  }, [allNodes, showDiscovered]);

  const boardNodes = useMemo(
    () =>
      visibleNodes
        .filter((node) => !isExternalReviewNode(node))
        .sort((left, right) => {
          if (isViolated(left) !== isViolated(right)) {
            return Number(isViolated(left)) - Number(isViolated(right));
          }
          return (
            (metaNumber(left, "aiRank") ?? 999) -
            (metaNumber(right, "aiRank") ?? 999)
          );
        }),
    [visibleNodes],
  );

  const reviewNodes = useMemo(
    () =>
      visibleNodes
        .filter((node) => isExternalReviewNode(node))
        .sort(
          (left, right) =>
            (metaNumber(left, "aiRank") ?? 999) -
            (metaNumber(right, "aiRank") ?? 999),
        ),
    [visibleNodes],
  );

  const boardNodeIds = useMemo(
    () => new Set(boardNodes.map((node) => node.id)),
    [boardNodes],
  );
  const visibleNodeIds = useMemo(
    () => new Set(visibleNodes.map((node) => node.id)),
    [visibleNodes],
  );

  const boardColumns = useMemo(
    () => boardColumnsFor(boardNodes.length, viewportWidth),
    [boardNodes.length, viewportWidth],
  );
  const boardWidth = useMemo(
    () => boardWidthFor(boardColumns, viewportWidth, !!selectedNode),
    [boardColumns, viewportWidth, !!selectedNode],
  );
  const boardHeight = useMemo(
    () => boardHeightFor(boardNodes.length, boardColumns),
    [boardColumns, boardNodes.length],
  );

  const visibleEdges = useMemo(
    () =>
      (data?.graph?.edges || []).filter(
        (edge) => boardNodeIds.has(edge.from) && boardNodeIds.has(edge.to),
      ),
    [boardNodeIds, data],
  );

  useEffect(() => {
    setPositions(computeLayout(boardNodes, viewportWidth, boardWidth, boardHeight));
  }, [boardNodes, viewportWidth, boardWidth, boardHeight]);

  useEffect(() => {
    const handleResize = () => setViewportWidth(window.innerWidth);
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  async function runQuery(nextQuery, nextConstraint, nextEnableDiscovery = enableDiscovery) {
    const resolvedQuery = nextQuery.trim() || DEFAULT_QUERY;
    const resolvedConstraint = nextConstraint.trim();
    setIsLoading(true);
    setError("");

    try {
      const appliedConstraint = typeof session?.user_constraint === "string"
        ? session.user_constraint.trim()
        : "";

      const shouldReuseSession =
        Boolean(session) &&
        typeof session.query === "string" &&
        session.query.trim().toLowerCase() === resolvedQuery.toLowerCase() &&
        appliedConstraint !== resolvedConstraint;

      const payload = shouldReuseSession
        ? await applyConstraintToSession(session, resolvedConstraint)
        : await fetchSession(resolvedQuery, resolvedConstraint, nextEnableDiscovery, session);

      setSession(payload);
      setQuery(payload.query || resolvedQuery);
      setEnableDiscovery(Boolean(nextEnableDiscovery));
      setSelected(null);
      setDigestOrder([]);
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "Failed to load session.",
      );
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const prompt = params.get("prompt")?.trim() || DEFAULT_QUERY;
    const constraintParam = params.get("constraint")?.trim() || "";
    const discoveryParam = params.get("discover");
    const discoveryEnabled =
      discoveryParam == null ? true : !["0", "false", "off"].includes(discoveryParam.toLowerCase());
    setQuery(prompt);
    setConstraint(constraintParam);
    setEnableDiscovery(discoveryEnabled);
    runQuery(prompt, constraintParam, discoveryEnabled);
  }, []);

  const baseDigestEntries = useMemo(() => {
    const entries = [...(data?.digest?.entries || [])].filter((entry) =>
      visibleNodeIds.has(entry.node_id),
    );

    return entries.sort((left, right) => {
      const leftNode = nodeMap[left.node_id];
      const rightNode = nodeMap[right.node_id];
      const leftValue = getNodeSortValue(leftNode, sortMode);
      const rightValue = getNodeSortValue(rightNode, sortMode);

      if (leftValue !== rightValue) {
        return leftValue - rightValue;
      }
      if (isViolated(leftNode) !== isViolated(rightNode)) {
        return Number(isViolated(leftNode)) - Number(isViolated(rightNode));
      }
      return right.relevance - left.relevance;
    });
  }, [data, nodeMap, sortMode, visibleNodeIds]);

  useEffect(() => {
    setDigestOrder([]);
  }, [sortMode, showDiscovered]);

  const visibleMatrixRows = useMemo(
    () => (data?.matrix?.rows || []).filter((row) => boardNodeIds.has(row.node_id)),
    [boardNodeIds, data],
  );

  useEffect(() => {
    const nextIds = baseDigestEntries.map((entry) => entry.node_id);
    setDigestOrder((current) => {
      if (!nextIds.length) {
        return [];
      }
      if (!current.length) {
        return nextIds;
      }
      const preserved = current.filter((id) => nextIds.includes(id));
      const missing = nextIds.filter((id) => !preserved.includes(id));
      return [...preserved, ...missing];
    });
  }, [baseDigestEntries]);

  const digestEntries = useMemo(() => {
    if (!digestOrder.length) {
      return baseDigestEntries;
    }
    const entryMap = new Map(
      baseDigestEntries.map((entry) => [entry.node_id, entry]),
    );
    return digestOrder.map((id) => entryMap.get(id)).filter(Boolean);
  }, [baseDigestEntries, digestOrder]);

  function swapDigestCards(sourceId, targetId) {
    if (!sourceId || !targetId || sourceId === targetId) {
      return;
    }

    setDigestOrder((current) => {
      const base = current.length
        ? [...current]
        : baseDigestEntries.map((entry) => entry.node_id);
      const sourceIndex = base.indexOf(sourceId);
      const targetIndex = base.indexOf(targetId);

      if (sourceIndex === -1 || targetIndex === -1) {
        return base;
      }

      [base[sourceIndex], base[targetIndex]] = [
        base[targetIndex],
        base[sourceIndex],
      ];
      return base;
    });
  }

  const compareColumns = visibleMatrixRows.map((row) => ({
    row,
    node: nodeMap[row.node_id],
  }));
  const leadBoardNode = boardNodes[0] || null;
  const flaggedBoardCount = boardNodes.filter((node) => isViolated(node)).length;
  const discoveredBoardCount = boardNodes.filter((node) => isDiscoveredNode(node)).length;
  const capturedBoardCount = boardNodes.filter((node) => isSeedNode(node)).length;

  function handleRunSubmit(event) {
    event.preventDefault();
    const formData = new FormData(event.currentTarget);
    const nextQuery = String(formData.get("query") || "");
    const nextConstraint = String(formData.get("constraint") || "");
    setQuery(nextQuery);
    setConstraint(nextConstraint);
    runQuery(nextQuery, nextConstraint);
  }

  return (
    <div className="synapse-app">
      <div className="app-grid-overlay" />

      <header className="workspace-header">
        <div className="brand-cluster">
          <div className="brand-mark">S</div>
          <div>
            <p className="workspace-eyebrow">Workspace</p>
            <h1 className="workspace-title">Context canvas</h1>
          </div>
        </div>

        <div className="header-status">
          <div className={`live-pill${isLoading ? " is-loading" : ""}`}>
            <span className="live-dot" />
            {isLoading
              ? "Refreshing session"
              : data?.digest?.stats
                ? `${data.digest.stats.ready || 0} ready`
                : "Awaiting capture"}
          </div>
          <div className="header-domain">{data?.domain || "Live session"}</div>
        </div>
      </header>

      <section className="control-shell">
        <form
          className="control-form"
          onSubmit={handleRunSubmit}
        >
          <label className="input-shell prompt-shell">
            <Search size={16} strokeWidth={2.1} />
            <div className="input-copy">
              <span className="input-label">Prompt</span>
              <input
                name="query"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="What should Synapse synthesize?"
              />
            </div>
          </label>

          <label className="input-shell constraint-shell">
            <SlidersHorizontal size={16} strokeWidth={2.1} />
            <div className="input-copy">
              <span className="input-label">Constraint</span>
              <input
                name="constraint"
                value={constraint}
                onChange={(event) => setConstraint(event.target.value)}
                placeholder="Optional filter, threshold, or exclusion"
              />
            </div>
          </label>

          <button className="run-button" type="submit" disabled={isLoading}>
            {isLoading ? (
              <Loader2 size={16} strokeWidth={2.1} className="spin-icon" />
            ) : (
              <Sparkles size={16} strokeWidth={2.1} />
            )}
            <span>{isLoading ? "Running" : "Run synthesis"}</span>
          </button>
        </form>

        <div className="signal-row">
          <div className="signal-group">
            {data?.digest?.theme_signals
              ?.filter((signal) => typeof signal === "string" && !signal.startsWith("constraint:"))
              .slice(0, 3)
              .map((signal) => (
                <span key={signal} className="signal-chip soft">
                  {signal}
                </span>
              ))}
          </div>

          <div className="stats-cluster">
            <span className="stat-chip">{boardNodes.length} canvas</span>
            <span className="stat-chip">{digestEntries.length} digest</span>
            <span className="stat-chip">{capturedBoardCount} captured</span>
            <span className="stat-chip">{discoveredBoardCount} AI found</span>
            <button
              type="button"
              className={`stat-chip button-chip${showDiscovered ? " is-on" : ""}`}
              onClick={() => setShowDiscovered((current) => !current)}
              title={
                showDiscovered
                  ? "Showing captured tabs and AI-discovered nodes"
                  : "Showing captured tabs only"
              }
            >
              AI results {showDiscovered ? "on" : "off"}
            </button>
            <button
              type="button"
              className={`stat-chip button-chip${enableDiscovery ? " is-on" : ""}${enableDiscovery ? "" : " is-muted"}`}
              onClick={() => setEnableDiscovery((current) => !current)}
              title={
                enableDiscovery
                  ? "Firecrawl discovery is enabled for the next synthesis run"
                  : "Firecrawl discovery is disabled for the next synthesis run"
              }
            >
              Find more with AI {enableDiscovery ? "on" : "off"}
            </button>
          </div>
        </div>

        {error ? <div className="error-banner">{error}</div> : null}
      </section>

      <div className={`workspace-shell${selectedNode ? " with-detail" : ""}`}>
        <AnimatePresence>
          {isLoading && (
            <LoadingOverlay message={query ? `Researching "${query}"` : null} />
          )}
        </AnimatePresence>
        <main className="stage-column">
          <section className="stage-shell">
            <div className="stage-header">
              <div>
                <p className="workspace-eyebrow">Workspace</p>
                <h2 className="stage-title">Context canvas</h2>
                <p className="stage-caption">
                  {view === "compare" && data?.matrix
                    ? data.matrix.rubric
                    : "Captured pages, AI summaries, and comparisons stay in one view."}
                </p>
              </div>

              <div className="stage-toolbar">
                <div className="view-tabs" role="tablist" aria-label="Workspace views">
                  <button
                    type="button"
                    className={view === "board" ? "active" : ""}
                    onClick={() => setView("board")}
                  >
                    <LayoutGrid size={16} strokeWidth={2.1} />
                    <span>Board</span>
                  </button>
                  <button
                    type="button"
                    className={view === "digest" ? "active" : ""}
                    onClick={() => setView("digest")}
                  >
                    <BookText size={16} strokeWidth={2.1} />
                    <span>Digest</span>
                  </button>
                  <button
                    type="button"
                    className={view === "compare" ? "active" : ""}
                    onClick={() => setView("compare")}
                  >
                    <Table2 size={16} strokeWidth={2.1} />
                    <span>Compare</span>
                  </button>
                </div>

                {view === "digest" ? (
                  <label className="sort-control">
                    <span>Sort</span>
                    <select
                      value={sortMode}
                      onChange={(event) => setSortMode(event.target.value)}
                    >
                      {SORT_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>
                ) : null}
              </div>
            </div>

            {view === "board" ? (
              <section className="view-shell">
                <div className="board-topline">
                  <span className="axis-pill">{boardNodes.length} nodes in context</span>
                  <span className="axis-pill">{visibleEdges.length} relationships</span>
                  <span className="axis-pill">{flaggedBoardCount} flagged</span>
                  <span className="axis-pill">{discoveredBoardCount} discovered</span>
                </div>

                {leadBoardNode ? (
                  <motion.div
                    className="board-hero-card"
                    initial={{ opacity: 0, y: 12 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: 0.04 }}
                  >
                    <div className="board-hero-copy">
                      <div className="section-kicker">Current front-runner</div>
                      <h3>{leadBoardNode.title}</h3>
                      <p>{leadBoardNode.summary}</p>
                    </div>
                    <div className="board-hero-meta">
                      <span className="signal-chip soft">
                        {leadBoardNode.source}
                      </span>
                      <span className="signal-chip soft">
                        {metaNumber(leadBoardNode, "aiRank")
                          ? `AI #${metaNumber(leadBoardNode, "aiRank")}`
                          : "Rank pending"}
                      </span>
                      <span className="signal-chip soft">
                        {metaNumber(leadBoardNode, "priceUsd")
                          ? `$${metaNumber(leadBoardNode, "priceUsd").toFixed(0)}`
                          : metaText(leadBoardNode, "priceDisplay")}
                      </span>
                    </div>
                  </motion.div>
                ) : null}

                <div className="board-frame">
                  <div
                    className="board-canvas"
                    style={{ width: boardWidth, height: boardHeight }}
                  >
                    <div className="board-zone board-zone-left" />
                    <div className="board-zone board-zone-center" />
                    <div className="board-zone board-zone-right" />
                    <div className="board-zone-label board-zone-label-left">
                      <span>Under review</span>
                      <strong>Lower fit / unresolved</strong>
                    </div>
                    <div className="board-zone-label board-zone-label-center">
                      <span>Viable cluster</span>
                      <strong>Strong contenders</strong>
                    </div>
                    <div className="board-zone-label board-zone-label-right">
                      <span>Priority zone</span>
                      <strong>Highest signal</strong>
                    </div>
                    <div className="board-legend">
                      <span>{boardAxes.y.label} rises upward</span>
                      <span>{boardAxes.x.label} strengthens to the right</span>
                    </div>

                    <svg className="board-lines" aria-hidden="true">
                      {visibleEdges.map((edge) => {
                        const from = positions[edge.from];
                        const to = positions[edge.to];
                        if (!from || !to) {
                          return null;
                        }

                        return (
                          <line
                            key={edge.id}
                            x1={from.x + NODE_WIDTH / 2}
                            y1={from.y + NODE_HEIGHT / 2}
                            x2={to.x + NODE_WIDTH / 2}
                            y2={to.y + NODE_HEIGHT / 2}
                            stroke={EDGE_CLR[edge.label] || EDGE_CLR.related_to}
                            strokeWidth={edge.label === "mentions" ? 1.6 : 1}
                            strokeDasharray={edge.label === "enriches" ? "5 5" : "none"}
                          />
                        );
                      })}
                    </svg>

                    {boardNodes.map((node) => {
                      const position = positions[node.id] || { x: 80, y: 80 };
                      return (
                        <div
                          key={node.id}
                          className="board-node-anchor"
                          style={{ left: position.x, top: position.y }}
                        >
                          <div
                            className={`board-node-halo${
                              selected === node.id ? " is-active" : ""
                            }`}
                            style={{
                              "--accent":
                                TYPE_CLR[normalizeType(node.type)] || TYPE_CLR.item,
                            }}
                          />
                          <BoardCard
                            node={node}
                            active={selected === node.id}
                            onSelect={setSelected}
                          />
                        </div>
                      );
                    })}
                  </div>
                </div>

                {reviewNodes.length ? (
                  <div className="review-section">
                    <div className="section-kicker">External reviews</div>
                    <div className="review-grid">
                      {reviewNodes.map((node) => (
                        <motion.button
                          key={node.id}
                          type="button"
                          className={`review-card${selected === node.id ? " is-active" : ""}`}
                          onClick={() =>
                            setSelected((prev) => (prev === node.id ? null : node.id))
                          }
                          initial={{ opacity: 0, y: 10 }}
                          animate={{ opacity: 1, y: 0 }}
                        >
                          <div className="review-topline">
                            <span
                              className="source-pill"
                              style={{ "--accent": TYPE_CLR.review }}
                            >
                              {node.source}
                            </span>
                            <span className="status-pill">Review</span>
                          </div>
                          <div className="review-title">{node.title}</div>
                          <div className="review-summary">{node.summary}</div>
                        </motion.button>
                      ))}
                    </div>
                  </div>
                ) : null}
              </section>
            ) : null}

            {view === "digest" ? (
              <section className="view-shell">
                <div className="digest-grid">
                  {digestEntries.map((entry, index) => (
                    <DigestCard
                      key={entry.node_id}
                      entry={entry}
                      node={nodeMap[entry.node_id]}
                      active={selected === entry.node_id}
                      onSelect={setSelected}
                      onSwap={swapDigestCards}
                      index={index}
                    />
                  ))}
                </div>
              </section>
            ) : null}

            {view === "compare" && data?.matrix ? (
              <section className="view-shell">
                <div className="compare-frame">
                  <div
                    className="compare-grid"
                    style={{
                      gridTemplateColumns: `190px repeat(${compareColumns.length}, minmax(240px, 1fr))`,
                    }}
                  >
                    <div className="compare-corner">Compare</div>

                    {compareColumns.map(({ row, node }) => (
                      <div
                        key={row.node_id}
                        className={`compare-column-head${
                          selected === row.node_id ? " is-active" : ""
                        }`}
                      >
                        <span className="compare-source">{node?.source || "Captured"}</span>
                        <strong>{node?.title || row.node_id}</strong>
                      </div>
                    ))}

                    {data.matrix.columns.map((column) => (
                      <Fragment key={column.key}>
                        <div className="compare-row-title">
                          <span>{column.label}</span>
                        </div>

                        {compareColumns.map(({ row, node }) => {
                          const cell = row.cells[column.key];
                          const best = cell?.rank === 1;
                          const worst = cell?.rank === visibleMatrixRows.length;

                          return (
                            <button
                              key={`${row.node_id}-${column.key}`}
                              type="button"
                              className={`compare-cell${selected === row.node_id ? " is-active" : ""}${
                                best ? " is-best" : ""
                              }${worst ? " is-worst" : ""}${
                                isViolated(node) ? " is-flagged" : ""
                              }`}
                              onClick={() =>
                                setSelected((prev) =>
                                  prev === row.node_id ? null : row.node_id,
                                )
                              }
                            >
                              {column.type === "sentiment" ? (
                                <Badge sentiment={cell?.sentiment}>
                                  {cell?.display || "Unknown"}
                                </Badge>
                              ) : (
                                <span>{cell?.display || "Unknown"}</span>
                              )}
                            </button>
                          );
                        })}
                      </Fragment>
                    ))}
                  </div>
                </div>
              </section>
            ) : null}
          </section>
        </main>

        <AnimatePresence>
          {selectedNode ? (
            <motion.aside
              key="detail"
              className="detail-shell"
              initial={{ x: 28, opacity: 0 }}
              animate={{ x: 0, opacity: 1 }}
              exit={{ x: 28, opacity: 0 }}
              transition={{ type: "spring", stiffness: 260, damping: 28 }}
            >
              <div className="detail-topline">
                <span
                  className="source-pill"
                  style={{
                    "--accent":
                      TYPE_CLR[normalizeType(selectedNode.type)] || TYPE_CLR.item,
                  }}
                >
                  {selectedNode.source} / {selectedNode.type}
                </span>
                <button
                  type="button"
                  className="icon-button"
                  onClick={() => setSelected(null)}
                >
                  <X size={16} strokeWidth={2.1} />
                </button>
              </div>

              <div className="detail-heading">
                <h3>{selectedNode.title}</h3>
                <p>{selectedNode.subtitle}</p>
              </div>

              {selectedNode.url ? (
                <section className="detail-block">
                  <div className="section-kicker">Open product</div>
                  <a
                    className="detail-link-button"
                    href={selectedNode.url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <span>Visit listing</span>
                    <ExternalLink size={15} strokeWidth={2.1} />
                  </a>
                </section>
              ) : null}

              <section className="detail-block">
                <div className="section-kicker">Summary</div>
                <p>{selectedNode.summary}</p>
              </section>

              {selectedNode.metadata?.constraintViolated ? (
                <section className="detail-block constraint-block">
                  <div className="section-kicker">Constraint</div>
                  <p>{selectedNode.metadata.constraintReason || "Constraint mismatch"}</p>
                </section>
              ) : null}

              <section className="detail-block">
                <div className="section-kicker">Key metrics</div>
                <div className="detail-metric-grid">
                  {selectedNode.metadata?.metrics?.map((m, i) => (
                    <div key={i} className="metric-pair">
                      <div className="metric-label">{m.label}</div>
                      <div className="metric-value">{m.value}</div>
                    </div>
                  ))}
                  <div className="metric-pair">
                    <div className="metric-label">AI rank</div>
                    <div className="metric-value">
                      {metaNumber(selectedNode, "aiRank")
                        ? `#${metaNumber(selectedNode, "aiRank").toFixed(0)}`
                        : "Unknown"}
                    </div>
                  </div>
                </div>
              </section>

              {selectedNode.tags?.length ? (
                <section className="detail-block">
                  <div className="section-kicker">Tags</div>
                  <div className="detail-tag-row">
                    {selectedNode.tags.map((tag) => (
                      <span key={tag} className="signal-chip soft">
                        {tag}
                      </span>
                    ))}
                  </div>
                </section>
              ) : null}

              {selectedMetadataEntries.length > 0 ? (
                <section className="detail-block">
                  <div className="section-kicker">Metadata</div>
                  <div className="metadata-list">
                    {selectedMetadataEntries.map(([key, value]) => (
                      <div key={key} className="metadata-row">
                        <span>{key.replace(/_/g, " ")}</span>
                        <strong>
                          {typeof value === "number"
                            ? value.toLocaleString()
                            : String(value)}
                        </strong>
                      </div>
                    ))}
                  </div>
                </section>
              ) : null}
            </motion.aside>
          ) : null}
        </AnimatePresence>
      </div>
    </div>
  );
}
