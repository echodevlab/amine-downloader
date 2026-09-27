"""Configuration loading and default config generation."""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .renamer import DEFAULT_TEMPLATE

APP_NAME = "amine-downloader"

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
# hit = 0                    # 取第几个搜索结果
# road = 0                   # 取第几条播放线路
# quality = "1080p"
# save_path = ""
# enabled = true
# title = ""                 # 覆盖解析出的番剧名（可选）

# 媒体库：下载直接入库（Jellyfin 可直接识别）
# [library]
# enabled = true
# root = "/wenwen/media/acg"          # 媒体库根目录（下载器侧路径）
# local_root = "Z:/wenwen/media/acg"  # 同一目录在本机看到的路径（可选，用于磁盘重命名）
# series_template = "{{title}}"           # 剧集文件夹名
# season_template = "Season {{season}}"   # 季文件夹名
'''


def config_dir() -> Path:
    override = os.environ.get("AMINE_DOWNLOADER_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        if base:
            return Path(base) / APP_NAME
    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / ".config" / APP_NAME


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


@dataclass(slots=True)
class RssFeed:
    name: str
    url: str
    enabled: bool = True
    title: str = ""


@dataclass(slots=True)
class KazumiSubscription:
    name: str
    rule: str = ""
    hit: int = 0
    road: int = 0
    quality: str = ""
    save_path: str = ""
    enabled: bool = True
    title: str = ""


@dataclass(slots=True)
class AppConfig:
    downloader: str = "qbittorrent"
    rename: bool = True
    rename_template: str = DEFAULT_TEMPLATE
    save_path: str = ""
    category: str = "amine"
    episode_offset: int = 0
    resolution_preference: list[str] = field(default_factory=list)
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
        for entry in data.get("rss", []):
            if not entry.get("url"):
                continue
            config.rss.append(
                RssFeed(
                    name=str(entry.get("name") or entry["url"]),
                    url=str(entry["url"]),
                    enabled=bool(entry.get("enabled", True)),
                    title=str(entry.get("title", "")),
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
                    hit=int(entry.get("hit", 0)),
                    road=int(entry.get("road", 0)),
                    quality=str(entry.get("quality", "")),
                    save_path=str(entry.get("save_path", "")),
                    enabled=bool(entry.get("enabled", True)),
                    title=str(entry.get("title", "")),
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
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(DEFAULT_CONFIG_TEXT, encoding="utf-8")
    return target
