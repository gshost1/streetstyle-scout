"""Load workshop credentials from env / the single /config/*.config (never echo secrets)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def _source_team_config() -> Optional[Path]:
    config_dir = Path("/config")
    if not config_dir.is_dir():
        return None
    configs = sorted(config_dir.glob("*.config"))
    if len(configs) != 1:
        return None
    path = configs[0]
    # Parse KEY=VALUE without shell so we never print values.
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = val
    return path


# GPU hosts are documented in the workshop gpu skills (shared NIM host).
DEFAULT_GPU_HOST = "166.19.38.112"


@dataclass(frozen=True)
class Settings:
    ingress_url: str
    username: str
    password: str
    s3_endpoint: str
    access_key: str
    secret_key: str
    segments_bucket: str
    chunks_bucket: str
    gpu_bearer_token: str
    cosmos_reason_url: str
    yolo_url: str
    data_dir: Path
    host: str = "0.0.0.0"
    port: int = 8080

    @property
    def clips_dir(self) -> Path:
        return self.data_dir / "clips"

    @property
    def frames_dir(self) -> Path:
        return self.data_dir / "frames"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is not None:
        return _settings

    _source_team_config()
    gpu_host = os.environ.get("GPU_HOST", DEFAULT_GPU_HOST)
    data_dir = Path(os.environ.get("SSS_DATA_DIR", Path(__file__).resolve().parents[1] / "data"))

    required = {
        "INGRESS_URL": os.environ.get("INGRESS_URL", "").rstrip("/"),
        "USERNAME": os.environ.get("USERNAME", ""),
        "PASSWORD": os.environ.get("PASSWORD", ""),
        "S3_ENDPOINT": os.environ.get("S3_ENDPOINT", ""),
        "ACCESS_KEY": os.environ.get("ACCESS_KEY", ""),
        "SECRET_KEY": os.environ.get("SECRET_KEY", ""),
        "S3_SEGMENTS_BUCKET": os.environ.get("S3_SEGMENTS_BUCKET", ""),
        "S3_CHUNKS_BUCKET": os.environ.get("S3_CHUNKS_BUCKET", ""),
        "GPU_BEARER_TOKEN": os.environ.get("GPU_BEARER_TOKEN", ""),
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise RuntimeError(f"Missing required config keys: {', '.join(missing)}")

    _settings = Settings(
        ingress_url=required["INGRESS_URL"],
        username=required["USERNAME"],
        password=required["PASSWORD"],
        s3_endpoint=required["S3_ENDPOINT"],
        access_key=required["ACCESS_KEY"],
        secret_key=required["SECRET_KEY"],
        segments_bucket=required["S3_SEGMENTS_BUCKET"],
        chunks_bucket=required["S3_CHUNKS_BUCKET"],
        gpu_bearer_token=required["GPU_BEARER_TOKEN"],
        cosmos_reason_url=os.environ.get(
            "COSMOS3_REASON_URL", f"http://{gpu_host}:8001"
        ).rstrip("/"),
        yolo_url=os.environ.get("YOLO_URL", f"http://{gpu_host}:8002").rstrip("/"),
        data_dir=data_dir,
        host=os.environ.get("SSS_HOST", "0.0.0.0"),
        port=int(os.environ.get("SSS_PORT", "8080")),
    )
    for d in (_settings.clips_dir, _settings.frames_dir, _settings.cache_dir):
        d.mkdir(parents=True, exist_ok=True)
    return _settings
