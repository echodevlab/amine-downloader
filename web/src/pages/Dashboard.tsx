import { useEffect, useState } from "react"
import { toast } from "sonner"
import { Pause, Play, RefreshCw, Trash2 } from "lucide-react"

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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { api, type TorrentInfo } from "@/lib/api"

function formatSize(bytes: number) {
  if (!bytes) return "-"
  const units = ["B", "KB", "MB", "GB", "TB"]
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`
}

function stateVariant(state: string) {
  if (state.includes("error") || state.includes("missing")) return "destructive"
  if (state.includes("UP") || state.includes("upload")) return "secondary"
  if (state.includes("DL") || state.includes("download") || state.includes("stalled"))
    return "default"
  return "outline"
}

export default function Dashboard() {
  const [torrents, setTorrents] = useState<TorrentInfo[]>([])
  const [loading, setLoading] = useState(false)

  async function load(showSpinner = false) {
    if (showSpinner) setLoading(true)
    try {
      setTorrents(await api.torrents())
    } catch (error) {
      if (showSpinner) toast.error((error as Error).message)
    } finally {
      if (showSpinner) setLoading(false)
    }
  }

  useEffect(() => {
    load(true)
    const timer = setInterval(() => load(false), 5000)
    return () => clearInterval(timer)
  }, [])

  async function act(id: string, action: "pause" | "resume" | "remove", deleteFiles = false) {
    try {
      await api.torrentAction(id, action, deleteFiles)
      toast.success(action === "remove" ? "已移除任务" : "已发送指令")
      load(false)
    } catch (error) {
      toast.error((error as Error).message)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>下载中</CardTitle>
        <CardDescription>下载器中的任务（每 5 秒自动刷新）</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="mb-3 flex justify-end">
          <Button variant="outline" size="sm" disabled={loading} onClick={() => load(true)}>
            <RefreshCw /> 刷新
          </Button>
        </div>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>名称</TableHead>
              <TableHead className="w-48">进度</TableHead>
              <TableHead>状态</TableHead>
              <TableHead>大小</TableHead>
              <TableHead className="w-28">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {torrents.length === 0 ? (
              <TableRow>
                <TableCell colSpan={5} className="text-muted-foreground">
                  暂无任务
                </TableCell>
              </TableRow>
            ) : (
              torrents.map((torrent) => (
                <TableRow key={torrent.id}>
                  <TableCell className="max-w-96 truncate" title={torrent.name}>
                    {torrent.name}
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center gap-2">
                      <Progress value={torrent.progress * 100} className="w-32" />
                      <span className="text-xs tabular-nums text-muted-foreground">
                        {(torrent.progress * 100).toFixed(1)}%
                      </span>
                    </div>
                  </TableCell>
                  <TableCell>
                    <Badge variant={stateVariant(torrent.state)}>{torrent.state || "-"}</Badge>
                  </TableCell>
                  <TableCell className="tabular-nums">{formatSize(torrent.size)}</TableCell>
                  <TableCell>
                    <div className="flex gap-1">
                      <Button
                        size="icon-xs"
                        variant="ghost"
                        title="暂停"
                        onClick={() => act(torrent.id, "pause")}
                      >
                        <Pause />
                      </Button>
                      <Button
                        size="icon-xs"
                        variant="ghost"
                        title="继续"
                        onClick={() => act(torrent.id, "resume")}
                      >
                        <Play />
                      </Button>
                      <Button
                        size="icon-xs"
                        variant="ghost"
                        title="移除"
                        onClick={() => act(torrent.id, "remove")}
                      >
                        <Trash2 />
                      </Button>
                    </div>
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
