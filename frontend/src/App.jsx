import { useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8010";
const DEFAULT_QUERY = "Compare the laptop coolers";
const BOARD_WIDTH = 1120;
const BOARD_HEIGHT = 620;
const NODE_WIDTH = 248;
const NODE_HEIGHT = 156;
const VIEWS = ["board", "digest", "compare"];
const SORT_OPTIONS = [
  { value: "ai", label: "AI best choice" },
  { value: "price", label: "Price" },
  { value: "noise", label: "Noise" },
];

const TYPE_CLR = {
  listing: "#3b82f6",
  review: "#ef4444",
  location: "#22c55e",
  enrichment: "#a855f7",
  reference: "#f59e0b",
  item: "#14b8a6",
  laptop_cooler: "#14b8a6",
};
const SENT_CLR = { positive: "#22c55e", neutral: "#6b7280", negative: "#ef4444", unknown: "#eab308" };
const STAT_CLR = { ready: "#22c55e", flagged: "#ef4444", pending: "#6b7280" };
const EDGE_CLR = {
  mentions: "rgba(239,68,68,0.35)",
  competes_with: "rgba(59,130,246,0.2)",
  located_in: "rgba(255,255,255,0.08)",
  enriches: "rgba(168,85,247,0.2)",
  related_to: "rgba(255,255,255,0.14)",
};

const pill = {
  padding: "4px 10px",
  borderRadius: 999,
  fontSize: 12,
  background: "rgba(255,255,255,0.06)",
  color: "#d1d5db",
  border: "1px solid rgba(255,255,255,0.06)",
};
const card = {
  borderRadius: 14,
  border: "1px solid rgba(255,255,255,0.06)",
  background: "rgba(255,255,255,0.03)",
};
const label = {
  fontSize: 11,
  textTransform: "uppercase",
  letterSpacing: "0.06em",
  color: "#6b7280",
  marginBottom: 8,
};

function Badge({ sentiment, children }) {
  const bg = {
    positive: "rgba(34,197,94,0.15)",
    neutral: "rgba(107,114,128,0.15)",
    negative: "rgba(239,68,68,0.15)",
    unknown: "rgba(234,179,8,0.15)",
  };
  return (
    <span
      style={{
        ...pill,
        background: bg[sentiment] || bg.unknown,
        color: SENT_CLR[sentiment] || SENT_CLR.unknown,
        fontWeight: 500,
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
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : null;
}

function metaNumber(node, key) {
  const value = node?.metadata?.[key];
  return numberOrNull(typeof value === "string" ? Number(value) : value);
}

function metaText(node, key, fallback = "Unknown") {
  const value = node?.metadata?.[key];
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

function isViolated(node) {
  return Boolean(node?.metadata?.constraintViolated);
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

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function axisMetaForDomain(domain) {
  if (domain === "housing") {
    return {
      x: { key: "priceUsd", label: "Lower price to higher price" },
      y: { key: "squareFeet", label: "Smaller space to larger space", invert: true },
    };
  }
  return {
    x: { key: "priceUsd", label: "Lower price to higher price" },
    y: { key: "noiseLevelDb", label: "Quieter to louder" },
  };
}

function computeLayout(nodes, domain) {
  const positions = {};
  if (!nodes.length) {
    return positions;
  }

  const centerX = BOARD_WIDTH / 2 - NODE_WIDTH / 2;
  const centerY = BOARD_HEIGHT / 2 - NODE_HEIGHT / 2;
  const axisMeta = axisMetaForDomain(domain);
  const valuesFor = (key) => nodes.map((node) => metaNumber(node, key)).filter((value) => value !== null);
  const xValues = valuesFor(axisMeta.x.key);
  const yValues = valuesFor(axisMeta.y.key);
  const xMin = xValues.length ? Math.min(...xValues) : null;
  const xMax = xValues.length ? Math.max(...xValues) : null;
  const yMin = yValues.length ? Math.min(...yValues) : null;
  const yMax = yValues.length ? Math.max(...yValues) : null;
  const maxSpreadX = BOARD_WIDTH / 2 - NODE_WIDTH / 2 - 48;
  const maxSpreadY = BOARD_HEIGHT / 2 - NODE_HEIGHT / 2 - 48;

  nodes.forEach((node, index) => {
    const rank = metaNumber(node, "aiRank") ?? index + 1;
    const radiusFactor = clamp(0.18 + ((rank - 1) / Math.max(nodes.length, 1)) * 0.9, 0.18, 1);
    const xValue = metaNumber(node, axisMeta.x.key);
    const yValue = metaNumber(node, axisMeta.y.key);
    let xDeviation = 0;
    let yDeviation = 0;

    if (xValue !== null && xMin !== null && xMax !== null && xMax > xMin) {
      xDeviation = ((xValue - xMin) / (xMax - xMin)) * 2 - 1;
    } else {
      xDeviation = (index % 2 === 0 ? -1 : 1) * 0.14;
    }

    if (yValue !== null && yMin !== null && yMax !== null && yMax > yMin) {
      yDeviation = ((yValue - yMin) / (yMax - yMin)) * 2 - 1;
      if (axisMeta.y.invert) {
        yDeviation *= -1;
      }
    } else {
      yDeviation = Math.floor(index / 2) % 2 === 0 ? -0.14 : 0.14;
    }

    const jitterX = ((index % 3) - 1) * 18;
    const jitterY = ((index % 4) - 1.5) * 16;
    positions[node.id] = {
      x: clamp(centerX + xDeviation * maxSpreadX * radiusFactor + jitterX, 24, BOARD_WIDTH - NODE_WIDTH - 24),
      y: clamp(centerY + yDeviation * maxSpreadY * radiusFactor + jitterY, 24, BOARD_HEIGHT - NODE_HEIGHT - 24),
    };
  });

  return positions;
}

async function fetchSession(query, constraint) {
  const statsResponse = await fetch(`${API_BASE}/api/v1/extension/history/stats`);
  if (!statsResponse.ok) {
    throw new Error(`Failed to load extension history stats: ${statsResponse.status}`);
  }

  const stats = await statsResponse.json();
  if (Number(stats.count ?? 0) <= 0) {
    throw new Error("No synced extension snapshots found. Open the extension, capture a page, and click Graph again.");
  }

  const response = await fetch(`${API_BASE}/api/v1/session/synthesize-from-extension-history`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      user_prompt: query,
      user_constraint: constraint || null,
      firecrawl_query_budget: 4,
      max_tabs: 20,
    }),
  });

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

function BoardCard({ node, active, onSelect }) {
  const color = TYPE_CLR[normalizeType(node.type)] || TYPE_CLR.item;
  const price = metaNumber(node, "priceUsd");
  const noise = metaNumber(node, "noiseLevelDb");
  const cooling = metaText(node, "coolingPerformance");
  const violated = isViolated(node);

  return (
    <motion.button
      type="button"
      initial={{ opacity: 0, scale: 0.88 }}
      animate={{ opacity: violated ? 0.76 : 1, scale: 1 }}
      transition={{ type: "spring", stiffness: 240, damping: 24 }}
      onClick={() => onSelect((prev) => (prev === node.id ? null : node.id))}
      style={{
        width: NODE_WIDTH,
        minHeight: NODE_HEIGHT,
        padding: 14,
        borderRadius: 16,
        textAlign: "left",
        border: `1px solid ${violated ? "#ef4444" : active ? color : "rgba(255,255,255,0.08)"}`,
        background: "rgba(14,14,14,0.94)",
        backdropFilter: "blur(8px)",
        cursor: "pointer",
        boxShadow: violated ? "0 0 18px rgba(239,68,68,0.16)" : active ? `0 0 18px ${color}22` : "0 8px 26px rgba(0,0,0,0.28)",
        transition: "border-color 0.2s, box-shadow 0.2s",
        color: "#e5e7eb",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
        <span style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.06em", color }}>
          {node.source}
        </span>
        <span style={{ width: 6, height: 6, borderRadius: "50%", background: STAT_CLR[node.status] || "#6b7280" }} />
        <span style={{ marginLeft: "auto", fontSize: 10, color: violated ? "#fca5a5" : "#81838d" }}>
          {violated ? "flagged" : "fit"}
        </span>
      </div>
      <div style={{ fontWeight: 600, fontSize: 15, lineHeight: 1.3 }}>{node.title}</div>
      <div style={{ fontSize: 12, color: "#8f96a3", marginTop: 4, minHeight: 32 }}>{node.subtitle}</div>
      {violated && node.metadata?.constraintReason ? (
        <div style={{ marginTop: 8, fontSize: 11, color: "#fca5a5" }}>{node.metadata.constraintReason}</div>
      ) : null}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8, marginTop: 12 }}>
        <div>
          <div style={{ fontSize: 10, color: "#6b7280", textTransform: "uppercase" }}>Price</div>
          <div style={{ fontSize: 13 }}>{price ? `$${price.toFixed(0)}` : "Unknown"}</div>
        </div>
        <div>
          <div style={{ fontSize: 10, color: "#6b7280", textTransform: "uppercase" }}>Noise</div>
          <div style={{ fontSize: 13 }}>{noise ? `${noise.toFixed(0)} dB` : metaText(node, "noiseDisplay")}</div>
        </div>
        <div>
          <div style={{ fontSize: 10, color: "#6b7280", textTransform: "uppercase" }}>Cooling</div>
          <div style={{ fontSize: 13 }}>{cooling}</div>
        </div>
      </div>
    </motion.button>
  );
}

function DigestCard({ entry, node, active, onSelect, onSwap, index }) {
  const color = TYPE_CLR[normalizeType(node?.type)] || TYPE_CLR.item;
  const violated = isViolated(node);
  const price = metaNumber(node, "priceUsd");
  const noise = metaNumber(node, "noiseLevelDb");
  const cooling = metaText(node, "coolingPerformance");
  const aiRank = metaNumber(node, "aiRank");

  return (
    <motion.div
      layout
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
        ...card,
        padding: 14,
        cursor: "grab",
        borderColor: violated ? "#ef4444" : active ? color : undefined,
        opacity: entry.relevance < 0.4 ? 0.6 : 1,
        boxShadow: violated ? "0 0 0 1px rgba(239,68,68,0.15) inset" : "none",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 8 }}>
        <span style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", color }}>{node?.source || "Captured"}</span>
        <span style={{ fontSize: 10, color: violated ? "#fca5a5" : STAT_CLR[node?.status], textTransform: "capitalize" }}>
          {violated ? "flagged" : node?.status || "ready"}
        </span>
        <span style={{ marginLeft: "auto", fontSize: 10, color: "#6b7280" }}>{aiRank ? `AI #${aiRank}` : `${Math.round(entry.relevance * 100)}%`}</span>
      </div>
      <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 4 }}>{node?.title || entry.node_id}</div>
      <div style={{ fontSize: 12, color: "#9ca3af", lineHeight: 1.5, marginBottom: 10 }}>{entry.summary}</div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8, marginBottom: 10 }}>
        <div>
          <div style={{ fontSize: 10, color: "#6b7280", textTransform: "uppercase" }}>Price</div>
          <div style={{ fontSize: 13 }}>{price ? `$${price.toFixed(0)}` : "Unknown"}</div>
        </div>
        <div>
          <div style={{ fontSize: 10, color: "#6b7280", textTransform: "uppercase" }}>Noise</div>
          <div style={{ fontSize: 13 }}>{noise ? `${noise.toFixed(0)} dB` : metaText(node, "noiseDisplay")}</div>
        </div>
        <div>
          <div style={{ fontSize: 10, color: "#6b7280", textTransform: "uppercase" }}>Cooling</div>
          <div style={{ fontSize: 13 }}>{cooling}</div>
        </div>
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
        {violated && node?.metadata?.constraintReason ? (
          <span style={{ ...pill, fontSize: 11, background: "rgba(239,68,68,0.12)", border: "1px solid rgba(239,68,68,0.22)", color: "#fca5a5" }}>
            {node.metadata.constraintReason}
          </span>
        ) : null}
        {entry.signals.map((signal) => (
          <span key={`${entry.node_id}-${signal.label}`} style={{ ...pill, fontSize: 11 }}>
            {signal.label}
          </span>
        ))}
      </div>
    </motion.div>
  );
}

export default function App() {
  const [selected, setSelected] = useState(null);
  const [query, setQuery] = useState(DEFAULT_QUERY);
  const [constraint, setConstraint] = useState("");
  const [view, setView] = useState("all");
  const [sortMode, setSortMode] = useState("ai");
  const [showDiscovered, setShowDiscovered] = useState(true);
  const [session, setSession] = useState(null);
  const [positions, setPositions] = useState({});
  const [digestOrder, setDigestOrder] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");

  const data = session;
  const show = (value) => view === "all" || view === value;

  const nodeMap = useMemo(
    () => Object.fromEntries((data?.graph?.nodes || []).map((node) => [node.id, node])),
    [data],
  );
  const selectedNode = selected ? nodeMap[selected] : null;
  const boardAxes = useMemo(() => axisMetaForDomain(data?.domain), [data]);

  const visibleNodes = useMemo(() => {
    const nodes = data?.graph?.nodes || [];
    if (showDiscovered) {
      return nodes;
    }
    return nodes.filter((node) => node?.metadata?.sourceType !== "discovered");
  }, [data, showDiscovered]);

  const visibleNodeIds = useMemo(() => new Set(visibleNodes.map((node) => node.id)), [visibleNodes]);

  const visibleEdges = useMemo(
    () => (data?.graph?.edges || []).filter((edge) => visibleNodeIds.has(edge.from) && visibleNodeIds.has(edge.to)),
    [data, visibleNodeIds],
  );

  useEffect(() => {
    setPositions(computeLayout(visibleNodes, data?.domain));
  }, [visibleNodes, data]);

  async function runQuery(nextQuery, nextConstraint) {
    const resolvedQuery = nextQuery.trim() || DEFAULT_QUERY;
    const resolvedConstraint = nextConstraint.trim();
    setIsLoading(true);
    setError("");
    try {
      const appliedConstraint =
        session?.digest?.theme_signals
          ?.find((signal) => typeof signal === "string" && signal.startsWith("constraint:"))
          ?.replace(/^constraint:\s*/i, "")
          ?.trim() || "";
      const shouldReuseSession =
        session &&
        typeof session.query === "string" &&
        session.query.trim().toLowerCase() === resolvedQuery.toLowerCase() &&
        appliedConstraint !== resolvedConstraint;
      const payload = shouldReuseSession
        ? await applyConstraintToSession(session, resolvedConstraint)
        : await fetchSession(resolvedQuery, resolvedConstraint);
      setSession(payload);
      setQuery(payload.query || resolvedQuery);
      setSelected(null);
      setDigestOrder([]);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Failed to load session.");
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const prompt = params.get("prompt")?.trim() || DEFAULT_QUERY;
    const constraintParam = params.get("constraint")?.trim() || "";
    setQuery(prompt);
    setConstraint(constraintParam);
    runQuery(prompt, constraintParam);
  }, []);

  const baseDigestEntries = useMemo(() => {
    const entries = [...(data?.digest?.entries || [])].filter((entry) => visibleNodeIds.has(entry.node_id));
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
    () => (data?.matrix?.rows || []).filter((row) => visibleNodeIds.has(row.node_id)),
    [data, visibleNodeIds],
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
    const entryMap = new Map(baseDigestEntries.map((entry) => [entry.node_id, entry]));
    return digestOrder.map((id) => entryMap.get(id)).filter(Boolean);
  }, [baseDigestEntries, digestOrder]);

  function swapDigestCards(sourceId, targetId) {
    if (!sourceId || !targetId || sourceId === targetId) {
      return;
    }
    setDigestOrder((current) => {
      const base = current.length ? [...current] : baseDigestEntries.map((entry) => entry.node_id);
      const sourceIndex = base.indexOf(sourceId);
      const targetIndex = base.indexOf(targetId);
      if (sourceIndex === -1 || targetIndex === -1) {
        return base;
      }
      [base[sourceIndex], base[targetIndex]] = [base[targetIndex], base[sourceIndex]];
      return base;
    });
  }

  return (
    <div
      style={{
        minHeight: "100vh",
        background: "#0a0a0a",
        color: "#e5e7eb",
        fontFamily: "'Inter', system-ui, sans-serif",
        position: "relative",
        overflow: "hidden",
      }}
    >
      <div style={{ position: "absolute", top: "-20%", left: "-10%", width: "50%", height: "50%", background: "rgba(59,130,246,0.06)", filter: "blur(140px)", borderRadius: "50%", pointerEvents: "none" }} />
      <div style={{ position: "absolute", bottom: "-20%", right: "-10%", width: "50%", height: "50%", background: "rgba(168,85,247,0.06)", filter: "blur(140px)", borderRadius: "50%", pointerEvents: "none" }} />

      <header
        style={{
          position: "sticky",
          top: 0,
          zIndex: 50,
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "14px 24px",
          borderBottom: "1px solid rgba(255,255,255,0.06)",
          background: "rgba(0,0,0,0.6)",
          backdropFilter: "blur(12px)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{ width: 30, height: 30, borderRadius: 8, background: "linear-gradient(135deg,#3b82f6,#a855f7)", display: "grid", placeItems: "center", fontWeight: 700, fontSize: 13, color: "#fff" }}>
            S
          </div>
          <span style={{ fontWeight: 600, fontSize: 17, letterSpacing: "-0.02em" }}>Synapse</span>
          <span style={{ fontSize: 12, color: "#6b7280", marginLeft: 8 }}>{data?.domain ? `· ${data.domain}` : "· live session"}</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ width: 7, height: 7, borderRadius: "50%", background: isLoading ? "#eab308" : "#22c55e" }} />
          <span style={{ fontSize: 12, color: "#6b7280" }}>
            {isLoading
              ? "loading"
              : data?.digest?.stats
                ? `${data.digest.stats.ready} ready${data.digest.stats.pending ? ` · ${data.digest.stats.pending} flagged` : ""}`
                : "awaiting data"}
          </span>
        </div>
      </header>

      <div style={{ padding: "18px 24px", borderBottom: "1px solid rgba(255,255,255,0.04)" }}>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            runQuery(query, constraint);
          }}
          style={{ maxWidth: 1080, display: "grid", gridTemplateColumns: "minmax(260px,1fr) auto", gap: 10 }}
        >
          <input
            value={constraint}
            onChange={(event) => setConstraint(event.target.value)}
            placeholder="Constraint, e.g. only under $50"
            style={{
              width: "100%",
              padding: "12px 16px",
              borderRadius: 12,
              border: "1px solid rgba(255,255,255,0.08)",
              background: "rgba(255,255,255,0.04)",
              color: "#e5e7eb",
              fontSize: 14,
              fontFamily: "inherit",
              outline: "none",
            }}
          />
          <button
            type="submit"
            disabled={isLoading}
            style={{
              padding: "12px 18px",
              borderRadius: 12,
              border: "1px solid rgba(59,130,246,0.5)",
              background: isLoading ? "rgba(59,130,246,0.25)" : "linear-gradient(135deg,#3b82f6,#7c3aed)",
              color: "#fff",
              cursor: isLoading ? "default" : "pointer",
              fontWeight: 600,
            }}
          >
            {isLoading ? "Loading..." : "Run"}
          </button>
        </form>

        {(query || constraint) ? (
          <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
            {query ? (
              <span style={pill}>
                {query}
              </span>
            ) : null}
            <span style={{ ...pill, background: "rgba(59,130,246,0.12)", border: "1px solid rgba(59,130,246,0.22)" }}>
              Constraint: {constraint || "None"}
            </span>
          </div>
        ) : null}

        {error ? (
          <div style={{ marginTop: 10, maxWidth: 1080, padding: "10px 12px", borderRadius: 12, border: "1px solid rgba(239,68,68,0.25)", background: "rgba(239,68,68,0.08)", color: "#fca5a5", fontSize: 13 }}>
            {error}
          </div>
        ) : null}

        {data?.digest?.theme_signals?.length ? (
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
            {data.digest.theme_signals.map((signal) => (
              <span key={signal} style={pill}>
                {signal}
              </span>
            ))}
            <label style={{ ...pill, display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={showDiscovered}
                onChange={(event) => setShowDiscovered(event.target.checked)}
              />
              Show AI discovered
            </label>
            <div style={{ marginLeft: "auto", display: "flex", background: "rgba(255,255,255,0.04)", borderRadius: 10, padding: 3, border: "1px solid rgba(255,255,255,0.06)" }}>
              {VIEWS.map((value) => (
                <button
                  key={value}
                  onClick={() => setView(value)}
                  style={{
                    padding: "6px 16px",
                    borderRadius: 8,
                    border: "none",
                    cursor: "pointer",
                    fontSize: 12,
                    fontWeight: 500,
                    fontFamily: "inherit",
                    textTransform: "capitalize",
                    background: view === value ? "linear-gradient(135deg,#3b82f6,#7c3aed)" : "transparent",
                    color: view === value ? "#fff" : "#6b7280",
                  }}
                >
                  {value}
                </button>
              ))}
              <div style={{ width: 1, background: "rgba(255,255,255,0.1)", margin: "2px 4px" }} />
              <button
                onClick={() => setView("all")}
                style={{
                  padding: "6px 16px",
                  borderRadius: 8,
                  border: "none",
                  cursor: "pointer",
                  fontSize: 12,
                  fontWeight: 500,
                  fontFamily: "inherit",
                  background: view === "all" ? "linear-gradient(135deg,#3b82f6,#7c3aed)" : "transparent",
                  color: view === "all" ? "#fff" : "#6b7280",
                }}
              >
                All
              </button>
            </div>
          </div>
        ) : null}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: selectedNode ? "1fr 340px" : "1fr", minHeight: "calc(100vh - 152px)" }}>
        <div style={{ padding: 20, display: "flex", flexDirection: "column", gap: 20, overflow: "auto" }}>
          {show("board") && (
            <section>
              <div style={{ ...label, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span>Board</span>
                <span style={{ color: "#8d93a0", textTransform: "none", letterSpacing: 0 }}>
                  Best choices stay closest to center. Constraints are highlighted in red.
                </span>
              </div>
              <div style={{ ...card, padding: 12, overflow: "auto", minHeight: 520 }}>
                <div style={{ position: "relative", width: BOARD_WIDTH, height: BOARD_HEIGHT, margin: "0 auto" }}>
                  <div style={{ position: "absolute", left: 24, right: 24, top: BOARD_HEIGHT / 2, height: 1, background: "rgba(255,255,255,0.08)" }} />
                  <div style={{ position: "absolute", top: 24, bottom: 24, left: BOARD_WIDTH / 2, width: 1, background: "rgba(255,255,255,0.08)" }} />
                  <div style={{ position: "absolute", left: BOARD_WIDTH / 2 - 34, top: BOARD_HEIGHT / 2 - 34, width: 68, height: 68, borderRadius: "50%", border: "1px dashed rgba(34,197,94,0.3)", background: "rgba(34,197,94,0.04)" }} />
                  <div style={{ position: "absolute", left: 28, bottom: 20, fontSize: 11, color: "#7b8190" }}>
                    {boardAxes.x.label}
                  </div>
                  <div style={{ position: "absolute", right: 28, top: 20, fontSize: 11, color: "#7b8190", writingMode: "vertical-rl", textOrientation: "mixed" }}>
                    {boardAxes.y.label}
                  </div>
                  <svg style={{ position: "absolute", inset: 0, width: "100%", height: "100%", pointerEvents: "none" }}>
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
                          strokeWidth={edge.label === "mentions" ? 1.5 : 1}
                          strokeDasharray={edge.label === "enriches" ? "4 4" : "none"}
                        />
                      );
                    })}
                  </svg>
                  {visibleNodes.map((node) => {
                    const position = positions[node.id] || { x: 80, y: 80 };
                    return (
                      <div key={node.id} style={{ position: "absolute", left: position.x, top: position.y }}>
                        <BoardCard node={node} active={selected === node.id} onSelect={setSelected} />
                      </div>
                    );
                  })}
                </div>
              </div>
            </section>
          )}

          {show("digest") && (
            <section>
              <div style={{ ...label, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span>Digest</span>
                <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, textTransform: "none", letterSpacing: 0, color: "#9ca3af" }}>
                  Sort by
                  <select
                    value={sortMode}
                    onChange={(event) => setSortMode(event.target.value)}
                    style={{
                      padding: "7px 10px",
                      borderRadius: 10,
                      border: "1px solid rgba(255,255,255,0.08)",
                      background: "rgba(255,255,255,0.04)",
                      color: "#e5e7eb",
                    }}
                  >
                    {SORT_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))", gap: 10 }}>
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
          )}

          {show("compare") && data?.matrix && (
            <section>
              <div style={label}>Compare - {data.matrix.rubric}</div>
              <div style={{ ...card, padding: 12, overflowX: "auto" }}>
                <div style={{ display: "grid", gridTemplateColumns: `190px repeat(${data.matrix.columns.length}, 1fr)`, gap: 6, minWidth: 760 }}>
                  <div />
                  {data.matrix.columns.map((column) => (
                    <div key={column.key} style={{ padding: "8px 10px", fontSize: 11, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em", color: "#9ca3af" }}>
                      {column.label}
                    </div>
                  ))}
                  {visibleMatrixRows.map((row, rowIndex) => {
                    const node = nodeMap[row.node_id];
                    const active = selected === row.node_id;
                    const violated = isViolated(node);
                    return [
                      <motion.div
                        key={`label-${row.node_id}`}
                        initial={{ opacity: 0, x: -8 }}
                        animate={{ opacity: 1, x: 0 }}
                        transition={{ delay: rowIndex * 0.06 }}
                        onClick={() => setSelected((prev) => (prev === row.node_id ? null : row.node_id))}
                        style={{
                          padding: "10px 12px",
                          borderRadius: 8,
                          border: `1px solid ${violated ? "#ef4444" : active ? "#3b82f6" : "rgba(255,255,255,0.04)"}`,
                          background: "rgba(255,255,255,0.02)",
                          cursor: "pointer",
                        }}
                      >
                        <div style={{ fontWeight: 600, fontSize: 13 }}>{node?.title || row.node_id}</div>
                        <div style={{ fontSize: 10, color: violated ? "#fca5a5" : "#6b7280" }}>
                          {violated ? node?.metadata?.constraintReason || "Constraint mismatch" : node?.source || "Captured"}
                        </div>
                      </motion.div>,
                      ...data.matrix.columns.map((column) => {
                        const cell = row.cells[column.key];
                        const best = cell?.rank === 1;
                        const worst = cell?.rank === visibleMatrixRows.length;
                        return (
                          <motion.div
                            key={`${row.node_id}-${column.key}`}
                            initial={{ opacity: 0, y: 6 }}
                            animate={{ opacity: 1, y: 0 }}
                            transition={{ delay: rowIndex * 0.06 + 0.03 }}
                            style={{
                              padding: "10px 12px",
                              borderRadius: 8,
                              display: "flex",
                              alignItems: "center",
                              background: violated
                                ? "rgba(239,68,68,0.06)"
                                : best
                                  ? "rgba(34,197,94,0.07)"
                                  : worst
                                    ? "rgba(239,68,68,0.04)"
                                    : "rgba(255,255,255,0.02)",
                              border: `1px solid ${violated ? "rgba(239,68,68,0.2)" : best ? "rgba(34,197,94,0.15)" : "rgba(255,255,255,0.04)"}`,
                            }}
                          >
                            {column.type === "sentiment" ? (
                              <Badge sentiment={cell?.sentiment}>{cell?.display || "Unknown"}</Badge>
                            ) : (
                              <span style={{ fontSize: 13, color: violated ? "#fca5a5" : best ? "#22c55e" : "#d1d5db" }}>{cell?.display || "Unknown"}</span>
                            )}
                          </motion.div>
                        );
                      }),
                    ];
                  })}
                </div>
              </div>
            </section>
          )}
        </div>

        <AnimatePresence>
          {selectedNode ? (
            <motion.aside
              key="detail"
              initial={{ x: 30, opacity: 0 }}
              animate={{ x: 0, opacity: 1 }}
              exit={{ x: 30, opacity: 0 }}
              transition={{ type: "spring", stiffness: 300, damping: 28 }}
              style={{ borderLeft: "1px solid rgba(255,255,255,0.06)", background: "rgba(10,10,10,0.8)", backdropFilter: "blur(12px)", padding: 20, overflowY: "auto" }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "start", marginBottom: 16 }}>
                <div>
                  <div style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.08em", color: TYPE_CLR[normalizeType(selectedNode.type)] || "#6b7280" }}>
                    {selectedNode.source} · {selectedNode.type}
                  </div>
                  <h3 style={{ fontSize: 17, fontWeight: 600, margin: "4px 0 0" }}>{selectedNode.title}</h3>
                  <div style={{ fontSize: 12, color: "#6b7280" }}>{selectedNode.subtitle}</div>
                </div>
                <button onClick={() => setSelected(null)} style={{ background: "rgba(255,255,255,0.06)", border: "none", borderRadius: 6, width: 24, height: 24, cursor: "pointer", color: "#9ca3af", fontSize: 14 }}>
                  ×
                </button>
              </div>

              <div style={{ ...card, padding: 12, marginBottom: 12 }}>
                <div style={{ ...label, marginBottom: 4 }}>Summary</div>
                <p style={{ fontSize: 13, lineHeight: 1.6, color: "#d1d5db", margin: 0 }}>{selectedNode.summary}</p>
              </div>

              {selectedNode.metadata?.constraintViolated ? (
                <div style={{ ...card, padding: 12, marginBottom: 12, borderColor: "rgba(239,68,68,0.25)", background: "rgba(239,68,68,0.08)" }}>
                  <div style={{ ...label, marginBottom: 4, color: "#fca5a5" }}>Constraint</div>
                  <div style={{ color: "#fecaca", fontSize: 13 }}>{selectedNode.metadata.constraintReason || "Constraint mismatch"}</div>
                </div>
              ) : null}

              <div style={{ ...card, padding: 12, marginBottom: 12 }}>
                <div style={{ ...label, marginBottom: 6 }}>Key metrics</div>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0,1fr))", gap: 10 }}>
                  <div>
                    <div style={{ fontSize: 10, color: "#6b7280", textTransform: "uppercase" }}>Price</div>
                    <div>{metaNumber(selectedNode, "priceUsd") ? `$${metaNumber(selectedNode, "priceUsd").toFixed(0)}` : "Unknown"}</div>
                  </div>
                  <div>
                    <div style={{ fontSize: 10, color: "#6b7280", textTransform: "uppercase" }}>Noise</div>
                    <div>{metaNumber(selectedNode, "noiseLevelDb") ? `${metaNumber(selectedNode, "noiseLevelDb").toFixed(0)} dB` : metaText(selectedNode, "noiseDisplay")}</div>
                  </div>
                  <div>
                    <div style={{ fontSize: 10, color: "#6b7280", textTransform: "uppercase" }}>Cooling</div>
                    <div>{metaText(selectedNode, "coolingPerformance")}</div>
                  </div>
                  <div>
                    <div style={{ fontSize: 10, color: "#6b7280", textTransform: "uppercase" }}>AI rank</div>
                    <div>{metaNumber(selectedNode, "aiRank") ? `#${metaNumber(selectedNode, "aiRank").toFixed(0)}` : "Unknown"}</div>
                  </div>
                  <div>
                    <div style={{ fontSize: 10, color: "#6b7280", textTransform: "uppercase" }}>Reviews</div>
                    <div>{metaText(selectedNode, "reviewSentimentLabel")}</div>
                  </div>
                </div>
              </div>

              <div style={{ ...card, padding: 12, marginBottom: 12 }}>
                <div style={{ ...label, marginBottom: 6 }}>Tags</div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
                  {selectedNode.tags?.map((tag) => (
                    <span key={tag} style={pill}>
                      {tag}
                    </span>
                  ))}
                </div>
              </div>

              {selectedNode.metadata && Object.keys(selectedNode.metadata).length > 0 ? (
                <div style={{ ...card, padding: 12 }}>
                  <div style={{ ...label, marginBottom: 4 }}>Metadata</div>
                  {Object.entries(selectedNode.metadata).map(([key, value]) => (
                    <div key={key} style={{ display: "flex", justifyContent: "space-between", padding: "6px 0", borderBottom: "1px solid rgba(255,255,255,0.04)", fontSize: 12, gap: 12 }}>
                      <span style={{ color: "#6b7280" }}>{key.replace(/_/g, " ")}</span>
                      <span style={{ color: "#e5e7eb", fontWeight: 500, textAlign: "right" }}>{typeof value === "number" ? value.toLocaleString() : String(value)}</span>
                    </div>
                  ))}
                </div>
              ) : null}
            </motion.aside>
          ) : null}
        </AnimatePresence>
      </div>
    </div>
  );
}
