import { GraphCanvas } from "@/components/GraphCanvas";
import { type ResearchEdge, type ResearchNode } from "@/store/useGraphStore";

const initialNodes: ResearchNode[] = [];
const initialEdges: ResearchEdge[] = [];

export default function Page() {
  return (
    <main className="min-h-screen bg-slate-950">
      <GraphCanvas initialNodes={initialNodes} initialEdges={initialEdges} />
    </main>
  );
}
