import pytest

from amine_downloader.kazumi.jsonpath import JsonPathError, first, query


DATA = {
    "data": {
        "videos": [
            {"id": "a", "name": "One", "play-sources": [{"name": "线路B"}]},
            {"id": "b", "name": "Two"},
        ],
        "total": 2,
    }
}


def test_field_and_wildcard():
    assert [v["name"] for v in query(DATA, "$.data.videos[*]")] == ["One", "Two"]


def test_index_and_quoted_field():
    assert first(DATA, "$.data.videos[0].name") == "One"
    assert first(DATA, "$.data.videos[0]['play-sources'][0].name") == "线路B"


def test_missing_returns_default():
    assert first(DATA, "$.data.missing", "fallback") == "fallback"


def test_rejects_unsupported_syntax():
    with pytest.raises(JsonPathError):
        query(DATA, "$..videos")
    with pytest.raises(JsonPathError):
        query(DATA, "$.data.videos[?(@.enabled)]")
    with pytest.raises(JsonPathError):
        query(DATA, "data.videos")
