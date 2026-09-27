"""Command line interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import AppConfig, default_config_path, kazumi_rules_dir, write_default_config
from .errors import AmineError
from .kazumi import KazumiService, RuleStore
from .models import RssEpisode
from .parser import parse_title
from .renamer import render
from .rss import fetch_feed
from .service import DownloadService


def _print_json(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _episode_row(episode: RssEpisode) -> dict:
    parsed = episode.parsed
    return {
        "title": episode.title,
        "group": parsed.group if parsed else "",
        "name": parsed.title if parsed else "",
        "season": parsed.season if parsed else 1,
        "episode": parsed.episode if parsed else "",
        "resolution": parsed.resolution if parsed else "",
        "torrent": episode.torrent_url,
    }


def _load_config(args) -> AppConfig:
    config = AppConfig.load(args.config)
    if not config.path or not Path(config.path).exists():
        print(f"提示: 未找到配置文件 {config.path}，正在使用默认配置。", file=sys.stderr)
        print("     可运行 `amine-downloader init` 生成配置文件。", file=sys.stderr)
    return config


def _load_service(args) -> DownloadService:
    return DownloadService(_load_config(args))


# -- commands -------------------------------------------------------------
def cmd_init(args) -> int:
    target = Path(args.path).expanduser() if args.path else default_config_path()
    try:
        path = write_default_config(target, overwrite=args.force)
    except FileExistsError:
        print(f"配置文件已存在: {target}（使用 --force 覆盖）")
        return 1
    print(f"已生成配置文件: {path}")
    return 0


def cmd_config(args) -> int:
    path = Path(args.config).expanduser() if args.config else default_config_path()
    print(f"配置文件: {path}")
    if not path.exists():
        print("（不存在，运行 `amine-downloader init` 生成）")
        return 1
    print(path.read_text(encoding="utf-8"))
    return 0


def cmd_parse(args) -> int:
    results = []
    for title in args.titles:
        parsed = parse_title(title)
        name = render(parsed, args.template)
        results.append({"parsed": parsed.to_dict(), "renamed": name})
    if args.json:
        _print_json(results)
        return 0
    for item in results:
        parsed = item["parsed"]
        print(f"原始标题: {parsed['raw']}")
        print(f"  字幕组 : {parsed['group']}")
        print(f"  番剧名 : {parsed['title']}")
        print(f"  季/集  : S{parsed['season']:02d}E{parsed['episode'] or '?'}")
        print(f"  分辨率 : {parsed['resolution']}")
        print(f"  语言   : {parsed['language']}")
        print(f"  来源   : {parsed['source']}")
        print(f"  重命名 : {item['renamed']}")
        print()
    return 0


def _resolve_feed(args, config: AppConfig) -> str:
    if args.source.lower().startswith(("http://", "https://")):
        return args.source
    for feed in config.rss:
        if feed.name.lower() == args.source.lower():
            return feed.url
    print(f"未找到订阅源: {args.source}（请填写 URL 或在配置中添加 [[rss]]）", file=sys.stderr)
    raise SystemExit(2)


def cmd_rss(args) -> int:
    config = AppConfig.load(args.config)
    url = _resolve_feed(args, config)
    try:
        episodes = fetch_feed(url)
    except AmineError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    if args.json:
        _print_json([_episode_row(episode) for episode in episodes])
        return 0
    print(f"共 {len(episodes)} 条:")
    for episode in episodes:
        row = _episode_row(episode)
        print(
            f"  [{row['group'] or '-'}] {row['name'] or row['title']} "
            f"S{row['season']:02d}E{row['episode'] or '?'} [{row['resolution'] or '-'}]"
        )
    return 0


def cmd_download(args) -> int:
    service = _load_service(args)
    try:
        task = service.add_torrent(
            args.source,
            raw_title=args.title,
            save_path=args.save_path,
            rename=not args.no_rename,
            paused=args.paused,
        )
    except AmineError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    finally:
        service.close()
    print(f"已添加: {task.torrent_id}")
    if task.new_name:
        if task.status == "renamed":
            print(f"已重命名为: {task.new_name}")
        else:
            print(f"目标文件名: {task.new_name}（稍后可用 `amine-downloader rename` 完成）")
    return 0


def cmd_run(args) -> int:
    config = _load_config(args)
    ran_something = False

    feeds = [feed for feed in config.rss if feed.enabled]
    if args.feeds or feeds:
        ran_something = True
        service = DownloadService(config)
        try:
            results = service.run(
                feed_names=args.feeds or None,
                limit=args.limit,
                dry_run=args.dry_run,
                rename=not args.no_rename,
            )
            if not args.dry_run and service.client.rename_requires_complete:
                renamed = service.rename_all()
                if renamed:
                    print(f"已重命名 {renamed} 个已完成任务。")
        except AmineError as exc:
            print(f"错误: {exc}", file=sys.stderr)
            return 1
        finally:
            service.close()
        _print_run_results(results)

    if not args.no_kazumi and config.kazumi_subscriptions and not args.feeds:
        ran_something = True
        kazumi_service = KazumiService(config)
        try:
            kazumi_results = kazumi_service.run(dry_run=args.dry_run, limit=args.limit)
        except AmineError as exc:
            print(f"错误: {exc}", file=sys.stderr)
            return 1
        finally:
            kazumi_service.close()
        _print_kazumi_results(kazumi_results, dry_run=args.dry_run)

    if not ran_something:
        print("没有配置任何订阅（RSS 或 Kazumi）。")
    return 0


def _print_run_results(results) -> None:
    if not results:
        print("没有新的剧集。")
        return
    for item in results:
        row = _episode_row(item.episode)
        label = f"[{row['group'] or '-'}] {row['name'] or row['title']} E{row['episode'] or '?'}"
        if item.task:
            print(f"下载: {label} -> {item.task.torrent_id}")
        else:
            print(f"跳过: {label} ({item.skipped})")


def _print_kazumi_results(results, *, dry_run: bool) -> None:
    if not results:
        print("没有新的剧集。")
        return
    for result in results:
        label = f"{result.title} E{result.episode}" if result.episode else result.title
        if result.skipped:
            print(f"跳过: {label} ({result.skipped})")
        elif dry_run:
            print(f"解析: {label} -> {result.stream_url} [{result.kind}]")
        else:
            print(f"下载: {label} -> {result.output or result.task_id}")


def cmd_list(args) -> int:
    service = _load_service(args)
    try:
        torrents = service.client.list()
    except AmineError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    finally:
        service.close()
    if args.json:
        _print_json(
            [
                {
                    "id": t.id,
                    "name": t.name,
                    "progress": round(t.progress * 100, 2),
                    "state": t.state,
                    "save_path": t.save_path,
                    "category": t.category,
                }
                for t in torrents
            ]
        )
        return 0
    for torrent in torrents:
        print(f"{torrent.id[:12]:12} {torrent.progress * 100:6.2f}% {torrent.state:12} {torrent.name}")
    return 0


def cmd_status(args) -> int:
    service = _load_service(args)
    try:
        description = service.client.test_connection()
    except AmineError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    finally:
        service.close()
    print(f"下载器: {service.client.name}")
    print(f"状态  : {description}")
    return 0


def cmd_rename(args) -> int:
    service = _load_service(args)
    try:
        count = service.rename_all(only_torrent_id=args.id, limit=args.limit)
    except AmineError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    finally:
        service.close()
    print(f"已重命名 {count} 个任务。")
    return 0


def cmd_remove(args) -> int:
    service = _load_service(args)
    try:
        service.client.remove(args.id, delete_files=args.delete_files)
    except AmineError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    finally:
        service.close()
    print(f"已移除: {args.id}")
    return 0


def cmd_serve(args) -> int:
    from .server import serve

    serve(host=args.host, port=args.port, config_path=args.config)
    return 0


# -- kazumi commands ------------------------------------------------------
def _load_kazumi(args) -> KazumiService:
    config = AppConfig.load(args.config)
    return KazumiService(config)


def cmd_kazumi_rules(args) -> int:
    config = AppConfig.load(args.config)
    store = RuleStore(kazumi_rules_dir(config))
    rules = store.list()
    if args.json:
        _print_json(
            [
                {
                    "name": rule.name,
                    "version": rule.version,
                    "api": rule.api,
                    "base_url": rule.base_url,
                    "search_mode": rule.search_mode,
                    "chapter_mode": rule.chapter_mode,
                }
                for rule in rules
            ]
        )
        return 0
    print(f"规则目录: {store.directory}")
    if not rules:
        print("（空，使用 `amine-downloader kazumi import <文件或URL>` 导入）")
        return 0
    for rule in rules:
        print(f"{rule.name:16} v{rule.version:8} api={rule.api:>2}  {rule.base_url}")
    return 0


def cmd_kazumi_import(args) -> int:
    config = AppConfig.load(args.config)
    store = RuleStore(kazumi_rules_dir(config))
    for source in args.sources:
        try:
            written = store.import_from(source)
        except AmineError as exc:
            print(f"错误: {exc}", file=sys.stderr)
            return 1
        for path in written:
            print(f"已导入: {path}")
    return 0


def cmd_kazumi_search(args) -> int:
    service = _load_kazumi(args)
    try:
        hits = service.search(args.keyword, rule_name=args.rule)
    except AmineError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    finally:
        service.close()
    if args.json:
        _print_json(
            [
                {
                    "rule": hit.rule.name,
                    "name": hit.item.name,
                    "source": hit.item.source,
                    "page_url": hit.item.page_url,
                }
                for hit in hits
            ]
        )
        return 0
    if not hits:
        print("没有搜索结果。")
        return 0
    for index, hit in enumerate(hits):
        print(f"[{index}] ({hit.rule.name}) {hit.item.name}")
    return 0


def cmd_kazumi_chapters(args) -> int:
    service = _load_kazumi(args)
    try:
        hits = service.search(args.keyword, rule_name=args.rule)
        if not hits:
            print("没有搜索结果。", file=sys.stderr)
            return 1
        if args.hit >= len(hits):
            print(f"搜索结果不足 {args.hit + 1} 条", file=sys.stderr)
            return 1
        hit = hits[args.hit]
        roads = service.chapters(hit.rule, hit.item)
    except AmineError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    finally:
        service.close()

    if args.json:
        _print_json(
            [
                {
                    "road": road.name,
                    "episodes": [
                        {"name": episode.name, "page_url": episode.page_url, "url": episode.url}
                        for episode in road.episodes
                    ],
                }
                for road in roads
            ]
        )
        return 0
    print(f"作品: {hits[args.hit].item.name}")
    for road_index, road in enumerate(roads):
        print(f"  [{road_index}] {road.name}（{len(road.episodes)} 集）")
        for episode_index, episode in enumerate(road.episodes):
            print(f"      E{episode_index + 1:>3} {episode.name}")
    return 0


def cmd_kazumi_download(args) -> int:
    service = _load_kazumi(args)
    try:
        results = service.download(
            args.keyword,
            rule_name=args.rule,
            hit_index=args.hit,
            road_index=args.road,
            episode=args.episode,
            limit=args.limit,
            quality=args.quality,
            save_path=args.save_path,
            dry_run=args.dry_run,
            sniffed_url=args.url,
        )
    except AmineError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    finally:
        service.close()

    if not results:
        print("没有可下载的剧集。")
        return 0
    for result in results:
        label = f"{result.title} E{result.episode}"
        if result.skipped:
            print(f"跳过: {label} ({result.skipped})")
        elif args.dry_run:
            print(f"解析: {label} -> {result.stream_url} [{result.kind}]")
        else:
            print(f"下载: {label} -> {result.output or result.task_id}")
    return 0


def cmd_kazumi_run(args) -> int:
    service = _load_kazumi(args)
    try:
        results = service.run(names=args.names or None, dry_run=args.dry_run, limit=args.limit)
    except AmineError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    finally:
        service.close()
    _print_kazumi_results(results, dry_run=args.dry_run)
    return 0


# -- parser ---------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="amine-downloader",
        description="从 BT / Kazumi 源下载番剧，并按标准格式重命名（支持 qBittorrent 与 aria2）。",
    )
    parser.add_argument("--config", metavar="PATH", help="配置文件路径")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="生成默认配置文件")
    p_init.add_argument("--path", help="写入路径")
    p_init.add_argument("--force", action="store_true", help="覆盖已有文件")
    p_init.set_defaults(func=cmd_init)

    p_config = sub.add_parser("config", help="显示当前配置")
    p_config.set_defaults(func=cmd_config)

    p_parse = sub.add_parser("parse", help="解析种子标题并预览重命名结果")
    p_parse.add_argument("titles", nargs="+", help="原始标题")
    p_parse.add_argument("--template", help="自定义重命名模板")
    p_parse.add_argument("--json", action="store_true")
    p_parse.set_defaults(func=cmd_parse)

    p_rss = sub.add_parser("rss", help="查看某个订阅源的剧集列表")
    p_rss.add_argument("source", help="订阅源名称或 RSS URL")
    p_rss.add_argument("--json", action="store_true")
    p_rss.set_defaults(func=cmd_rss)

    p_download = sub.add_parser("download", help="下载单个磁力/种子")
    p_download.add_argument("source", help="magnet / .torrent URL / 本地 .torrent 文件")
    p_download.add_argument("--title", help="原始标题（用于解析与重命名）")
    p_download.add_argument("--save-path", help="保存路径")
    p_download.add_argument("--no-rename", action="store_true", help="不重命名")
    p_download.add_argument("--paused", action="store_true", help="添加后暂停")
    p_download.set_defaults(func=cmd_download)

    p_run = sub.add_parser("run", help="拉取所有订阅并下载新剧集")
    p_run.add_argument("feeds", nargs="*", help="仅处理指定订阅源（名称或 URL）")
    p_run.add_argument("--limit", type=int, help="每个源最多下载最新的 N 集")
    p_run.add_argument("--dry-run", action="store_true", help="只显示，不下载")
    p_run.add_argument("--no-rename", action="store_true", help="不重命名")
    p_run.add_argument("--no-kazumi", action="store_true", help="不处理 Kazumi 解析订阅")
    p_run.set_defaults(func=cmd_run)

    p_list = sub.add_parser("list", help="列出下载器中的任务")
    p_list.add_argument("--json", action="store_true")
    p_list.set_defaults(func=cmd_list)

    p_status = sub.add_parser("status", help="测试下载器连接")
    p_status.set_defaults(func=cmd_status)

    p_rename = sub.add_parser("rename", help="对已完成的任务执行重命名")
    p_rename.add_argument("--id", help="仅处理指定 torrent id / gid")
    p_rename.add_argument("--limit", type=int, help="最多处理 N 个任务")
    p_rename.set_defaults(func=cmd_rename)

    p_remove = sub.add_parser("remove", help="移除任务")
    p_remove.add_argument("id", help="torrent id / gid")
    p_remove.add_argument("--delete-files", action="store_true", help="同时删除文件")
    p_remove.set_defaults(func=cmd_remove)

    p_serve = sub.add_parser("serve", help="启动 Web UI（React + shadcn）")
    p_serve.add_argument("--host", default="127.0.0.1", help="监听地址")
    p_serve.add_argument("--port", type=int, default=8420, help="监听端口")
    p_serve.set_defaults(func=cmd_serve)

    p_kazumi = sub.add_parser("kazumi", help="Kazumi 规则解析下载（m3u8 / mp4）")
    kazumi_sub = p_kazumi.add_subparsers(dest="kazumi_command", required=True)

    k_rules = kazumi_sub.add_parser("rules", help="列出已导入的规则")
    k_rules.add_argument("--json", action="store_true")
    k_rules.set_defaults(func=cmd_kazumi_rules)

    k_import = kazumi_sub.add_parser("import", help="导入规则（JSON 文件或 URL）")
    k_import.add_argument("sources", nargs="+", help="规则 JSON 文件或 URL")
    k_import.set_defaults(func=cmd_kazumi_import)

    k_search = kazumi_sub.add_parser("search", help="按规则搜索番剧")
    k_search.add_argument("keyword", help="番剧名称")
    k_search.add_argument("--rule", help="指定规则名称")
    k_search.add_argument("--json", action="store_true")
    k_search.set_defaults(func=cmd_kazumi_search)

    k_chapters = kazumi_sub.add_parser("chapters", help="查看播放线路与剧集")
    k_chapters.add_argument("keyword", help="番剧名称")
    k_chapters.add_argument("--rule", help="指定规则名称")
    k_chapters.add_argument("--hit", type=int, default=0, help="第几个搜索结果（从 0 开始）")
    k_chapters.add_argument("--json", action="store_true")
    k_chapters.set_defaults(func=cmd_kazumi_chapters)

    k_download = kazumi_sub.add_parser("download", help="解析并下载（交给 aria2）")
    k_download.add_argument("keyword", help="番剧名称")
    k_download.add_argument("--rule", help="指定规则名称")
    k_download.add_argument("--hit", type=int, default=0, help="第几个搜索结果（从 0 开始）")
    k_download.add_argument("--road", type=int, default=0, help="第几条播放线路（从 0 开始）")
    k_download.add_argument("--episode", help="仅下载指定集数")
    k_download.add_argument("--limit", type=int, help="最多下载 N 集")
    k_download.add_argument("--quality", help="优先清晰度，如 1080p")
    k_download.add_argument("--save-path", help="保存路径")
    k_download.add_argument("--url", help="已知视频流地址，跳过浏览器嗅探")
    k_download.add_argument("--dry-run", action="store_true", help="只解析，不下载")
    k_download.set_defaults(func=cmd_kazumi_download)

    k_run = kazumi_sub.add_parser("run", help="按订阅批量追番（自动跳过已下载）")
    k_run.add_argument("names", nargs="*", help="仅处理指定订阅（名称）")
    k_run.add_argument("--limit", type=int, help="每个订阅最多下载最新的 N 集")
    k_run.add_argument("--dry-run", action="store_true", help="只解析，不下载")
    k_run.set_defaults(func=cmd_kazumi_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
