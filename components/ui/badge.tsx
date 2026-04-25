import * as React from "react";

import { cn } from "@/lib/utils";

export interface BadgeProps extends React.HTMLAttributes<HTMLDivElement> {
  variant?: "default" | "outline" | "evidence";
}

function Badge({ className, variant = "default", ...props }: BadgeProps) {
  return (
    <div
      className={cn(
        "inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium transition-colors",
        variant === "default" && "border-transparent bg-violet-300/15 text-violet-100",
        variant === "outline" && "border-white/15 bg-white/5 text-slate-200",
        variant === "evidence" && "border-emerald-300/30 bg-emerald-300/12 text-emerald-100",
        className
      )}
      {...props}
    />
  );
}

export { Badge };
