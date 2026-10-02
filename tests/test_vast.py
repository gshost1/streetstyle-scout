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
ENV = {
    "VAST_INGRESS_URL": BASE,
    "VAST_USERNAME": "workshop",
    "VAST_PASSWORD": SECRET,
}


def make_client(handler, **overrides):
    config = VastConfig.from_env({**ENV, **overrides})
    return VastClient(config, transport=httpx.MockTransport(handler))


def login_ok(request):
    assert request.url.path == "/api/v1/auth/login"
    assert json.loads(request.content) == {"username": "workshop", "password": SECRET}
    return httpx.Response(200, json={"access_token": TOKEN})


def test_missing_config_fails_clearly_without_leaking_values():
    with pytest.raises(VastNotConfigured) as caught:
        VastConfig.from_env({"VAST_PASSWORD": SECRET})

    assert caught.value.status_code == 503
    assert caught.value.missing == ["VAST_INGRESS_URL", "VAST_USERNAME"]
    assert SECRET not in caught.value.message


def test_from_env_reads_process_environment(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(VastNotConfigured):
        VastClient.from_env()


def test_invalid_url_is_rejected():
    with pytest.raises(VastError) as caught:
        VastConfig.from_env({**ENV, "VAST_INGRESS_URL": "vast.example.test"})
    assert caught.value.status_code == 503
    assert "vast.example.test" not in caught.value.message


def test_config_repr_hides_secrets_and_url():
    text = repr(VastConfig.from_env(ENV))
    assert SECRET not in text
    assert BASE not in text


def test_search_logs_in_and_returns_only_returned_values():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path == "/api/v1/auth/login":
            return login_ok(request)
        assert request.url.path == "/api/v1/search"
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert json.loads(request.content) == {"query": "yellow jacket", "limit": 5}
        return httpx.Response(
            200,
            json={
                "results": [
                    {"clip_id": "sf4_chunk_0008.mp4", "timestamp_seconds": 83.5, "duration": 4, "score": 0.9},
                    {"filename": "to1_chunk_0002.mp4", "start": "12.0"},
                ]
            },
        )

    with make_client(handler) as client:
        results = client.search("yellow jacket", limit=5)

    assert seen == ["/api/v1/auth/login", "/api/v1/search"]
    assert results[0].clip_id == "sf4_chunk_0008.mp4"
    assert results[0].timestamp_seconds == 83.5
    assert results[0].duration == 4
    assert results[1].clip_id == "to1_chunk_0002.mp4"
    assert results[1].timestamp_seconds == 12.0
    assert results[1].duration is None
    assert results[1].score is None
    assert results[1].collection is None


@pytest.mark.parametrize(
    "item",
    [
        {"timestamp_seconds": 1.0},
        {"clip_id": "a.mp4"},
        {"clip_id": "a.mp4", "timestamp_seconds": "soon"},
        {"clip_id": "a.mp4", "timestamp_seconds": -1},
        "a.mp4",
    ],
)
def test_search_rejects_ungrounded_results(item):
    def handler(request):
        if request.url.path.endswith("/login"):
            return login_ok(request)
        return httpx.Response(200, json={"results": [item]})

    with make_client(handler) as client, pytest.raises(VastSchemaError):
        client.search("jacket")


def test_search_rejects_unrecognized_body():
    def handler(request):
        if request.url.path.endswith("/login"):
            return login_ok(request)
        return httpx.Response(200, json={"hits": []})

    with make_client(handler) as client, pytest.raises(VastSchemaError):
        client.search("jacket")


def test_bad_credentials_map_to_clear_error():
    def handler(request):
        return httpx.Response(401, json={"detail": "bad"})

    with make_client(handler) as client, pytest.raises(VastError) as caught:
        client.search("jacket")
    assert caught.value.detail == "authentication failed"
    assert caught.value.status_code == 502


def test_expired_token_relogs_once():
    calls = {"login": 0, "search": 0}

    def handler(request):
        if request.url.path.endswith("/login"):
            calls["login"] += 1
            return httpx.Response(200, json={"token": f"t{calls['login']}"})
        calls["search"] += 1
        if request.headers["Authorization"] == "Bearer t1":
            return httpx.Response(401)
        return httpx.Response(200, json=[])

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
    assert BASE not in caplog.text
    assert SECRET not in caplog.text


def test_successful_calls_never_log_secrets_or_url(caplog):
    def handler(request):
        if request.url.path.endswith("/login"):
            return login_ok(request)
        return httpx.Response(200, json={"results": []})

    caplog.set_level(logging.DEBUG)
    with make_client(handler) as client:
        client.search("jacket")
    assert "vast.example.test" not in caplog.text
    assert SECRET not in caplog.text
    assert TOKEN not in caplog.text


def test_explore_lists_videos():
    def handler(request):
        if request.url.path.endswith("/login"):
            return login_ok(request)
        assert request.url.path == "/api/v1/videos/explore"
        return httpx.Response(200, json={"videos": [{"video_id": "sf4.mp4", "duration": 600, "collection": "sf"}]})

    with make_client(handler) as client:
        videos = client.explore()
    assert [(v.clip_id, v.duration, v.collection) for v in videos] == [("sf4.mp4", 600, "sf")]


def test_stream_forwards_range_and_partial_content():
    def handler(request):
        if request.url.path.endswith("/login"):
            return login_ok(request)
        assert request.url.path == "/api/v1/videos/stream"
        assert request.url.params["filename"] == "sf4.mp4"
        assert request.headers["Range"] == "bytes=0-3"
        return httpx.Response(
            206,
            content=b"\x00\x01\x02\x03",
            headers={
                "Content-Type": "video/mp4",
                "Content-Range": "bytes 0-3/100",
                "Accept-Ranges": "bytes",
                "Content-Length": "4",
                "Location": f"{BASE}/internal/sf4.mp4",
            },
        )

    with make_client(handler) as client:
        stream = client.stream("sf4.mp4", "bytes=0-3")
        body = b"".join(stream.iter_bytes())

    assert stream.status_code == 206
    assert body == b"\x00\x01\x02\x03"
    assert stream.headers == {
        "content-type": "video/mp4",
        "content-length": "4",
        "content-range": "bytes 0-3/100",
        "accept-ranges": "bytes",
    }


@pytest.mark.parametrize("status,expected", [(404, 404), (416, 416), (500, 502)])
def test_stream_errors(status, expected):
    def handler(request):
        if request.url.path.endswith("/login"):
            return login_ok(request)
        return httpx.Response(status)

    with make_client(handler) as client, pytest.raises(VastError) as caught:
        client.stream("sf4.mp4")
    assert caught.value.status_code == expected


def test_health_returns_latency():
    with make_client(login_ok) as client:
        assert client.health() >= 0
