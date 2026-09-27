# amine-downloader

从 **BT / Kazumi 风格订阅源**（蜜柑计划 Mikan、動漫花園 DMHY 等 RSS）自动下载番剧，
把种子标题解析成结构化信息，并按**标准格式重命名**成易于刮削的文件名。

同时支持 **Kazumi 规则解析下载**：用 Kazumi 的自定义规则（XPath / JSON API）在第三方站点
搜索番剧、解析剧集，用隐身浏览器嗅探出 `m3u8` / `mp4` 流，再交给 **aria2** 下载。

支持 **qBittorrent**（WebUI API）与 **aria2**（JSON-RPC）两种下载器。

参考了 [Auto_Bangumi](https://github.com/EstrellaXD/Auto_Bangumi) 的标题解析 / 订阅思路，
以及 [Kazumi](https://github.com/Predidit/Kazumi) 的规则解析、命名与下载体验。

## 特性

- **RSS 订阅**：解析 Mikan / DMHY 等 RSS 2.0 / Atom 源，自动识别 `enclosure` 种子链接或 `description` 中的磁力链接。
- **标题解析**：从 `[字幕组] 番剧名 - 05 [1080p][Baha][WEB-DL][CHT]` 这类标题中提取字幕组、番剧名、季、集、分辨率、语言、来源。
- **标准重命名**：默认输出 `[字幕组] 番剧名 S01E05 [1080p].mkv`，模板可自定义。
- **去重**：使用本地 SQLite 记录已下载条目，重复运行不会重复下载。
- **双下载器**：qBittorrent 通过 WebUI API 在服务端重命名；aria2 通过 JSON-RPC 下载并在完成后于本地重命名。
- **Kazumi 规则解析下载**：兼容 Kazumi 规则仓库的 JSON（XPath 规则与 API/JSONPath 规则），
  用 CloakBrowser（隐身 Chromium）嗅探播放页的视频流，aria2 分片下载 `m3u8` 后合并、重命名。
- **零重依赖**：核心仅依赖 `httpx`；解析下载额外用到 `lxml` 与 `cloakbrowser`。

## 安装

```bash
git clone <this repo>
cd amine-downloader
uv sync
```

安装后可直接使用 `uv run amine-downloader ...`，或在已激活的环境中调用 `amine-downloader`。

## 快速开始

```bash
# 1. 生成默认配置文件
amine-downloader init

# 2. 编辑配置文件，填入下载器信息与订阅源
#    Windows: %APPDATA%\amine-downloader\config.toml
#    其它系统: ~/.config/amine-downloader/config.toml
amine-downloader config

# 3. 测试下载器连接
amine-downloader status

# 4. 预览某个订阅源有哪些剧集
amine-downloader rss "Mikan - 葬送的芙莉莲"

# 5. 只看看会下载什么（不实际下载）
amine-downloader run --dry-run

# 6. 下载所有订阅的新剧集
amine-downloader run

# 7. 对已完成的任务补做重命名（aria2 必须，qBittorrent 可选）
amine-downloader rename
```

配合定时任务即可实现追番，例如 crontab：

```cron
*/30 * * * * cd /path/to/amine-downloader && amine-downloader run
```

> 首次运行时数据库为空，`run` 会把订阅源里的**全部历史剧集**都加入下载。
> 建议第一次先执行 `amine-downloader run --dry-run` 查看，或用 `--limit 1` 只下载最新一集。

## 配置文件

默认路径：

| 平台 | 路径 |
| --- | --- |
| Windows | `%APPDATA%\amine-downloader\config.toml` |
| Linux / macOS | `~/.config/amine-downloader/config.toml` |

可用环境变量覆盖：`AMINE_DOWNLOADER_CONFIG`（配置文件）、`AMINE_DOWNLOADER_CONFIG_DIR`（配置目录）、
`AMINE_DOWNLOADER_DATA`（数据库文件）。

```toml
[app]
# 下载器: "qbittorrent" 或 "aria2"
downloader = "qbittorrent"
# 是否重命名
rename = true
# 重命名模板
rename_template = "[{group}] {title} S{season}E{episode} [{resolution}]"
# 保存路径，留空使用下载器默认路径
save_path = ""
# 分类 / 标签
category = "amine"
# 集数偏移：字幕组集数与番剧集数不一致时使用
episode_offset = 0
# 只下载指定分辨率（留空表示全部）
resolution_preference = ["1080p", "2160p"]

[qbittorrent]
url = "http://127.0.0.1:8080"
# qBittorrent >= 5.2 推荐使用 API Key（设置后优先于用户名/密码）
api_key = ""
username = "admin"
password = "adminadmin"

[aria2]
rpc_url = "http://127.0.0.1:6800/jsonrpc"
secret = ""
# aria2 侧下载目录（作为 dir 选项传给 aria2）
download_dir = ""
# 同一目录在本机看到的路径（映射盘 / UNC），用于下载完成后重命名磁力等文件
local_dir = ""

# 订阅源，可添加多个
[[rss]]
name = "Mikan - 葬送的芙莉莲"
url = "https://mikanani.me/RSS/Bangumi?bangumiId=xxxx"
enabled = true
```

### 获取订阅源地址

- **蜜柑计划 Mikan**：进入番剧详情页，右上角 RSS 图标，复制形如
  `https://mikanani.me/RSS/Bangumi?bangumiId=xxxx` 的地址。
- **動漫花園 DMHY**：搜索后使用页面上的 RSS 按钮，得到形如
  `https://share.dmhy.org/topics/rss/...` 的地址。
- 任意提供 `<enclosure>` 种子链接或描述中包含磁力链接的 RSS 2.0 / Atom 源都可以使用。

## 重命名模板

模板中的变量会在渲染后自动清理空括号、多余空格与非法字符。

| 变量 | 含义 | 示例 |
| --- | --- | --- |
| `{group}` | 字幕组 | `Lilith-Raws` |
| `{title}` | 番剧名 | `葬送的芙莉莲` |
| `{season}` | 季，默认补零到 2 位 | `01` |
| `{episode}` | 集，默认补零到 2 位 | `05` |
| `{resolution}` | 分辨率 | `1080p` |
| `{language}` | 语言 | `CHS` / `CHT` / `CHS&CHT` |
| `{source}` | 来源 | `WEB-DL` / `BDRip` |
| `{extra}` | 其它标签 | `AAC AVC` |
| `{raw}` | 原始标题 | `[Lilith-Raws] ...` |

补零宽度可以调整：`{episode:3}` → `005`，`{season:1}` → `2`。

示例：

```toml
# 默认
rename_template = "[{group}] {title} S{season}E{episode} [{resolution}]"
# => [Lilith-Raws] 葬送的芙莉莲 S01E05 [1080p].mkv

# 更接近 Kazumi 的风格
rename_template = "[{group}] {title} - {episode}"
# => [Lilith-Raws] 葬送的芙莉莲 - 05.mkv
```

先用 `parse` 命令预览效果：

```bash
amine-downloader parse "[Lilith-Raws] 葬送的芙莉莲 - 05 [1080p][Baha][WEB-DL][CHT]"
```

## 命令参考

| 命令 | 说明 |
| --- | --- |
| `init [--path P] [--force]` | 生成默认配置文件 |
| `config` | 显示配置文件路径与内容 |
| `parse TITLE... [--template T] [--json]` | 解析标题并预览重命名结果 |
| `rss SOURCE [--json]` | 查看订阅源剧集列表（不下载） |
| `download SOURCE [--title T] [--save-path P] [--no-rename] [--paused]` | 下载单个磁力 / 种子 |
| `run [FEED...] [--limit N] [--dry-run] [--no-rename]` | 拉取所有订阅并下载新剧集 |
| `list [--json]` | 列出下载器中的任务 |
| `status` | 测试下载器连接 |
| `rename [--id ID] [--limit N]` | 对任务执行重命名 |
| `remove ID [--delete-files]` | 移除任务 |
| `kazumi rules [--json]` | 列出已导入的 Kazumi 规则 |
| `kazumi import SOURCE...` | 导入规则（本地文件或 URL） |
| `kazumi search KEYWORD [--rule R] [--json]` | 按规则搜索番剧 |
| `kazumi chapters KEYWORD [--rule R] [--hit N] [--json]` | 查看播放线路与剧集 |
| `kazumi download KEYWORD [--rule R] [--road N] [--episode N] [--quality Q] [--url U] [--dry-run]` | 解析并下载（交给 aria2） |
| `kazumi run [NAME...] [--limit N] [--dry-run]` | 按 `[[kazumi.subscribe]]` 批量追番（自动跳过已下载） |

全局参数 `--config PATH` 可指定配置文件。顶层 `run` 会同时处理 RSS 订阅与 Kazumi 解析订阅
（用 `--no-kazumi` 可跳过解析部分）。

`SOURCE` 支持：

- 磁力链接：`magnet:?xt=urn:btih:...`
- 种子 URL：`https://.../xxx.torrent`
- 本地文件：`./xxx.torrent`

## 下载器配置

### qBittorrent

**qBittorrent ≥ 5.2（推荐用 API Key）**

1. 打开 **工具 → 选项 → WebUI → API Key**，点击生成图标，得到形如 `qbt_xxxxxxxx` 的密钥。
2. 填入配置的 `api_key` 字段即可，无需用户名 / 密码。

API Key 采用 `Authorization: Bearer <key>` 无状态认证；注意它**不能**用于访问 `auth/login` 等认证接口，
本工具在配置了 `api_key` 时会自动跳过登录。

**qBittorrent < 5.2（用户名 / 密码）**

1. 打开 **工具 → 选项 → WebUI**，启用 Web 用户界面并设置用户名 / 密码。
2. 将地址、用户名、密码填入配置。

重命名通过 `torrents/renameFile` 在服务端完成。添加任务后 qBittorrent 可能仍在初始化元数据，
本工具会自动重试几次；若仍未完成，可稍后运行 `amine-downloader rename`。

### aria2

以启用 RPC 的方式启动，例如：

```bash
aria2c --enable-rpc --rpc-listen-port=6800 --rpc-secret=your_token
```

- `rpc_url` 填 `http://127.0.0.1:6800/jsonrpc`，`secret` 填 `your_token`。

**重命名方式（不需要本地磁盘访问）：**

- **`.torrent` 种子**（Mikan / DMHY 的 enclosure 就是这种）：本程序会解析种子内的文件列表，
  用 aria2 的 **`index-out`** 在**添加任务时**就把视频文件命名成标准格式。远程 aria2 也能用。
- **直链 / HLS**：用 `out` 在添加时命名。
- **磁力链接**：添加时还拿不到文件列表，aria2 无法用 `index-out` 预先命名。
  改为**下载完成后重命名**：`amine-downloader rename` 会等任务完成后，按标准格式重命名磁盘上的文件。
  这要求本程序能访问下载目录——如果 aria2 在另一台机器/容器里，请用 `[aria2].local_dir`
  把 aria2 侧路径映射到本机路径（映射盘或 UNC），例如：
  ```toml
  [aria2]
  download_dir = "/wenwen/downloads"          # aria2 容器内路径
  local_dir    = "Z:/wenwen/downloads"        # 本机映射盘（或 \\192.168.11.50\wenwen\downloads）
  ```

> `index-out` 的索引是 **1-based**（与 aria2 `--show-files` 一致），本程序已自动换算。

**推荐使用 [aria2-next](https://github.com/AnInsomniacy/aria2-next)**（本程序完全兼容其 JSON-RPC）：

- 原生支持 **HLS (.m3u8) / DASH**，单任务下载并 remux 成 mp4/mkv，无需 ffmpeg；
- 注意它需要显式指定状态目录，否则 BT 会报 `Unable to create BitTorrent state directory`：

```bash
aria2-next --enable-rpc --rpc-listen-port=6800 --rpc-secret=your_token \
  --dir=/downloads --state-dir=/var/lib/aria2-next
```

## Kazumi 规则解析下载

除了 BT/RSS，本工具还实现了 Kazumi 的**规则解析下载**（在线站点取流），流程如下：

```
规则 JSON ──► 搜索作品 ──► 解析播放线路/剧集 ──► CloakBrowser 嗅探 m3u8/mp4
                                                          │
                              ┌───────────────────────────┴───────────────────────┐
                              ▼                                                   ▼
                   直接文件 (mp4/mp3)                                     m3u8 (HLS)
                    aria2.addUri                                         解析播放列表
                                                                        aria2 逐片下载
                                                                     合并 (+ffmpeg 转 mp4)
                                                          ▼
                                                   按标准格式重命名
```

### 规则来源

兼容 [Predidit/KazumiRules](https://github.com/Predidit/KazumiRules) 的规则 JSON：

- **XPath 规则**（api 1–5）：`searchURL` / `searchList` / `searchName` / `searchResult` / `chapterRoads` / `chapterResult`
- **API 规则**（api 8）：`searchApiConfig` / `chapterApiConfig`，使用受限 JSONPath（`$.a.b[*]`、`$['x-y']`）

```bash
# 导入规则（支持本地文件或 URL，也支持一次导入多条的 JSON 数组）
amine-downloader kazumi import AGE.json
amine-downloader kazumi import https://raw.githubusercontent.com/Predidit/KazumiRules/main/giriGiriLove.json

# 查看已导入的规则
amine-downloader kazumi rules
```

规则默认放在配置目录下的 `kazumi-rules/`，可用 `[kazumi].rules_dir` 或环境变量
`AMINE_DOWNLOADER_RULES_DIR` 修改。

### 使用

```bash
# 搜索
amine-downloader kazumi search "葬送的芙莉莲" --rule AGE

# 查看播放线路与剧集
amine-downloader kazumi chapters "葬送的芙莉莲" --rule AGE --hit 0

# 解析并下载（交给 aria2）
amine-downloader kazumi download "葬送的芙莉莲" --rule AGE --road 0 --episode 1

# 只解析看会得到什么流地址，不下载
amine-downloader kazumi download "葬送的芙莉莲" --dry-run

# 已知流地址时跳过浏览器嗅探
amine-downloader kazumi download "葬送的芙莉莲" --url "https://cdn.example/index.m3u8"

# 按订阅批量追番（先在配置里写 [[kazumi.subscribe]]）
amine-downloader kazumi run
amine-downloader kazumi run --dry-run --limit 2

# 顶层 run 会同时处理 RSS 订阅与 Kazumi 解析订阅
amine-downloader run
```

### 配置

```toml
[kazumi]
# 规则目录，留空使用 <配置目录>/kazumi-rules
rules_dir = ""
# 解析下载的重命名模板（解析源通常没有字幕组信息）
rename_template = "{title} S{season}E{episode}"
# 优先清晰度（匹配 m3u8 的 RESOLUTION / URL）
preferred_quality = "1080p"
# HLS 下载方式：auto / native / segments
media_mode = "auto"
# 浏览器嗅探（CloakBrowser）
headless = true
humanize = false
sniff_timeout = 30
# CloakBrowser 许可证 key（可选）
license_key = ""
# 浏览器嗅探临时目录（系统临时目录不可写时指定）
temp_dir = ""
# 代理（可选）
proxy = ""
# ffmpeg 路径（可选，用于把 m3u8 合并结果转成 mp4 / 下载加密流）
ffmpeg = ""
# aria2 同时下载的分片数量
concurrency = 8

# 解析追番订阅
[[kazumi.subscribe]]
name = "葬送的芙莉莲"
rule = "AGE"
road = 0
quality = "1080p"
```

### 说明与限制

- **首次嗅探会下载 CloakBrowser 的 Chromium 二进制（约 200MB）**，请预留时间与磁盘。
  可先运行 `uv run python -m cloakbrowser install` 预下载。
- CloakBrowser 免费版在启动时会打印一段横幅，一次 `run` 只启动一次浏览器（整批剧集复用同一实例），
  因此横幅只会出现一次。
- 若系统临时目录不可写（或想放到大磁盘），设置 `[kazumi].temp_dir` 指定一个可写目录。
- **HLS (m3u8) 下载方式**由 `[kazumi].media_mode` 决定：
  - `auto`（默认）：如果下载器是 **aria2-next**（原生支持 HLS/DASH），就用**单个媒体任务**
    下载并直接 remux 成 mp4（无需 ffmpeg、无需本地临时目录，远程可用）；否则退回分片方案。
  - `native`：强制原生；下载器不支持时报错。
  - `segments`：始终用「解析 m3u8 → 逐分片 `aria2.addUri` → 二进制合并」的兜底方案（原版 aria2）。
- 原生模式会用 `media-pause-after-probe` 探测清晰度，按 `preferred_quality` 选择最接近的视频轨；
  原版 aria2 的分片方案则按 `RESOLUTION`/URL 选择。
- **加密的 HLS（AES-128）** aria2 无法解密，会改用 **ffmpeg** 直接下载（需安装并配置 ffmpeg）。
- 合并出的文件默认是 `.ts`；若检测到 ffmpeg 会自动转封装为 `.mp4`。
- 只支持规则里能解析出的剧集页面；若站点要求登录/验证码，需要规则自带 Cookie 或改用其他来源。
- 站点结构与反爬策略随时可能变化，规则失效时请更新规则或改用 BT/RSS 方式。
- 请遵守当地法律法规与站点条款，仅将本工具用于个人合法用途。

## 工作原理

```
RSS 源 ──► rss.py 解析条目 ──► parser.py 解析标题
                                      │
                                      ▼
                              renamer.py 生成标准名
                                      │
              ┌───────────────────────┴───────────────────────┐
              ▼                                               ▼
   qbittorrent.py (WebUI API)                      aria2.py (JSON-RPC)
   添加种子 + renameFile/renameFolder              添加种子 + 完成后本地重命名
              │                                               │
              └───────────────────────┬───────────────────────┘
                                      ▼
                         store.py (SQLite 去重与状态记录)

Kazumi 解析下载（kazumi/）：
  规则 JSON ──► client.py (lxml XPath / JSONPath) ──► 剧集页面
                                                       │
                                          sniffer.py (CloakBrowser 嗅探)
                                                       │
                          ┌────────────────────────────┴────────────────────────────┐
                          ▼                                                         ▼
              直链 → aria2(addUri + out)                          m3u8 → aria2-next 原生媒体任务
              BT .torrent → aria2(index-out)                      或 分片下载 + 合并（兜底）
```

目录结构：

```
src/amine_downloader/
├── cli.py            # 命令行入口
├── config.py         # 配置加载 / 生成
├── parser.py         # 种子标题解析
├── renamer.py        # 标准命名模板
├── rss.py            # RSS 抓取与解析
├── bencode.py        # 计算种子 info hash
├── store.py          # SQLite 记录
├── service.py        # 订阅 → 下载 → 重命名 编排
├── models.py         # 数据模型
├── errors.py         # 统一异常
├── downloaders/
│   ├── base.py       # 下载器抽象
│   ├── qbittorrent.py
│   └── aria2.py
└── kazumi/           # Kazumi 规则解析下载
    ├── rule.py       # 规则模型与规则库
    ├── client.py     # XPath / JSONPath 规则执行
    ├── jsonpath.py   # 受限 JSONPath
    ├── hls.py        # m3u8 播放列表解析
    ├── sniffer.py    # CloakBrowser 视频流嗅探
    └── download.py   # 解析 → 嗅探 → aria2 → 合并/重命名
```

## 与 Auto_Bangumi / Kazumi 的关系

- **标题解析**借鉴 Auto_Bangumi 的 `raw_parser`：从字幕组前缀、中文「第 N 话 / 第 N 季」、`SxxExx`、
  方括号数字等模式中提取季集信息，并忽略年份、码率等噪声标签。
- **订阅与去重**借鉴 Auto_Bangumi：RSS → 过滤 → 添加 → 记录，重复运行幂等。
- **规则解析**直接兼容 Kazumi 的规则 JSON（XPath 与 API/JSONPath），并复刻其「规则解析 → 播放页 →
  视频嗅探」的数据流；嗅探部分用 CloakBrowser 替代 Kazumi 的原生 WebView。
- **命名**借鉴 Kazumi 的可配置模板思想，默认采用社区通用的 `[字幕组] 番剧名 SxxExx [分辨率]` 形式。
- 本项目是**轻量 CLI**，不包含 Web 界面、弹幕、番剧数据库与季度番剧表；如需完整管理面板请使用 Auto_Bangumi，
  如需在线观看与弹幕请使用 Kazumi。

## 开发

```bash
uv sync
uv run pytest
```

## 已知限制

- 只做重命名与下载，不做媒体库刮削（可配合 Plex / Jellyfin 的命名规则使用）。
- `resolution_preference` 非空时，标题中识别不出分辨率的条目会被跳过。
- aria2 的 `.torrent` 重命名在**添加任务时**用 `index-out` 完成（远程也可用）；
  **磁力链接**则在**下载完成后**由 `rename` 重命名磁盘文件，需要能访问下载目录（用 `[aria2].local_dir` 做路径映射）。
- 批量种子的每个视频文件会分别解析文件名并重命名；文件夹名保持不变。
- 标题解析是启发式的，冷门命名可能识别不准，可先用 `parse` 验证。
- Kazumi 的 XPath 规则引擎用 lxml 实现，位置索引（如 `//div[2]`）的语义与 Kazumi 自带的
  `xpath_selector` 可能存在细微差异；如某条规则解析不出结果，可换用 API 规则或反馈。
- 解析下载依赖第三方站点，站点改版或反爬会导致规则失效。
