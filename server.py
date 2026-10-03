"""StreetStyle Scout local API.

When VAST is configured, search queries the workshop archive live. Otherwise it
can search a local JSON catalog containing verified VAST moments. It never
imports the invented browser fixtures and never claims an external service
responded when it did not.
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from threading import Lock
from time import perf_counter
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.vast import VastClient, VastConfig, VastError, VastNotConfigured

app = FastAPI(title="StreetStyle Scout")
ROOT = Path(__file__).parent
WEB = ROOT / "web"
SERVICES = ("vast", "yolo", "cosmos", "wandb")

DISALLOWED_TEXT = re.compile(
    r"\b(face|facial|identity|identified|re-?identified|"
    r"age[ds]?|gender|ethnicity|race|racial|"
    r"man|woman|male|female|boy|girl)\b",
    re.IGNORECASE,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchRequest(StrictModel):
    query: str = Field(min_length=1, max_length=500)
    dataset: Literal["sf", "to", "both"]

    @field_validator("query")
    @classmethod
    def query_must_have_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query must contain non-whitespace text")
        return value


class BoundingBox(StrictModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(gt=0, le=1)
    h: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def fits_frame(self) -> "BoundingBox":
        if self.x + self.w > 1 or self.y + self.h > 1:
            raise ValueError("bounding box must fit within the normalized frame")
        return self


class TrackSample(BoundingBox):
    t: float = Field(ge=0)
    conf: float | None = Field(default=None, ge=0, le=1)


class ObservedDetail(StrictModel):
    kind: Literal["garment", "color", "accessory"]
    value: str = Field(min_length=1)


class UncertainDetail(ObservedDetail):
    note: str = Field(min_length=1)


class Provenance(StrictModel):
    source: Literal["vast"]
    detection: Literal["yolo"] | None
    analysis: Literal["cosmos", "human_review"]  # human_review = described by a person from the frame, not by Cosmos


class Moment(StrictModel):
    id: str = Field(min_length=1)
    collection: Literal["San Francisco", "Toronto"]
    clip_id: str = Field(min_length=1)
    timestamp_seconds: float = Field(ge=0)
    duration: float = Field(gt=0)
    frame_url: str | None
    video_url: str | None
    bounding_box: BoundingBox | None
    # Optional in-clip localization track for the reviewed person: boxes over time within THIS clip
    # only. Never links people across clips.
    track: list[TrackSample] | None = None
    readability: Literal["clear", "partial", "unreadable"]
    observed: list[ObservedDetail]
    uncertain: list[UncertainDetail]
    description: str = Field(min_length=1)
    match_reason: str = Field(min_length=1)
    provenance: Provenance
    score: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def enforce_privacy_and_readability(self) -> "Moment":
        texts = [
            self.description,
            self.match_reason,
            *(detail.value for detail in self.observed),
            *(detail.value for detail in self.uncertain),
            *(detail.note for detail in self.uncertain),
        ]
        if any(DISALLOWED_TEXT.search(text) for text in texts):
            raise ValueError("moment contains a prohibited identity or demographic description")
        if self.readability == "unreadable" and self.observed:
            raise ValueError("unreadable moments cannot contain observed details")
        return self


def not_connected_services(catalog_loaded: bool = False) -> dict[str, dict[str, str]]:
    vast_detail = (
        "External search not called; using a local verified catalog"
        if catalog_loaded
        else "Set SCOUT_CATALOG_PATH for offline search or configure VAST"
    )
    return {
        "vast": {"state": "not_connected", "detail": vast_detail},
        "yolo": {"state": "not_connected", "detail": "No live detection call made; any boxes shown were precomputed offline with YOLOv8n on this machine"},
        "cosmos": {"state": "not_connected", "detail": "No live analysis call made"},
        "wandb": {"state": "not_connected", "detail": "No live logging call made"},
    }


def catalog_path() -> Path | None:
    value = os.getenv("SCOUT_CATALOG_PATH")
    return Path(value).expanduser() if value else None


@lru_cache(maxsize=8)
def load_catalog(path_value: str, modified_ns: int) -> tuple[Moment, ...]:
    del modified_ns  # Included in the cache key so file edits trigger a reload.
    data = json.loads(Path(path_value).read_text(encoding="utf-8"))
    records = data["moments"] if isinstance(data, dict) else data
    if not isinstance(records, list):
        raise ValueError("catalog must be a JSON array or an object with a moments array")
    moments = tuple(Moment.model_validate(record) for record in records)
    if len({moment.id for moment in moments}) != len(moments):
        raise ValueError("catalog moment ids must be unique")
    return moments


def current_catalog() -> tuple[Moment, ...] | None:
    path = catalog_path()
    if path is None or not path.is_file():
        return None
    return load_catalog(str(path.resolve()), path.stat().st_mtime_ns)


TOKEN_RE = re.compile(r"[a-z0-9]+")


def search_catalog(moments: tuple[Moment, ...], request: SearchRequest) -> list[Moment]:
    query_tokens = set(TOKEN_RE.findall(request.query.lower()))
    collection = {"sf": "San Francisco", "to": "Toronto"}.get(request.dataset)
    ranked: list[tuple[float, Moment]] = []
    for moment in moments:
        if collection and moment.collection != collection:
            continue
        observed_text = " ".join(detail.value for detail in moment.observed).lower()
        uncertain_text = " ".join(detail.value for detail in moment.uncertain).lower()
        observed_hits = {token for token in query_tokens if token in observed_text}
        uncertain_hits = {token for token in query_tokens if token in uncertain_text}
        score = (len(observed_hits) + 0.4 * len(uncertain_hits - observed_hits)) / len(query_tokens)
        if score <= 0:
            continue
        matched = sorted(observed_hits | uncertain_hits)
        reason = f'Matched {", ".join(f"“{token}”" for token in matched)}.'
        ranked.append(
            (
                score,
                moment.model_copy(
                    update={"score": round(score, 4), "match_reason": reason}
                ),
            )
        )
    return [moment for _, moment in sorted(ranked, key=lambda item: (-item[0], item[1].id))]


def service_error(
    message: str,
    detail: str,
    status_code: int = 503,
    *,
    service: str = "vast",
    services: dict[str, dict] | None = None,
) -> JSONResponse:
    if services is None:
        services = not_connected_services()
        services[service] = {"state": "error", "detail": detail}
    return JSONResponse(
        status_code=status_code,
        content={
            "error": "service_error",
            "service": service,
            "message": message,
            "services": services,
        },
    )


_vast_lock = Lock()
_vast_clients: dict[VastConfig, VastClient] = {}
_clip_sources: dict[str, str] = {}


def vast_client() -> VastClient:
    """Shared client for the current environment; raises VastNotConfigured."""
    config = VastConfig.from_env()
    with _vast_lock:
        client = _vast_clients.get(config)
        if client is None:
            client = _vast_clients[config] = VastClient(config)
        return client


def vast_configured() -> bool:
    try:
        VastConfig.from_env()
    except VastNotConfigured:
        return False
    except VastError:
        return True
    return True


def live_search(body: SearchRequest) -> JSONResponse:
    services = not_connected_services()
    try:
        client = vast_client()
        vast_started = perf_counter()
        hits = client.search(body.query)
    except VastError as exc:
        return service_error(exc.message, exc.detail, exc.status_code)
    services["vast"] = {
        "state": "ok",
        "ms": round((perf_counter() - vast_started) * 1000),
        "detail": f"{len(hits)} candidate clip{'' if len(hits) == 1 else 's'}",
    }
    with _vast_lock:
        _clip_sources.update({hit.clip_id: hit.source for hit in hits})
    services["cosmos"] = {"state": "not_connected", "detail": "Cosmos analysis is not wired into this backend"}
    message = (
        f"VAST returned {len(hits)} candidate clips, but Moments require Cosmos analysis, "
        "which is not connected; no results were produced"
        if hits
        else "VAST returned no candidate clips; Cosmos analysis is not connected"
    )
    return service_error(message, "not connected", 503, service="cosmos", services=services)


@app.exception_handler(RequestValidationError)
async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
    messages = "; ".join(error["msg"] for error in exc.errors())
    return JSONResponse(
        status_code=422,
        content={"error": "invalid_request", "message": messages},
    )


@app.get("/api/status")
def status() -> dict:
    if vast_configured():
        services = not_connected_services()
        try:
            services["vast"] = {"state": "ok", "ms": round(vast_client().health()), "detail": "login succeeded"}
        except VastError as exc:
            services["vast"] = {"state": "error", "detail": exc.detail}
        return {"services": services}
    try:
        catalog_loaded = current_catalog() is not None
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        services = not_connected_services()
        services["vast"] = {"state": "error", "detail": f"Invalid local catalog: {exc}"}
        return {"services": services}
    return {"services": not_connected_services(catalog_loaded)}


@app.post("/api/search")
def search(body: SearchRequest):
    if vast_configured():
        return live_search(body)
    started = perf_counter()
    try:
        moments = current_catalog()
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return service_error("The local moment catalog is invalid", str(exc), 500)
    if moments is None:
        return service_error(
            "Search is not connected: set INGRESS_URL, USERNAME and PASSWORD for VAST, "
            "or SCOUT_CATALOG_PATH for a local verified catalog",
            "not configured",
        )
    results = search_catalog(moments, body)
    return {
        "results": [moment.model_dump(mode="json") for moment in results],
        "meta": {
            "mode": "live",
            "tookMs": round((perf_counter() - started) * 1000),
            "services": not_connected_services(catalog_loaded=True),
        },
    }


@app.get("/api/moments/{moment_id}")
def moment(moment_id: str):
    try:
        moments = current_catalog()
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return service_error("The local moment catalog is invalid", str(exc), 500)
    if moments:
        for item in moments:
            if item.id == moment_id:
                return {
                    "moment": item.model_dump(mode="json"),
                    "meta": {
                        "mode": "live",
                        "services": not_connected_services(catalog_loaded=True),
                    },
                }
    return JSONResponse(
        status_code=404,
        content={"error": "not_found", "message": f"No moment {moment_id}"},
    )


CLIPS = ROOT / "clips"
CLIP_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\.mp4")


@app.get("/clips/{clip_id}")
def clip(clip_id: str, request: Request):
    if not CLIP_ID_RE.fullmatch(clip_id):
        return JSONResponse(status_code=404, content={"error": "not_found", "message": f"No clip {clip_id}"})
    local = CLIPS / clip_id
    if local.is_file():
        return FileResponse(local, media_type="video/mp4")
    try:
        client = vast_client()
        with _vast_lock:
            source = _clip_sources.get(clip_id)
        if source is None:
            source = client.find_source(clip_id)
            with _vast_lock:
                _clip_sources[clip_id] = source
        upstream = client.stream(source, request.headers.get("range"))
    except VastNotConfigured as exc:
        return service_error(exc.message, exc.detail, 503)
    except VastError as exc:
        if exc.status_code == 404:
            return JSONResponse(status_code=404, content={"error": "not_found", "message": f"No clip {clip_id}"})
        return service_error(exc.message, exc.detail, exc.status_code)
    headers = {"accept-ranges": "bytes", **upstream.headers}
    media_type = headers.pop("content-type", "video/mp4")
    return StreamingResponse(
        upstream.iter_bytes(),
        status_code=upstream.status_code,
        headers=headers,
        media_type=media_type,
    )


# ---------------------------------------------------------------------------
# Style board: persisted saved observations + honest tallies.
# Counts are saved observations in reviewed footage. They are not unique people
# (one person can be saved from several moments) and not a citywide trend.
# ---------------------------------------------------------------------------
from datetime import datetime, timezone

BOARD_PATH = Path(os.getenv("SCOUT_BOARD_PATH", ROOT / "data" / "board.json"))
BOARD_SCOPE_NOTE = (
    "Counts are saved observations in human-reviewed workshop footage: not unique people, "
    "not a sample of a city, not a trend."
)


class SaveRequest(StrictModel):
    moment_id: str = Field(min_length=1)


class TitleRequest(StrictModel):
    title: str = Field(min_length=1, max_length=120)


def load_board() -> dict:
    if BOARD_PATH.is_file():
        try:
            data = json.loads(BOARD_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("saved"), list):
                data.setdefault("title", "Style board")
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return {"title": "Style board", "saved": []}


def save_board(board: dict) -> None:
    BOARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    BOARD_PATH.write_text(json.dumps(board, indent=2), encoding="utf-8")


def tally(moments: list[Moment]) -> dict:
    counts: dict[str, dict[str, int]] = {"garment": {}, "color": {}, "accessory": {}}
    for m in moments:
        for detail in m.observed:  # observed only; uncertain details are never counted
            bucket = counts[detail.kind]
            bucket[detail.value] = bucket.get(detail.value, 0) + 1
    ranked = {
        kind: sorted(({"value": v, "count": c} for v, c in bucket.items()), key=lambda r: (-r["count"], r["value"]))
        for kind, bucket in counts.items()
    }
    return {
        "observations": len(moments),
        "clips": len({m.clip_id for m in moments}),
        "by_kind": ranked,
        "note": BOARD_SCOPE_NOTE,
    }


def summarize(moments: list[Moment]) -> str:
    if not moments:
        return "No observations saved yet."
    t = tally(moments)
    parts = []
    for kind, label in (("color", "colors"), ("garment", "garments"), ("accessory", "accessories")):
        top = t["by_kind"][kind][:3]
        if top:
            parts.append(label + ": " + ", ".join(f"{r['value']} ({r['count']})" for r in top))
    return (
        f"{t['observations']} saved observation{'s' if t['observations'] != 1 else ''} from {t['clips']} clip"
        f"{'s' if t['clips'] != 1 else ''}. Most saved " + "; ".join(parts) + ". " + BOARD_SCOPE_NOTE
    )


def board_payload(board: dict, moments: tuple[Moment, ...] | None) -> dict:
    by_id = {m.id: m for m in (moments or ())}
    saved_moments = [by_id[s["moment_id"]] for s in board["saved"] if s["moment_id"] in by_id]
    return {
        "title": board["title"],
        "saved": board["saved"],
        "moments": [m.model_dump(mode="json") for m in saved_moments],
        "tallies": tally(saved_moments),
        "summary": summarize(saved_moments),
    }


def _catalog_or_error():
    try:
        return current_catalog(), None
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return None, service_error("The local moment catalog is invalid", str(exc), 500)


@app.get("/api/board")
def get_board():
    moments, err = _catalog_or_error()
    return err or board_payload(load_board(), moments)


@app.post("/api/board")
def add_to_board(body: SaveRequest):
    moments, err = _catalog_or_error()
    if err:
        return err
    if not moments or not any(m.id == body.moment_id for m in moments):
        return JSONResponse(status_code=404, content={"error": "not_found", "message": f"No moment {body.moment_id}"})
    board = load_board()
    if not any(s["moment_id"] == body.moment_id for s in board["saved"]):
        board["saved"].append({"moment_id": body.moment_id, "saved_at": datetime.now(timezone.utc).isoformat()})
        save_board(board)
    return board_payload(board, moments)


@app.delete("/api/board/{moment_id}")
def remove_from_board(moment_id: str):
    moments, err = _catalog_or_error()
    if err:
        return err
    board = load_board()
    board["saved"] = [s for s in board["saved"] if s["moment_id"] != moment_id]
    save_board(board)
    return board_payload(board, moments)


@app.put("/api/board/title")
def set_board_title(body: TitleRequest):
    moments, err = _catalog_or_error()
    if err:
        return err
    board = load_board()
    board["title"] = body.title.strip()
    save_board(board)
    return board_payload(board, moments)


@app.get("/api/stats")
def get_stats():
    """Tallies over every reviewed moment in the catalog (observed details only)."""
    moments, err = _catalog_or_error()
    return err or tally(list(moments or ()))


if (ROOT / "frames").is_dir():
    app.mount("/frames", StaticFiles(directory=ROOT / "frames"), name="frames")
app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
