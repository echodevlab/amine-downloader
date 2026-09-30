import { Loader2 } from "lucide-react"
import { cn } from "cn"

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn("size-4 animate-spin", className)} />
}

/** 一行式加载提示：转圈 + 文案，可带进度条。 */
export function LoadingBar({
  label = "处理中…",
  done,
  total,
}: {
  label?: string
  done?: number
  total?: number
}) {
  const percent = total && total > 0 ? Math.round(((done ?? 0) / total) * 100) : null
  return (
    <div className="flex flex-col gap-1 rounded-lg border bg-muted/30 p-3 text-sm">
      <div className="flex items-center gap-2 text-muted-foreground">
        <Spinner />
        <span>{label}</span>
        {percent !== null && (
          <span className="ml-auto tabular-nums">
            {done ?? 0}/{total}（{percent}%）
          </span>
        )}
      </div>
      <div className="h-1 w-full overflow-hidden rounded-full bg-muted">
        <div
          className={`h-full bg-primary transition-all ${percent === null ? "w-1/3 animate-pulse" : ""}`}
          style={percent === null ? undefined : { width: `${percent}%` }}
        />
      </div>
    </div>
  )
}
