"""StreetStyle Scout FastAPI — live VAST clothing search + media."""
from __future__ import annotations

import logging
import mimetypes
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from app.config import get_settings
from app.models import (
    MomentResponse,
    SearchRequest,
    SearchResponse,
    StatusResponse,
)
from app.services.cosmos import CosmosClient
from app.services.pipeline import Pipeline
from app.services.vast_client import VastClient, VastError
from app.services.yolo import YoloClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("streetstyle")


class _RedactTokenFilter(logging.Filter):
    """Avoid leaking JWTs from VAST stream ?token= URLs into logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        if "token=" in msg:
            record.msg = re.sub(r"(token=)[^&\s]+", r"\1[REDACTED]", msg)
            record.args = ()
        return True


logging.getLogger("httpx").addFilter(_RedactTokenFilter())
logging.getLogger("httpcore").addFilter(_RedactTokenFilter())

RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    vast = VastClient(settings)
    yolo = YoloClient(settings)
    cosmos = CosmosClient(settings)
    pipeline = Pipeline(settings, vast, yolo, cosmos)
    app.state.settings = settings
    app.state.pipeline = pipeline
    try:
        await pipeline.refresh_services()
        logger.info("StreetStyle Scout ready (mode=live)")
        yield
    finally:
        await vast.aclose()
        await yolo.aclose()
        await cosmos.aclose()


app = FastAPI(title="StreetStyle Scout", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(VastError)
async def vast_error_handler(_request: Request, exc: VastError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": "vast_failure", "detail": exc.message, "mode": "live"},
    )


@app.get("/api/status", response_model=StatusResponse)
async def status(request: Request) -> StatusResponse:
    pipeline: Pipeline = request.app.state.pipeline
    services = await pipeline.refresh_services()
    return StatusResponse(services=services)


@app.post("/api/search", response_model=SearchResponse)
async def search(body: SearchRequest, request: Request) -> SearchResponse:
    pipeline: Pipeline = request.app.state.pipeline
    if not body.query.strip():
        raise HTTPException(status_code=400, detail={"error": "empty_query"})
    try:
        results, meta = await pipeline.search(body.query.strip(), body.dataset)
    except VastError:
        raise
    except Exception as exc:
        logger.exception("search failed")
        raise HTTPException(
            status_code=502,
            detail={"error": "search_failed", "detail": str(exc), "mode": "live"},
        ) from exc
    return SearchResponse(results=results, meta=meta)


@app.get("/api/moments/{moment_id}", response_model=MomentResponse)
async def get_moment(moment_id: str, request: Request) -> MomentResponse:
    pipeline: Pipeline = request.app.state.pipeline
    moment = pipeline.get_moment(moment_id)
    if moment is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "moment_not_found", "id": moment_id},
        )
    # Refresh service snapshot for meta without failing the read.
    try:
        await pipeline.refresh_services()
    except Exception:
        pass
    return MomentResponse(moment=moment, meta=pipeline.meta())


@app.get("/frames/{frame_name}")
async def get_frame(frame_name: str, request: Request) -> FileResponse:
    settings = request.app.state.settings
    # Accept {id}.jpg or bare id
    name = frame_name if frame_name.endswith(".jpg") else f"{frame_name}.jpg"
    path: Path = settings.frames_dir / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail={"error": "frame_not_found"})
    return FileResponse(path, media_type="image/jpeg", filename=name)


@app.get("/clips/{clip_id}")
async def get_clip(clip_id: str, request: Request) -> Response:
    settings = request.app.state.settings
    # Preserve exact basename; reject path traversal.
    if "/" in clip_id or "\\" in clip_id or clip_id.startswith("."):
        raise HTTPException(status_code=400, detail={"error": "invalid_clip_id"})
    path: Path = settings.clips_dir / clip_id
    if not path.is_file():
        raise HTTPException(status_code=404, detail={"error": "clip_not_found"})

    file_size = path.stat().st_size
    media_type = mimetypes.guess_type(clip_id)[0] or "video/mp4"
    range_header = request.headers.get("range") or request.headers.get("Range")

    if not range_header:
        return FileResponse(
            path,
            media_type=media_type,
            filename=clip_id,
            headers={"Accept-Ranges": "bytes", "Content-Length": str(file_size)},
        )

    match = RANGE_RE.match(range_header.strip())
    if not match:
        raise HTTPException(status_code=416, detail={"error": "invalid_range"})

    start_s, end_s = match.group(1), match.group(2)
    start = int(start_s) if start_s else 0
    end = int(end_s) if end_s else file_size - 1
    if start >= file_size or end >= file_size or start > end:
        return Response(
            status_code=416,
            headers={"Content-Range": f"bytes */{file_size}"},
            content=b"",
        )

    length = end - start + 1

    def iter_file():
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            chunk = 1024 * 1024
            while remaining > 0:
                data = f.read(min(chunk, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    return StreamingResponse(
        iter_file(),
        status_code=206,
        media_type=media_type,
        headers={
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(length),
            "Content-Disposition": f'inline; filename="{clip_id}"',
        },
    )


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True, "service": "streetstyle-scout"}


def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
from fastapi.staticfiles import StaticFiles
app.mount("/", StaticFiles(directory=str(Path(__file__).resolve().parent.parent / "web"), html=True), name="web")
