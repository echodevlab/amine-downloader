import { useEffect, useState } from "react"
import { toast } from "sonner"
import { Pencil, RefreshCw, RotateCcw, Trash2, Wand2 } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { LoadingBar, Spinner } from "@/components/ui/spinner"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { api, type DownloadTask } from "@/lib/api"

const PAGE_SIZE = 20

const STATUSES = [
  "added",
  "downloaded",
  "renamed",
  "waiting-complete",
  "no-video",
  "failed",
  "seen",
  "removed",
]

const selectClass =
  "h-8 rounded-lg border border-border bg-background px-2 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50"

function statusVariant(status: string) {
  if (status === "renamed" || status === "downloaded") return "default"
  if (status === "failed" || status === "removed") return "destructive"
  if (status === "no-video" || status === "waiting-complete") return "secondary"
  return "outline"
}

export default function HistoryPage({ onNavigate }: { onNavigate: (key: "jobs") => void }) {
  const [tasks, setTasks] = useState<DownloadTask[]>([])
  const [total, setTotal] = useState(0)
  const [titles, setTitles] = useState<string[]>([])
  const [search, setSearch] = useState("")
  const [status, setStatus] = useState("")
  const [title, setTitle] = useState("")
  const [page, setPage] = useState(0)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(false)
  const [retrying, setRetrying] = useState<string | null>(null)

  async function load(targetPage = page) {
    setLoading(true)
    try {
      const data = await api.tasks({
        status: status || undefined,
        q: search || undefined,
        title: title || undefined,
        limit: PAGE_SIZE,
        offset: targetPage * PAGE_SIZE,
      })
      setTasks(data.items)
      setTotal(data.total)
      setTitles(data.titles)
    } catch (error) {
      toast.error((error as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load(0)
    setPage(0)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, title])

  async function rename() {
    setBusy(true)
    try {
      const result = await api.rename()
      toast.success(`已重命名 ${result.renamed} 个任务`)
      load()
    } catch (error) {
      toast.error((error as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function setTitleFor(task: DownloadTask) {
    const next = window.prompt("新的番剧标题", task.title || task.raw_title)
    if (!next || !next.trim()) return
    const applyAll =
      Boolean(task.title) &&
      window.confirm("是否应用到同名的所有记录？（取消 = 只改这一条）")
    const saveAlias = window.confirm("是否保存为别名（写入 [titles]）？")
    try {
      await api.createJob("set_title", {
        key: task.key,
        title: next.trim(),
        apply_all: applyAll,
        save_alias: saveAlias,
      })
      toast.success("任务已创建，可在「任务」中查看进度")
      onNavigate("jobs")
    } catch (error) {
      toast.error((error as Error).message)
    }
  }

  async function remove(task: DownloadTask) {
    if (!window.confirm(`删除记录「${task.title || task.raw_title}」？\n下次运行时会重新下载。`)) return
    try {
      await api.deleteTask(task.key)
      toast.success("已删除记录")
      load()
    } catch (error) {
      toast.error((error as Error).message)
    }
  }

  function isRefreshLink(task: DownloadTask) {
    return task.client === "aria2" || task.key.startsWith("kazumi:")
  }

  function canRetry(task: DownloadTask) {
    // Kazumi 的流地址会过期，任何状态都允许「重新获取链接」；其它记录只在失败时重试。
    if (task.key.startsWith("kazumi:")) return true
    return ["failed", "no-video", "removed"].includes(task.status)
  }

  async function retry(task: DownloadTask) {
    setRetrying(task.key)
    try {
      await api.retryTask(task.key)
      toast.success(isRefreshLink(task) ? "已重新获取链接并提交下载" : "已重新提交")
      load()
    } catch (error) {
      toast.error((error as Error).message)
    } finally {
      setRetrying(null)
    }
  }

  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <Card>
      <CardHeader>
        <CardTitle>历史记录</CardTitle>
        <CardDescription>已添加/已下载的任务，以及标准格式的目标文件名</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <Input
            className="w-56"
            placeholder="搜索番剧 / 原始标题"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            onKeyDown={(event) => event.key === "Enter" && load(0)}
          />
          <select
            className={selectClass}
            value={status}
            onChange={(event) => setStatus(event.target.value)}
          >
            <option value="">全部状态</option>
            {STATUSES.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
          <select
            className={selectClass}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          >
            <option value="">全部番剧</option>
            {titles.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
          <Button variant="outline" size="sm" onClick={() => load(0)}>
            <RefreshCw /> 查询
          </Button>
          <div className="ml-auto flex gap-2">
            <Button variant="outline" size="sm" onClick={rename} disabled={busy}>
              {busy ? <Spinner /> : <Wand2 />} 重命名已完成任务
            </Button>
          </div>
        </div>
        {loading && (
          <div className="mb-3">
            <LoadingBar label="加载历史记录…" />
          </div>
        )}
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>番剧</TableHead>
              <TableHead>季/集</TableHead>
              <TableHead>目标文件名</TableHead>
              <TableHead>状态</TableHead>
              <TableHead>下载器</TableHead>
              <TableHead>时间</TableHead>
              <TableHead className="w-40">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {tasks.length === 0 ? (
              <TableRow>
                <TableCell colSpan={7} className="text-muted-foreground">
                  暂无记录
                </TableCell>
              </TableRow>
            ) : (
              tasks.map((task) => (
                <TableRow key={task.key}>
                  <TableCell className="max-w-64 truncate" title={task.title || task.raw_title}>
                    {task.title || task.raw_title}
                  </TableCell>
                  <TableCell className="tabular-nums">
                    S{String(task.season).padStart(2, "0")}E{task.episode || "?"}
                  </TableCell>
                  <TableCell className="max-w-72 truncate" title={task.new_name}>
                    {task.new_name || "-"}
                  </TableCell>
                  <TableCell>
                    <Badge variant={statusVariant(task.status)}>{task.status}</Badge>
                  </TableCell>
                  <TableCell className="text-muted-foreground">{task.client}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">{task.created_at}</TableCell>
                  <TableCell>
                    <div className="flex gap-1">
                      <Button size="xs" variant="outline" onClick={() => setTitleFor(task)}>
                        <Pencil /> 改标题
                      </Button>
                      {canRetry(task) && (
                        <Button
                          size="xs"
                          variant="outline"
                          disabled={retrying === task.key}
                          onClick={() => retry(task)}
                        >
                          {retrying === task.key ? <Spinner /> : <RotateCcw />}
                          {isRefreshLink(task) ? "重新获取链接" : "重试"}
                        </Button>
                      )}
                      <Button size="xs" variant="ghost" onClick={() => remove(task)}>
                        <Trash2 />
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
        <div className="mt-3 flex items-center justify-between text-sm text-muted-foreground">
          <span>
            共 {total} 条，第 {page + 1}/{pages} 页
          </span>
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={page <= 0}
              onClick={() => {
                const next = page - 1
                setPage(next)
                load(next)
              }}
            >
              上一页
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={page + 1 >= pages}
              onClick={() => {
                const next = page + 1
                setPage(next)
                load(next)
              }}
            >
              下一页
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
