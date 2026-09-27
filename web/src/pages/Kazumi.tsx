import { useEffect, useState } from "react"
import { toast } from "sonner"
import { Download, Eye, Import, RefreshCw, Search } from "lucide-react"

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
import {
  api,
  type KazumiChapters,
  type KazumiHit,
  type KazumiRule,
  type KazumiSubscription,
} from "@/lib/api"

const selectClass =
  "h-8 rounded-lg border border-border bg-background px-2 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50"

export default function KazumiPage({ onNavigate }: { onNavigate: (key: "jobs") => void }) {
  const [rules, setRules] = useState<KazumiRule[]>([])
  const [importSource, setImportSource] = useState("")
  const [keyword, setKeyword] = useState("")
  const [rule, setRule] = useState("")
  const [hits, setHits] = useState<KazumiHit[]>([])
  const [hitIndex, setHitIndex] = useState(0)
  const [chapters, setChapters] = useState<KazumiChapters | null>(null)
  const [road, setRoad] = useState(0)
  const [episode, setEpisode] = useState("")
  const [quality, setQuality] = useState("")
  const [subs, setSubs] = useState<KazumiSubscription[]>([])
  const [busy, setBusy] = useState(false)

  async function loadRules() {
    try {
      setRules(await api.kazumiRules())
    } catch (error) {
      toast.error((error as Error).message)
    }
  }

  async function loadSubs() {
    try {
      setSubs(await api.kazumiSubscriptions())
    } catch (error) {
      toast.error((error as Error).message)
    }
  }

  useEffect(() => {
    loadRules()
    loadSubs()
  }, [])

  async function importRules() {
    if (!importSource.trim()) return
    setBusy(true)
    try {
      const result = await api.kazumiImport([importSource.trim()])
      toast.success(`已导入 ${result.imported.length} 条规则`)
      setImportSource("")
      loadRules()
    } catch (error) {
      toast.error((error as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function search() {
    if (!keyword.trim()) return
    setBusy(true)
    setChapters(null)
    try {
      const found = await api.kazumiSearch(keyword.trim(), rule || undefined)
      setHits(found)
      setHitIndex(0)
      if (!found.length) toast.message("没有搜索结果")
    } catch (error) {
      toast.error((error as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function loadChapters(index: number) {
    setBusy(true)
    setHitIndex(index)
    try {
      const data = await api.kazumiChapters(keyword.trim(), rule || undefined, index)
      setChapters(data)
      setRoad(0)
    } catch (error) {
      toast.error((error as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function download(dryRun: boolean) {
    setBusy(true)
    try {
      await api.createJob("kazumi_download", {
        keyword: keyword.trim(),
        rule: rule || undefined,
        hit: hitIndex,
        road,
        episode: episode || undefined,
        quality: quality || undefined,
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

  async function runSubs(dryRun: boolean) {
    setBusy(true)
    try {
      await api.createJob("kazumi_run", { dry_run: dryRun })
      toast.success("任务已创建，可在「任务」中查看进度")
      onNavigate("jobs")
    } catch (error) {
      toast.error((error as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const currentRoad = chapters?.roads[road]

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <CardTitle>规则</CardTitle>
          <CardDescription>兼容 KazumiRules 的规则 JSON，支持本地文件或 URL</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="mb-3 flex gap-2">
            <Input
              placeholder="https://raw.githubusercontent.com/Predidit/KazumiRules/main/AGE.json"
              value={importSource}
              onChange={(event) => setImportSource(event.target.value)}
            />
            <Button size="sm" disabled={busy} onClick={importRules}>
              <Import /> 导入
            </Button>
            <Button size="sm" variant="outline" onClick={loadRules}>
              <RefreshCw /> 刷新
            </Button>
          </div>
          <div className="flex flex-wrap gap-2">
            {rules.length === 0 ? (
              <span className="text-sm text-muted-foreground">暂无规则</span>
            ) : (
              rules.map((item) => (
                <Badge key={item.name} variant="outline">
                  {item.name} · v{item.version} · {item.search_mode}/{item.chapter_mode}
                </Badge>
              ))
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>搜索与解析</CardTitle>
          <CardDescription>搜索作品 → 选择线路 → 下载或预览</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="flex flex-wrap items-end gap-2">
            <div className="flex flex-col gap-1">
              <Label className="text-xs">关键词</Label>
              <Input
                className="w-56"
                placeholder="葬送的芙莉莲"
                value={keyword}
                onChange={(event) => setKeyword(event.target.value)}
              />
            </div>
            <div className="flex flex-col gap-1">
              <Label className="text-xs">规则</Label>
              <select
                className={selectClass}
                value={rule}
                onChange={(event) => setRule(event.target.value)}
              >
                <option value="">自动（仅一条时）</option>
                {rules.map((item) => (
                  <option key={item.name} value={item.name}>
                    {item.name}
                  </option>
                ))}
              </select>
            </div>
            <Button size="sm" disabled={busy} onClick={search}>
              <Search /> 搜索
            </Button>
          </div>

          {hits.length > 0 && (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>#</TableHead>
                  <TableHead>作品</TableHead>
                  <TableHead>规则</TableHead>
                  <TableHead className="w-24">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {hits.map((hit, index) => (
                  <TableRow key={index}>
                    <TableCell>{index}</TableCell>
                    <TableCell>{hit.name}</TableCell>
                    <TableCell className="text-muted-foreground">{hit.rule}</TableCell>
                    <TableCell>
                      <Button size="xs" variant="outline" onClick={() => loadChapters(index)}>
                        选集
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}

          {chapters && (
            <div className="flex flex-wrap items-end gap-2">
              <div className="text-sm">
                <span className="font-medium">{chapters.name}</span>
                <span className="ml-2 text-muted-foreground">
                  共 {chapters.roads.length} 条线路
                </span>
              </div>
              <div className="flex flex-col gap-1">
                <Label className="text-xs">线路</Label>
                <select
                  className={selectClass}
                  value={road}
                  onChange={(event) => setRoad(Number(event.target.value))}
                >
                  {chapters.roads.map((item, index) => (
                    <option key={index} value={index}>
                      {item.name}（{item.episodes.length} 集）
                    </option>
                  ))}
                </select>
              </div>
              <div className="flex flex-col gap-1">
                <Label className="text-xs">集（留空为全部）</Label>
                <Input
                  className="w-24"
                  value={episode}
                  onChange={(event) => setEpisode(event.target.value)}
                  placeholder="例如 1"
                />
              </div>
              <div className="flex flex-col gap-1">
                <Label className="text-xs">清晰度</Label>
                <Input
                  className="w-24"
                  value={quality}
                  onChange={(event) => setQuality(event.target.value)}
                  placeholder="1080p"
                />
              </div>
              <Button size="sm" variant="outline" disabled={busy} onClick={() => download(true)}>
                <Eye /> 预览
              </Button>
              <Button size="sm" disabled={busy} onClick={() => download(false)}>
                <Download /> 下载
              </Button>
            </div>
          )}

          {currentRoad && (
            <div className="text-xs text-muted-foreground">
              当前线路剧集：{currentRoad.episodes.map((item) => item.name).join("、")}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>追番订阅</CardTitle>
          <CardDescription>配置 [[kazumi.subscribe]] 后，一键处理所有订阅</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="mb-3 flex gap-2">
            <Button size="sm" variant="outline" disabled={busy} onClick={() => runSubs(true)}>
              <Eye /> 预览
            </Button>
            <Button size="sm" disabled={busy} onClick={() => runSubs(false)}>
              <Download /> 追番
            </Button>
            <Button size="sm" variant="outline" onClick={loadSubs}>
              <RefreshCw /> 刷新
            </Button>
          </div>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>关键词</TableHead>
                <TableHead>规则</TableHead>
                <TableHead>线路</TableHead>
                <TableHead>清晰度</TableHead>
                <TableHead>状态</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {subs.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={5} className="text-muted-foreground">
                    未配置订阅
                  </TableCell>
                </TableRow>
              ) : (
                subs.map((sub) => (
                  <TableRow key={sub.name}>
                    <TableCell>{sub.name}</TableCell>
                    <TableCell>{sub.rule || "-"}</TableCell>
                    <TableCell>{sub.road}</TableCell>
                    <TableCell>{sub.quality || "-"}</TableCell>
                    <TableCell>
                      <Badge variant={sub.enabled ? "default" : "outline"}>
                        {sub.enabled ? "启用" : "停用"}
                      </Badge>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  )
}
