import { useEffect, useState } from "react"
import { toast } from "sonner"
import { Download, Eye, Pencil, Plus, RefreshCw, Trash2, X } from "lucide-react"

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
import { LoadingBar, Spinner } from "@/components/ui/spinner"
import { api, type RssFeed, type RssPreview } from "@/lib/api"

type FeedPreview = {
  feed: string
  total: number
  names: string[]
  items: RssPreview["items"]
  error?: string
}

const selectClass =
  "h-8 rounded-lg border border-border bg-background px-2 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50"

type FeedForm = {
  index: number | null
  name: string
  url: string
  names: string
  exclude_names: string
  initial: "latest" | "all" | "none"
  one_per_episode: boolean
  enabled: boolean
}

const emptyForm: FeedForm = {
  index: null,
  name: "",
  url: "",
  names: "",
  exclude_names: "",
  initial: "latest",
  one_per_episode: true,
  enabled: true,
}

function distinctNames(items: RssPreview["items"]) {
  const names: string[] = []
  for (const item of items) {
    const name = item.name?.trim()
    if (name && !names.includes(name)) names.push(name)
  }
  return names
}

export default function RssPage({ onNavigate }: { onNavigate: (key: "jobs") => void }) {
  const [feeds, setFeeds] = useState<RssFeed[]>([])
  const [limit, setLimit] = useState("")
  const [busy, setBusy] = useState(false)
  const [form, setForm] = useState<FeedForm>(emptyForm)
  const [preview, setPreview] = useState<RssPreview | null>(null)
  const [previewName, setPreviewName] = useState("")
  const [previewAll, setPreviewAll] = useState<FeedPreview[] | null>(null)
  const [allProgress, setAllProgress] = useState<{ done: number; total: number } | null>(null)

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

  async function run(dryRun: boolean, feedName?: string) {
    setBusy(true)
    try {
      await api.createJob("rss_run", {
        limit: limit ? Number(limit) : null,
        dry_run: dryRun,
        feeds: feedName ? [feedName] : undefined,
      })
      toast.success(feedName ? `已提交「${feedName}」` : "任务已创建，可在「任务」中查看进度")
      onNavigate("jobs")
    } catch (error) {
      toast.error((error as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function previewFeed(feedName: string) {
    setBusy(true)
    setPreviewName(feedName)
    try {
      setPreview(await api.previewRss({ name: feedName }))
    } catch (error) {
      toast.error((error as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function previewAllFeeds() {
    const enabled = feeds.filter((feed) => feed.enabled)
    if (!enabled.length) {
      toast.message("没有启用的订阅")
      return
    }
    setPreviewAll(null)
    setAllProgress({ done: 0, total: enabled.length })
    const out: FeedPreview[] = []
    for (let index = 0; index < enabled.length; index += 1) {
      const feed = enabled[index]
      try {
        const data = await api.previewRss({ name: feed.name })
        out.push({
          feed: feed.name,
          total: data.total,
          names: distinctNames(data.items),
          items: data.items,
        })
      } catch (error) {
        out.push({
          feed: feed.name,
          total: 0,
          names: [],
          items: [],
          error: (error as Error).message,
        })
      }
      setAllProgress({ done: index + 1, total: enabled.length })
    }
    setPreviewAll(out)
    setAllProgress(null)
  }

  async function previewForm() {
    if (!form.url.trim()) {
      toast.error("请先填写 RSS 地址")
      return
    }
    setBusy(true)
    setPreviewName(form.name || form.url)
    try {
      setPreview(await api.previewRss({ url: form.url.trim() }))
    } catch (error) {
      toast.error((error as Error).message)
    } finally {
      setBusy(false)
    }
  }

  function splitList(value: string) {
    return value
      .split(/[,，]/)
      .map((item) => item.trim())
      .filter(Boolean)
  }

  function toggleName(name: string) {
    const list = splitList(form.names)
    const next = list.includes(name) ? list.filter((item) => item !== name) : [...list, name]
    setForm({ ...form, names: next.join(", ") })
  }

  async function submit() {
    if (!form.url.trim()) {
      toast.error("订阅 URL 不能为空")
      return
    }
    const payload = {
      name: form.name.trim() || form.url.trim(),
      url: form.url.trim(),
      names: splitList(form.names),
      exclude_names: splitList(form.exclude_names),
      initial: form.initial,
      one_per_episode: form.one_per_episode,
      enabled: form.enabled,
    }
    setBusy(true)
    try {
      if (form.index === null) {
        await api.addRss(payload)
      } else {
        await api.updateRss(form.index, payload)
      }
      toast.success("已保存订阅")
      setForm(emptyForm)
      load()
    } catch (error) {
      toast.error((error as Error).message)
    } finally {
      setBusy(false)
    }
  }

  function edit(index: number) {
    const feed = feeds[index]
    setForm({
      index,
      name: feed.name,
      url: feed.url,
      names: (feed.names ?? []).join(", "),
      exclude_names: (feed.exclude_names ?? []).join(", "),
      initial: feed.initial ?? "latest",
      one_per_episode: feed.one_per_episode ?? true,
      enabled: feed.enabled,
    })
  }

  async function remove(index: number) {
    if (!window.confirm(`删除订阅「${feeds[index].name}」？`)) return
    try {
      await api.deleteRss(index)
      toast.success("已删除")
      load()
    } catch (error) {
      toast.error((error as Error).message)
    }
  }

  const previewNames = preview ? distinctNames(preview.items) : []

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <CardTitle>RSS 订阅</CardTitle>
          <CardDescription>在网页上增删改订阅；首次运行策略可避免一次下载整季历史</CardDescription>
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
            <Button
              variant="outline"
              size="sm"
              disabled={busy || allProgress !== null}
              onClick={previewAllFeeds}
            >
              {allProgress !== null ? <Spinner /> : <Eye />} 预览全部
            </Button>
            <Button size="sm" disabled={busy} onClick={() => run(false)}>
              <Download /> 下载全部新剧集
            </Button>
          </div>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>名称</TableHead>
                <TableHead>RSS 地址</TableHead>
                <TableHead>番剧名筛选</TableHead>
                <TableHead>首次运行</TableHead>
                <TableHead>状态</TableHead>
                <TableHead className="w-48">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {feeds.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="text-muted-foreground">
                    未配置订阅源
                  </TableCell>
                </TableRow>
              ) : (
                feeds.map((feed, index) => (
                  <TableRow key={`${feed.url}-${index}`}>
                    <TableCell>{feed.name}</TableCell>
                    <TableCell className="max-w-72 truncate text-muted-foreground" title={feed.url}>
                      {feed.url}
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {feed.names?.length
                        ? feed.names.join(" / ")
                        : feed.exclude_names?.length
                          ? `排除 ${feed.exclude_names.join(" / ")}`
                          : "全部"}
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {feed.initial ?? "latest"}
                      {feed.one_per_episode ? " · 同集一个" : ""}
                    </TableCell>
                    <TableCell>
                      <Badge variant={feed.enabled ? "default" : "outline"}>
                        {feed.enabled ? "启用" : "停用"}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1">
                        <Button
                          size="xs"
                          variant="outline"
                          disabled={busy}
                          onClick={() => previewFeed(feed.name)}
                        >
                          {busy && previewName === feed.name ? <Spinner /> : <Eye />} 预览
                        </Button>
                        <Button
                          size="xs"
                          variant="outline"
                          disabled={busy}
                          onClick={() => run(false, feed.name)}
                        >
                          <Download /> 下载
                        </Button>
                        <Button size="xs" variant="ghost" onClick={() => edit(index)}>
                          <Pencil />
                        </Button>
                        <Button size="xs" variant="ghost" onClick={() => remove(index)}>
                          <Trash2 />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>

          {allProgress && (
            <div className="mt-3">
              <LoadingBar
                label="正在预览订阅源…"
                done={allProgress.done}
                total={allProgress.total}
              />
            </div>
          )}

          {previewAll && (
            <div className="mt-3 flex flex-col gap-2">
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium">预览结果</span>
                <Button size="xs" variant="ghost" onClick={() => setPreviewAll(null)}>
                  <X />
                </Button>
              </div>
              {previewAll.map((item) => (
                <details key={item.feed} className="rounded-lg border p-2 text-sm" open>
                  <summary className="cursor-pointer">
                    {item.feed}：{item.error ? `失败（${item.error}）` : `${item.total} 条`}
                    {item.names.length ? ` · ${item.names.slice(0, 6).join(" / ")}` : ""}
                  </summary>
                  <div className="mt-1 max-h-48 overflow-y-auto">
                    {item.items.slice(0, 50).map((row, index) => (
                      <div key={index} className="truncate text-xs text-muted-foreground">
                        [{row.group || "-"}] {row.name || row.title} E{row.episode || "?"}
                      </div>
                    ))}
                  </div>
                </details>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{form.index === null ? "添加订阅" : "编辑订阅"}</CardTitle>
          <CardDescription>先预览解析结果，再勾选要保留的番剧名</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="grid gap-3 md:grid-cols-2">
            <div className="flex flex-col gap-1">
              <Label className="text-xs">名称</Label>
              <Input
                value={form.name}
                onChange={(event) => setForm({ ...form, name: event.target.value })}
                placeholder="Mikan - 葬送的芙莉莲"
              />
            </div>
            <div className="flex flex-col gap-1">
              <Label className="text-xs">RSS 地址</Label>
              <Input
                value={form.url}
                onChange={(event) => setForm({ ...form, url: event.target.value })}
                placeholder="https://mikanani.me/RSS/Bangumi?bangumiId=xxxx"
              />
            </div>
            <div className="flex flex-col gap-1">
              <Label className="text-xs">只保留番剧名（逗号分隔，留空为全部）</Label>
              <Input
                value={form.names}
                onChange={(event) => setForm({ ...form, names: event.target.value })}
                placeholder="葬送的芙莉莲, 孤独摇滚"
              />
            </div>
            <div className="flex flex-col gap-1">
              <Label className="text-xs">排除番剧名（逗号分隔）</Label>
              <Input
                value={form.exclude_names}
                onChange={(event) => setForm({ ...form, exclude_names: event.target.value })}
              />
            </div>
            <div className="flex flex-col gap-1">
              <Label className="text-xs">首次运行</Label>
              <select
                className={selectClass}
                value={form.initial}
                onChange={(event) =>
                  setForm({ ...form, initial: event.target.value as FeedForm["initial"] })
                }
              >
                <option value="latest">只下最新一集</option>
                <option value="all">全部下载</option>
                <option value="none">只标记已见</option>
              </select>
            </div>
            <div className="flex items-end gap-4">
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  className="size-4"
                  checked={form.one_per_episode}
                  onChange={(event) => setForm({ ...form, one_per_episode: event.target.checked })}
                />
                同一集只下一个版本
              </label>
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  className="size-4"
                  checked={form.enabled}
                  onChange={(event) => setForm({ ...form, enabled: event.target.checked })}
                />
                启用
              </label>
            </div>
          </div>
          <div className="flex gap-2">
            <Button size="sm" disabled={busy} onClick={submit}>
              <Plus /> {form.index === null ? "添加" : "保存"}
            </Button>
            <Button size="sm" variant="outline" disabled={busy} onClick={previewForm}>
              <Eye /> 预览解析结果
            </Button>
            {form.index !== null && (
              <Button size="sm" variant="ghost" onClick={() => setForm(emptyForm)}>
                <X /> 取消编辑
              </Button>
            )}
          </div>

          {preview && (
            <div className="rounded-lg border p-3 text-sm">
              <div className="mb-2 flex items-center justify-between">
                <span className="font-medium">
                  {previewName}：共 {preview.total} 条
                </span>
                <Button size="xs" variant="ghost" onClick={() => setPreview(null)}>
                  <X />
                </Button>
              </div>
              <div className="mb-2 flex flex-wrap items-center gap-1 text-muted-foreground">
                <span>番剧名（点击加入白名单）：</span>
                {previewNames.length ? (
                  previewNames.map((name) => (
                    <button
                      key={name}
                      type="button"
                      onClick={() => toggleName(name)}
                      className={`rounded border px-1.5 py-0.5 text-xs ${
                        splitList(form.names).includes(name) ? "bg-muted" : ""
                      }`}
                    >
                      {name}
                    </button>
                  ))
                ) : (
                  <span>（未识别）</span>
                )}
              </div>
              <div className="max-h-64 overflow-y-auto">
                {preview.items.map((item, index) => (
                  <div key={index} className="truncate text-xs text-muted-foreground">
                    [{item.group || "-"}] {item.name || item.title} E{item.episode || "?"}
                  </div>
                ))}
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
