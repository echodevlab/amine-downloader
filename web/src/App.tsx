import { useEffect, useState } from "react"
import { useTheme } from "next-themes"
import { Toaster } from "sonner"
import {
  Download,
  History,
  ListChecks,
  Monitor,
  Moon,
  RefreshCw,
  Rss as RssIcon,
  Settings,
  Sun,
  Wand2,
  Wrench,
} from "lucide-react"

import { Button } from "@/components/ui/button"
import { api, type Status } from "@/lib/api"
import Dashboard from "@/pages/Dashboard"
import HistoryPage from "@/pages/History"
import JobsPage from "@/pages/Jobs"
import KazumiPage from "@/pages/Kazumi"
import RssPage from "@/pages/Rss"
import SettingsPage from "@/pages/Settings"
import ToolsPage from "@/pages/Tools"

const NAV = [
  { key: "downloads", label: "下载中", icon: Download },
  { key: "history", label: "历史记录", icon: History },
  { key: "rss", label: "RSS 订阅", icon: RssIcon },
  { key: "kazumi", label: "解析下载", icon: Wand2 },
  { key: "jobs", label: "任务", icon: ListChecks },
  { key: "tools", label: "工具", icon: Wrench },
  { key: "settings", label: "设置", icon: Settings },
] as const

type NavKey = (typeof NAV)[number]["key"]

const NAV_KEYS = NAV.map((item) => item.key) as readonly string[]

function pageFromHash(): NavKey {
  const hash = window.location.hash.replace(/^#\/?/, "")
  return NAV_KEYS.includes(hash) ? (hash as NavKey) : "downloads"
}

function ThemeToggle() {
  const { theme, setTheme } = useTheme()
  const current = theme ?? "system"
  const next = current === "light" ? "dark" : current === "dark" ? "system" : "light"
  const Icon = current === "light" ? Sun : current === "dark" ? Moon : Monitor
  const label = current === "light" ? "浅色" : current === "dark" ? "深色" : "跟随系统"
  return (
    <Button variant="ghost" size="sm" className="w-full justify-start" onClick={() => setTheme(next)}>
      <Icon /> {label}
    </Button>
  )
}

export default function App() {
  const { resolvedTheme } = useTheme()
  const [page, setPage] = useState<NavKey>(() => pageFromHash())
  const [status, setStatus] = useState<Status | null>(null)
  const [statusError, setStatusError] = useState("")

  async function loadStatus() {
    try {
      setStatus(await api.status())
      setStatusError("")
    } catch (error) {
      setStatusError((error as Error).message)
    }
  }

  useEffect(() => {
    loadStatus()
    const timer = setInterval(loadStatus, 30000)
    return () => clearInterval(timer)
  }, [])

  useEffect(() => {
    const onHash = () => setPage(pageFromHash())
    window.addEventListener("hashchange", onHash)
    return () => window.removeEventListener("hashchange", onHash)
  }, [])

  function navigate(key: NavKey) {
    window.location.hash = `#/${key}`
    setPage(key)
  }

  const goToJobs = () => navigate("jobs")

  return (
    <div className="min-h-svh bg-background text-foreground">
      <div className="mx-auto flex max-w-7xl gap-6 p-6">
        <aside className="w-56 shrink-0">
          <div className="mb-6 px-2">
            <div className="text-lg font-semibold">amine-downloader</div>
            <div className="text-xs text-muted-foreground">番剧下载管理</div>
          </div>
          <nav className="flex flex-col gap-1">
            {NAV.map((item) => (
              <Button
                key={item.key}
                variant={page === item.key ? "secondary" : "ghost"}
                className="justify-start"
                onClick={() => navigate(item.key)}
              >
                <item.icon />
                {item.label}
              </Button>
            ))}
          </nav>
          <div className="mt-6 rounded-lg border p-3 text-xs">
            {statusError ? (
              <div className="text-destructive">{statusError}</div>
            ) : status ? (
              <>
                <div className="font-medium">{status.downloader}</div>
                <div className="text-muted-foreground">{status.connection}</div>
                <div className="mt-1 text-muted-foreground">
                  RSS {status.rss} · 解析订阅 {status.kazumi_subscriptions}
                </div>
                <div className="mt-1 text-muted-foreground">
                  {status.interval ? `定时每 ${status.interval} 分钟` : "定时已关闭"}
                </div>
              </>
            ) : (
              <div className="text-muted-foreground">连接中…</div>
            )}
            <Button size="xs" variant="ghost" className="mt-2 w-full" onClick={loadStatus}>
              <RefreshCw /> 刷新状态
            </Button>
          </div>
          <div className="mt-2">
            <ThemeToggle />
          </div>
        </aside>
        <main className="min-w-0 flex-1">
          {page === "downloads" && <Dashboard />}
          {page === "history" && <HistoryPage onNavigate={goToJobs} />}
          {page === "rss" && <RssPage onNavigate={goToJobs} />}
          {page === "kazumi" && <KazumiPage onNavigate={goToJobs} />}
          {page === "jobs" && <JobsPage />}
          {page === "tools" && <ToolsPage />}
          {page === "settings" && <SettingsPage />}
        </main>
      </div>
      <Toaster theme={resolvedTheme === "dark" ? "dark" : "light"} richColors position="top-center" />
    </div>
  )
}
