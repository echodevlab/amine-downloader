import { useEffect, useState } from "react"
import { toast } from "sonner"
import { PlugZap, RefreshCw, Save } from "lucide-react"

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
import { api } from "@/lib/api"

type Section = "app" | "aria2" | "qbittorrent" | "library" | "kazumi"
type Form = Record<Section, Record<string, unknown>>

const selectClass =
  "h-8 rounded-lg border border-border bg-background px-2 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50"

function Field({
  label,
  value,
  onChange,
  placeholder,
  type = "text",
}: {
  label: string
  value: unknown
  onChange: (value: string) => void
  placeholder?: string
  type?: string
}) {
  return (
    <div className="flex flex-col gap-1">
      <Label className="text-xs">{label}</Label>
      <Input
        value={value === undefined || value === null ? "" : String(value)}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        type={type}
      />
    </div>
  )
}

function Check({
  label,
  checked,
  onChange,
}: {
  label: string
  checked: unknown
  onChange: (value: boolean) => void
}) {
  return (
    <label className="flex items-center gap-2 text-sm">
      <input
        type="checkbox"
        className="size-4"
        checked={Boolean(checked)}
        onChange={(event) => onChange(event.target.checked)}
      />
      {label}
    </label>
  )
}

export default function SettingsPage() {
  const [form, setForm] = useState<Form | null>(null)
  const [configPath, setConfigPath] = useState("")
  const [aria2Secret, setAria2Secret] = useState("")
  const [qbApiKey, setQbApiKey] = useState("")
  const [qbPassword, setQbPassword] = useState("")
  const [busy, setBusy] = useState(false)

  async function load() {
    try {
      const data = await api.getConfig()
      setForm({
        app: data.app,
        aria2: data.aria2,
        qbittorrent: data.qbittorrent,
        library: data.library,
        kazumi: data.kazumi,
      })
      setConfigPath(data.config_path)
    } catch (error) {
      toast.error((error as Error).message)
    }
  }

  useEffect(() => {
    load()
  }, [])

  function patch(section: Section, key: string, value: unknown) {
    setForm((current) =>
      current ? { ...current, [section]: { ...current[section], [key]: value } } : current,
    )
  }

  function body(): Record<string, unknown> {
    if (!form) return {}
    return {
      app: form.app,
      aria2: { ...form.aria2, secret: aria2Secret },
      qbittorrent: { ...form.qbittorrent, api_key: qbApiKey, password: qbPassword },
      library: form.library,
      kazumi: form.kazumi,
    }
  }

  async function save() {
    setBusy(true)
    try {
      const result = await api.saveConfig(body())
      toast.success(`已保存到 ${result.config_path}（旧文件备份为 .bak）`)
      setAria2Secret("")
      setQbApiKey("")
      setQbPassword("")
      load()
    } catch (error) {
      toast.error((error as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function test() {
    setBusy(true)
    try {
      const result = await api.testConfig(body())
      toast.success(`${result.downloader}：${result.connection}`)
    } catch (error) {
      toast.error((error as Error).message)
    } finally {
      setBusy(false)
    }
  }

  if (!form) {
    return (
      <Card>
        <CardContent className="pt-4 text-sm text-muted-foreground">加载中…</CardContent>
      </Card>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <CardTitle>下载器</CardTitle>
          <CardDescription>{configPath}</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="flex flex-col gap-1">
            <Label className="text-xs">当前下载器</Label>
            <select
              className={selectClass}
              value={String(form.app.downloader ?? "aria2")}
              onChange={(event) => patch("app", "downloader", event.target.value)}
            >
              <option value="aria2">aria2</option>
              <option value="qbittorrent">qBittorrent</option>
            </select>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" disabled={busy} onClick={save}>
              <Save /> 保存
            </Button>
            <Button size="sm" variant="outline" disabled={busy} onClick={test}>
              <PlugZap /> 测试连接
            </Button>
            <Button size="sm" variant="outline" onClick={load}>
              <RefreshCw /> 重新加载
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>aria2</CardTitle>
          <CardDescription>JSON-RPC</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-2">
          <Field
            label="RPC 地址"
            value={form.aria2.rpc_url}
            onChange={(value) => patch("aria2", "rpc_url", value)}
            placeholder="http://aria2-next:6800/jsonrpc"
          />
          <Field
            label={form.aria2.secret_set ? "密钥（已设置，留空不改）" : "密钥"}
            value={aria2Secret}
            onChange={setAria2Secret}
            type="password"
            placeholder="CHANGE_ME_RPC_SECRET"
          />
          <Field
            label="下载目录（aria2 侧）"
            value={form.aria2.download_dir}
            onChange={(value) => patch("aria2", "download_dir", value)}
            placeholder="/wenwen/media/acg"
          />
          <Field
            label="本机映射目录（可选）"
            value={form.aria2.local_dir}
            onChange={(value) => patch("aria2", "local_dir", value)}
            placeholder="/wenwen/media/acg"
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>qBittorrent</CardTitle>
          <CardDescription>WebUI API（切换下载器为 qBittorrent 时生效）</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-2">
          <Field
            label="地址"
            value={form.qbittorrent.url}
            onChange={(value) => patch("qbittorrent", "url", value)}
            placeholder="http://qbittorrent:8085"
          />
          <Field
            label={form.qbittorrent.api_key_set ? "API Key（已设置，留空不改）" : "API Key（>=5.2 推荐）"}
            value={qbApiKey}
            onChange={setQbApiKey}
            type="password"
          />
          <Field
            label="用户名"
            value={form.qbittorrent.username}
            onChange={(value) => patch("qbittorrent", "username", value)}
            placeholder="admin"
          />
          <Field
            label={form.qbittorrent.password_set ? "密码（已设置，留空不改）" : "密码"}
            value={qbPassword}
            onChange={setQbPassword}
            type="password"
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>媒体库</CardTitle>
          <CardDescription>下载直接入库，Jellyfin 可直接识别</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-2">
          <div className="md:col-span-2">
            <Check
              label="启用直接入库"
              checked={form.library.enabled}
              onChange={(value) => patch("library", "enabled", value)}
            />
          </div>
          <Field
            label="媒体库根目录"
            value={form.library.root}
            onChange={(value) => patch("library", "root", value)}
            placeholder="/wenwen/media/acg"
          />
          <Field
            label="本机映射目录（可选）"
            value={form.library.local_root}
            onChange={(value) => patch("library", "local_root", value)}
            placeholder="/wenwen/media/acg"
          />
          <Field
            label="剧集文件夹模板"
            value={form.library.series_template}
            onChange={(value) => patch("library", "series_template", value)}
            placeholder="{title}"
          />
          <Field
            label="季文件夹模板"
            value={form.library.season_template}
            onChange={(value) => patch("library", "season_template", value)}
            placeholder="Season {season}"
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>解析下载（Kazumi）</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-2">
          <Field
            label="规则目录"
            value={form.kazumi.rules_dir}
            onChange={(value) => patch("kazumi", "rules_dir", value)}
          />
          <Field
            label="优先清晰度"
            value={form.kazumi.preferred_quality}
            onChange={(value) => patch("kazumi", "preferred_quality", value)}
            placeholder="1080p"
          />
          <div className="flex flex-col gap-1">
            <Label className="text-xs">HLS 方式</Label>
            <select
              className={selectClass}
              value={String(form.kazumi.media_mode ?? "auto")}
              onChange={(event) => patch("kazumi", "media_mode", event.target.value)}
            >
              <option value="auto">auto</option>
              <option value="native">native</option>
              <option value="segments">segments</option>
            </select>
          </div>
          <Field
            label="临时目录"
            value={form.kazumi.temp_dir}
            onChange={(value) => patch("kazumi", "temp_dir", value)}
          />
          <div className="md:col-span-2">
            <Check
              label="首次嗅探自动下载 CloakBrowser"
              checked={form.kazumi.auto_install_browser}
              onChange={(value) => patch("kazumi", "auto_install_browser", value)}
            />
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
