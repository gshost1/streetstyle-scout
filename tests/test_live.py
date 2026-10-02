import httpx
import pytest
from fastapi.testclient import TestClient

import server
from backend.vast import VastClient, VastConfig

BASE = "https://vast.example.test"
SOURCE = "s3://workshop-bucket/sf/20261001_101025_sf4_chunk_0008.mp4"
CLIP = "20261001_101025_sf4_chunk_0008.mp4"
VIDEO = bytes(range(100))


def fake_vast(request):
    path = request.url.path
    if path == "/api/v1/auth/login":
        return httpx.Response(200, json={"access_token": "tok"})
    if path == "/api/v1/search":
        return httpx.Response(200, json={"results": [{"source": SOURCE}]})
    if path == "/api/v1/videos/explore":
        return httpx.Response(200, json={"videos": [{"source": SOURCE}]})
    if path == "/api/v1/videos/stream":
        assert request.url.params["source"] == SOURCE
        if request.headers.get("range") == "bytes=10-19":
            return httpx.Response(
                206,
                content=VIDEO[10:20],
                headers={"Content-Type": "video/mp4", "Content-Range": "bytes 10-19/100", "Content-Length": "10"},
            )
        return httpx.Response(200, content=VIDEO, headers={"Content-Type": "video/mp4"})
    return httpx.Response(404)


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setenv("INGRESS_URL", BASE)
    monkeypatch.setenv("USERNAME", "workshop")
    monkeypatch.setenv("PASSWORD", "secret")
    handler = {"fn": fake_vast}
    client = VastClient(VastConfig.from_env(), transport=httpx.MockTransport(lambda r: handler["fn"](r)))
    monkeypatch.setattr(server, "vast_client", lambda: client)
    yield handler
    client.close()


@pytest.fixture
def api():
    return TestClient(server.app)


def test_live_search_reports_vast_candidates_but_refuses_without_cosmos(api, live):
    response = api.post("/api/search", json={"query": "yellow jacket", "dataset": "sf"})

    assert response.status_code == 503
    body = response.json()
    assert body["error"] == "service_error"
    assert body["service"] == "cosmos"
    assert "results" not in body
    assert body["services"]["vast"]["state"] == "ok"
    assert body["services"]["vast"]["detail"] == "1 candidate clip"
    assert body["services"]["cosmos"]["state"] == "not_connected"
    assert body["services"]["yolo"]["state"] == "not_connected"
    assert SOURCE not in response.text and BASE not in response.text


def test_live_search_surfaces_vast_failure(api, live):
    live["fn"] = lambda request: httpx.Response(503)

    response = api.post("/api/search", json={"query": "jacket", "dataset": "both"})

    assert response.status_code == 502
    assert response.json()["service"] == "vast"
    assert response.json()["services"]["vast"] == {"state": "error", "detail": "http 503"}


def test_live_search_rejects_unknown_schema(api, live):
    live["fn"] = lambda r: httpx.Response(200, json={"access_token": "t", "hits": []})

    response = api.post("/api/search", json={"query": "jacket", "dataset": "both"})

    assert response.status_code == 502
    assert response.json()["services"]["vast"]["detail"] == "unexpected response schema"


def test_status_runs_real_login_check(api, live):
    services = api.get("/api/status").json()["services"]
    assert services["vast"]["state"] == "ok"
    assert {services[name]["state"] for name in ("yolo", "cosmos", "wandb")} == {"not_connected"}

    live["fn"] = lambda request: httpx.Response(401)
    assert api.get("/api/status").json()["services"]["vast"] == {
        "state": "error",
        "detail": "authentication failed",
    }


def test_clip_streams_same_origin_with_range(api, live):
    partial = api.get(f"/clips/{CLIP}", headers={"Range": "bytes=10-19"})
    full = api.get(f"/clips/{CLIP}")

    assert partial.status_code == 206
    assert partial.content == VIDEO[10:20]
    assert partial.headers["content-range"] == "bytes 10-19/100"
    assert partial.headers["content-type"] == "video/mp4"
    assert partial.headers["accept-ranges"] == "bytes"
    assert full.status_code == 200 and full.content == VIDEO


def test_clip_uses_source_remembered_from_search(api, live):
    api.post("/api/search", json={"query": "jacket", "dataset": "sf"})
    seen = []

    def no_explore(request):
        seen.append(request.url.path)
        return fake_vast(request)

    live["fn"] = no_explore
    assert api.get(f"/clips/{CLIP}").status_code == 200
    assert "/api/v1/videos/explore" not in seen


def test_unknown_clip_is_404(api, live):
    assert api.get("/clips/missing.mp4").status_code == 404
    assert api.get("/clips/..%2Fserver.py").status_code == 404


def test_clip_without_vast_config_is_clear_503(api):
    response = api.get(f"/clips/{CLIP}")
    assert response.status_code == 503
    assert response.json()["service"] == "vast"
    assert "INGRESS_URL" in response.json()["message"]


def test_local_clip_supports_range(api, tmp_path, monkeypatch):
    (tmp_path / CLIP).write_bytes(VIDEO)
    monkeypatch.setattr(server, "CLIPS", tmp_path)

    response = api.get(f"/clips/{CLIP}", headers={"Range": "bytes=0-9"})

    assert response.status_code == 206
    assert response.content == VIDEO[:10]
    assert response.headers["content-range"] == "bytes 0-9/100"
