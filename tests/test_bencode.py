import hashlib

from amine_downloader.bencode import decode, encode, info_hash, torrent_files


def test_roundtrip():
    value = {b"name": b"test.mkv", b"length": 1234, b"list": [1, 2, 3]}
    assert decode(encode(value)) == value


def test_strings_and_ints():
    assert decode(b"i42e") == 42
    assert decode(b"4:spam") == b"spam"
    assert decode(b"l4:spami3ee") == [b"spam", 3]


def test_info_hash():
    info = {b"name": b"test.mkv", b"piece length": 16384, b"length": 10}
    torrent = encode({b"announce": b"http://tracker", b"info": info})
    expected = hashlib.sha1(encode(info)).hexdigest()
    assert info_hash(torrent) == expected


def test_torrent_files_single():
    torrent = encode({b"info": {b"name": b"movie.mp4", b"length": 5}})
    assert torrent_files(torrent) == [("movie.mp4", 5)]


def test_torrent_files_multi():
    info = {
        b"name": b"Anime",
        b"files": [
            {b"length": 10, b"path": [b"01.mkv"]},
            {b"length": 20, b"path": [b"sub", b"02.mkv"]},
        ],
    }
    torrent = encode({b"info": info})
    assert torrent_files(torrent) == [("Anime/01.mkv", 10), ("Anime/sub/02.mkv", 20)]
