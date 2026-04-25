import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Edge, Node } from "reactflow";

import { GraphCanvas } from "@/components/GraphCanvas";
import {
  calculateCombinedScore,
  useGraphStore,
  type ResearchNodeData
} from "@/store/useGraphStore";

vi.mock("reactflow", async () => {
  const React = await import("react");

  return {
    default: ({
      nodes,
      edges,
      nodeTypes,
      onNodeClick,
      onNodeDragStop
    }: {
      nodes: Node<ResearchNodeData>[];
      edges: Edge[];
      nodeTypes: Record<string, React.ComponentType<any>>;
      onNodeClick?: (event: React.MouseEvent, node: Node<ResearchNodeData>) => void;
      onNodeDragStop?: (event: React.MouseEvent, node: Node<ResearchNodeData>) => void;
    }) => (
      <div data-testid="mock-react-flow" data-edge-count={edges.length}>
        {nodes.map((node) => {
          const NodeComponent = nodeTypes[node.type || "research"];
          return (
            <div
              key={node.id}
              data-testid={`flow-node-${node.id}`}
              data-x={node.position.x}
              data-y={node.position.y}
              onClick={(event) => onNodeClick?.(event, node)}
              onMouseUp={(event) => onNodeDragStop?.(event, node)}
            >
              <NodeComponent id={node.id} data={node.data} selected={false} />
            </div>
          );
        })}
      </div>
    ),
    ReactFlowProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    Background: () => <div data-testid="mock-background" />,
    Handle: ({ children }: { children?: React.ReactNode }) => <div data-testid="mock-handle">{children}</div>,
    Position: { Top: "top", Bottom: "bottom", Left: "left", Right: "right" },
    BackgroundVariant: { Dots: "dots" },
    applyNodeChanges: vi.fn((_changes, nodes) => nodes)
  };
});

function buildNode(
  id: string,
  overrides: Partial<ResearchNodeData> = {}
): Node<ResearchNodeData, "research"> {
  const base = {
    title: "Rental",
    url: `https://example.com/${id}`,
    locationLabel: "Pasadena",
    priceUsd: 1400,
    distanceMiles: 1,
    bedrooms: 1,
    bathrooms: 1,
    squareFeet: 300,
    rentalType: "Studio apartment",
    combinedScore: calculateCombinedScore({ priceUsd: 1400, distanceMiles: 1, squareFeet: 300 }),
    aiRank: 1,
    aiReason: "Best mix of price and distance.",
    sourceType: "seed" as const,
    constraintViolated: false,
    constraintReason: ""
  };

  return {
    id,
    type: "research",
    position: { x: 0, y: 0 },
    data: { ...base, ...overrides }
  };
}

const mockNodes: Node<ResearchNodeData, "research">[] = [
  buildNode("green", {
    title: "1030 E Green St Apt 11B",
    priceUsd: 1450,
    distanceMiles: 0.7,
    squareFeet: 240,
    combinedScore: calculateCombinedScore({ priceUsd: 1450, distanceMiles: 0.7, squareFeet: 240 }),
    aiRank: 2
  }),
  buildNode("michigan", {
    title: "Michigan Ave room listing",
    priceUsd: 1150,
    distanceMiles: 0.7,
    bedrooms: 1,
    squareFeet: 150,
    rentalType: "Private room",
    combinedScore: calculateCombinedScore({ priceUsd: 1150, distanceMiles: 0.7, squareFeet: 150 }),
    aiRank: 1
  }),
  buildNode("washington", {
    title: "337 E Washington Blvd #1",
    priceUsd: 1500,
    distanceMiles: 2.2,
    squareFeet: 450,
    combinedScore: calculateCombinedScore({ priceUsd: 1500, distanceMiles: 2.2, squareFeet: 450 }),
    aiRank: 3,
    sourceType: "discovered"
  })
];

describe("GraphCanvas housing board", () => {
  beforeEach(() => {
    useGraphStore.setState({
      allNodes: [],
      allEdges: [],
      nodes: [],
      edges: [],
      sortMode: "ai",
      showDiscovered: true,
      selectedNodeId: null
    });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders the correct number of housing cards", async () => {
    render(<GraphCanvas initialNodes={mockNodes} initialEdges={[]} />);

    await waitFor(() => {
      expect(screen.getAllByTestId(/^flow-node-/)).toHaveLength(mockNodes.length);
    });
  });

  it("keeps all results in one ranked list and sorts by price", async () => {
    render(<GraphCanvas initialNodes={mockNodes} initialEdges={[]} />);

    await waitFor(() => {
      expect(screen.getByTestId("flow-node-green")).toBeInTheDocument();
    });

    fireEvent.change(screen.getByRole("combobox", { name: /sort results/i }), {
      target: { value: "price" }
    });

    await waitFor(() => {
      expect(useGraphStore.getState().sortMode).toBe("price");
    });

    expect(useGraphStore.getState().nodes[0].id).toBe("michigan");
    expect(useGraphStore.getState().nodes[0].position.x).toBeGreaterThan(useGraphStore.getState().nodes[1].position.x);
  });

  it("supports distance sorting", async () => {
    render(<GraphCanvas initialNodes={mockNodes} initialEdges={[]} />);

    await waitFor(() => {
      expect(screen.getByTestId("flow-node-green")).toBeInTheDocument();
    });

    fireEvent.change(screen.getByRole("combobox", { name: /sort results/i }), {
      target: { value: "distance" }
    });

    await waitFor(() => {
      expect(useGraphStore.getState().sortMode).toBe("distance");
    });

    expect(useGraphStore.getState().nodes[0].data.distanceMiles).toBe(0.7);
  });

  it("selects a housing card when clicked", async () => {
    render(<GraphCanvas initialNodes={mockNodes} initialEdges={[]} />);

    await waitFor(() => {
      expect(screen.getByTestId("flow-node-washington")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("flow-node-washington"));

    expect(useGraphStore.getState().selectedNodeId).toBe("washington");
  });

  it("renders outbound listing links from each card", async () => {
    render(<GraphCanvas initialNodes={mockNodes} initialEdges={[]} />);

    const link = await screen.findByRole("link", { name: /open 1030 e green st apt 11b listing/i });
    expect(link).toHaveAttribute("href", "https://example.com/green");
  });
});
