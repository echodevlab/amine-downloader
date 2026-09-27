from amine_downloader.kazumi.hls import parse_playlist, pick_variant

MASTER = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=1280x720
720/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1920x1080
1080/index.m3u8
"""

MEDIA = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:6
#EXT-X-MAP:URI="init.mp4"
#EXTINF:6.0,
seg-1.m4s
#EXTINF:6.0,
seg-2.m4s
#EXT-X-ENDLIST
"""

ENCRYPTED = """#EXTM3U
#EXT-X-KEY:METHOD=AES-128,URI="key.bin"
#EXTINF:6.0,
seg-1.ts
"""


def test_master_playlist():
    playlist = parse_playlist(MASTER, "https://cdn.test/hls/master.m3u8")
    assert playlist.kind == "master"
    assert [v.resolution for v in playlist.variants] == ["1280x720", "1920x1080"]
    assert playlist.variants[1].url == "https://cdn.test/hls/1080/index.m3u8"


def test_pick_variant_prefers_quality():
    playlist = parse_playlist(MASTER, "https://cdn.test/hls/master.m3u8")
    assert pick_variant(playlist.variants, "1080p").resolution == "1920x1080"
    assert pick_variant(playlist.variants).bandwidth == 3000000


def test_media_playlist_with_init_segment():
    playlist = parse_playlist(MEDIA, "https://cdn.test/hls/1080/index.m3u8")
    assert playlist.kind == "media"
    assert playlist.init_segment == "https://cdn.test/hls/1080/init.mp4"
    assert playlist.segments == [
        "https://cdn.test/hls/1080/seg-1.m4s",
        "https://cdn.test/hls/1080/seg-2.m4s",
    ]
    assert playlist.extension == ".mp4"


def test_encrypted_playlist():
    playlist = parse_playlist(ENCRYPTED, "https://cdn.test/hls/index.m3u8")
    assert playlist.encrypted is True
    assert playlist.extension == ".ts"
