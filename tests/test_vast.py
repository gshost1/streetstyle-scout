import json
import logging

import httpx
import pytest

from backend.vast import (
    VastClient,
    VastConfig,
    VastError,
    VastNotConfigured,
    VastSchemaError,
)

BASE = "https://vast.example.test"
SECRET = "s3cret-pass"
TOKEN = "tok-123"
SOURCE = "s3://workshop-bucket/sf/20261001_101025_sf4_chunk_0008.mp4"
CLIP = "20261001_101025_sf4_chunk_0008.mp4"
ENV = {"INGRESS_URL": BASE, "USERNAME": "workshop", "PASSWORD": SECRET}


def make_client(handler, **overrides):
    return VastClient(VastConfig.from_env({**ENV, **overrides}), transport=httpx.MockTransport(handler))


def login_ok(request):
    assert request.url.path == "/api/v1/auth/login"
    assert json.loads(request.content) == {"username": "workshop", "password": SECRET}
    return httpx.Response(200, json={"access_token": TOKEN})


def with_login(handler):
    def wrapped(request):
        if request.url.path == "/api/v1/auth/login":
            return login_ok(request)
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        return handler(request)

    return wrapped


def test_missing_config_names_variables_without_values():
    with pytest.raises(VastNotConfigured) as caught:
        VastConfig.from_env({"PASSWORD": SECRET})

    assert caught.value.status_code == 503
    assert caught.value.missing == ["VAST_INGRESS_URL or INGRESS_URL", "VAST_USERNAME or USERNAME"]
    assert SECRET not in caught.value.message


def test_vast_prefixed_aliases_take_precedence():
    config = VastConfig.from_env(
        {**ENV, "VAST_INGRESS_URL": "https://other.test/", "VAST_USERNAME": "u2", "VAST_PASSWORD": "p2"}
    )
    assert (config.base_url, config.username, config.password) == ("https://other.test", "u2", "p2")


def test_from_env_reads_process_environment():
    with pytest.raises(VastNotConfigured):
        VastClient.from_env()


def test_invalid_url_is_rejected_without_echoing_it():
    with pytest.raises(VastError) as caught:
        VastConfig.from_env({**ENV, "INGRESS_URL": "vast.example.test"})
    assert caught.value.status_code == 503
    assert "vast.example.test" not in caught.value.message


def test_config_repr_hides_secrets_and_url():
    text = repr(VastConfig.from_env(ENV))
    assert SECRET not in text and BASE not in text


def test_search_sends_workshop_body_and_parses_sources():
    def handler(request):
        assert request.url.path == "/api/v1/search"
        assert json.loads(request.content) == {
            "query": "yellow jacket",
            "top_k": 15,
            "llm_top_n": 3,
            "include_public": True,
        }
        return httpx.Response(200, json={"results": [{"source": SOURCE}]})

    with make_client(with_login(handler)) as client:
        hits = client.search("yellow jacket")

    assert [hit.clip_id for hit in hits] == [CLIP]
    assert hits[0].source == SOURCE
    assert SOURCE not in repr(hits[0])


@pytest.mark.parametrize(
    "body",
    [
        {"hits": []},
        {"results": [{"clip_id": CLIP}]},
        {"results": [{"source": "https://cdn.test/a.mp4"}]},
        {"results": ["a.mp4"]},
    ],
)
def test_search_rejects_unknown_schema(body):
    handler = with_login(lambda request: httpx.Response(200, json=body))
    with make_client(handler) as client, pytest.raises(VastSchemaError):
        client.search("jacket")


def test_login_without_access_token_is_schema_error():
    with make_client(lambda request: httpx.Response(200, json={"token": TOKEN})) as client:
        with pytest.raises(VastSchemaError):
            client.login()


def test_bad_credentials_map_to_clear_error():
    with make_client(lambda request: httpx.Response(401)) as client, pytest.raises(VastError) as caught:
        client.search("jacket")
    assert (caught.value.status_code, caught.value.detail) == (502, "authentication failed")


def test_expired_token_relogs_once():
    calls = {"login": 0, "search": 0}

    def handler(request):
        if request.url.path.endswith("/login"):
            calls["login"] += 1
            return httpx.Response(200, json={"access_token": f"t{calls['login']}"})
        calls["search"] += 1
        if request.headers["Authorization"] == "Bearer t1":
            return httpx.Response(401)
        return httpx.Response(200, json={"results": []})

    with make_client(handler) as client:
        assert client.search("jacket") == []
    assert calls == {"login": 2, "search": 2}


def test_timeout_and_unreachable_errors_do_not_leak_url(caplog):
    def timeout(request):
        raise httpx.ReadTimeout("timed out", request=request)

    def refused(request):
        raise httpx.ConnectError(f"cannot connect to {BASE}", request=request)

    caplog.set_level(logging.DEBUG)
    with make_client(timeout, VAST_TIMEOUT_S="2") as client, pytest.raises(VastError) as slow:
        client.search("jacket")
    with make_client(refused) as client, pytest.raises(VastError) as down:
        client.search("jacket")

    assert (slow.value.status_code, slow.value.detail) == (504, "timeout")
    assert slow.value.message == "VAST request timed out after 2 s"
    assert down.value.status_code == 503
    for error in (slow.value, down.value):
        assert BASE not in str(error) and BASE not in error.detail
        assert error.__cause__ is None and error.__suppress_context__
    assert BASE not in caplog.text and SECRET not in caplog.text


def test_successful_calls_never_log_secrets_url_or_source(caplog):
    caplog.set_level(logging.DEBUG)
    handler = with_login(lambda request: httpx.Response(200, json={"results": [{"source": SOURCE}]}))
    with make_client(handler) as client:
        client.search("jacket")
    for secret in ("vast.example.test", SECRET, TOKEN, SOURCE):
        assert secret not in caplog.text


def test_explore_sends_scope_paging_and_location():
    def handler(request):
        assert request.url.path == "/api/v1/videos/explore"
        assert dict(request.url.params) == {"scope": "all", "limit": "10", "offset": "20", "location": "sf"}
        return httpx.Response(200, json={"videos": [{"source": SOURCE}]})

    with make_client(with_login(handler)) as client:
        videos = client.explore(limit=10, offset=20, location="sf")
    assert [video.clip_id for video in videos] == [CLIP]


def test_find_source_pages_explore_until_match():
    pages = {
        "0": [{"source": f"s3://b/other_{i}.mp4"} for i in range(2)],
        "2": [{"source": SOURCE}],
    }

    def handler(request):
        return httpx.Response(200, json={"videos": pages.get(request.url.params["offset"], [])})

    with make_client(with_login(handler)) as client:
        assert client.find_source(CLIP, page_size=2) == SOURCE
        with pytest.raises(VastError) as missing:
            client.find_source("absent.mp4", page_size=2)
    assert missing.value.status_code == 404


def test_stream_uses_s3_source_and_forwards_range():
    def handler(request):
        assert request.url.path == "/api/v1/videos/stream"
        assert dict(request.url.params) == {"source": SOURCE}
        assert request.headers["Range"] == "bytes=0-3"
        return httpx.Response(
            206,
            content=b"\x00\x01\x02\x03",
            headers={
                "Content-Type": "video/mp4",
                "Content-Range": "bytes 0-3/100",
                "Accept-Ranges": "bytes",
                "Content-Length": "4",
                "Location": f"{BASE}/internal",
            },
        )

    with make_client(with_login(handler)) as client:
        stream = client.stream(SOURCE, "bytes=0-3")
        body = b"".join(stream.iter_bytes())

    assert (stream.status_code, body) == (206, b"\x00\x01\x02\x03")
    assert stream.headers == {
        "content-type": "video/mp4",
        "content-length": "4",
        "content-range": "bytes 0-3/100",
        "accept-ranges": "bytes",
    }


def test_stream_refuses_non_s3_source():
    with make_client(login_ok) as client, pytest.raises(VastError) as caught:
        client.stream(CLIP)
    assert caught.value.status_code == 400


@pytest.mark.parametrize("status,expected", [(404, 404), (416, 416), (500, 502)])
def test_stream_errors(status, expected):
    with make_client(with_login(lambda request: httpx.Response(status))) as client:
        with pytest.raises(VastError) as caught:
            client.stream(SOURCE)
    assert caught.value.status_code == expected


def test_health_returns_latency():
    with make_client(login_ok) as client:
        assert client.health() >= 0
