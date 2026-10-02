"""YOLO11 person detection via shared GPU endpoint."""
from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


def normalize_bbox(
    bbox: List[float], shape: List[int]
) -> Optional[Dict[str, float]]:
    """Convert [x1,y1,x2,y2] absolute pixels + shape [H,W] → normalized x,y,w,h."""
    if not bbox or len(bbox) < 4 or not shape or len(shape) < 2:
        return None
    h, w = float(shape[0]), float(shape[1])
    if h <= 0 or w <= 0:
        return None
    x1, y1, x2, y2 = map(float, bbox[:4])
    nw = (x2 - x1) / w
    nh = (y2 - y1) / h
    if nw <= 0 or nh <= 0:
        return None
    return {
        "x": max(0.0, min(1.0, x1 / w)),
        "y": max(0.0, min(1.0, y1 / h)),
        "w": max(0.0, min(1.0, nw)),
        "h": max(0.0, min(1.0, nh)),
    }


def pick_person_box(
    frames: List[Dict[str, Any]],
    *,
    target_time: float,
    video_shape: Optional[List[int]] = None,
) -> Tuple[Optional[Dict[str, float]], Optional[str]]:
    """Pick highest-confidence person near target_time. Returns (box, detection_source)."""
    if not frames:
        return None, None
    best_frame = min(
        frames,
        key=lambda f: abs(float(f.get("time_sec") if f.get("time_sec") is not None else f.get("frame_index", 0)) - target_time),
    )
    shape = best_frame.get("shape") or video_shape
    persons = [
        d
        for d in (best_frame.get("detections") or [])
        if str(d.get("label", "")).lower() == "person" and d.get("bbox")
    ]
    if not persons:
        return None, None
    persons.sort(key=lambda d: float(d.get("confidence") or 0), reverse=True)
    box = normalize_bbox(persons[0]["bbox"], shape)
    if not box:
        return None, None
    return box, "yolo_person"


class YoloClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=15.0))
        self.last_ok = False
        self.last_detail: Optional[str] = None

    async def aclose(self) -> None:
        await self._client.aclose()

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.settings.gpu_bearer_token}"}

    async def health(self) -> Dict[str, Any]:
        try:
            resp = await self._client.get(
                f"{self.settings.yolo_url}/healthz", headers=self._headers()
            )
            if resp.status_code != 200:
                self.last_ok = False
                self.last_detail = f"HTTP {resp.status_code}"
                return {"ok": False, "detail": self.last_detail}
            data = resp.json()
            ok = bool(data.get("ok") and data.get("model_loaded"))
            self.last_ok = ok
            self.last_detail = "model_loaded" if ok else str(data)
            return {"ok": ok, "detail": self.last_detail, "raw_ok": data.get("ok")}
        except Exception as exc:
            self.last_ok = False
            self.last_detail = str(exc)
            return {"ok": False, "detail": self.last_detail}

    async def infer_clip(
        self, clip_path: Path, *, include_frames: bool = True
    ) -> Optional[Dict[str, Any]]:
        try:
            b64 = base64.b64encode(clip_path.read_bytes()).decode("ascii")
            resp = await self._client.post(
                f"{self.settings.yolo_url}/v1/infer",
                headers={**self._headers(), "Content-Type": "application/json"},
                json={
                    "video_base64": b64,
                    "filename": clip_path.name,
                    "include_frames": include_frames,
                },
            )
            if resp.status_code != 200:
                self.last_ok = False
                self.last_detail = f"infer HTTP {resp.status_code}"
                logger.warning("YOLO infer failed: %s", self.last_detail)
                return None
            data = resp.json()
            if not (data.get("ok") or data.get("perception_ok")):
                self.last_ok = False
                self.last_detail = "perception_ok false"
                return None
            self.last_ok = True
            self.last_detail = "infer_ok"
            return data
        except Exception as exc:
            self.last_ok = False
            self.last_detail = str(exc)
            logger.warning("YOLO infer error: %s", exc)
            return None
