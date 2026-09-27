"""A tiny JSONPath subset, matching the syntax Kazumi's API rules support.

Supported:
    $.data.videos        nested field access
    $.data.videos[*]     array wildcard
    $.data.videos[0]     array index
    $['play-sources']    quoted field names

Unsupported (and rejected): recursive descent, filters, slices, functions.
"""

from __future__ import annotations

import re
from typing import Any

_STEP_RE = re.compile(
    r"""\.
        (?P<field>[A-Za-z_][\w\-]*)     # .field
      | \[(?P<wildcard>\*)\]            # [*]
      | \['(?P<single>[^']*)'\]         # ['field']
      | \["(?P<double>[^"]*)"\]         # ["field"]
      | \[(?P<index>\d+)\]              # [0]
    """,
    re.VERBOSE,
)


class JsonPathError(ValueError):
    pass


def _parse(path: str) -> list[tuple[str, Any]]:
    path = (path or "").strip()
    if not path.startswith("$"):
        raise JsonPathError(f"JSONPath 必须以 $ 开头: {path!r}")
    if path == "$":
        return []
    if ".." in path:
        raise JsonPathError(f"不支持递归查找: {path!r}")
    if "[?" in path:
        raise JsonPathError(f"不支持条件过滤: {path!r}")

    steps: list[tuple[str, Any]] = []
    index = 1
    while index < len(path):
        match = _STEP_RE.match(path, index)
        if not match:
            raise JsonPathError(f"无法解析 JSONPath: {path!r}")
        index = match.end()
        if match.group("wildcard"):
            steps.append(("wildcard", None))
        elif match.group("field"):
            steps.append(("field", match.group("field")))
        elif match.group("single") is not None:
            steps.append(("field", match.group("single")))
        elif match.group("double") is not None:
            steps.append(("field", match.group("double")))
        else:
            steps.append(("index", int(match.group("index"))))
    return steps


def query(data: Any, path: str) -> list[Any]:
    """Return every value matched by *path*."""

    current = [data]
    for kind, value in _parse(path):
        results: list[Any] = []
        for item in current:
            if kind == "wildcard":
                if isinstance(item, list):
                    results.extend(item)
                elif isinstance(item, dict):
                    results.extend(item.values())
            elif kind == "field":
                if isinstance(item, dict) and value in item:
                    results.append(item[value])
            elif kind == "index":
                if isinstance(item, list) and -len(item) <= value < len(item):
                    results.append(item[value])
        current = results
    return current


def first(data: Any, path: str, default: Any = None) -> Any:
    """Return the first value matched by *path*, or *default*."""

    values = query(data, path)
    return values[0] if values else default


def render(template: str, variables: dict[str, Any]) -> str:
    """Replace ``@name`` placeholders in *template* with values."""

    def replace(match: re.Match[str]) -> str:
        return str(variables.get(match.group(1), match.group(0)))

    return re.sub(r"@([A-Za-z_][\w\-]*)", replace, template or "")
