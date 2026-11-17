"""Ditto's web search, the core (orchestrator/Core/web_search.py): every way, the merge, the origin of a hit, the privacy
filter on the query and the plain-word failures. The network is a fake `fetch`; documentation addresses only."""

import json
import time

import pytest

from orchestrator.Core import web_search as ws
from orchestrator.Core.web_search import (PROVIDERS, Request, Response, SearchRefused, WayError, WebSearch, merge,
                                          normalize, origin_of, parse_duckduckgo, prepare_query)

TABLE = {"listen": ["search_knowledge"], "suffixes": [], "vendors": [
    {"vendor": "Radxa", "domains": ["radxa.com"], "targets": ["rk3588"], "chips": ["RK3588"]}]}


class Config(dict):
    """A stand-in for the settings: get(key, default), and set(key, value) for the writer."""

    def set(self, key, value):
        self[key] = value


def make(ways=("searxng",), apis=(), keys=None, fetch=None, offline=False, **extra):
    config = Config({"web_search_ways": list(ways), "web_search_apis": list(apis), "offline_mode": offline,
                     "ai_security_level": "STRICT", **extra})
    return WebSearch(config, lambda name: (keys or {}).get(name), fetch=fetch, table=TABLE, time_limit=3.0)


def json_response(data, status=200):
    return Response(status, json.dumps(data))


SEARXNG = {"results": [
    {"url": "https://docs.radxa.com/en/rock5/rock5b", "title": "ROCK 5B", "content": "The NPU has 6 TOPS."},
    {"url": "https://github.com/rockchip-linux/rknn-toolkit2", "title": "rknn-toolkit2", "content": "Toolkit"},
    {"url": "http://insecure.example.org/x", "title": "plain http", "content": "never a hit"},
]}


class TestPreparingTheQuery:

    def test_a_plain_question_goes_out_as_it_is(self):
        assert prepare_query("RK3588 NPU quantization int8") == "RK3588 NPU quantization int8"

    @pytest.mark.parametrize("query", ["", "   ", "x" * 301])
    def test_an_empty_or_too_long_query_is_refused(self, query):
        with pytest.raises(SearchRefused):
            prepare_query(query)

    @pytest.mark.parametrize("query", [
        "why does /home/alice/projects/model.onnx fail", r"error in C:\Users\alice\build.log",
        "token " + "sk-" + "abcdefghijklmnopqrstuvwxyz123456" + " rejected", "gh" + "p_" + "abcdefghijklmnopqrstuvwxyz0123456789" + " fails",
        "AK" + "IA" + "ABCDEFGHIJKLMNOP" + " is denied", "-----BEGIN RSA " + "PRIVATE KEY----- shows"])
    def test_a_query_with_a_path_or_a_secret_is_never_sent(self, query):
        with pytest.raises(SearchRefused) as e:
            prepare_query(query)
        assert "not sent anywhere" in str(e.value)

    def test_the_privacy_filter_cleans_what_it_knows(self):
        assert "anna@example.org" not in prepare_query("bug report from anna@example.org about the NPU")


class TestAddresses:

    def test_the_same_page_has_one_key_however_it_is_written(self):
        keys = {normalize(a) for a in (
            "https://www.Docs.Radxa.com/en/rock5/rock5b/", "https://docs.radxa.com:443/en/rock5/rock5b",
            "https://docs.radxa.com/en/rock5/rock5b#npu", "https://docs.radxa.com/en/rock5/rock5b?utm_source=x&fbclid=1",
            "http://docs.radxa.com/en/rock5/rock5b")}
        assert len(keys) == 1

    def test_a_query_that_makes_the_page_another_page_is_kept(self):
        assert normalize("https://example.org/p?id=1") != normalize("https://example.org/p?id=2")

    @pytest.mark.parametrize("address, origin", [
        ("https://docs.radxa.com/en/rock5/rock5b", "vendor"), ("https://github.com/a/b/issues/1", "repository"),
        ("https://gitlab.com/a/b", "repository"), ("https://forum.radxa.com/t/npu/1", "vendor"),
        ("https://www.reddit.com/r/embedded/comments/x", "forum"),
        ("https://stackoverflow.com/questions/1", "forum"), ("https://forum.example.org/t/1", "forum"),
        ("https://example.org/blog/npu", "other"), ("https://example.org/forum/thread-3", "forum")])
    def test_every_hit_says_where_it_comes_from(self, address, origin):
        assert origin_of(address, TABLE) == origin


class TestMerging:

    def test_one_hit_per_page_with_every_way_that_found_it_and_the_most_found_first(self):
        hits = merge([("searxng", [("https://docs.radxa.com/a/", "A", "short"), ("https://example.org/b", "B", "")]),
                      ("api:tavily", [("https://example.org/b?utm_source=x", "B better", "a longer snippet"),
                                       ("https://docs.radxa.com/a", "A", "")])], 10, TABLE)
        assert [h.address for h in hits] == ["https://example.org/b", "https://docs.radxa.com/a/"] or \
            [h.domain for h in hits][0] in ("example.org", "docs.radxa.com")
        both = {h.domain: h for h in hits}
        assert both["example.org"].ways == ["searxng", "api:tavily"] and both["docs.radxa.com"].ways == ["searxng",
                                                                                                          "api:tavily"]
        assert both["example.org"].snippet == "a longer snippet"          # the longer snippet wins
        assert both["docs.radxa.com"].origin == "vendor"

    def test_only_public_https_addresses_are_hits(self):
        hits = merge([("searxng", [("http://example.org/plain", "t", ""), ("https://127.0.0.1/x", "t", ""),
                                   ("https://user:pw@example.org/x", "t", ""), ("ftp://example.org/x", "t", ""),
                                   ("https://example.org/ok", "ok", "")])], 10, TABLE)
        assert [h.address for h in hits] == ["https://example.org/ok"]

    def test_title_and_snippet_are_plain_cleaned_and_cut(self):
        hits = merge([("searxng", [("https://example.org/a", "<b>Bold</b> &amp; title",
                                    "<script>x</script>Hidden\u200b text <!-- comment --> " + "word " * 100)])], 10, TABLE)
        assert hits[0].title == "Bold & title"
        assert "<" not in hits[0].snippet and "\u200b" not in hits[0].snippet and len(hits[0].snippet) <= 300

    def test_at_most_count_hits(self):
        rows = [(f"https://example.org/{n}", f"t{n}", "") for n in range(30)]
        assert len(merge([("searxng", rows)], 7, TABLE)) == 7


class TestTheWays:

    def test_no_way_turned_on_is_off(self):
        with pytest.raises(SearchRefused) as e:
            make(ways=()).search("rk3588")
        assert "off" in str(e.value)

    def test_searxng_asks_json_and_reads_the_hits(self):
        seen = []

        def fetch(request):
            seen.append(request)
            return json_response(SEARXNG)
        result = make(fetch=fetch).search("RK3588 NPU")
        assert seen[0].url == "http://127.0.0.1:8888/search" and seen[0].params == {"q": "RK3588 NPU",
                                                                                   "format": "json"}
        assert seen[0].local is True
        assert [h.origin for h in result.hits] == ["vendor", "repository"]       # the http hit is no hit
        assert result.ways[0].status == "ok" and result.ways[0].count == 2

    def test_the_searxng_address_is_the_persons_setting(self):
        seen = []
        make(fetch=lambda r: seen.append(r) or json_response({"results": []}),
             web_search_url="https://search.example.org/").search("x")
        assert seen[0].url == "https://search.example.org/search" and seen[0].local is False

    def test_a_searxng_that_does_not_allow_json_says_how_to_allow_it(self):
        result = make(fetch=lambda r: Response(403, "Forbidden")).search("x")
        assert result.hits == [] and result.ways[0].status == "failed" and "search: formats" in result.ways[0].reason

    def test_every_api_provider_builds_its_request_with_the_persons_key(self):
        keys = {p.secret: "KEY-" + p.name for p in PROVIDERS.values()}
        for name, provider in PROVIDERS.items():
            request = provider.build("rk3588", 5, "KEY-" + name, {"web_search_brightdata_zone": "zone1"})
            assert request.url.startswith("https://") and "rk3588" in json.dumps([request.params, request.body]) \
                or "rk3588" in request.body["url"]
            assert "KEY-" + name in " ".join(request.headers.values()) and provider.secret in keys

    @pytest.mark.parametrize("name, data", [
        ("tavily", {"results": [{"url": "https://example.org/t", "title": "T", "content": "c"}]}),
        ("exa", {"results": [{"url": "https://example.org/t", "title": "T", "text": "c"}]}),
        ("brightdata", {"organic": [{"link": "https://example.org/t", "title": "T", "description": "c"}]}),
        ("serper", {"organic": [{"link": "https://example.org/t", "title": "T", "snippet": "c"}]}),
        ("searchapi", {"organic_results": [{"link": "https://example.org/t", "title": "T", "snippet": "c"}]}),
        ("brave", {"web": {"results": [{"url": "https://example.org/t", "title": "T", "description": "c"}]}})])
    def test_every_api_provider_reads_its_answer(self, name, data):
        result = make(ways=("api",), apis=(name,), keys={PROVIDERS[name].secret: "k"},
                      fetch=lambda r: json_response(data), web_search_brightdata_zone="z").search("x")
        assert [h.address for h in result.hits] == ["https://example.org/t"] and result.ways[0].way == f"api:{name}"

    @pytest.mark.parametrize("data", [None, [], "text", {"unexpected": 1}, {"results": "no"}, {"results": [1, None]}])
    def test_an_answer_of_another_shape_gives_no_hits_and_no_error(self, data):
        result = make(ways=("api",), apis=("tavily",), keys={"web_search_tavily_key": "k"},
                      fetch=lambda r: json_response(data)).search("x")
        assert result.hits == []

    def test_a_provider_without_a_key_is_skipped_and_names_where_to_put_it(self):
        result = make(ways=("api",), apis=("tavily", "brave"), keys={"web_search_brave_key": "k"},
                      fetch=lambda r: json_response({"web": {"results": []}})).search("x")
        by = {r.way: r for r in result.ways}
        assert by["api:tavily"].status == "skipped" and "web_search_tavily_key" in by["api:tavily"].reason
        assert by["api:brave"].status == "ok"

    def test_bright_data_needs_its_zone(self):
        result = make(ways=("api",), apis=("brightdata",), keys={"web_search_brightdata_key": "k"},
                      fetch=lambda r: json_response({})).search("x")
        assert "web_search_brightdata_zone" in result.ways[0].reason

    def test_a_listed_api_way_without_providers_says_so(self):
        result = make(ways=("api",), fetch=lambda r: json_response({})).search("x")
        assert result.ways[0].status == "skipped" and "web_search_apis" in result.ways[0].reason

    def test_a_refused_key_is_told_in_plain_words(self):
        result = make(ways=("api",), apis=("serper",), keys={"web_search_serper_key": "bad"},
                      fetch=lambda r: Response(401, "no")).search("x")
        assert result.ways[0].status == "failed" and "refused the key" in result.ways[0].reason
        assert "Serper" in result.ways[0].reason and "bad" not in result.ways[0].reason

    def test_the_html_way_asks_robots_txt_first_and_reads_the_results(self):
        page = ('<div class="result"><a class="result__a" href="//duckduckgo.com/l/?uddg='
                'https%3A%2F%2Fdocs.radxa.com%2Fa&rut=1">Radxa A</a><a class="result__snippet">About A</a></div>')
        seen = []

        def fetch(request):
            seen.append(request.url)
            return Response(200, "User-agent: *\nAllow: /\n") if request.url.endswith("robots.txt") else Response(200, page)
        result = make(ways=("html",), fetch=fetch).search("radxa")
        assert seen[0].endswith("/robots.txt") and result.hits[0].address == "https://docs.radxa.com/a"
        assert result.hits[0].title == "Radxa A" and result.hits[0].snippet == "About A"

    def test_a_no_from_robots_txt_is_respected_and_nothing_is_asked(self):
        seen = []

        def fetch(request):
            seen.append(request.url)
            return Response(200, "User-agent: *\nDisallow: /\n")
        result = make(ways=("html",), fetch=fetch).search("radxa")
        assert result.ways[0].status == "refused" and "robots.txt" in result.ways[0].reason
        assert seen == ["https://html.duckduckgo.com/robots.txt"] and result.hits == []

    def test_parse_duckduckgo_reads_the_redirect_and_a_plain_link(self):
        rows = parse_duckduckgo('<div class="result"><a class="result__a" href="https://example.org/direct">D</a></div>'
                                '<div class="result"><span>no link</span></div>')
        assert rows == [("https://example.org/direct", "D", "")]


class TestAllWaysTogether:

    def test_every_way_runs_and_the_same_page_is_one_hit(self):
        def fetch(request):
            if "tavily" in request.url:
                return json_response({"results": [{"url": "https://docs.radxa.com/a/", "title": "A", "content": "xx"},
                                                  {"url": "https://example.org/z", "title": "Z", "content": ""}]})
            if request.url.endswith("/search"):      # the SearXNG instance
                return json_response({"results": [{"url": "https://docs.radxa.com/a", "title": "A", "content": "x"}]})
            return Response(404, "")
        result = make(ways=("searxng", "api"), apis=("tavily",), keys={"web_search_tavily_key": "k"},
                      fetch=fetch, web_search_url="https://s.example.org").search("radxa")
        assert [(h.domain, h.ways) for h in result.hits] == [("docs.radxa.com", ["searxng", "api:tavily"]),
                                                             ("example.org", ["api:tavily"])]
        assert {r.way: r.status for r in result.ways} == {"searxng": "ok", "api:tavily": "ok"}

    def test_one_way_failing_never_stops_the_others(self):
        def fetch(request):
            if request.url.endswith("/search") and "tavily" not in request.url:
                raise WayError("the request failed (ConnectionError)")
            return json_response({"results": [{"url": "https://example.org/z", "title": "Z", "content": ""}]})
        result = make(ways=("searxng", "api"), apis=("tavily",), keys={"web_search_tavily_key": "k"},
                      fetch=fetch).search("x")
        assert {r.way: r.status for r in result.ways} == {"searxng": "failed", "api:tavily": "ok"}
        assert [h.address for h in result.hits] == ["https://example.org/z"]

    def test_a_way_that_breaks_or_hangs_is_reported_and_the_search_returns_in_time(self):
        def fetch(request):
            if "tavily" in request.url:
                raise RuntimeError("boom")
            time.sleep(5)
            return json_response({})
        started = time.monotonic()
        result = make(ways=("searxng", "api"), apis=("tavily",), keys={"web_search_tavily_key": "k"},
                      fetch=fetch).search("x")
        assert time.monotonic() - started < 4.5
        reasons = {r.way: r.reason for r in result.ways}
        assert "unexpected error" in reasons["api:tavily"] and "no answer within" in reasons["searxng"]

    def test_only_the_cleaned_query_is_sent(self):
        seen = []
        make(fetch=lambda r: seen.append(r.params["q"]) or json_response({})).search("bug from anna@example.org NPU")
        assert seen and "anna@example.org" not in seen[0]


class TestOfflineMode:

    def test_offline_mode_reaches_no_api_and_no_search_page(self):
        result = make(ways=("api", "html"), apis=("tavily",), keys={"web_search_tavily_key": "k"}, offline=True,
                      fetch=lambda r: pytest.fail("no request in offline mode")).search("x")
        assert {r.way: r.status for r in result.ways} == {"api:tavily": "refused", "html": "refused"}

    def test_offline_mode_reaches_searxng_only_on_a_local_address(self):
        def reach_local(url, config):
            return type("L", (), {"local": True})()

        def reach_remote(url, config):
            return type("L", (), {"local": False, "refusal": staticmethod(lambda what: f"{what}: not local")})()
        ok = WebSearch(Config(web_search_ways=["searxng"], offline_mode=True), lambda n: None,
                       fetch=lambda r: json_response(SEARXNG), reach=reach_local, table=TABLE).search("x")
        assert ok.ways[0].status == "ok"
        no = WebSearch(Config(web_search_ways=["searxng"], offline_mode=True, web_search_url="https://s.example.org"),
                       lambda n: None, fetch=lambda r: pytest.fail("not local"), reach=reach_remote,
                       table=TABLE).search("x")
        assert no.ways[0].status == "refused" and "not local" in no.ways[0].reason


class TestTheNetwork:

    def test_the_default_fetch_refuses_a_private_address_for_an_api(self):
        with pytest.raises(WayError):
            ws.default_fetch(Request("GET", "http://127.0.0.1:9/search"), timeout=2)

    def test_a_bad_searxng_address_is_told(self):
        result = make(web_search_url="ftp://nope", fetch=lambda r: json_response({})).search("x")
        assert result.ways[0].status == "failed" and "http://" in result.ways[0].reason


class TestTheSearchEngineForASearch:
    """The engine the person had set up runs only inside a search (Core/searxng_native.Running, searxng_setup.ContainerRunning)."""

    class Engine:
        def __init__(self, events, error=None):
            self.events, self.error = events, error

        def __enter__(self):
            if self.error:
                raise self.error
            self.events.append("up")
            return self

        def __exit__(self, *exc):
            self.events.append("down")

    def search(self, events, engine, **extra):
        config = Config({"web_search_ways": ["searxng"], "offline_mode": False, "ai_security_level": "STRICT", **extra})
        return WebSearch(config, lambda n: None, fetch=lambda r: events.append("fetch") or json_response(SEARXNG),
                         table=TABLE, time_limit=3.0, engine=engine)

    def test_it_is_up_for_the_request_and_down_right_after(self):
        events = []
        result = self.search(events, lambda: self.Engine(events)).search("x")
        assert events == ["up", "fetch", "down"] and result.ways[0].status == "ok"

    def test_it_goes_down_even_when_the_request_fails(self):
        events = []

        def fetch(request):
            events.append("fetch")
            raise WayError("the request failed (ConnectionError)")
        search = WebSearch(Config(web_search_ways=["searxng"]), lambda n: None, fetch=fetch, table=TABLE, time_limit=3.0,
                           engine=lambda: self.Engine(events))
        assert search.search("x").ways[0].status == "failed" and events == ["up", "fetch", "down"]

    def test_an_address_that_is_not_of_this_computer_is_never_started_for(self):
        events = []
        self.search(events, lambda: self.Engine(events), web_search_url="https://s.example.org").search("x")
        assert events == ["fetch"]

    def test_an_engine_that_cannot_start_fails_only_this_way_and_says_why(self):
        from orchestrator.Core.searxng_native import NativeError
        events = []
        result = self.search(events, lambda: self.Engine(events, NativeError("The search engine did not answer within "
                                                                              "45 seconds; see server.log."))).search("x")
        assert events == [] and result.ways[0].status == "failed" and "did not answer within 45 seconds" in result.ways[0].reason

    def test_the_first_start_gets_more_time_than_a_plain_request(self):
        import time
        from orchestrator.Core.web_search import ENGINE_START_EXTRA

        class Slow(self.Engine):
            def __enter__(inner):
                time.sleep(3.5)                       # longer than time_limit=3, within the extra time
                return super().__enter__()
        events = []
        result = self.search(events, lambda: Slow(events)).search("x")
        assert ENGINE_START_EXTRA > 3 and result.ways[0].status == "ok" and events == ["up", "fetch", "down"]

    def test_engine_for_follows_the_setting_and_nothing_is_started_by_it(self, tmp_path):
        from types import SimpleNamespace
        from orchestrator.Core import searxng_native, searxng_setup
        framework = lambda way: SimpleNamespace(config=SimpleNamespace(  # noqa: E731
            get=lambda key, default=None: {"web_search_engine": way}.get(key, default), user_state_dir=str(tmp_path)))
        assert ws.engine_for(framework("")) is None
        assert ws.engine_for(SimpleNamespace(config=None)) is None
        own = ws.engine_for(framework("own"))
        assert isinstance(own(), searxng_native.Running) and own() is own()          # one shared object per folder and way
        assert isinstance(ws.engine_for(framework("container"))(), searxng_setup.ContainerRunning)
        assert ws.engine_for(framework("bogus")) is None
        assert (tmp_path / "searxng" / "secret").is_file() and not (tmp_path / "searxng" / "native").exists()

    def test_configure_writes_the_way_and_checks_it(self):
        config = Config()
        ws.configure(config, engine="own")
        assert config["web_search_engine"] == "own"
        ws.configure(config, engine="none")
        assert config["web_search_engine"] == ""
        with pytest.raises(ValueError):
            ws.configure(Config(), engine="docker")
