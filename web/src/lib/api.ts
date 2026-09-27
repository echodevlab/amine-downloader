export type TorrentInfo = {
  id: string
  name: string
  save_path: string
  progress: number
  state: string
  size: number
  category: string
  files: { path: string; size: number; progress: number }[]
}

export type DownloadTask = {
  key: string
  raw_title: string
  client: string
  torrent_id: string
  group: string
  title: string
  season: number
  episode: string
  resolution: string
  new_name: string
  source: string
  status: string
  created_at: string
  updated_at: string
}

export type RssFeed = { name: string; url: string; enabled: boolean }
export type RssRunItem = {
  title: string
  group: string
  name: string
  episode: string
  task_id: string
  skipped: string
}

export type KazumiRule = {
  name: string
  version: string
  api: string
  base_url: string
  search_mode: string
  chapter_mode: string
}

export type KazumiHit = { rule: string; name: string; source: string }
export type KazumiRoad = {
  name: string
  episodes: { name: string; page_url: string; url: string }[]
}
export type KazumiChapters = { name: string; roads: KazumiRoad[] }
export type KazumiResult = {
  title: string
  episode: string
  stream_url: string
  kind: string
  task_id: string
  output: string
  skipped: string
}
export type KazumiSubscription = {
  name: string
  rule: string
  hit: number
  road: number
  quality: string
  save_path: string
  enabled: boolean
}

export type Status = {
  downloader: string
  connection: string
  config_path: string
  rss: number
  kazumi_subscriptions: number
}

export type ParseResult = {
  parsed: {
    raw: string
    group: string
    title: string
    season: number
    episode: string
    resolution: string
    language: string
    source: string
  }
  renamed: string
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  })
  const text = await response.text()
  const data = text ? JSON.parse(text) : null
  if (!response.ok) {
    throw new Error(data?.error ?? `HTTP ${response.status}`)
  }
  return data as T
}

export const api = {
  status: () => request<Status>("/api/status"),
  torrents: () => request<TorrentInfo[]>("/api/torrents"),
  torrentAction: (id: string, action: "pause" | "resume" | "remove", deleteFiles = false) =>
    request<{ ok: boolean }>(
      `/api/torrents/${id}/${action}${action === "remove" && deleteFiles ? "?delete_files=1" : ""}`,
      { method: "POST" },
    ),
  tasks: () => request<DownloadTask[]>("/api/tasks"),
  rename: () => request<{ renamed: number }>("/api/rename", { method: "POST" }),
  rss: () => request<RssFeed[]>("/api/rss"),
  runRss: (body: { feeds?: string[]; limit?: number | null; dry_run?: boolean }) =>
    request<RssRunItem[]>("/api/run", { method: "POST", body: JSON.stringify(body) }),
  parse: (title: string, template?: string) =>
    request<ParseResult>("/api/parse", {
      method: "POST",
      body: JSON.stringify({ title, template: template || undefined }),
    }),
  kazumiRules: () => request<KazumiRule[]>("/api/kazumi/rules"),
  kazumiImport: (sources: string[]) =>
    request<{ imported: string[] }>("/api/kazumi/import", {
      method: "POST",
      body: JSON.stringify({ sources }),
    }),
  kazumiSearch: (q: string, rule?: string) =>
    request<KazumiHit[]>(
      `/api/kazumi/search?q=${encodeURIComponent(q)}${rule ? `&rule=${encodeURIComponent(rule)}` : ""}`,
    ),
  kazumiChapters: (q: string, rule: string | undefined, hit: number) =>
    request<KazumiChapters>(
      `/api/kazumi/chapters?q=${encodeURIComponent(q)}${rule ? `&rule=${encodeURIComponent(rule)}` : ""}&hit=${hit}`,
    ),
  kazumiDownload: (body: {
    keyword: string
    rule?: string
    hit?: number
    road?: number
    episode?: string
    limit?: number | null
    quality?: string
    dry_run?: boolean
    url?: string
  }) =>
    request<KazumiResult[]>("/api/kazumi/download", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  kazumiSubscriptions: () => request<KazumiSubscription[]>("/api/kazumi/subscriptions"),
  kazumiRun: (body: { names?: string[]; limit?: number | null; dry_run?: boolean }) =>
    request<KazumiResult[]>("/api/kazumi/run", { method: "POST", body: JSON.stringify(body) }),
}
