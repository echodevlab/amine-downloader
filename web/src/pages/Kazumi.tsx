import { useEffect, useState } from "react"
import { toast } from "sonner"
import { Download, Eye, Import, Plus, RefreshCw, Search, Trash2 } from "lucide-react"

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
import { LoadingBar, Spinner } from "@/components/ui/spinner"
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

function episodeNumberOf(name: string, index: number) {
  const patterns = [
    /第\s*(\d{1,4}(?:\.\d+)?)\s*[集话話回]/,
    /\bS\d{1,2}E(\d{1,4})\b/i,
    /\bEP?\s*[-_.]?\s*(\d{1,4})\b/i,
  ]
  for (const pattern of patterns) {
    const match = name.match(pattern)
    if (match) return match[1]
  }
  const fallback = name.match(/(\d+(?:\.\d+)?)/)
  return fallback ? fallback[1] : String(index + 1)
}

export default function KazumiPage({ onNavigate }: { onNavigate: (key: "jobs") => void }) {
  const [rules, setRules] = useState<KazumiRule[]>([])
  const [importSource, setImportSource] = useState("")
  const [keyword, setKeyword] = useState("")
  const [rule, setRule] = useState("")
  const [hits, setHits] = useState<KazumiHit[]>([])
  const [searchErrors, setSearchErrors] = useState<string[]>([])
  const [hitIndex, setHitIndex] = useState(0)
  const [chapters, setChapters] = useState<KazumiChapters | null>(null)
  const [road, setRoad] = useState(0)
  const [episode, setEpisode] = useState("")
  const [selectedEpisodes, setSelectedEpisodes] = useState<Set<number>>(new Set())
  const [force, setForce] = useState(false)
  const [quality, setQuality] = useState("")
  const [titleOverride, setTitleOverride] = useState("")
  const [subs, setSubs] = useState<KazumiSubscription[]>([])
  const [busy, setBusy] = useState(false)
  const [busyLabel, setBusyLabel] = useState("")

  function begin(label: string) {
    setBusy(true)
    setBusyLabel(label)
  }

  function end() {
    end()
    setBusyLabel("")
  }

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
    begin("正在导入规则…")
    try {
      const result = await api.kazumiImport([importSource.trim()])
      toast.success(`已导入 ${result.imported.length} 条规则`)
      setImportSource("")
      loadRules()
    } catch (error) {
      toast.error((error as Error).message)
    } finally {
      end()
    }
  }

  async function search() {
    if (!keyword.trim()) return
    begin("正在并发搜索所有规则…")
    setChapters(null)
    try {
      const result = await api.kazumiSearch(keyword.trim(), rule || undefined)
      setHits(result.hits)
      setSearchErrors(result.errors)
      setHitIndex(0)
      if (!result.hits.length) toast.message("没有搜索结果")
      if (result.errors.length) toast.message(`${result.errors.length} 个源失败`)
    } catch (error) {
      toast.error((error as Error).message)
    } finally {
      end()
    }
  }

  async function loadChapters(index: number) {
    const hit = hits[index]
    begin("正在解析剧集…")
    setHitIndex(index)
    setSelectedEpisodes(new Set())
    try {
      const data = await api.kazumiChapters({
        q: keyword.trim(),
        rule: hit?.rule || rule || undefined,
        source: hit?.source,
        name: hit?.name,
        hit: index,
      })
      setChapters(data)
      setRoad(0)
    } catch (error) {
      toast.error((error as Error).message)
    } finally {
      end()
    }
  }

  const currentHit = hits[hitIndex]
  const currentRoad = chapters?.roads[road]

  function episodeParam() {
    if (currentRoad && selectedEpisodes.size) {
      return [...selectedEpisodes]
        .sort((a, b) => a - b)
        .map((index) => episodeNumberOf(currentRoad.episodes[index].name, index))
        .join(",")
    }
    return episode || undefined
  }

  async function download(dryRun: boolean) {
    begin(dryRun ? "正在创建预览任务…" : "正在创建下载任务…")
    try {
      await api.createJob("kazumi_download", {
        keyword: keyword.trim(),
        rule: currentHit?.rule || rule || undefined,
        source: currentHit?.source,
        name: currentHit?.name,
        hit: hitIndex,
        road,
        episode: episodeParam(),
        quality: quality || undefined,
        title: titleOverride || undefined,
        force,
        dry_run: dryRun,
      })
      toast.success("任务已创建，可在「任务」中查看进度")
      onNavigate("jobs")
    } catch (error) {
      toast.error((error as Error).message)
    } finally {
      end()
    }
  }

  async function addSubscription() {
    if (!currentHit) {
      toast.error("请先搜索并选择作品")
      return
    }
    begin("正在保存订阅…")
    try {
      await api.addKazumiSubscription({
        name: chapters?.name || currentHit.name,
        rule: currentHit.rule,
        source: currentHit.source,
        quality: quality || undefined,
        title: titleOverride || undefined,
        initial: "latest",
        enabled: true,
      })
      toast.success("已加入订阅（保存了规则与作品地址，无需重复搜索）")
      loadSubs()
    } catch (error) {
      toast.error((error as Error).message)
    } finally {
      end()
    }
  }

  async function removeSubscription(index: number) {
    if (!window.confirm(`删除订阅「${subs[index].name}」？`)) return
    try {
      await api.deleteKazumiSubscription(index)
      toast.success("已删除")
      loadSubs()
    } catch (error) {
      toast.error((error as Error).message)
    }
  }

  async function runSubs(dryRun: boolean) {
    begin(dryRun ? "正在创建预览任务…" : "正在创建追番任务…")
    try {
      await api.createJob("kazumi_run", { dry_run: dryRun })
      toast.success("任务已创建，可在「任务」中查看进度")
      onNavigate("jobs")
    } catch (error) {
      toast.error((error as Error).message)
    } finally {
      end()
    }
  }

  async function sync() {
    begin("正在同步下载状态…")
    try {
      const counts = await api.kazumiSync()
      toast.success(
        `已同步：完成 ${counts.downloaded}，失败 ${counts.failed}，进行中 ${counts.pending}`,
      )
    } catch (error) {
      toast.error((error as Error).message)
    } finally {
      end()
    }
  }

  function toggleEpisode(index: number) {
    setSelectedEpisodes((current) => {
      const next = new Set(current)
      if (next.has(index)) next.delete(index)
      else next.add(index)
      return next
    })
  }

  return (
    <div className="flex flex-col gap-4">
      {busyLabel && <LoadingBar label={busyLabel} />}
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
          <CardDescription>搜索作品 → 选择线路与集数 → 下载、预览或加入订阅</CardDescription>
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
                <option value="">全部规则（并发）</option>
                {rules.map((item) => (
                  <option key={item.name} value={item.name}>
                    {item.name}
                  </option>
                ))}
              </select>
            </div>
            <Button size="sm" disabled={busy} onClick={search}>
              {busyLabel.includes("搜索") ? <Spinner /> : <Search />} 搜索
            </Button>
          </div>

          {searchErrors.length > 0 && (
            <details className="rounded-lg border p-2 text-xs text-muted-foreground">
              <summary className="cursor-pointer">
                {searchErrors.length} 个源搜索失败（点击查看原因）
              </summary>
              <ul className="mt-1 list-disc pl-4">
                {searchErrors.map((error, index) => (
                  <li key={index}>{error}</li>
                ))}
              </ul>
            </details>
          )}

          {hits.length > 0 && (
            <div className="text-sm text-muted-foreground">
              共 {hits.length} 个结果，来自 {new Set(hits.map((hit) => hit.rule)).size} 个源
            </div>
          )}
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
            <div className="flex flex-col gap-3">
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
                    onChange={(event) => {
                      setRoad(Number(event.target.value))
                      setSelectedEpisodes(new Set())
                    }}
                  >
                    {chapters.roads.map((item, index) => (
                      <option key={index} value={index}>
                        {item.name}（{item.episodes.length} 集）
                      </option>
                    ))}
                  </select>
                </div>
                <div className="flex flex-col gap-1">
                  <Label className="text-xs">集（留空为全部，支持 1-12、1,3,5）</Label>
                  <Input
                    className="w-40"
                    value={episode}
                    onChange={(event) => setEpisode(event.target.value)}
                    placeholder="例如 1-12,15"
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
                <div className="flex flex-col gap-1">
                  <Label className="text-xs">标题（可选覆盖）</Label>
                  <Input
                    className="w-48"
                    value={titleOverride}
                    onChange={(event) => setTitleOverride(event.target.value)}
                    placeholder="用于 Jellyfin 匹配"
                  />
                </div>
                <label className="flex items-center gap-2 pb-1 text-sm">
                  <input
                    type="checkbox"
                    className="size-4"
                    checked={force}
                    onChange={(event) => setForce(event.target.checked)}
                  />
                  重新下载已下载过的
                </label>
                <Button size="sm" variant="outline" disabled={busy} onClick={() => download(true)}>
                  <Eye /> 预览
                </Button>
                <Button size="sm" disabled={busy} onClick={() => download(false)}>
                  <Download /> 下载
                </Button>
                <Button size="sm" variant="secondary" disabled={busy} onClick={addSubscription}>
                  <Plus /> 加入订阅
                </Button>
              </div>

              {currentRoad && (
                <div className="flex max-h-48 flex-wrap gap-2 overflow-y-auto rounded-lg border p-2">
                  {currentRoad.episodes.map((item, index) => (
                    <label
                      key={index}
                      className={`flex cursor-pointer items-center gap-1 rounded border px-2 py-1 text-xs ${
                        selectedEpisodes.has(index) ? "bg-muted" : ""
                      }`}
                    >
                      <input
                        type="checkbox"
                        className="size-3"
                        checked={selectedEpisodes.has(index)}
                        onChange={() => toggleEpisode(index)}
                      />
                      {item.name}
                    </label>
                  ))}
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>追番订阅</CardTitle>
          <CardDescription>订阅保存了规则与作品地址，运行时会跳过搜索</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="mb-3 flex flex-wrap gap-2">
            <Button size="sm" variant="outline" disabled={busy} onClick={() => runSubs(true)}>
              <Eye /> 预览
            </Button>
            <Button size="sm" disabled={busy} onClick={() => runSubs(false)}>
              <Download /> 追番
            </Button>
            <Button size="sm" variant="outline" disabled={busy} onClick={sync}>
              <RefreshCw /> 同步状态
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
                <TableHead>作品地址</TableHead>
                <TableHead>首次运行</TableHead>
                <TableHead>清晰度</TableHead>
                <TableHead>状态</TableHead>
                <TableHead className="w-16">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {subs.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={7} className="text-muted-foreground">
                    未配置订阅
                  </TableCell>
                </TableRow>
              ) : (
                subs.map((sub, index) => (
                  <TableRow key={`${sub.name}-${index}`}>
                    <TableCell>{sub.name}</TableCell>
                    <TableCell>{sub.rule || "-"}</TableCell>
                    <TableCell className="max-w-64 truncate text-muted-foreground" title={sub.source}>
                      {sub.source || "（运行时搜索）"}
                    </TableCell>
                    <TableCell className="text-muted-foreground">{sub.initial ?? "latest"}</TableCell>
                    <TableCell>{sub.quality || "-"}</TableCell>
                    <TableCell>
                      <Badge variant={sub.enabled ? "default" : "outline"}>
                        {sub.enabled ? "启用" : "停用"}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <Button size="xs" variant="ghost" onClick={() => removeSubscription(index)}>
                        <Trash2 />
                      </Button>
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
