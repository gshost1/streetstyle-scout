"""Local clip/frame media helpers + Range-capable responses."""
from __future__ import annotations

import hashlib
import logging
import subprocess
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image

from app.config import Settings

logger = logging.getLogger(__name__)


def clip_basename(source: str) -> str:
    """Exact clip basename from s3://…/name.mp4 or a bare filename."""
    name = source.rstrip("/").split("/")[-1]
    return name


def moment_id(clip_id: str, timestamp_seconds: float) -> str:
    raw = f"{clip_id}|{timestamp_seconds:.3f}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def extract_frame(
    clip_path: Path,
    frame_path: Path,
    *,
    time_sec: float = 0.5,
) -> bool:
    frame_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{max(0.0, time_sec):.3f}",
        "-i",
        str(clip_path),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(frame_path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=60)
        return frame_path.is_file() and frame_path.stat().st_size > 0
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        logger.warning("ffmpeg frame extract failed: %s", exc)
        return False


def crop_person_frame(
    frame_path: Path,
    out_path: Path,
    box: dict,
    *,
    pad: float = 0.15,
) -> Optional[Path]:
    """Crop a loose region around normalized person box for clothing analysis."""
    try:
        im = Image.open(frame_path).convert("RGB")
        w, h = im.size
        x = float(box["x"])
        y = float(box["y"])
        bw = float(box["w"])
        bh = float(box["h"])
        x0 = max(0, int((x - pad * bw) * w))
        y0 = max(0, int((y - pad * bh) * h))
        x1 = min(w, int((x + bw + pad * bw) * w))
        y1 = min(h, int((y + bh + pad * bh) * h))
        if x1 - x0 < 8 or y1 - y0 < 8:
            return None
        crop = im.crop((x0, y0, x1, y1))
        # Upscale tiny crops so Cosmos can see garments.
        if crop.width < 128 or crop.height < 128:
            scale = max(128 / crop.width, 128 / crop.height)
            crop = crop.resize(
                (int(crop.width * scale), int(crop.height * scale)),
                Image.Resampling.BICUBIC,
            )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        crop.save(out_path, format="JPEG", quality=90)
        return out_path
    except Exception as exc:
        logger.warning("crop failed: %s", exc)
        return None


def ensure_clip_cached(settings: Settings, clip_id: str) -> Path:
    return settings.clips_dir / clip_id


def frame_path_for(settings: Settings, mid: str) -> Path:
    return settings.frames_dir / f"{mid}.jpg"
