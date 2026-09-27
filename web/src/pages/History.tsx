import { useEffect, useState } from "react"
import { toast } from "sonner"
import { Pencil, RefreshCw, Wand2 } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { api, type DownloadTask } from "@/lib/api"

function statusVariant(status: string) {
  if (status === "renamed" || status === "downloaded") return "default"
  if (status === "no-video" || status === "waiting-complete") return "secondary"
  return "outline"
}

export default function HistoryPage({ onNavigate }: { onNavigate: (key: "jobs") => void }) {
  const [tasks, setTasks] = useState<DownloadTask[]>([])
  const [busy, setBusy] = useState(false)

  async function load() {
    try {
      setTasks(await api.tasks())
    } catch (error) {
      toast.error((error as Error).message)
    }
  }

  useEffect(() => {
    load()
  }, [])

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

  async function setTitle(task: DownloadTask) {
    const next = window.prompt("新的番剧标题", task.title || task.raw_title)
    if (!next || !next.trim()) return
    try {
      await api.createJob("set_title", { key: task.key, title: next.trim() })
      toast.success("任务已创建，可在「任务」中查看进度")
      onNavigate("jobs")
    } catch (error) {
      toast.error((error as Error).message)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>历史记录</CardTitle>
        <CardDescription>已添加/已下载的任务，以及标准格式的目标文件名</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="mb-3 flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={load}>
            <RefreshCw /> 刷新
          </Button>
          <Button size="sm" disabled={busy} onClick={rename}>
            <Wand2 /> 重命名已完成任务
          </Button>
        </div>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>番剧</TableHead>
              <TableHead>季/集</TableHead>
              <TableHead>目标文件名</TableHead>
              <TableHead>状态</TableHead>
              <TableHead>下载器</TableHead>
              <TableHead>时间</TableHead>
              <TableHead className="w-24">操作</TableHead>
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
                    <Button size="xs" variant="outline" onClick={() => setTitle(task)}>
                      <Pencil /> 改标题
                    </Button>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  )
}
