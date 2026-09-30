import { useEffect, useRef, useState } from "react"
import { toast } from "sonner"
import { Ban, RefreshCw } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { api, type Job, type JobEvent } from "@/lib/api"

const KIND_LABEL: Record<string, string> = {
  rss_run: "RSS 追番",
  rss_preview: "RSS 预览",
  kazumi_run: "解析追番",
  kazumi_preview: "解析追番预览",
  kazumi_download: "解析下载",
  kazumi_download_preview: "解析预览",
  kazumi_sync: "同步状态",
  set_title: "修改标题",
  rename: "重命名",
}

function statusVariant(status: string) {
  if (status === "done") return "default"
  if (status === "error") return "destructive"
  if (status === "running") return "secondary"
  return "outline"
}

function formatItem(item?: Record<string, unknown>) {
  if (!item) return ""
  const title = String(item.title ?? item.name ?? "")
  const episode = item.episode ? ` E${item.episode}` : ""
  const target = String(item.output ?? item.task_id ?? item.stream_url ?? item.skipped ?? "")
  return `${title}${episode} → ${target}`
}

export default function JobsPage() {
  const [jobs, setJobs] = useState<Job[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [events, setEvents] = useState<JobEvent[]>([])
  const logRef = useRef<HTMLDivElement>(null)

  async function loadJobs() {
    try {
      const list = await api.jobs()
      setJobs(list)
      setSelected((current) => current ?? list[0]?.id ?? null)
    } catch (error) {
      toast.error((error as Error).message)
    }
  }

  useEffect(() => {
    loadJobs()
    const timer = setInterval(loadJobs, 3000)
    return () => clearInterval(timer)
  }, [])

  useEffect(() => {
    if (!selected) return
    setEvents([])
    const source = new EventSource(`/api/jobs/${selected}/events`)
    source.onmessage = (message) => {
      try {
        const event = JSON.parse(message.data) as JobEvent
        setEvents((previous) => [...previous, event])
        if (event.type === "end") {
          source.close()
          loadJobs()
        }
      } catch {
        // ignore malformed frame
      }
    }
    source.onerror = () => source.close()
    return () => source.close()
  }, [selected])

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight })
  }, [events])

  const current = jobs.find((job) => job.id === selected)
  const progress = [...events].reverse().find((event) => event.type === "progress")
  const logEvents = events.filter((event) => event.type !== "progress")

  async function cancel() {
    if (!selected) return
    try {
      await api.cancelJob(selected)
      toast.message("已请求取消")
    } catch (error) {
      toast.error((error as Error).message)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>任务</CardTitle>
        <CardDescription>后台任务与 SSE 实时进度</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="mb-3 flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={loadJobs}>
            <RefreshCw /> 刷新
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={!current || current.status !== "running"}
            onClick={cancel}
          >
            <Ban /> 取消
          </Button>
        </div>

        <div className="grid gap-4 md:grid-cols-[16rem_1fr]">
          <div className="flex max-h-96 flex-col gap-1 overflow-y-auto">
            {jobs.length === 0 ? (
              <div className="text-sm text-muted-foreground">暂无任务</div>
            ) : (
              jobs.map((job) => (
                <button
                  key={job.id}
                  type="button"
                  onClick={() => setSelected(job.id)}
                  className={`flex flex-col gap-1 rounded-lg border p-2 text-left text-sm transition-colors hover:bg-muted ${
                    selected === job.id ? "bg-muted" : ""
                  }`}
                >
                  <span className="flex w-full items-center justify-between gap-2">
                    <span className="font-medium">{KIND_LABEL[job.kind] ?? job.kind}</span>
                    <Badge variant={statusVariant(job.status)}>{job.status}</Badge>
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {new Date(job.created_at * 1000).toLocaleTimeString()}
                  </span>
                </button>
              ))
            )}
          </div>

          <div className="min-w-0">
            {current && (
              <div className="mb-2 text-sm">
                <span className="font-medium">{KIND_LABEL[current.kind] ?? current.kind}</span>
                <Badge className="ml-2" variant={statusVariant(current.status)}>
                  {current.status}
                </Badge>
                {current.error && <div className="mt-1 text-destructive">{current.error}</div>}
              </div>
            )}
            {progress && (
              <div className="mb-2 flex items-center gap-2">
                <Progress
                  value={progress.total ? ((progress.done ?? 0) / progress.total) * 100 : 0}
                  className="w-56"
                />
                <span className="text-xs tabular-nums text-muted-foreground">
                  {progress.done ?? 0}/{progress.total ?? "?"}
                  {progress.message ? ` · ${progress.message}` : ""}
                </span>
              </div>
            )}
            <div
              ref={logRef}
              className="h-80 overflow-y-auto rounded-lg border bg-muted/30 p-3 font-mono text-xs leading-relaxed"
            >
              {logEvents.length === 0 ? (
                <div className="text-muted-foreground">等待事件…</div>
              ) : (
                logEvents.map((event, index) => (
                  <div key={index} className="flex gap-2">
                    <span className="shrink-0 text-muted-foreground">
                      {new Date(event.t * 1000).toLocaleTimeString()}
                    </span>
                    <span className={event.status === "error" || event.error ? "text-destructive" : ""}>
                      {event.type === "log" && event.message}
                      {event.type === "item" && `· ${formatItem(event.item)}`}
                      {event.type === "end" &&
                        `任务结束：${event.status}${event.error ? ` - ${event.error}` : ""}`}
                    </span>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
