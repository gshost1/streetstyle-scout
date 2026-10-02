import json

import pytest
from fastapi.testclient import TestClient

import server


@pytest.fixture
def moment():
    return {
        "id": "sf-000123",
        "collection": "San Francisco",
        "clip_id": "verified-sf-clip.mp4",
        "timestamp_seconds": 83.5,
        "duration": 4.0,
        "frame_url": "/frames/sf-000123.jpg",
        "video_url": "/clips/verified-sf-clip.mp4",
        "bounding_box": {"x": 0.41, "y": 0.16, "w": 0.2, "h": 0.66},
        "readability": "clear",
        "observed": [
            {"kind": "garment", "value": "puffer jacket"},
            {"kind": "color", "value": "bright yellow"},
            {"kind": "accessory", "value": "black backpack"},
        ],
        "uncertain": [
            {
                "kind": "garment",
                "value": "dark trousers",
                "note": "legs out of frame",
            }
        ],
        "description": "A bright yellow puffer jacket and black backpack are visible.",
        "match_reason": "Matched “yellow” and “backpack”.",
        "provenance": {
            "source": "vast",
            "detection": "yolo",
            "analysis": "cosmos",
        },
        "score": 0.91,
    }


@pytest.fixture
def catalog(tmp_path, monkeypatch, moment):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps({"moments": [moment]}), encoding="utf-8")
    monkeypatch.setenv("SCOUT_CATALOG_PATH", str(path))
    server.load_catalog.cache_clear()
    yield path
    server.load_catalog.cache_clear()


@pytest.fixture
def client():
    return TestClient(server.app)


def test_status_never_claims_external_services_are_ok(client, catalog):
    response = client.get("/api/status")

    assert response.status_code == 200
    services = response.json()["services"]
    assert set(services) == {"vast", "yolo", "cosmos", "wandb"}
    assert {service["state"] for service in services.values()} == {"not_connected"}


def test_search_returns_matching_verified_catalog_moment(client, catalog):
    response = client.post(
        "/api/search",
        json={"query": "yellow backpack", "dataset": "sf"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["mode"] == "live"
    assert isinstance(body["meta"]["tookMs"], int)
    assert body["results"][0]["id"] == "sf-000123"
    assert body["results"][0]["score"] == 1
    assert body["results"][0]["provenance"] == {
        "source": "vast",
        "detection": "yolo",
        "analysis": "cosmos",
    }
    assert body["meta"]["services"]["vast"]["state"] == "not_connected"


def test_search_respects_dataset_filter(client, catalog):
    response = client.post(
        "/api/search",
        json={"query": "yellow", "dataset": "to"},
    )

    assert response.status_code == 200
    assert response.json()["results"] == []


def test_moment_lookup_and_not_found_contract(client, catalog):
    found = client.get("/api/moments/sf-000123")
    missing = client.get("/api/moments/missing")

    assert found.status_code == 200
    assert found.json()["moment"]["id"] == "sf-000123"
    assert missing.status_code == 404
    assert missing.json() == {
        "error": "not_found",
        "message": "No moment missing",
    }


def test_missing_catalog_returns_service_error(client, monkeypatch):
    monkeypatch.delenv("SCOUT_CATALOG_PATH", raising=False)

    response = client.post(
        "/api/search",
        json={"query": "jacket", "dataset": "both"},
    )

    assert response.status_code == 503
    assert response.json()["error"] == "service_error"
    assert response.json()["service"] == "vast"
    assert response.json()["services"]["vast"]["state"] == "error"


@pytest.mark.parametrize(
    "payload",
    [
        {"query": "", "dataset": "both"},
        {"query": "jacket", "dataset": "invalid"},
        {"query": "jacket", "dataset": "sf", "unexpected": True},
    ],
)
def test_invalid_search_request_uses_json_error_contract(client, payload):
    response = client.post("/api/search", json=payload)

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"
    assert isinstance(response.json()["message"], str)


def test_catalog_rejects_privacy_violations(client, catalog, moment):
    moment["description"] = "A woman wearing a yellow jacket."
    catalog.write_text(json.dumps([moment]), encoding="utf-8")

    response = client.post(
        "/api/search",
        json={"query": "yellow", "dataset": "both"},
    )

    assert response.status_code == 500
    assert response.json()["error"] == "service_error"
    assert response.json()["service"] == "vast"


def test_catalog_rejects_out_of_frame_bounding_box(client, catalog, moment):
    moment["bounding_box"] = {"x": 0.9, "y": 0.1, "w": 0.2, "h": 0.5}
    catalog.write_text(json.dumps([moment]), encoding="utf-8")

    response = client.get("/api/status")

    assert response.status_code == 200
    assert response.json()["services"]["vast"]["state"] == "error"
