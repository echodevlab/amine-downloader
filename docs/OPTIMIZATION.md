# 使用逻辑优化清单

本文整理 amine-downloader 在**使用流程 / 交互逻辑**上的问题与改进建议，按优先级排列。
每条都注明涉及的代码位置，方便后续逐项处理。

优先级说明：

- **P0**：会导致结果错误（下错、漏下、重复下）或功能名不副实
- **P1**：明显影响日常使用体验，建议近期处理
- **P2**：锦上添花

---

## P0 — 结果可能出错

### 1. Kazumi 搜索结果顺序不稳定，「选第 N 个」可能选错作品

- 现状：`search_all` 用 `as_completed` 并发收集结果（[kazumi/download.py:186-200](../src/amine_downloader/kazumi/download.py#L186-L200)），**结果顺序取决于哪个源先返回**。
- 而之后的每一步都靠**下标**重新定位作品：
  - 前端点「选集」→ `/api/kazumi/chapters?hit=i` 会**重新搜索一遍**再取 `hits[i]`（[server.py:269-273](../src/amine_downloader/server.py#L269-L273)）
  - 点「下载」→ `kazumi_download` 任务**又搜一遍**再取 `hits[hit_index]`（[kazumi/download.py:238-243](../src/amine_downloader/kazumi/download.py#L238-L243)）
  - 订阅里的 `hit = 0` 同理，没指定 `rule` 时每次运行可能落到不同的源
- 后果：界面上看到的是 A 作品，实际下载的可能是 B 作品。
- 建议：
  1. 搜索结果按规则名 + 原始顺序排序，保证顺序稳定（最小改动）；
  2. 更好的做法是前端把选中项的 `rule + source` 直接传给后端，后端跳过搜索、直接用 `source` 拉剧集。订阅也改为保存 `rule + source`，搜索只在「添加订阅」时做一次。
  3. 顺带还能省掉每一步都并发搜全部源的耗时。

### 2. 「取消任务」按钮实际上不起作用

- 现状：`job.cancel` 被传给了 work 函数，但 `DownloadService.run` / `KazumiService.run` / `download` 都**从不检查**它（全仓库只有 [jobs.py](../src/amine_downloader/jobs.py) 和 [server.py](../src/amine_downloader/server.py) 引用 `cancel`）。
- 后果：用户点了取消，任务照常跑完，最后状态只是被标成 `cancelled`，看起来像是取消成功了，其实该下的都已经下了。
- 建议：把 `cancel` 传进各个 `run` / `download`，在每集 / 每个源的循环开头检查 `cancel.is_set()`，命中就提前返回并记一条 `log`。

### 3. Kazumi 任务在 aria2 刚接收时就被标记为「已下载」

- 现状：`_record(... status="downloaded")` 在 `add_uri` / `add_media` 返回 GID 后**立即**写入（[kazumi/download.py:416](../src/amine_downloader/kazumi/download.py#L416)、[:480](../src/amine_downloader/kazumi/download.py#L480)）。
- 后果：aria2 侧下载失败（链接过期、403、磁盘满）后，追番订阅会因为 `store.has(key)` 永久跳过这一集，而且不会有任何提示。
- 建议：先记为 `added`，由后续的状态同步（见第 6 条）根据 aria2 的 `complete / error` 更新为 `downloaded / failed`；`failed` 的不算进去重，下次追番自动重试。

### 4. 首次订阅会把整季历史剧集全部下载

- 现状：README 里提醒「首次运行会下载全部历史剧集」，只能靠用户记得先 `--dry-run` 或 `--limit 1`。Web UI 的「下载新剧集」按钮默认 limit 为空，也就是全部下载。
- 后果：新加一个 Mikan 聚合源，一次推几十个种子进下载器，是很常见的误操作。
- 建议：给订阅加一个 `since` / `initial` 策略，例如：
  - `initial = "latest"`（默认）：第一次运行只下最新一集，其余标记为已见；
  - `initial = "all"`：保持现在的行为；
  - `initial = "none"`：只标记为已见，从下一集开始追。

### 5. 同一集的多个版本会全部下载

- 现状：去重键是 `guid or torrent_url or title`（[models.py:70-71](../src/amine_downloader/models.py#L70-L71)），**按种子**去重而不是**按剧集**去重。
- 后果：Mikan 的番剧聚合 RSS 里同一集有多个字幕组、1080p/720p、简/繁多个版本，只要没配 `names` / `resolution_preference` 就会全部下载，重命名后还会撞名（结果变成 `xxx - 2.mkv`）。
- 建议：订阅增加 `one_per_episode = true`，按 `(title, season, episode)` 去重；同一集有多个候选时，按「分辨率偏好 → 发布时间」选一个。

---

## P1 — 日常体验

### 6. 没有内置定时，追番要靠外部 cron

- 现状：追番要靠 crontab 调 `amine-downloader run`。Docker 镜像里只跑了 `serve`，**没有 cron**，所以用 compose 部署的用户实际上没有自动追番。
- 建议：`serve` 里加一个后台调度线程，配置 `[app] interval = 30`（分钟，0 表示关闭），定时提交 `rss_run`、`kazumi_run` 和「重命名已完成任务」这几个 job。任务页可以直接看到每次定时运行的记录。

### 7. aria2 磁力任务下载完不会自动重命名

- 现状：aria2 需要等下载完成后在磁盘上改名（`rename_requires_complete`）。`rss_run` 结束时虽然调了一次 `rename_all()`（[server.py:355-356](../src/amine_downloader/server.py#L355-L356)），但那时任务刚加进去，**肯定还没下完**，等于白调。之后只能手动点「历史记录 → 重命名已完成任务」。
- 建议：和第 6 条合并，调度器每轮顺手跑一次 `rename_all`；或者在「下载中」页面轮询时发现有任务刚完成，就触发一次重命名。

### 8. 订阅、别名、偏好只能手改 TOML

- 现状：Web 设置页只覆盖了 `app / aria2 / qbittorrent / library / kazumi` 几个基础字段。`[[rss]]`、`[[kazumi.subscribe]]`、`[titles]`、`resolution_preference`、`names / exclude_names / name_regex / exclude_name_regex` 都只能编辑配置文件；RSS 页和解析页的说明也只能写「在配置文件里添加 …」。
- 另外 `put_config` 用 `tomli_w` 重写整个文件（[server.py:622](../src/amine_downloader/server.py#L622)），**用户手写的注释会全部丢失**，而且 `.bak` 只保留一份。
- 建议：
  - RSS 页支持增删改订阅，输入 URL 后先预览解析结果（番剧名、集数），再勾选要保留的番剧名；
  - 解析页搜索完成后直接「加入订阅」，把第 1 条提到的 `rule + source` 一起保存；
  - 换用 `tomlkit` 写配置，保留注释和原有格式。

### 9. 设置页的默认值是作者 NAS 上的路径

- 现状：`CONFIG_DEFAULTS` 硬编码了 `/wenwen/media/acg`、`http://aria2-next:6800/jsonrpc`、`library.enabled = True`（[server.py:509-541](../src/amine_downloader/server.py#L509-L541)）。设置页的规则是「留空就用默认值」。
- 后果：
  - 其他用户在设置页把某项清空再保存，会被写成 `/wenwen/...`；
  - 设置页默认 `library.enabled = True`、`downloader = "aria2"`，但 `AppConfig` 和 `init` 生成的配置文件里默认是 `qbittorrent`，媒体库也是关闭的。两边默认值不一致，只要在 Web 上点一次保存，行为就悄悄变了。
- 建议：默认值统一从 `AppConfig` 里取；个人环境的值放进 `docker/config.example.toml`，不要写死在代码里。

### 10. 一集只能改一次标题，没法对整部番生效

- 现状：历史记录里的「改标题」只改当前这一条（[server.py:409-431](../src/amine_downloader/server.py#L409-L431)）。
- 后果：解析出的标题不对时（常见于 Jellyfin 匹配不上），用户得一集一集地改，而且下一集下载时又会用错误的标题。
- 建议：弹窗里增加「应用到同名的所有记录」和「保存为别名（写入 `[titles]`）」两个选项。

### 11. 历史记录缺少管理操作

- 现状：只能看列表和改标题。
- 建议：
  - 搜索、按番剧 / 状态筛选、分页（记录一多，表格会越来越长）；
  - **删除记录**：删掉后下次运行会重新下载，适合下错了想重下的情况，现在只能手动改 SQLite；
  - **重试**：对 `failed` / `no-video` 的记录重新提交；
  - 在「下载中」页移除任务时，同步更新历史记录的状态。

### 12. 同类任务可以同时跑，存在重复添加的竞态

- 现状：任务在内存中管理，没有互斥。连点两次「下载新剧集」（或者以后定时任务和手动运行撞上），两个 `rss_run` 会同时在 `store.has()` 那里判断为未下载，结果同一个种子被添加两次。
- 另外服务重启后任务列表会清空。
- 建议：同一 `kind` 同时只允许运行一个（已有的在跑就直接返回那个 job）；任务的摘要写入 SQLite 持久化。

### 13. 「下载中」页的操作容易误点

- 移除没有二次确认；API 支持 `delete_files`，但界面上没有入口。
- 暂停和继续两个按钮始终同时显示，没有根据任务状态切换。
- 没有「只显示 amine 添加的任务」的筛选（qBittorrent 里通常还有别的种子）。

### 14. Kazumi 解析页的交互问题

- 「集」只能填一个数字，不支持 `1-12`、`1,3,5` 这样的范围写法，也不能在剧集列表里勾选（现在剧集列表只是一段用顿号拼起来的纯文本）。
- 搜索失败的源只显示数量，看不到具体原因（`errors` 其实已经返回给前端了）。
- 手动下载不做去重：同一集可以反复下载，而订阅会跳过它。建议提示「已下载过，是否重新下载？」。
- 集号取的是剧集名里的**第一个数字**（[kazumi/download.py:612-616](../src/amine_downloader/kazumi/download.py#L612-L616)），像「第2季 第05集」、「2024 01」这类名字会解析错。建议复用 `parse_title` 的集号逻辑，或者优先匹配 `第N集` / `EP N`。

### 15. RSS 页只能「全部一起跑」

- 后端的 `rss_run` 支持 `feeds` 参数，但界面没有提供单个订阅的「预览 / 下载」按钮。调试新订阅时，只能连带所有订阅一起跑。

---

## P2 — 细节

- **`episode_offset` 是全局配置**：实际使用中，集数偏移几乎总是针对某一部番（比如分割放送的第二部分从 13 集开始）。应该移到 `[[rss]]` / `[[kazumi.subscribe]]` 下面，同时支持 `season` 覆盖。
- **路径映射概念重复**：`aria2.local_dir` 和 `library.local_root` 做的是同一件事（把下载器那边的路径映射成本机路径），用户很难搞清楚分别该填什么。建议合并为一个 `path_map = [["/downloader/side", "Z:/local/side"]]`。
- **HLS 分片模式的本地路径问题**：`_download_hls_segments` 把 aria2 那边的路径直接当成本机路径来写合并后的文件（[kazumi/download.py:498](../src/amine_downloader/kazumi/download.py#L498)），只有在 Docker 同路径挂载时才正常，Windows 本机运行会写到错误的位置。应该复用上面的路径映射。
- **分片模式会改掉 aria2 的全局配置**：`set_global_option(max-concurrent-downloads)`（[kazumi/download.py:526](../src/amine_downloader/kazumi/download.py#L526)）修改后没有恢复，会影响 aria2 上其它的下载任务。
- **`rename_all` 每次都扫描全部记录**：只跳过 `renamed` 状态，所以 Kazumi 的记录（`downloaded`）、`no-video` 的记录，以及 aria2 通过 `index-out` 已经在添加时命名好的记录（状态一直停在 `added`），每次都会重新请求下载器。建议对这些状态直接跳过，或者在添加时就把状态标记为 `renamed`。
- **侧边栏的连接状态只在页面加载时获取一次**：下载器断线后不会自动更新。
- **当前页面不写入 URL**：刷新浏览器后总是回到「下载中」。可以用 hash 路由（`#/history`）记住当前页。
- **Web UI 没有鉴权**：部署在局域网 / 公网时，任何人都能改配置、删任务。建议至少支持 `[app] web_password` 或 Basic Auth。
- **静态文件路由存在路径穿越（安全问题，建议优先修）**：`spa()` 直接拼接 `_DIST / path` 而没有校验（[server.py:660-666](../src/amine_downloader/server.py#L660-L666)）。实测 `GET /a/..%2f..%2fsecret.txt` 能读到 `web/dist` 目录之外的文件，在 Docker 里就能读到 `/config/config.toml`（里面有下载器密码）。修复方式：`candidate.resolve().is_relative_to(_DIST.resolve())` 校验不通过就返回 `index.html`。

---

## 建议的处理顺序

1. 静态文件路径穿越修复（改动小，风险大）
2. P0-1 搜索结果稳定 + 用 `rule + source` 定位作品
3. P0-2 取消任务真正生效、P1-12 同类任务互斥
4. P1-6 / P1-7 内置调度器（顺带解决 aria2 完成后的自动重命名）
5. P0-3 / P0-4 / P0-5 去重与首次订阅策略
6. P1-8 在 Web 上管理订阅（依赖第 2 步的 `rule + source`）
7. 其余按需处理
