"""API models for StreetStyle Scout moments."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


Dataset = Literal["sf", "to", "both"]


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    dataset: Dataset = "sf"


class ObservedItem(BaseModel):
    kind: str
    value: str


class UncertainItem(BaseModel):
    kind: str
    value: str
    note: str


class BoundingBox(BaseModel):
    """Normalized person box: x, y, w, h in [0, 1]."""

    x: float
    y: float
    w: float
    h: float


class Provenance(BaseModel):
    source: str
    detection: Optional[str] = None
    analysis: Optional[str] = None


class Moment(BaseModel):
    id: str
    collection: str
    clip_id: str
    timestamp_seconds: float
    duration: float
    frame_url: str
    video_url: str
    bounding_box: Optional[BoundingBox] = None
    readability: str
    observed: List[ObservedItem] = Field(default_factory=list)
    uncertain: List[UncertainItem] = Field(default_factory=list)
    description: str
    match_reason: str
    provenance: Provenance


class ServiceStatus(BaseModel):
    name: str
    ok: bool
    detail: Optional[str] = None


class Meta(BaseModel):
    mode: Literal["live"] = "live"
    services: Dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    results: List[Moment]
    meta: Meta


class MomentResponse(BaseModel):
    moment: Moment
    meta: Meta


class StatusResponse(BaseModel):
    services: Dict[str, Any]
