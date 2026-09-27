from amine_downloader.config import AppConfig, write_default_config


def test_write_and_load_default_config(tmp_path):
    path = tmp_path / "config.toml"
    write_default_config(path)
    config = AppConfig.load(path)
    assert config.downloader == "qbittorrent"
    assert "{title}" in config.rename_template
    assert config.qbittorrent["url"] == "http://127.0.0.1:8080"


def test_load_rss_feeds(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[app]
downloader = "aria2"
resolution_preference = ["1080p"]

[aria2]
rpc_url = "http://127.0.0.1:6800/jsonrpc"

[[rss]]
name = "Frieren"
url = "https://mikanani.me/RSS/Bangumi?bangumiId=1"

[[rss]]
name = "Disabled"
url = "https://example.com/rss"
enabled = false
""",
        encoding="utf-8",
    )
    config = AppConfig.load(path)
    assert config.downloader == "aria2"
    assert config.resolution_preference == ["1080p"]
    assert len(config.rss) == 2
    assert config.rss[0].name == "Frieren"
    assert config.rss[0].enabled is True
    assert config.rss[1].enabled is False


def test_missing_config_uses_defaults(tmp_path):
    config = AppConfig.load(tmp_path / "does-not-exist.toml")
    assert config.downloader == "qbittorrent"
    assert config.rename is True
    assert config.rss == []
    assert config.kazumi_subscriptions == []


def test_title_aliases_and_overrides(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[titles]
"葬送的芙莉莲 第二季" = "葬送的芙莉莲"

[[rss]]
name = "Frieren"
url = "https://example.com/rss"
title = "葬送的芙莉莲"

[[kazumi.subscribe]]
name = "芙莉莲"
title = "葬送的芙莉莲"
""",
        encoding="utf-8",
    )
    config = AppConfig.load(path)
    assert config.title_aliases == {"葬送的芙莉莲 第二季": "葬送的芙莉莲"}
    assert config.rss[0].title == "葬送的芙莉莲"
    assert config.kazumi_subscriptions[0].title == "葬送的芙莉莲"


def test_load_kazumi_subscriptions(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[kazumi]
rules_dir = "/rules"

[[kazumi.subscribe]]
name = "葬送的芙莉莲"
rule = "AGE"
road = 1
quality = "1080p"

[[kazumi.subscribe]]
name = "孤独摇滚"
enabled = false
""",
        encoding="utf-8",
    )
    config = AppConfig.load(path)
    assert config.kazumi["rules_dir"] == "/rules"
    assert len(config.kazumi_subscriptions) == 2
    first = config.kazumi_subscriptions[0]
    assert first.name == "葬送的芙莉莲"
    assert first.rule == "AGE"
    assert first.road == 1
    assert first.quality == "1080p"
    assert config.kazumi_subscriptions[1].enabled is False
