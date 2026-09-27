import { useEffect, useState } from "react"
import { Toaster } from "sonner"
import {
  Download,
  History,
  ListChecks,
  RefreshCw,
  Rss as RssIcon,
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
import ToolsPage from "@/pages/Tools"

const NAV = [
  { key: "downloads", label: "下载中", icon: Download },
  { key: "history", label: "历史记录", icon: History },
  { key: "rss", label: "RSS 订阅", icon: RssIcon },
  { key: "kazumi", label: "解析下载", icon: Wand2 },
  { key: "jobs", label: "任务", icon: ListChecks },
  { key: "tools", label: "工具", icon: Wrench },
] as const

type NavKey = (typeof NAV)[number]["key"]

export default function App() {
  const [page, setPage] = useState<NavKey>("downloads")
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
  }, [])

  const goToJobs = () => setPage("jobs")

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
                onClick={() => setPage(item.key)}
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
              </>
            ) : (
              <div className="text-muted-foreground">连接中…</div>
            )}
            <Button size="xs" variant="ghost" className="mt-2 w-full" onClick={loadStatus}>
              <RefreshCw /> 刷新状态
            </Button>
          </div>
        </aside>
        <main className="min-w-0 flex-1">
          {page === "downloads" && <Dashboard />}
          {page === "history" && <HistoryPage onNavigate={goToJobs} />}
          {page === "rss" && <RssPage onNavigate={goToJobs} />}
          {page === "kazumi" && <KazumiPage onNavigate={goToJobs} />}
          {page === "jobs" && <JobsPage />}
          {page === "tools" && <ToolsPage />}
        </main>
      </div>
      <Toaster theme="system" richColors position="top-center" />
    </div>
  )
}
