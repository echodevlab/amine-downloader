import json

from amine_downloader.kazumi.rule import KazumiRule, RuleStore

AGE_RULE = {
    "api": "1",
    "type": "anime",
    "name": "AGE",
    "version": "1.5",
    "baseURL": "https://www.agedm.io/",
    "searchURL": "https://www.agedm.io/search?query=@keyword",
    "searchList": "//div[2]/div/section/div/div/div/div",
    "searchName": "//div/div[2]/h5/a",
    "searchResult": "//div/div[2]/h5/a",
    "chapterRoads": "//div[2]/div/section/div/div[2]/div[2]/div[2]/div",
    "chapterResult": "//ul/li/a",
}


def test_rule_from_dict():
    rule = KazumiRule.from_dict(AGE_RULE)
    assert rule.name == "AGE"
    assert rule.base_url == "https://www.agedm.io/"
    assert rule.search_mode == "xpath"
    assert rule.chapter_mode == "xpath"
    assert rule.chapter_result == "//ul/li/a"


def test_api_rule_modes():
    rule = KazumiRule.from_dict(
        {
            "api": "8",
            "name": "TvTFun",
            "searchMode": "api",
            "chapterMode": "api",
            "searchApiConfig": {"listPath": "$.data.videos[*]"},
            "chapterApiConfig": {"roadsPath": "$.data.playSources[*]"},
        }
    )
    assert rule.search_mode == "api"
    assert rule.chapter_mode == "api"
    assert rule.search_api is not None
    assert rule.search_api.list_path == "$.data.videos[*]"
    assert rule.chapter_api is not None
    assert rule.chapter_api.roads_path == "$.data.playSources[*]"


def test_rule_store_roundtrip(tmp_path):
    store = RuleStore(tmp_path)
    store.save(KazumiRule.from_dict(AGE_RULE))
    rules = store.list()
    assert len(rules) == 1
    assert store.get("AGE").name == "AGE"
    assert store.get("age").name == "AGE"
    assert store.get("missing") is None


def test_rule_store_import_list(tmp_path):
    source = tmp_path / "bundle.json"
    source.write_text(json.dumps([AGE_RULE, {**AGE_RULE, "name": "Second"}]), encoding="utf-8")
    store = RuleStore(tmp_path / "rules")
    written = store.import_from(str(source))
    assert len(written) == 2
    assert {rule.name for rule in store.list()} == {"AGE", "Second"}
