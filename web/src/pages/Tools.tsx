import { useState } from "react"
import { toast } from "sonner"
import { Wand2 } from "lucide-react"

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
import { api, type ParseResult } from "@/lib/api"

export default function ToolsPage() {
  const [title, setTitle] = useState("")
  const [template, setTemplate] = useState("")
  const [result, setResult] = useState<ParseResult | null>(null)
  const [busy, setBusy] = useState(false)

  async function parse() {
    if (!title.trim()) return
    setBusy(true)
    try {
      setResult(await api.parse(title.trim(), template.trim() || undefined))
    } catch (error) {
      toast.error((error as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>标题解析预览</CardTitle>
        <CardDescription>粘贴种子标题，查看解析结果与重命名效果</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div className="flex flex-col gap-1">
          <Label className="text-xs">原始标题</Label>
          <Input
            placeholder="[Lilith-Raws] 葬送的芙莉莲 - 05 [1080p][Baha][WEB-DL][CHT]"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            onKeyDown={(event) => event.key === "Enter" && parse()}
          />
        </div>
        <div className="flex flex-col gap-1">
          <Label className="text-xs">重命名模板（留空用配置默认）</Label>
          <Input
            placeholder="[{group}] {title} S{season}E{episode} [{resolution}]"
            value={template}
            onChange={(event) => setTemplate(event.target.value)}
          />
        </div>
        <div>
          <Button size="sm" disabled={busy} onClick={parse}>
            <Wand2 /> 解析
          </Button>
        </div>

        {result && (
          <div className="rounded-lg border p-3 text-sm">
            <div className="mb-2">
              <span className="text-muted-foreground">重命名：</span>
              <span className="font-medium">{result.renamed}</span>
            </div>
            <div className="grid grid-cols-2 gap-x-4 gap-y-1 md:grid-cols-3">
              {(
                [
                  ["字幕组", result.parsed.group],
                  ["番剧名", result.parsed.title],
                  ["季", String(result.parsed.season)],
                  ["集", result.parsed.episode],
                  ["分辨率", result.parsed.resolution],
                  ["语言", result.parsed.language],
                  ["来源", result.parsed.source],
                ] as const
              ).map(([key, value]) => (
                <div key={key} className="flex gap-2">
                  <span className="text-muted-foreground">{key}</span>
                  <span>{value || "-"}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
