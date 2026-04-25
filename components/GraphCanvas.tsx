"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowUpDown, ChevronDown, Sparkles } from "lucide-react";
import ReactFlow, {
  Background,
  ReactFlowProvider,
  applyNodeChanges,
  type Node,
  type NodeChange,
} from "reactflow";
import "reactflow/dist/style.css";

import { ResearchCard } from "@/components/nodes/ResearchCard";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import {
  CARD_GAP,
  CARD_HEIGHT,
  CARD_WIDTH,
  type ResearchEdge,
  type ResearchMetric,
  type ResearchNode,
  type SortMode,
  type WorkspaceView,
  getBoardColumns,
  getBoardHeight,
  getBoardWidth,
  useGraphStore,
} from "@/store/useGraphStore";

type GraphCanvasProps = {
  initialNodes: ResearchNode[];
  initialEdges: ResearchEdge[];
};

type CompareRow = {
  label: string;
  values: string[];
};

const EXCLUDED_COMPARE_KEYS = new Set([
  "title",
  "url",
  "sourceurl",
  "sourcetype",
  "airank",
  "aireason",
  "constraintviolated",
  "constraintreason",
  "combinedscore",
  "thumbnail",
  "rawdata",
  "metrics",
  "chips",
  "summary",
  "statuslabel",
  "kindlabel",
  "sourcelabel",
]);

const NODE_TYPES = {
  research: ResearchCard,
};

export function GraphCanvas({ initialNodes, initialEdges }: GraphCanvasProps) {
  return (
    <ReactFlowProvider>
      <GraphCanvasInner initialNodes={initialNodes} initialEdges={initialEdges} />
    </ReactFlowProvider>
  );
}

function normalizeKey(key: string) {
  return key.toLowerCase().replace(/[\s_()/:-]+/g, "");
}

function hostnameLabel(url: string) {
  try {
    return new URL(url).hostname.replace(/^www\./, "").toUpperCase();
  } catch {
    return "Captured";
  }
}

function titleCaseLabel(value: string) {
  return value
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/[_-]+/g, " ")
    .trim()
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function parseNumeric(value: unknown, fallback = 0) {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }

  if (typeof value === "string") {
    const match = value.replace(/,/g, "").match(/-?\d+(\.\d+)?/);
    if (match) {
      return Number(match[0]);
    }
  }

  return fallback;
}

function stringifyValue(value: unknown, fallback = "") {
  if (typeof value === "string") {
    return value.trim() || fallback;
  }
  if (typeof value === "number") {
    return `${value}`;
  }
  if (typeof value === "boolean") {
    return value ? "Yes" : "No";
  }
  return fallback;
}

function asRecord(value: unknown): Record<string, unknown> {
  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }

  return {};
}

function normalizeMetricArray(value: unknown): ResearchMetric[] {
  if (!Array.isArray(value)) {
    return [];
  }

  return value
    .map((item) => {
      const metric = asRecord(item);
      const label = stringifyValue(metric.label);
      const metricValue = stringifyValue(metric.value);
      if (!label || !metricValue) {
        return null;
      }

      return { label, value: metricValue };
    })
    .filter((metric): metric is ResearchMetric => Boolean(metric));
}

function normalizeChipArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }

  return value.map((item) => stringifyValue(item)).filter(Boolean);
}

function firstInterestingString(data: Record<string, unknown>) {
  const skip = new Set([
    "source type",
    "constraint reason",
    "ai reason",
    "summary",
    "description",
    "status",
  ]);

  for (const [key, value] of Object.entries(data)) {
    const text = stringifyValue(value);
    if (!text || skip.has(key.toLowerCase())) {
      continue;
    }
    if (text.length >= 3 && text.length <= 120 && /[a-z]/i.test(text)) {
      return text;
    }
  }

  return "";
}

function getValue(data: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    if (data[key] !== undefined && data[key] !== null && data[key] !== "") {
      return data[key];
    }
  }

  const normalizedEntries = Object.entries(data).map(([key, value]) => [normalizeKey(key), value] as const);
  for (const key of keys) {
    const match = normalizedEntries.find(([entryKey]) => entryKey === normalizeKey(key));
    if (match && match[1] !== undefined && match[1] !== null && match[1] !== "") {
      return match[1];
    }
  }

  return undefined;
}

function buildSummary(rawData: Record<string, unknown>, aiReason: string, title: string) {
  const summary = stringifyValue(getValue(rawData, ["summary", "oneSentenceSummary", "description"]));
  if (summary) {
    return summary;
  }
  if (aiReason) {
    return aiReason;
  }
  return `${title} is part of the current workspace graph.`;
}

function buildMetrics(rawData: Record<string, unknown>, priceUsd: number, distanceMiles: number, bedrooms: number, bathrooms: number, squareFeet: number, rentalType: string): ResearchMetric[] {
  const orderedKeys = [
    "Price USD",
    "Price",
    "External Review Consensus",
    "Common Complaints",
    "Review Summary",
    "Reddit Consensus",
    "Owner Feedback",
    "Fan Speed (RPM)",
    "Noise Level (dB)",
    "Number of Fans",
    "Fan Type/Count",
    "Cooling Method",
    "Maximum Laptop Size Supported",
    "Max Laptop Size Supported",
    "USB Pass-through Ports",
    "Additional USB Ports",
    "Adjustable Height Levels",
    "Cooling Performance Rating",
    "Distance to Anchor",
    "Neighborhood",
    "Rental Type",
    "Bedrooms",
    "Bathrooms",
    "Square Feet",
  ];

  const metrics: ResearchMetric[] = [];
  for (const key of orderedKeys) {
    const value = getValue(rawData, [key]);
    const text = stringifyValue(value);
    if (!text) {
      continue;
    }
    metrics.push({ label: titleCaseLabel(key), value: text });
    if (metrics.length >= 6) {
      return metrics;
    }
  }

  if (metrics.length === 0) {
    const genericMetrics = Object.entries(rawData)
      .filter(([key, value]) => !EXCLUDED_COMPARE_KEYS.has(normalizeKey(key)) && stringifyValue(value))
      .slice(0, 6)
      .map(([key, value]) => ({
        label: titleCaseLabel(key),
        value: stringifyValue(value),
      }));

    if (genericMetrics.length > 0) {
      return genericMetrics;
    }

    metrics.push(
      { label: "Price", value: priceUsd ? `$${priceUsd}` : "$0" },
      { label: "Signal", value: distanceMiles ? `${distanceMiles}` : "0" },
      { label: "Type", value: rentalType || "Unknown" },
      { label: "Shape", value: `${bedrooms}/${bathrooms}/${squareFeet}` },
    );
  }

  return metrics.slice(0, 6);
}

function buildChips(rawData: Record<string, unknown>, metrics: ResearchMetric[], sourceType: "seed" | "discovered"): string[] {
  const preferredKeys = [
    "Cooling Method",
    "Build Material",
    "Power Source",
    "Neighborhood",
    "Lease Term",
    "RGB Lighting",
    "Dust Protection",
    "External Review Consensus",
    "Common Complaints",
    "Reddit Consensus",
    "Commute Feel",
    "Trust Signal",
  ];
  const chips: string[] = [];
  for (const key of preferredKeys) {
    const value = stringifyValue(getValue(rawData, [key]));
    if (value) {
      chips.push(`${titleCaseLabel(key)} ${value}`);
    }
    if (chips.length >= 4) {
      return chips;
    }
  }

  for (const metric of metrics) {
    chips.push(`${metric.label} ${metric.value}`);
    if (chips.length >= 4) {
      return chips;
    }
  }

  chips.push(sourceType === "discovered" ? "AI found" : "Captured");
  return chips.slice(0, 4);
}

function compareValueForLabel(node: ResearchNode, label: string) {
  const rawData = node.data.rawData ?? {};
  const direct = Object.entries(rawData).find(([key, value]) => titleCaseLabel(key) === label && stringifyValue(value));
  if (direct) {
    return stringifyValue(direct[1], "Unknown");
  }

  const metric = node.data.metrics?.find((item) => item.label === label);
  if (metric) {
    return metric.value;
  }

  const normalized = normalizeKey(label);
  if (normalized === "title" || normalized === "productname") {
    return node.data.title;
  }
  if (normalized === "price" || normalized === "priceusd") {
    return node.data.priceUsd ? `$${node.data.priceUsd}` : "Unknown";
  }
  if (normalized === "type" || normalized === "coolingmethod" || normalized === "rentaltype") {
    return node.data.rentalType || node.data.kindLabel || "Unknown";
  }
  if (normalized === "signal" || normalized === "noiseleveldb" || normalized === "distancetoanchor") {
    return node.data.distanceMiles ? `${node.data.distanceMiles}` : "Unknown";
  }
  return "Unknown";
}

function debugNodePreview(node: ResearchNode) {
  return {
    id: node.id,
    title: node.data.title,
    sourceLabel: node.data.sourceLabel,
    sourceType: node.data.sourceType,
    url: node.data.url,
    priceUsd: node.data.priceUsd,
    rentalType: node.data.rentalType,
    kindLabel: node.data.kindLabel,
    metrics: (node.data.metrics || []).slice(0, 4),
    chips: (node.data.chips || []).slice(0, 4),
    rawKeys: Object.keys(node.data.rawData || {}),
  };
}

function GraphCanvasInner({ initialNodes, initialEdges }: GraphCanvasProps) {
  const nodes = useGraphStore((state) => state.nodes);
  const edges = useGraphStore((state) => state.edges);
  const sortMode = useGraphStore((state) => state.sortMode);
  const setGraph = useGraphStore((state) => state.setGraph);
  const setNodes = useGraphStore((state) => state.setNodes);
  const sortNodes = useGraphStore((state) => state.sortNodes);
  const showDiscovered = useGraphStore((state) => state.showDiscovered);
  const toggleShowDiscovered = useGraphStore((state) => state.toggleShowDiscovered);
  const swapNodeToGridPosition = useGraphStore((state) => state.swapNodeToGridPosition);
  const selectNode = useGraphStore((state) => state.selectNode);

  const [viewportWidth, setViewportWidth] = useState(1440);
  const [constraintInput, setConstraintInput] = useState("");
  const [userPromptInput, setUserPromptInput] = useState("");
  const [isApplyingConstraint, setIsApplyingConstraint] = useState(false);
  const [constraintError, setConstraintError] = useState<string | null>(null);
  const [domTabCount, setDomTabCount] = useState(0);
  const [viewMode, setViewMode] = useState<WorkspaceView>("board");
  const [renderStatus, setRenderStatus] = useState<string>("Idle");
  const requestSequenceRef = useRef(0);
  const didInitialLoadRef = useRef(false);

  useEffect(() => {
    setGraph(initialNodes, initialEdges);
  }, [initialEdges, initialNodes, setGraph]);

  useEffect(() => {
    const syncViewport = () => {
      setViewportWidth(window.innerWidth);
      useGraphStore.getState().refreshVisibleGraph();
    };

    syncViewport();
    window.addEventListener("resize", syncViewport);
    return () => window.removeEventListener("resize", syncViewport);
  }, []);

  const onNodesChange = useCallback(
    (changes: NodeChange[]) => {
      setNodes((currentNodes) => applyNodeChanges(changes, currentNodes) as ResearchNode[]);
    },
    [setNodes],
  );

  const onNodeDragStop = useCallback(
    (_event: unknown, node: Node) => {
      swapNodeToGridPosition(node.id, node.position);
    },
    [swapNodeToGridPosition],
  );

  const applySort = useCallback(
    (nextSortMode: SortMode) => {
      sortNodes(nextSortMode);
    },
    [sortNodes],
  );

  const normalizeGraphNodes = useCallback((rawNodes: any[]): ResearchNode[] => {
    return (rawNodes || []).map((node: any, index: number) => {
      const backendData = asRecord(node?.data);
      const rawDataCandidate = asRecord(backendData.rawData);
      const rawData = Object.keys(rawDataCandidate).length > 0 ? rawDataCandidate : backendData;
      const brand = stringifyValue(getValue(rawData, ["brand", "Brand"]));
      const model = stringifyValue(getValue(rawData, ["model", "Model"]));
      const title =
        stringifyValue(backendData.title) ||
        stringifyValue(getValue(rawData, ["title", "Title", "name", "Name", "Product Name", "productName"])) ||
        [brand, model].filter(Boolean).join(" ") ||
        firstInterestingString(rawData) ||
        "Untitled";
      const url = stringifyValue(
        backendData.url ?? getValue(rawData, ["url", "URL", "Source URL", "sourceUrl", "source_url"]),
        "",
      );
      const sourceTypeRaw = stringifyValue(
        backendData.sourceType ?? getValue(rawData, ["sourceType", "Source Type", "source_type"]),
        "seed",
      ).toLowerCase();
      const sourceType = sourceTypeRaw === "discovered" ? "discovered" : "seed";
      const sourceLabel =
        stringifyValue(backendData.sourceLabel) ||
        (url ? hostnameLabel(url) : stringifyValue(getValue(rawData, ["source", "Source", "Brand"]), sourceType === "discovered" ? "AI found" : "Captured"));
      const locationLabel =
        stringifyValue(backendData.locationLabel) ||
        stringifyValue(
          getValue(rawData, ["locationLabel", "Neighborhood", "Location", "Brand", "Platform"]),
        ) || "Unknown";
      const kindLabel =
        stringifyValue(backendData.kindLabel) ||
        stringifyValue(
          getValue(rawData, ["kind", "Type", "Rental Type", "Cooling Method", "Product Type"]),
        ) || "Item";
      const priceUsd = parseNumeric(backendData.priceUsd ?? getValue(rawData, ["priceUsd", "Price USD", "Price"]));
      const distanceMiles = parseNumeric(
        backendData.distanceMiles ?? getValue(rawData, ["distanceMiles", "Distance to Anchor", "Noise Level", "Noise Level (dB)"]),
      );
      const bedrooms = parseNumeric(
        backendData.bedrooms ?? getValue(rawData, ["bedrooms", "Bedrooms", "Number of Fans", "Fan Count", "Fan Type/Count"]),
      );
      const bathrooms = parseNumeric(
        backendData.bathrooms ?? getValue(rawData, ["bathrooms", "Bathrooms", "Adjustable Height Levels", "Modes"]),
      );
      const squareFeet = parseNumeric(
        backendData.squareFeet ?? getValue(rawData, ["squareFeet", "Square Feet", "Fan Speed (RPM)", "Airflow", "Cooling Performance Rating"]),
      );
      const rentalType = stringifyValue(
        backendData.rentalType ?? getValue(rawData, ["rentalType", "Rental Type", "Cooling Method", "Maximum Laptop Size Supported"]),
        "Unknown",
      );
      const aiRank = parseNumeric(backendData.aiRank ?? getValue(rawData, ["aiRank", "AI Rank", "rank"]), index + 1);
      const combinedScore = parseNumeric(
        backendData.combinedScore ?? getValue(rawData, ["combinedScore", "Combined Score", "score"]),
        Math.max(0, 100 - aiRank * 10),
      );
      const aiReason = stringifyValue(backendData.aiReason ?? getValue(rawData, ["aiReason", "AI Reason", "reason"]), "");
      const backendMetrics = normalizeMetricArray(backendData.metrics);
      const resolvedMetrics = backendMetrics.length
        ? backendMetrics
        : buildMetrics(rawData, priceUsd, distanceMiles, bedrooms, bathrooms, squareFeet, rentalType);
      const chips = normalizeChipArray(backendData.chips);
      const resolvedChips = chips.length ? chips : buildChips(rawData, resolvedMetrics, sourceType);
      const summary = stringifyValue(backendData.summary) || buildSummary(rawData, aiReason, title);
      const statusLabel =
        stringifyValue(backendData.statusLabel) ||
        stringifyValue(getValue(rawData, ["status", "Status"])) ||
        (sourceType === "discovered" ? "enriched" : "captured");

      return {
        ...(node || {}),
        id: String(node.id ?? `${sourceType}-${index}`),
        type: "research",
        position: {
          x: Number(node.position?.x ?? index * (CARD_WIDTH + CARD_GAP)),
          y: Number(node.position?.y ?? 0),
        },
        data: {
          title,
          url,
          sourceLabel,
          statusLabel,
          kindLabel,
          summary,
          locationLabel,
          priceUsd,
          distanceMiles,
          bedrooms,
          bathrooms,
          squareFeet,
          rentalType,
          combinedScore,
          aiRank,
          aiReason,
          sourceType,
          constraintViolated: Boolean(backendData.constraintViolated ?? getValue(rawData, ["constraintViolated", "Constraint Violated"])),
          constraintReason: stringifyValue(backendData.constraintReason ?? getValue(rawData, ["constraintReason", "Constraint Reason"]), ""),
          metrics: resolvedMetrics,
          chips: resolvedChips,
          rawData,
        },
      };
    });
  }, []);

  const getApiBases = useCallback(() => {
    const configured = process.env.NEXT_PUBLIC_API_BASE_URL;
    const candidates = [
      configured,
      "http://127.0.0.1:8010",
      "http://localhost:8010",
      "http://127.0.0.1:8000",
      "http://localhost:8000",
    ].filter((value): value is string => Boolean(value));
    return [...new Set(candidates)];
  }, []);

  const loadFromExtension = useCallback(
    async (constraint: string | null, userPrompt: string) => {
      const apiBases = getApiBases();
      let lastError = "Unknown network failure";

      for (const apiBase of apiBases) {
        let confirmedBackend = false;
        try {
          const statsResponse = await fetch(`${apiBase}/api/v1/extension/history/stats`);
          if (!statsResponse.ok) {
            throw new Error(`History stats failed: ${statsResponse.status}`);
          }
          confirmedBackend = true;

          const statsPayload = await statsResponse.json();
          console.info("[Synapse] extension history stats", {
            apiBase,
            count: statsPayload.count,
            userPrompt: statsPayload.user_prompt,
            recent: statsPayload.recent,
          });
          const count = Number(statsPayload.count ?? 0);
          setDomTabCount(count);
          const statsPrompt =
            typeof statsPayload.user_prompt === "string" ? statsPayload.user_prompt.trim() : "";
          if (statsPrompt) {
            setUserPromptInput((prev) => (prev.trim() ? prev : statsPrompt));
          }

          if (count === 0) {
            throw new Error(
              "No DOM tabs available on the active backend. If you just restarted it, click Graph in the extension to resync your captured tabs.",
            );
          }

          const resolvedUserPrompt = userPrompt.trim() || statsPrompt || "Graph the pages I have been looking at.";
          const response = await fetch(`${apiBase}/api/v1/synthesize-from-extension-history`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              user_prompt: resolvedUserPrompt,
              user_constraint: constraint,
              firecrawl_query_budget: 4,
              max_tabs: 20,
            }),
          });

          if (!response.ok) {
            const message = await response.text();
            throw new Error(message || `Failed with status ${response.status}`);
          }

          const payload = await response.json();
          console.info("[Synapse] raw synth response", {
            apiBase,
            nodeCount: Array.isArray(payload.nodes) ? payload.nodes.length : 0,
            edgeCount: Array.isArray(payload.edges) ? payload.edges.length : 0,
            firstNode: payload.nodes?.[0] ?? null,
          });
          const nextNodes = normalizeGraphNodes(payload.nodes || []);
          const nextEdges: ResearchEdge[] = (payload.edges || []).map((edge: any) => ({
            id: String(edge.id),
            source: String(edge.source),
            target: String(edge.target),
          }));
          console.info("[Synapse] normalized graph nodes", nextNodes.map(debugNodePreview));
          const placeholderNodes = nextNodes.filter((node) => {
            const titleLooksPlaceholder = /^Item \d+$/i.test(node.data.title) || /^Untitled$/i.test(node.data.title);
            const emptyMetrics =
              (node.data.metrics || []).every((metric) => !metric.value || metric.value === "0" || metric.value === "$0" || metric.value === "Unknown");
            return titleLooksPlaceholder || (node.data.priceUsd === 0 && node.data.rentalType === "Unknown" && emptyMetrics);
          });
          if (placeholderNodes.length > 0) {
            console.warn("[Synapse] placeholder-like normalized nodes detected", placeholderNodes.map(debugNodePreview));
          }

          setRenderStatus(`Rendered ${nextNodes.length} nodes`);
          setGraph(nextNodes, nextEdges);
          return;
        } catch (error) {
          lastError = error instanceof Error ? error.message : "Unknown request error";
          if (confirmedBackend) {
            break;
          }
        }
      }

      throw new Error(`Backend unreachable. Start FastAPI on 127.0.0.1:8010. Last error: ${lastError}`);
    },
    [getApiBases, normalizeGraphNodes, setGraph],
  );

  const runSynthesis = useCallback(async () => {
    const trimmed = constraintInput.trim();
    const prompt = userPromptInput.trim() || "Graph the pages I have been looking at.";

    const requestId = ++requestSequenceRef.current;
    setIsApplyingConstraint(true);
    setConstraintError(null);
    setRenderStatus("Rendering graph...");
    try {
      await loadFromExtension(trimmed || null, prompt);
      if (requestId !== requestSequenceRef.current) {
        return;
      }
    } catch (error) {
      if (requestId !== requestSequenceRef.current) {
        return;
      }
      throw error;
    } finally {
      if (requestId === requestSequenceRef.current) {
        setIsApplyingConstraint(false);
      }
    }
  }, [constraintInput, loadFromExtension, userPromptInput]);

  useEffect(() => {
    if (initialNodes.length > 0 || didInitialLoadRef.current) {
      return;
    }

    didInitialLoadRef.current = true;
    setConstraintError(null);
    const query = new URLSearchParams(window.location.search);
    const promptFromQuery = query.get("prompt")?.trim() || "";
    const constraintFromQuery = query.get("constraint")?.trim() || null;
    if (promptFromQuery) {
      setUserPromptInput(promptFromQuery);
    }
    setIsApplyingConstraint(true);
    setRenderStatus("Rendering graph...");
    const requestId = ++requestSequenceRef.current;
    loadFromExtension(constraintFromQuery, promptFromQuery)
      .catch((error: unknown) => {
        if (requestId !== requestSequenceRef.current) {
          return;
        }
        setConstraintError(error instanceof Error ? error.message : "Failed to load extension history");
      })
      .finally(() => {
        if (requestId === requestSequenceRef.current) {
          setIsApplyingConstraint(false);
        }
      });
  }, [initialNodes.length, loadFromExtension]);

  const topAiPick = useMemo(() => {
    if (nodes.length === 0) {
      return null;
    }
    return [...nodes].sort((a, b) => a.data.aiRank - b.data.aiRank)[0];
  }, [nodes]);

  const compareRows = useMemo<CompareRow[]>(() => {
    const activeNodes = nodes;
    const metricCounts = new Map<string, number>();

    for (const node of activeNodes) {
      for (const metric of node.data.metrics || []) {
        if (!metric.label || !metric.value) {
          continue;
        }
        metricCounts.set(metric.label, (metricCounts.get(metric.label) ?? 0) + 1);
      }
    }

    const rankedMetricLabels = Array.from(metricCounts.entries())
      .sort((a, b) => b[1] - a[1])
      .slice(0, 6)
      .map(([label]) => label);

    if (rankedMetricLabels.length > 0) {
      return rankedMetricLabels.map((label) => ({
        label,
        values: activeNodes.map((node) => compareValueForLabel(node, label)),
      }));
    }

    const labelCounts = new Map<string, number>();

    for (const node of activeNodes) {
      const rawData = node.data.rawData ?? {};
      for (const [key, value] of Object.entries(rawData)) {
        const normalized = normalizeKey(key);
        if (EXCLUDED_COMPARE_KEYS.has(normalized) || !stringifyValue(value)) {
          continue;
        }
        labelCounts.set(titleCaseLabel(key), (labelCounts.get(titleCaseLabel(key)) ?? 0) + 1);
      }
    }

    const metricLabels = Array.from(labelCounts.entries())
      .sort((a, b) => b[1] - a[1])
      .slice(0, 6)
      .map(([label]) => label);

    if (metricLabels.length === 0) {
      return activeNodes[0]?.data.metrics?.slice(0, 4).map((metric) => ({
        label: metric.label,
        values: activeNodes.map((node) => node.data.metrics?.find((item) => item.label === metric.label)?.value || "Unknown"),
      })) ?? [];
    }

    return metricLabels.map((label) => ({
      label,
      values: activeNodes.map((node) => {
        const rawData = node.data.rawData ?? {};
        return compareValueForLabel(node, label);
      }),
    }));
  }, [nodes]);

  const boardHeight = getBoardHeight(nodes.length, viewportWidth);
  const boardWidth = getBoardWidth(viewportWidth);
  const columns = getBoardColumns(viewportWidth);
  const discoveredCount = useGraphStore.getState().allNodes.filter((node) => node.data.sourceType === "discovered").length;
  const nodeExtent: [[number, number], [number, number]] = [
    [18, 18],
    [Math.max(18, boardWidth - CARD_WIDTH - 18), Math.max(18, boardHeight - CARD_HEIGHT - 18)],
  ];

  return (
    <section className="min-h-screen overflow-x-hidden bg-[#1c1b16] text-[#f1ece0]">
      <div className="min-h-screen bg-[radial-gradient(circle_at_top,#2a281f_0%,#1c1b16_52%,#181712_100%)]">
        <div className="mx-auto flex max-w-[2048px] flex-col px-5 py-6 sm:px-8">
          <div className="flex flex-col gap-6">
            <header className="flex flex-col gap-5 xl:flex-row xl:items-start xl:justify-between">
              <div>
                <p className="text-xs uppercase tracking-[0.18em] text-[#9b927f]">Workspace</p>
                <h1 className="mt-1 text-3xl font-semibold tracking-[-0.03em] text-[#f5f0e4]">Context canvas</h1>
              </div>

              <div className="flex flex-col gap-3 xl:items-end">
                <div className="inline-flex items-center gap-1 rounded-full border border-[#3a362d] bg-[#26231d] p-1 text-base text-[#aba293]">
                  {(["board", "digest", "compare"] as WorkspaceView[]).map((mode) => (
                    <button
                      key={mode}
                      type="button"
                      onClick={() => setViewMode(mode)}
                      className={cn(
                        "rounded-full px-6 py-2.5 capitalize transition-colors",
                        viewMode === mode ? "bg-[#e0b36b] text-[#241f16]" : "hover:text-[#f1ece0]",
                      )}
                    >
                      {mode}
                    </button>
                  ))}
                  <span className="mx-1 h-6 w-px bg-[#3a362d]" aria-hidden="true" />
                  <button
                    type="button"
                    onClick={toggleShowDiscovered}
                    aria-pressed={showDiscovered}
                    title={showDiscovered ? "Showing captured and AI-found nodes" : "Showing captured nodes only"}
                    className={cn(
                      "inline-flex items-center gap-2 rounded-full px-4 py-2.5 transition-colors",
                      showDiscovered ? "bg-[#556d49] text-[#f2ffe8]" : "text-[#aba293] hover:text-[#f1ece0]",
                    )}
                  >
                    <Sparkles className="h-4 w-4" />
                    <span>{showDiscovered ? "All" : "Seed only"}</span>
                    <span className="rounded-full bg-black/20 px-2 py-0.5 text-xs">{discoveredCount}</span>
                  </button>
                </div>

                <div className="grid w-full gap-2 xl:grid-cols-[minmax(560px,1fr)_auto]">
                  <form
                    className="flex flex-wrap items-center gap-2 rounded-[24px] border border-[#312e26] bg-[#211f19]/92 px-4 py-3"
                    onSubmit={(event) => {
                      event.preventDefault();
                      runSynthesis().catch((error: unknown) => {
                        setIsApplyingConstraint(false);
                        setConstraintError(error instanceof Error ? error.message : "Constraint request failed");
                      });
                    }}
                  >
                    <input
                      value={userPromptInput}
                      onChange={(event) => setUserPromptInput(event.target.value)}
                      placeholder="Graph the laptop coolers"
                      className="min-w-[240px] flex-1 rounded-2xl border border-[#353128] bg-[#15140f] px-4 py-3 text-sm text-[#f5f0e4] outline-none placeholder:text-[#7d7668]"
                    />
                    <input
                      value={constraintInput}
                      onChange={(event) => setConstraintInput(event.target.value)}
                      placeholder="Optional AI constraint..."
                      className="min-w-[200px] flex-1 rounded-2xl border border-[#353128] bg-[#15140f] px-4 py-3 text-sm text-[#f5f0e4] outline-none placeholder:text-[#7d7668]"
                    />
                    <button
                      type="submit"
                      disabled={isApplyingConstraint}
                      className="rounded-2xl bg-[#e0b36b] px-5 py-3 text-sm font-medium text-[#241f16] transition-opacity disabled:opacity-60"
                    >
                      {isApplyingConstraint ? "Running..." : "Run"}
                    </button>
                  </form>

                  <label className="flex items-center justify-between gap-3 rounded-[22px] border border-[#312e26] bg-[#211f19]/92 px-4 py-3 text-sm text-[#e6dfd0]">
                    <span className="flex items-center gap-2 text-[#d5ccbc]">
                      <ArrowUpDown className="h-4 w-4" />
                      Sort
                    </span>
                    <div className="relative">
                      <select
                        aria-label="Sort results"
                        className="min-w-[160px] appearance-none rounded-xl border border-[#353128] bg-[#15140f] py-2.5 pl-3 pr-9 text-sm text-[#f5f0e4] outline-none"
                        value={sortMode}
                        onChange={(event) => applySort(event.target.value as SortMode)}
                      >
                        <option value="manual">Manual board</option>
                        <option value="ai">AI ranking</option>
                        <option value="price">Lowest price</option>
                        <option value="distance">Closest signal</option>
                        <option value="space">Most detail</option>
                      </select>
                      <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[#8b816f]" />
                    </div>
                  </label>
                </div>
              </div>
            </header>

            {constraintError ? <div className="text-sm text-[#f39a88]">{constraintError}</div> : null}
            <div className="flex flex-wrap gap-2">
              <Badge variant="outline" className="inline-flex items-center gap-2 rounded-full border-[#4d4538] bg-[#24211b] px-3 py-1 text-[#e4d8c2]">
                <span
                  className={cn(
                    "h-2 w-2 rounded-full",
                    isApplyingConstraint ? "animate-pulse bg-[#e0b36b]" : "bg-[#8b816f]",
                  )}
                />
                {isApplyingConstraint ? "Rendering..." : renderStatus}
              </Badge>
            </div>

            {topAiPick ? (
              <section className="rounded-[30px] border border-[#322f28] bg-[#1c1a15]/84 px-6 py-5">
                <div className="text-sm font-medium text-[#cbc1af]">AI pick reasoning</div>
                <p className="mt-3 max-w-[1200px] text-[17px] leading-8 text-[#efe8db]">
                  <span className="font-semibold">{topAiPick.data.title}</span> is currently leading because{" "}
                  {topAiPick.data.aiReason || topAiPick.data.summary}
                </p>
                <div className="mt-3 flex flex-wrap gap-2">
                  {(topAiPick.data.chips || []).slice(0, 4).map((chip) => (
                    <Badge key={chip} variant="outline" className="rounded-full border-[#413b30] bg-[#24211b] px-3 py-1 text-[#d8cfbe]">
                      {chip}
                    </Badge>
                  ))}
                </div>
              </section>
            ) : null}

            {viewMode === "board" ? (
              <section className="rounded-[26px] border border-[#312e26] bg-[#1d1b16]/85 p-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.02)]">
                <div
                  className="relative overflow-hidden rounded-[20px] bg-[#201f19]"
                  style={{ width: `${Math.min(boardWidth, viewportWidth - 40)}px`, height: `${boardHeight}px` }}
                >
                  <div
                    className="pointer-events-none absolute inset-0"
                    style={{
                      background:
                        "linear-gradient(135deg, rgba(97,71,67,0.42) 0%, rgba(63,69,54,0.24) 48%, rgba(112,139,73,0.42) 100%)",
                    }}
                  />
                  <div className="pointer-events-none absolute left-4 top-4 z-10 rounded-full border border-[#4a4439] bg-[#1d1b16]/82 px-3 py-1 text-[11px] font-medium uppercase tracking-[0.12em] text-[#c4b9a6]">
                    Lower fit
                  </div>
                  <div className="pointer-events-none absolute right-4 top-4 z-10 rounded-full border border-[#708b49] bg-[#2a371f]/88 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.12em] text-[#efffdd]">
                    Best fit
                  </div>
                  <div className="pointer-events-none absolute bottom-4 right-4 z-10 text-[11px] font-medium uppercase tracking-[0.14em] text-[#cfc4b1]">
                    Better rank and constraint match rises to the upper right
                  </div>
                  <ReactFlow
                    className="relative z-[2] h-full w-full"
                    style={{ width: "100%", height: "100%" }}
                    nodes={nodes}
                    edges={edges}
                    nodeTypes={NODE_TYPES}
                    onNodesChange={onNodesChange}
                    onNodeDragStop={onNodeDragStop}
                    onNodeClick={(_, node) => selectNode(node.id)}
                    defaultViewport={{ x: 0, y: 0, zoom: 1 }}
                    minZoom={0.65}
                    maxZoom={1}
                    nodesDraggable
                    autoPanOnNodeDrag
                    nodesConnectable={false}
                    elementsSelectable
                    panOnDrag={false}
                    zoomOnScroll={false}
                    zoomOnPinch={false}
                    zoomOnDoubleClick={false}
                    snapToGrid
                    snapGrid={[CARD_WIDTH + CARD_GAP, CARD_HEIGHT + CARD_GAP]}
                    nodeExtent={nodeExtent}
                    proOptions={{ hideAttribution: true }}
                    fitView
                  >
                    <Background color="rgba(215,201,170,0.08)" gap={28} />
                  </ReactFlow>
                </div>
              </section>
            ) : null}

            {viewMode === "digest" ? (
              <section className="grid gap-4 xl:grid-cols-2">
                {nodes.map((node) => (
                  <article
                    key={node.id}
                    className="rounded-[28px] border border-[#332f27] bg-[#25231d]/86 p-5 shadow-[inset_0_1px_0_rgba(255,255,255,0.02)]"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant="outline" className="rounded-full border-[#4f4738] bg-[#2a271f] px-3 py-1 text-[#d8b06c]">
                        {node.data.sourceLabel || "Source"}
                      </Badge>
                      <Badge variant="outline" className="rounded-full border-[#40392f] bg-[#2a271f] px-3 py-1 text-[#d9d1c1]">
                        {node.data.statusLabel || "summarized"}
                      </Badge>
                    </div>
                    <h3 className="mt-4 text-[20px] font-semibold tracking-[-0.02em] text-[#efe8db]">{node.data.title}</h3>
                    <p className="mt-3 text-[17px] leading-8 text-[#c5bcaa]">{node.data.summary}</p>
                    <div className="mt-5 flex flex-wrap gap-2">
                      {(node.data.chips || []).map((chip) => (
                        <Badge key={chip} variant="outline" className="rounded-full border-[#433d32] bg-[#2b281f] px-4 py-1.5 text-[#ded6c7]">
                          {chip}
                        </Badge>
                      ))}
                    </div>
                  </article>
                ))}
              </section>
            ) : null}

            {viewMode === "compare" ? (
              <section className="overflow-x-auto rounded-[34px] border border-[#312e26] bg-[#25231d]/86 p-4">
                <div className="grid min-w-max gap-3" style={{ gridTemplateColumns: `204px repeat(${Math.max(1, nodes.length)}, minmax(220px, 1fr))` }}>
                  <div className="sticky left-0 z-10 rounded-[22px] border border-[#3a362d] bg-[#2b2923] px-4 py-5 text-[17px] text-[#bcb2a1]">
                    Compare
                  </div>
                  {nodes.map((node) => (
                    <div key={node.id} className="rounded-[22px] border border-[#3a362d] bg-[#2b2923] px-4 py-4">
                      <div className="text-xs uppercase tracking-[0.18em] text-[#d4ac66]">{node.data.sourceLabel}</div>
                      <div className="mt-6 text-[18px] font-semibold text-[#f0e9dc]">{node.data.title}</div>
                    </div>
                  ))}

                  {compareRows.map((row, rowIndex) => (
                    <FragmentCompareRow key={`${row.label}-${rowIndex}`} row={row} />
                  ))}
                </div>
              </section>
            ) : null}

            <div className="flex flex-wrap gap-2">
              <Badge variant="outline" className="rounded-full border-[#3d382e] bg-[#25221c] px-3 py-1 text-[#d9d1c1]">
                nodes: {nodes.length}
              </Badge>
              <Badge variant="outline" className="rounded-full border-[#3d382e] bg-[#25221c] px-3 py-1 text-[#d9d1c1]">
                dom tabs: {domTabCount}
              </Badge>
              <Badge variant="outline" className="rounded-full border-[#3d382e] bg-[#25221c] px-3 py-1 text-[#d9d1c1]">
                columns: {columns}
              </Badge>
              <Badge variant="outline" className="rounded-full border-[#3d382e] bg-[#25221c] px-3 py-1 text-[#d9d1c1]">
                sort: {sortMode}
              </Badge>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

function FragmentCompareRow({ row }: { row: CompareRow }) {
  return (
    <>
      <div className="sticky left-0 z-10 rounded-[22px] border border-[#3a362d] bg-[#2b2923] px-4 py-5 text-[17px] text-[#bcb2a1]">
        {row.label}
      </div>
      {row.values.map((value, index) => (
        <div
          key={`${row.label}-${index}`}
          className={cn(
            "rounded-[22px] border px-4 py-5 text-[17px] text-[#efe8db]",
            index === row.values.length - 1
              ? "border-[#6c5441] bg-[#3a2f27]/85"
              : "border-[#3a362d] bg-[#2b2923]",
          )}
        >
          {value || "Unknown"}
        </div>
      ))}
    </>
  );
}
