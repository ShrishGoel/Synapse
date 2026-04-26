"use client";

import { ExternalLink } from "lucide-react";
import { motion } from "framer-motion";
import { Handle, Position, type NodeProps } from "reactflow";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import type { ResearchNodeData } from "@/store/useGraphStore";
import { useGraphStore } from "@/store/useGraphStore";

function faviconFor(url: string) {
  try {
    const host = new URL(url).hostname;
    return `https://www.google.com/s2/favicons?domain=${host}&sz=64`;
  } catch {
    return undefined;
  }
}

export function ResearchCard({ id, data, selected }: NodeProps<ResearchNodeData>) {
  const selectNode = useGraphStore((state) => state.selectNode);
  const selectedNodeId = useGraphStore((state) => state.selectedNodeId);
  const nodeIndex = useGraphStore((state) => state.nodes.findIndex((node) => node.id === id));
  const nodeCount = useGraphStore((state) => state.nodes.length);
  const isSelected = selected || selectedNodeId === id;
  const image = data.thumbnail || faviconFor(data.url);
  const safeIndex = Math.max(0, nodeIndex);
  const safeCount = Math.max(1, nodeCount);
  const ratio = safeCount <= 1 ? 0 : safeIndex / (safeCount - 1);
  const auraHue = Math.round(145 - 145 * ratio);
  const auraColor = `hsla(${auraHue}, 58%, 63%, 0.62)`;
  const auraGlow = `0 0 34px hsla(${auraHue}, 58%, 60%, 0.12)`;
  const isConstraintViolated = data.constraintViolated;

  return (
    <motion.div
      layout
      transition={{ layout: { duration: 0.45, type: "spring", damping: 28, stiffness: 220 } }}
      data-testid={`research-node-${id}`}
      onClickCapture={() => selectNode(id)}
    >
      <Handle
        type="target"
        position={Position.Top}
        isConnectable={false}
        className="!h-2 !w-2 !border-0 !bg-transparent !opacity-0"
      />
      <Card
        style={{
          borderColor: isConstraintViolated ? "rgba(96,90,80,0.6)" : auraColor,
          boxShadow: isConstraintViolated ? "none" : auraGlow,
        }}
        className={cn(
          "flex h-[430px] w-[248px] flex-col overflow-hidden rounded-[18px] border bg-[#26231d]/88 text-[#f1ece0] backdrop-blur-sm",
          isConstraintViolated && "opacity-40 grayscale",
          isSelected && "ring-2 ring-[#e0b36b]/30",
        )}
      >
        <CardHeader className="shrink-0 gap-2 p-3 pb-2">
          <div className="flex items-start justify-between gap-2">
            <div className="flex min-w-0 items-start gap-2">
              {image ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  alt=""
                  src={image}
                  className="mt-0.5 h-8 w-8 rounded-lg border border-white/10 bg-white/10 object-cover"
                />
              ) : (
                <div className="mt-0.5 h-8 w-8 rounded-lg border border-white/10 bg-white/10" />
              )}
              <div className="min-w-0">
                <div className="truncate text-[10px] uppercase tracking-[0.12em] text-[#d7ad69]">{data.sourceLabel || "Source"}</div>
                <CardTitle className="mt-1 line-clamp-2 break-words whitespace-normal text-[15px] leading-5 text-[#f5f0e4]">
                  {data.title}
                </CardTitle>
                <div className="mt-1 line-clamp-2 break-words text-[12px] leading-4 text-[#b8ae9a]">{data.kindLabel || data.locationLabel}</div>
              </div>
            </div>
            <Badge variant="outline" className="shrink-0 rounded-full border-[#3f3a30] bg-[#2b281f] px-2 py-0.5 text-[10px] text-[#d2c8b8]">
              {data.statusLabel || "summarized"}
            </Badge>
          </div>
        </CardHeader>

        <CardContent className="flex min-h-0 flex-1 flex-col gap-3 p-3 pt-1">
          <p className="line-clamp-3 shrink-0 break-words text-[12px] leading-5 text-[#c5bcaa]">
            {data.summary || data.aiReason || "No summary available yet."}
          </p>

          <div className="grid shrink-0 grid-cols-2 gap-2">
            {(data.metrics || []).slice(0, 4).map((metric) => (
              <div key={`${id}-${metric.label}`} className="min-w-0 rounded-xl border border-[#3a362d] bg-[#211f19] px-2 py-2">
                <div className="truncate text-[9px] uppercase tracking-[0.1em] text-[#948872]">{metric.label}</div>
                <div className="mt-1 truncate text-[12px] font-medium text-[#f2ebde]">{metric.value}</div>
              </div>
            ))}
          </div>

          <div className="min-h-0 flex-1 overflow-hidden">
            <div className="flex flex-wrap gap-2">
              {(data.chips || []).slice(0, 3).map((chip) => (
                <Badge key={`${id}-${chip}`} variant="outline" className="max-w-full truncate rounded-full border-[#3f3a30] bg-[#2b281f] px-2 py-1 text-[10px] text-[#ddd4c4]">
                  {chip}
                </Badge>
              ))}
            </div>
          </div>

          <div className="mt-auto flex items-center justify-between gap-3 pt-1">
            <a
              href={data.url}
              target="_blank"
              rel="noreferrer"
              aria-label={`Open ${data.title} listing`}
              className="inline-flex items-center gap-1 rounded-full border border-[#6c7b66] bg-[#25322a] px-3 py-1.5 text-xs font-medium text-[#dbe8db] transition-colors hover:bg-[#2d3b31]"
            >
              Open
              <ExternalLink className="h-3.5 w-3.5" />
            </a>

            <div className="text-[10px] uppercase tracking-[0.12em] text-[#8f846f]">Rank {data.aiRank}</div>
          </div>

          {isConstraintViolated ? (
            <div className="shrink-0 pt-1">
              <Badge className="max-w-full truncate rounded-full border-[#8f4c4c] bg-[#542d2d] text-[#f1d1d1]">
                {data.constraintReason || "Constraint fail"}
              </Badge>
            </div>
          ) : null}
        </CardContent>
      </Card>
      <Handle
        type="source"
        position={Position.Bottom}
        isConnectable={false}
        className="!h-2 !w-2 !border-0 !bg-transparent !opacity-0"
      />
    </motion.div>
  );
}
