"""Minimal bencode codec, used to compute torrent info hashes."""

from __future__ import annotations

import hashlib
from typing import Any


class BencodeError(ValueError):
    pass


def decode(data: bytes) -> Any:
    value, index = _decode(data, 0)
    return value


def _decode(data: bytes, index: int) -> tuple[Any, int]:
    if index >= len(data):
        raise BencodeError("unexpected end of data")
    char = data[index : index + 1]
    if char == b"i":
        end = data.index(b"e", index)
        return int(data[index + 1 : end]), end + 1
    if char == b"l":
        index += 1
        result = []
        while data[index : index + 1] != b"e":
            value, index = _decode(data, index)
            result.append(value)
        return result, index + 1
    if char == b"d":
        index += 1
        result: dict[bytes, Any] = {}
        while data[index : index + 1] != b"e":
            key, index = _decode(data, index)
            value, index = _decode(data, index)
            result[key] = value
        return result, index + 1
    if char.isdigit():
        colon = data.index(b":", index)
        length = int(data[index:colon])
        start = colon + 1
        return data[start : start + length], start + length
    raise BencodeError(f"invalid bencode prefix {char!r}")


def encode(value: Any) -> bytes:
    if isinstance(value, int):
        return b"i" + str(value).encode() + b"e"
    if isinstance(value, bytes):
        return str(len(value)).encode() + b":" + value
    if isinstance(value, str):
        return encode(value.encode())
    if isinstance(value, list):
        return b"l" + b"".join(encode(item) for item in value) + b"e"
    if isinstance(value, dict):
        chunks = [b"d"]
        for key in sorted(value):
            chunks.append(encode(key))
            chunks.append(encode(value[key]))
        chunks.append(b"e")
        return b"".join(chunks)
    raise BencodeError(f"cannot encode {type(value)!r}")


def info_hash(torrent_bytes: bytes) -> str:
    """Return the v1 info hash (SHA-1 of the bencoded ``info`` dict)."""

    data = decode(torrent_bytes)
    if not isinstance(data, dict) or b"info" not in data:
        raise BencodeError("not a torrent file")
    return hashlib.sha1(encode(data[b"info"])).hexdigest()


def torrent_files(torrent_bytes: bytes) -> list[tuple[str, int]]:
    """Return ``[(path, length), ...]`` in aria2 file-index order.

    Paths are relative and use ``/`` separators, matching what aria2 uses for
    ``index-out`` (the top level directory/file name is included).
    """

    data = decode(torrent_bytes)
    if not isinstance(data, dict) or b"info" not in data:
        raise BencodeError("not a torrent file")
    info = data[b"info"]
    name = info.get(b"name", b"").decode("utf-8", "replace")
    if b"files" in info:
        files: list[tuple[str, int]] = []
        for entry in info[b"files"]:
            parts = [part.decode("utf-8", "replace") for part in entry.get(b"path", [])]
            path = "/".join([name, *parts]) if name else "/".join(parts)
            files.append((path, int(entry.get(b"length", 0))))
        return files
    if b"length" in info:
        return [(name, int(info[b"length"]))]
    raise BencodeError("torrent has no files")
