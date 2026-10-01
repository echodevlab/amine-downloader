"""Configuration loading and default config generation."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .errors import AmineError
from .renamer import DEFAULT_TEMPLATE

APP_NAME = "amine-downloader"


def ensure_dir(path: Path) -> Path:
    """Create *path* (and parents), raising a helpful error if not writable."""

    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AmineError(
            f"无法创建目录 {path}：{exc}\n"
            "该位置不可写，请改用可写目录，例如设置环境变量：\n"
            '  AMINE_DOWNLOADER_CONFIG_DIR="D:\\amine-downloader"'
        ) from exc
    return path

DEFAULT_CONFIG_TEXT = f'''# amine-downloader configuration
# See README.md for the full documentation.

[app]
# Download client: "qbittorrent" or "aria2"
downloader = "qbittorrent"
# Rename downloaded files into a standard format.
rename = true
# Rename template. Available tokens:
#   {{group}} {{title}} {{season}} {{episode}} {{resolution}}
#   {{language}} {{source}} {{extra}} {{raw}}
# {{season}} / {{episode}} are zero padded to 2 digits by default,
# use {{episode:3}} to pad to 3 digits.
rename_template = "{DEFAULT_TEMPLATE}"
# Save path. Leave empty to use the download client default.
save_path = ""
# qBittorrent category (also used as aria2 label).
category = "amine"
# Episode number offset, e.g. set to 12 when the fansub starts at 13.
episode_offset = 0
# Only download these resolutions (empty means all). Example: ["1080p", "2160p"]
resolution_preference = []
# 内置调度器间隔（分钟，0 = 关闭）。定时运行 RSS / 解析订阅，并重命名已完成任务。
interval = 30
# Web UI 访问密码（留空 = 不鉴权）。启用后浏览器会弹出 Basic Auth 登录框。
web_password = ""

# 下载器路径 -> 本机路径映射（可选）。用于 aria2 与本机路径不一致时
# （Docker / 映射盘 / UNC）。示例：path_map = [["/downloads", "Z:/downloads"]]
# path_map = []

[qbittorrent]
url = "http://127.0.0.1:8080"
username = "admin"
password = "adminadmin"
# qBittorrent >= 5.2 推荐使用 API Key（设置后优先于用户名/密码）
# 在 WebUI -> 选项 -> WebUI -> API Key 生成，形如 qbt_xxxxxxxx
api_key = ""

[aria2]
rpc_url = "http://127.0.0.1:6800/jsonrpc"
secret = ""
# aria2 的下载目录（aria2 侧路径，作为 dir 选项传给 aria2）
download_dir = ""
# 同一目录在本机看到的路径（映射盘 / UNC），用于下载完成后重命名磁力等文件
# 例如 download_dir="/wenwen/downloads" 时，local_dir 可填 "Z:/wenwen/downloads"
local_dir = ""

# Subscriptions (Mikan / DMHY / any BT RSS feed).
# Add as many [[rss]] blocks as you like.
#
# [[rss]]
# name = "Mikan - Sousou no Frieren"
# url = "https://mikanani.me/RSS/Bangumi?bangumiId=xxxx"
# enabled = true
# title = "葬送的芙莉莲"   # 覆盖解析出的番剧名（可选，用于 Jellyfin 匹配等）
# names = ["葬送的芙莉莲"]           # 只下番剧名包含这些关键词的条目（可选，子串匹配）
# exclude_names = ["某番剧"]         # 排除番剧名包含这些关键词的条目（可选）
# name_regex = ["(简|繁)体"]         # 只下番剧名匹配这些正则的条目（可选，大小写不敏感）
# exclude_name_regex = ["某正则"]     # 排除番剧名匹配这些正则的条目（可选）
# one_per_episode = true            # 同一集只保留一个版本（默认 true）
# initial = "latest"                # 首次运行：latest 只下最新一集 / all 全部 / none 只标记已见
# season = 2                        # 覆盖季号（可选）
# episode_offset = 12               # 覆盖全局集数偏移（可选）

# 标题别名：把解析出的标题映射为固定名称（可选）
# [titles]
# "葬送的芙莉莲 第二季" = "葬送的芙莉莲"
# "Bocchi the Rock!" = "孤独摇滚"

[kazumi]
# Kazumi 规则目录，留空使用 <配置目录>/kazumi-rules
rules_dir = ""
# 解析下载的重命名模板（解析源通常没有字幕组信息）
rename_template = "{{title}} S{{season}}E{{episode}}"
# 优先清晰度（匹配 m3u8 的 RESOLUTION / URL）
preferred_quality = "1080p"
# HLS 下载方式: auto(优先 aria2-next 原生) / native(强制原生) / segments(分片下载后合并)
media_mode = "auto"
# 浏览器嗅探设置（CloakBrowser）
headless = true
humanize = false
sniff_timeout = 30
# CloakBrowser 许可证 key（可选，设置后可获得更新的二进制）
license_key = ""
# 浏览器嗅探的临时目录（默认系统临时目录；不可写时请指定）
temp_dir = ""
# 首次嗅探时自动下载 CloakBrowser 的 Chromium 二进制（约 200MB）
auto_install_browser = true
# 代理（可选，如 http://user:pass@host:port 或 socks5://host:port）
proxy = ""
# ffmpeg 路径（可选，用于把 m3u8 合并结果转成 mp4）
ffmpeg = ""
# aria2 同时下载的分片数量
concurrency = 8

# Kazumi 解析追番订阅，可添加多个
#
# [[kazumi.subscribe]]
# name = "葬送的芙莉莲"       # 搜索关键词
# rule = "AGE"               # 规则名称（省略则在只有一条规则时自动选择）
# source = ""                # 已解析的作品地址（设置后跳过搜索，推荐由 Web 添加时自动写入）
# hit = 0                    # 取第几个搜索结果
# road = 0                   # 取第几条播放线路
# quality = "1080p"
# save_path = ""
# enabled = true
# title = ""                 # 覆盖解析出的番剧名（可选）
# initial = "latest"         # 首次运行：latest / all / none
# season = 2                 # 覆盖季号（可选）
# episode_offset = 12        # 覆盖全局集数偏移（可选）

# 媒体库：下载直接入库（Jellyfin 可直接识别）
# [library]
# enabled = true
# root = "/wenwen/media/acg"          # 媒体库根目录（下载器侧路径）
# local_root = "Z:/wenwen/media/acg"  # 同一目录在本机看到的路径（可选，用于磁盘重命名）
# series_template = "{{title}}"           # 剧集文件夹名
# season_template = "Season {{season}}"   # 季文件夹名
'''


def config_dir() -> Path:
    """Directory for config.toml / data.db.

    Defaults to the **current working directory** (so running from the repo
    keeps everything local). Override with ``AMINE_DOWNLOADER_CONFIG_DIR``.
    """

    override = os.environ.get("AMINE_DOWNLOADER_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    return Path.cwd()


def default_config_path() -> Path:
    override = os.environ.get("AMINE_DOWNLOADER_CONFIG")
    if override:
        return Path(override).expanduser()
    return config_dir() / "config.toml"


def default_data_path() -> Path:
    override = os.environ.get("AMINE_DOWNLOADER_DATA")
    if override:
        return Path(override).expanduser()
    return config_dir() / "data.db"


def kazumi_rules_dir(config: "AppConfig | None" = None) -> Path:
    override = (config.kazumi.get("rules_dir") if config else None) or os.environ.get(
        "AMINE_DOWNLOADER_RULES_DIR"
    )
    if override:
        return Path(str(override)).expanduser()
    return config_dir() / "kazumi-rules"


#: 首次运行订阅时的行为：latest=只下最新一集，all=全部，none=只标记为已见。
INITIAL_CHOICES = ("latest", "all", "none")


def _as_initial(value, default: str = "latest") -> str:
    text = str(value or default).strip().lower()
    return text if text in INITIAL_CHOICES else default


def _optional_int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass(slots=True)
class RssFeed:
    name: str
    url: str
    enabled: bool = True
    title: str = ""
    #: 只保留解析出的番剧名包含这些关键词的条目（留空 = 全部）；大小写不敏感的子串匹配
    names: list[str] = field(default_factory=list)
    #: 排除解析出的番剧名包含这些关键词的条目
    exclude_names: list[str] = field(default_factory=list)
    #: 只保留解析出的番剧名匹配这些正则的条目（大小写不敏感，search）
    name_regex: list[str] = field(default_factory=list)
    #: 排除解析出的番剧名匹配这些正则的条目
    exclude_name_regex: list[str] = field(default_factory=list)
    #: 同一集只保留一个版本（按字幕组 → 分辨率 → 发布时间选择）
    one_per_episode: bool = True
    #: 首次运行策略：latest / all / none
    initial: str = "latest"
    #: 覆盖全局的季号 / 集数偏移
    season: int | None = None
    episode_offset: int | None = None


@dataclass(slots=True)
class KazumiSubscription:
    name: str
    rule: str = ""
    #: 已解析作品地址；设置后跳过搜索，直接用它拉剧集
    source: str = ""
    hit: int = 0
    road: int = 0
    quality: str = ""
    save_path: str = ""
    enabled: bool = True
    title: str = ""
    #: 首次运行策略：latest / all / none
    initial: str = "latest"
    season: int | None = None
    episode_offset: int | None = None


@dataclass(slots=True)
class AppConfig:
    downloader: str = "qbittorrent"
    rename: bool = True
    rename_template: str = DEFAULT_TEMPLATE
    save_path: str = ""
    category: str = "amine"
    episode_offset: int = 0
    resolution_preference: list[str] = field(default_factory=list)
    #: 内置调度器间隔（分钟，0 = 关闭）
    interval: int = 30
    #: Web UI 访问密码（留空 = 不鉴权）
    web_password: str = ""
    #: 下载器路径 -> 本机路径的映射
    path_map: list[tuple[str, str]] = field(default_factory=list)
    rss: list[RssFeed] = field(default_factory=list)
    kazumi_subscriptions: list[KazumiSubscription] = field(default_factory=list)
    title_aliases: dict[str, str] = field(default_factory=dict)
    library: dict = field(default_factory=dict)
    qbittorrent: dict = field(default_factory=dict)
    aria2: dict = field(default_factory=dict)
    kazumi: dict = field(default_factory=dict)
    path: Path | None = None

    @classmethod
    def load(cls, path: Path | str | None = None) -> AppConfig:
        config_path = Path(path).expanduser() if path else default_config_path()
        config = cls(path=config_path)
        if not config_path.exists():
            return config
        with config_path.open("rb") as handle:
            data = tomllib.load(handle)
        app = data.get("app", {})
        config.downloader = str(app.get("downloader", config.downloader)).lower()
        config.rename = bool(app.get("rename", config.rename))
        config.rename_template = str(app.get("rename_template", config.rename_template))
        config.save_path = str(app.get("save_path", config.save_path))
        config.category = str(app.get("category", config.category))
        config.episode_offset = int(app.get("episode_offset", config.episode_offset))
        config.resolution_preference = list(app.get("resolution_preference", []))
        config.interval = int(app.get("interval", config.interval))
        config.web_password = str(app.get("web_password", config.web_password))
        for entry in data.get("path_map", []) or []:
            if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                config.path_map.append((str(entry[0]), str(entry[1])))
        for entry in data.get("rss", []):
            if not entry.get("url"):
                continue
            config.rss.append(
                RssFeed(
                    name=str(entry.get("name") or entry["url"]),
                    url=str(entry["url"]),
                    enabled=bool(entry.get("enabled", True)),
                    title=str(entry.get("title", "")),
                    names=[str(item) for item in entry.get("names", []) or []],
                    exclude_names=[str(item) for item in entry.get("exclude_names", []) or []],
                    name_regex=[str(item) for item in entry.get("name_regex", []) or []],
                    exclude_name_regex=[
                        str(item) for item in entry.get("exclude_name_regex", []) or []
                    ],
                    one_per_episode=bool(entry.get("one_per_episode", True)),
                    initial=_as_initial(entry.get("initial")),
                    season=_optional_int(entry.get("season")),
                    episode_offset=_optional_int(entry.get("episode_offset")),
                )
            )
        config.qbittorrent = dict(data.get("qbittorrent", {}))
        config.aria2 = dict(data.get("aria2", {}))
        config.kazumi = dict(data.get("kazumi", {}))
        for entry in config.kazumi.get("subscribe", []) or []:
            if not isinstance(entry, dict) or not entry.get("name"):
                continue
            config.kazumi_subscriptions.append(
                KazumiSubscription(
                    name=str(entry["name"]),
                    rule=str(entry.get("rule", "")),
                    source=str(entry.get("source", "")),
                    hit=int(entry.get("hit", 0)),
                    road=int(entry.get("road", 0)),
                    quality=str(entry.get("quality", "")),
                    save_path=str(entry.get("save_path", "")),
                    enabled=bool(entry.get("enabled", True)),
                    title=str(entry.get("title", "")),
                    initial=_as_initial(entry.get("initial")),
                    season=_optional_int(entry.get("season")),
                    episode_offset=_optional_int(entry.get("episode_offset")),
                )
            )
        aliases = data.get("titles") or {}
        if isinstance(aliases, dict):
            config.title_aliases = {str(key): str(value) for key, value in aliases.items()}
        config.library = dict(data.get("library", {}))
        return config


def write_default_config(path: Path | str | None = None, *, overwrite: bool = False) -> Path:
    target = Path(path).expanduser() if path else default_config_path()
    if target.exists() and not overwrite:
        raise FileExistsError(target)
    ensure_dir(target.parent)
    target.write_text(DEFAULT_CONFIG_TEXT, encoding="utf-8")
    return target
