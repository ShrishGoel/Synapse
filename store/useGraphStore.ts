import type { Edge, Node, XYPosition } from "reactflow";
import { create } from "zustand";

export type SortMode = "manual" | "ai" | "price" | "distance" | "space";
export type NodeSourceType = "seed" | "discovered";
export type WorkspaceView = "board" | "digest" | "compare";

export type ResearchMetric = {
  label: string;
  value: string;
};

export type ResearchNodeData = {
  title: string;
  url: string;
  thumbnail?: string;
  locationLabel: string;
  sourceLabel?: string;
  statusLabel?: string;
  kindLabel?: string;
  summary?: string;
  priceUsd: number;
  distanceMiles: number;
  bedrooms: number;
  bathrooms: number;
  squareFeet: number;
  rentalType: string;
  combinedScore: number;
  aiRank: number;
  effectiveAiRank?: number;
  aiReason: string;
  sourceType: NodeSourceType;
  constraintViolated: boolean;
  constraintReason: string;
  metrics?: ResearchMetric[];
  chips?: string[];
  rawData?: Record<string, unknown>;
};

export type ResearchNode = Node<ResearchNodeData, "research">;
export type ResearchEdge = Edge;

type GraphState = {
  allNodes: ResearchNode[];
  allEdges: ResearchEdge[];
  nodes: ResearchNode[];
  edges: ResearchEdge[];
  sortMode: SortMode;
  showDiscovered: boolean;
  selectedNodeId: string | null;
  setGraph: (nodes: ResearchNode[], edges: ResearchEdge[]) => void;
  setSortMode: (sortMode: SortMode) => void;
  toggleShowDiscovered: () => void;
  selectNode: (nodeId: string | null) => void;
  setNodes: (nodes: ResearchNode[] | ((nodes: ResearchNode[]) => ResearchNode[])) => void;
  refreshVisibleGraph: () => void;
  sortNodes: (sortMode: SortMode) => void;
  swapNodeToGridPosition: (nodeId: string, position: XYPosition) => void;
};

export const CARD_WIDTH = 248;
export const CARD_HEIGHT = 430;
export const CARD_GAP = 24;
const BOARD_PADDING_X = 18;
const BOARD_PADDING_Y = 18;

export function calculateCombinedScore(input: {
  priceUsd: number;
  distanceMiles: number;
  squareFeet: number;
}) {
  const priceFit = Math.max(0, 1 - Math.abs(input.priceUsd - 1250) / 250);
  const distanceFit = Math.max(0, 1 - input.distanceMiles / 2.5);
  const spaceFit = Math.min(1, input.squareFeet / 450);

  return Math.round((priceFit * 0.5 + distanceFit * 0.35 + spaceFit * 0.15) * 100);
}

export function getBoardColumns(viewportWidth = 1440) {
  if (viewportWidth < 720) {
    return 1;
  }

  if (viewportWidth < 1080) {
    return 2;
  }

  if (viewportWidth < 1460) {
    return 3;
  }

  return 4;
}

export function getBoardWidth(viewportWidth = 1440) {
  const available = Math.max(viewportWidth - BOARD_PADDING_X * 2, CARD_WIDTH);
  const columns = Math.min(
    getBoardColumns(viewportWidth),
    Math.max(1, Math.floor((available + CARD_GAP) / (CARD_WIDTH + CARD_GAP)))
  );

  return BOARD_PADDING_X * 2 + columns * CARD_WIDTH + Math.max(0, columns - 1) * CARD_GAP;
}

export function getBoardHeight(nodeCount: number, viewportWidth = 1440) {
  const columns = getBoardColumns(viewportWidth);
  const rows = Math.max(1, Math.ceil(nodeCount / columns));
  return BOARD_PADDING_Y * 2 + rows * CARD_HEIGHT + Math.max(0, rows - 1) * CARD_GAP;
}

export function sortResearchNodes(nodes: ResearchNode[], sortMode: SortMode) {
  if (sortMode === "manual") {
    return [...nodes];
  }

  return [...nodes].sort((a, b) => {
    const aViolated = Number(Boolean(a.data.constraintViolated));
    const bViolated = Number(Boolean(b.data.constraintViolated));
    if (aViolated !== bViolated) {
      return aViolated - bViolated;
    }

    if (sortMode === "price") {
      const priceDiff = a.data.priceUsd - b.data.priceUsd;
      if (priceDiff !== 0) {
        return priceDiff;
      }
      return a.data.aiRank - b.data.aiRank;
    }

    if (sortMode === "distance") {
      const distanceDiff = a.data.distanceMiles - b.data.distanceMiles;
      if (distanceDiff !== 0) {
        return distanceDiff;
      }
      return a.data.aiRank - b.data.aiRank;
    }

    if (sortMode === "space") {
      const spaceDiff = b.data.squareFeet - a.data.squareFeet;
      if (spaceDiff !== 0) {
        return spaceDiff;
      }
      return a.data.aiRank - b.data.aiRank;
    }

    return a.data.aiRank - b.data.aiRank;
  });
}

export function calculateBoardNodes(nodes: ResearchNode[], viewportWidth = 1440) {
  const columns = getBoardColumns(viewportWidth);

  return nodes.map((node, index) => ({
    ...node,
    position: {
      x: BOARD_PADDING_X + (columns - 1 - (index % columns)) * (CARD_WIDTH + CARD_GAP),
      y: BOARD_PADDING_Y + Math.floor(index / columns) * (CARD_HEIGHT + CARD_GAP)
    }
  }));
}

function getViewportWidth() {
  if (typeof window === "undefined") {
    return 1440;
  }

  return window.innerWidth;
}

function filterVisibleNodes(allNodes: ResearchNode[], showDiscovered: boolean) {
  if (showDiscovered) {
    return allNodes;
  }

  return allNodes.filter((node) => node.data.sourceType !== "discovered");
}

function deriveVisibleNodes(allNodes: ResearchNode[], sortMode: SortMode, showDiscovered: boolean) {
  const visibleNodes = filterVisibleNodes(allNodes, showDiscovered);
  const sorted = sortResearchNodes(visibleNodes, sortMode);
  const rankedNodes = sorted.map((node, index) => ({
    ...node,
    data: {
      ...node.data,
      effectiveAiRank: index + 1,
    },
  }));
  return calculateBoardNodes(rankedNodes, getViewportWidth());
}

function deriveVisibleEdges(allEdges: ResearchEdge[], visibleNodes: ResearchNode[]) {
  const visibleIds = new Set(visibleNodes.map((node) => node.id));
  return allEdges.filter((edge) => visibleIds.has(edge.source) && visibleIds.has(edge.target));
}

export const useGraphStore = create<GraphState>((set, get) => ({
  allNodes: [],
  allEdges: [],
  nodes: [],
  edges: [],
  sortMode: "ai",
  showDiscovered: true,
  selectedNodeId: null,
  setGraph: (allNodes, allEdges) => {
    const { sortMode, showDiscovered } = get();
    const nodes = deriveVisibleNodes(allNodes, sortMode, showDiscovered);
    const edges = deriveVisibleEdges(allEdges, nodes);
    set({ allNodes, allEdges, nodes, edges });
  },
  setSortMode: (sortMode) => {
    set({ sortMode });
    get().refreshVisibleGraph();
  },
  toggleShowDiscovered: () => {
    set((state) => ({ showDiscovered: !state.showDiscovered }));
    get().refreshVisibleGraph();
  },
  selectNode: (selectedNodeId) => set({ selectedNodeId }),
  setNodes: (updater) => {
    const currentNodes = get().nodes;
    const nextNodes = typeof updater === "function" ? updater(currentNodes) : updater;
    const nextPositions = new Map(nextNodes.map((node) => [node.id, node.position]));
    const allNodes = get().allNodes.map((node) =>
      nextPositions.has(node.id)
        ? {
            ...node,
            position: nextPositions.get(node.id) ?? node.position
          }
        : node
    );

    set({ allNodes, nodes: nextNodes, edges: deriveVisibleEdges(get().allEdges, nextNodes) });
  },
  refreshVisibleGraph: () => {
    const { allNodes, allEdges, sortMode, showDiscovered } = get();
    const nodes = deriveVisibleNodes(allNodes, sortMode, showDiscovered);
    const edges = deriveVisibleEdges(allEdges, nodes);
    set({ nodes, edges });
  },
  sortNodes: (sortMode) => {
    set({ sortMode });
    get().refreshVisibleGraph();
  },
  swapNodeToGridPosition: (nodeId, position) => {
    const { allNodes, allEdges, showDiscovered, nodes: currentNodes } = get();
    const viewportWidth = getViewportWidth();
    const visibleNodes = [...currentNodes];
    const sourceIndex = visibleNodes.findIndex((node) => node.id === nodeId);

    if (sourceIndex < 0 || visibleNodes.length === 0) {
      return;
    }

    const columns = getBoardColumns(viewportWidth);
    const visualCol = Math.max(0, Math.round((position.x - BOARD_PADDING_X) / (CARD_WIDTH + CARD_GAP)));
    const col = Math.max(0, columns - 1 - visualCol);
    const row = Math.max(0, Math.round((position.y - BOARD_PADDING_Y) / (CARD_HEIGHT + CARD_GAP)));
    const unclampedTargetIndex = row * columns + col;
    const targetIndex = Math.min(visibleNodes.length - 1, unclampedTargetIndex);

    const swappedVisibleNodes = [...visibleNodes];
    const draggedNode = swappedVisibleNodes[sourceIndex];
    swappedVisibleNodes[sourceIndex] = swappedVisibleNodes[targetIndex];
    swappedVisibleNodes[targetIndex] = draggedNode;

    const allNodesById = new Map(allNodes.map((node) => [node.id, node]));
    const swappedVisibleCanonical = swappedVisibleNodes.map((node) => allNodesById.get(node.id) ?? node);

    let nextAllNodes: ResearchNode[];
    if (showDiscovered) {
      nextAllNodes = swappedVisibleCanonical;
    } else {
      const visibleIds = new Set(swappedVisibleCanonical.map((node) => node.id));
      let queueIndex = 0;
      nextAllNodes = allNodes.map((node) => {
        if (!visibleIds.has(node.id)) {
          return node;
        }

        const nextVisibleNode = swappedVisibleCanonical[queueIndex++];
        return nextVisibleNode ?? node;
      });
    }

    const nextNodes = calculateBoardNodes(swappedVisibleNodes, viewportWidth);
    const nextEdges = deriveVisibleEdges(allEdges, nextNodes);

    set({
      allNodes: nextAllNodes,
      nodes: nextNodes,
      edges: nextEdges,
      sortMode: "manual"
    });
  }
}));
