import httpx

from amine_downloader.kazumi.client import RuleClient
from amine_downloader.kazumi.rule import KazumiRule

SEARCH_HTML = """<html><body><ul>
<li class="item"><h3><a href="/v/1">Frieren</a></h3></li>
<li class="item"><h3><a href="/v/2">Bocchi</a></h3></li>
</ul></body></html>"""

DETAIL_HTML = """<html><body>
<div class="road"><ul>
<li><a href="/play/1/1">第1集</a></li>
<li><a href="/play/1/2">第2集</a></li>
</ul></div>
<div class="road"><ul><li><a href="/play/2/1">第1集</a></li></ul></div>
</body></html>"""


def make_client(rule, handler):
    client = RuleClient(rule)
    client._http.close()
    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def test_xpath_search_and_chapters():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search":
            seen["keyword"] = request.url.params.get("q")
            return httpx.Response(200, text=SEARCH_HTML)
        if request.url.path == "/v/1":
            return httpx.Response(200, text=DETAIL_HTML)
        return httpx.Response(404)

    rule = KazumiRule.from_dict(
        {
            "name": "Test",
            "baseURL": "https://site.test/",
            "searchURL": "https://site.test/search?q=@keyword",
            "searchList": "//li[@class='item']",
            "searchName": ".//h3/a",
            "searchResult": ".//h3/a",
            "chapterRoads": "//div[@class='road']",
            "chapterResult": ".//ul/li/a",
        }
    )
    client = make_client(rule, handler)
    try:
        items = client.search("紫罗兰 永恒花园")
        assert [item.name for item in items] == ["Frieren", "Bocchi"]
        assert items[0].source == "https://site.test/v/1"
        assert seen["keyword"] == "紫罗兰 永恒花园"

        roads = client.chapters(items[0].source)
        assert [road.name for road in roads] == ["线路1", "线路2"]
        assert [ep.name for ep in roads[0].episodes] == ["第1集", "第2集"]
        assert roads[0].episodes[0].page_url == "https://site.test/play/1/1"
    finally:
        client.close()


def test_api_search_and_chapters():
    search_json = {"data": {"videos": [{"id": "cmp2", "name": "紫罗兰永恒花园", "slug": "183878"}], "total": 1}}
    detail_json = {
        "data": {
            "id": "cmp2",
            "slug": "183878",
            "playSources": [
                {
                    "name": "线路B",
                    "episodes": [
                        {"name": "第1集", "url": "protected"},
                        {"name": "第2集", "url": "protected"},
                    ],
                }
            ],
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/videos/search":
            assert request.url.params.get("q") == "紫罗兰永恒花园"
            assert request.url.params.get("pageSize") == "20"
            return httpx.Response(200, json=search_json)
        if request.url.path == "/api/videos/cmp2":
            return httpx.Response(200, json=detail_json)
        return httpx.Response(404)

    rule = KazumiRule.from_dict(
        {
            "api": "8",
            "name": "TvTFun",
            "baseURL": "https://api.test/",
            "searchMode": "api",
            "chapterMode": "api",
            "searchApiConfig": {
                "request": {
                    "method": "GET",
                    "url": "https://api.test/api/videos/search",
                    "query": {"q": "@keyword", "pageSize": 20},
                },
                "listPath": "$.data.videos[*]",
                "namePath": "$.name",
                "sourcePath": "$.id",
            },
            "chapterApiConfig": {
                "request": {"method": "GET", "url": "https://api.test/api/videos/@source"},
                "format": "nested",
                "roadsPath": "$.data.playSources[*]",
                "roadNamePath": "$.name",
                "episodesPath": "$.episodes[*]",
                "episodeNamePath": "$.name",
                "episodeUrlPath": "",
                "variables": {"slug": "$.data.slug"},
                "episodePage": {
                    "url": "https://api.test/video/@slug/play",
                    "query": {"source": "@roadIndex", "episode": "@episodeIndex"},
                },
            },
        }
    )
    client = make_client(rule, handler)
    try:
        items = client.search("紫罗兰永恒花园")
        assert len(items) == 1
        assert items[0].name == "紫罗兰永恒花园"
        assert items[0].source == "cmp2"

        roads = client.chapters(items[0].source)
        assert len(roads) == 1
        assert roads[0].name == "线路B"
        assert [ep.name for ep in roads[0].episodes] == ["第1集", "第2集"]
        assert roads[0].episodes[1].page_url == "https://api.test/video/183878/play?source=0&episode=1"
    finally:
        client.close()
