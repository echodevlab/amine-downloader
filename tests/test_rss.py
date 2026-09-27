from amine_downloader.rss import parse_feed

MIKAN_RSS = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>Mikan Project</title>
    <item>
      <title>[Lilith-Raws] 葬送的芙莉莲 - 05 [1080p][Baha][WEB-DL][CHT]</title>
      <link>https://mikanani.me/Home/Episode/aaa</link>
      <guid isPermaLink="false">https://mikanani.me/Home/Episode/aaa</guid>
      <enclosure url="https://mikanani.me/Download/aaa.torrent" length="123" type="application/x-bittorrent" />
      <pubDate>Mon, 01 Jan 2024 00:00:00 +0800</pubDate>
    </item>
    <item>
      <title>[ANi] 孤獨搖滾 - 06 [1080P][Baha]</title>
      <link>https://mikanani.me/Home/Episode/bbb</link>
      <guid>bbb</guid>
      <enclosure url="https://mikanani.me/Download/bbb.torrent" length="456" type="application/x-bittorrent" />
    </item>
  </channel>
</rss>
"""

MAGNET_RSS = """<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <item>
      <title>Some Anime - 01 [720p]</title>
      <description>magnet:?xt=urn:btih:abcdef0123456789&amp;dn=Some+Anime</description>
    </item>
  </channel>
</rss>
"""


def test_parse_mikan_rss():
    episodes = parse_feed(MIKAN_RSS)
    assert len(episodes) == 2
    first = episodes[0]
    assert first.torrent_url == "https://mikanani.me/Download/aaa.torrent"
    assert first.parsed.group == "Lilith-Raws"
    assert first.parsed.episode == "5"
    assert first.dedup_key == "https://mikanani.me/Home/Episode/aaa"


def test_parse_magnet_from_description():
    episodes = parse_feed(MAGNET_RSS)
    assert len(episodes) == 1
    assert episodes[0].torrent_url.startswith("magnet:?xt=urn:btih:abcdef0123456789")
