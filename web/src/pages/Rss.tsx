import { useEffect, useState } from "react"
import { toast } from "sonner"
import { Download, Eye, RefreshCw } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { api, type RssFeed } from "@/lib/api"

export default function RssPage({ onNavigate }: { onNavigate: (key: "jobs") => void }) {
  const [feeds, setFeeds] = useState<RssFeed[]>([])
  const [limit, setLimit] = useState("")
  const [busy, setBusy] = useState(false)

  async function load() {
    try {
      setFeeds(await api.rss())
    } catch (error) {
      toast.error((error as Error).message)
    }
  }

  useEffect(() => {
    load()
  }, [])

  async function run(dryRun: boolean) {
    setBusy(true)
    try {
      await api.createJob("rss_run", {
        limit: limit ? Number(limit) : null,
        dry_run: dryRun,
      })
      toast.success("任务已创建，可在「任务」中查看进度")
      onNavigate("jobs")
    } catch (error) {
      toast.error((error as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>RSS 订阅</CardTitle>
        <CardDescription>在配置文件里添加 [[rss]]，这里会列出并支持一键追番</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="mb-3 flex flex-wrap items-end gap-2">
          <div className="flex flex-col gap-1">
            <Label htmlFor="limit" className="text-xs">
              每个源最多 N 集（留空为全部）
            </Label>
            <Input
              id="limit"
              className="w-32"
              inputMode="numeric"
              value={limit}
              onChange={(event) => setLimit(event.target.value)}
              placeholder="例如 1"
            />
          </div>
          <Button variant="outline" size="sm" onClick={load}>
            <RefreshCw /> 刷新
          </Button>
          <Button variant="outline" size="sm" disabled={busy} onClick={() => run(true)}>
            <Eye /> 预览
          </Button>
          <Button size="sm" disabled={busy} onClick={() => run(false)}>
            <Download /> 下载新剧集
          </Button>
        </div>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>名称</TableHead>
              <TableHead>RSS 地址</TableHead>
              <TableHead>字幕组</TableHead>
              <TableHead>状态</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {feeds.length === 0 ? (
              <TableRow>
                <TableCell colSpan={4} className="text-muted-foreground">
                  未配置订阅源
                </TableCell>
              </TableRow>
            ) : (
              feeds.map((feed) => (
                <TableRow key={feed.url}>
                  <TableCell>{feed.name}</TableCell>
                  <TableCell className="max-w-96 truncate text-muted-foreground" title={feed.url}>
                    {feed.url}
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {feed.groups?.length
                      ? feed.groups.join(" / ")
                      : feed.exclude_groups?.length
                        ? `排除 ${feed.exclude_groups.join(" / ")}`
                        : "全部"}
                  </TableCell>
                  <TableCell>
                    <Badge variant={feed.enabled ? "default" : "outline"}>
                      {feed.enabled ? "启用" : "停用"}
                    </Badge>
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
