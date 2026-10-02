"""Search → detect → analyze pipeline. No invented detections or garment claims."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.config import Settings
from app.models import (
    BoundingBox,
    Meta,
    Moment,
    ObservedItem,
    Provenance,
    UncertainItem,
)
from app.services.cosmos import CosmosClient
from app.services.media import (
    clip_basename,
    crop_person_frame,
    extract_frame,
    frame_path_for,
    moment_id,
)
from app.services.vast_client import VastClient, VastError
from app.services.yolo import YoloClient, pick_person_box

logger = logging.getLogger(__name__)

COLLECTION_BY_LOCATION = {
    "san_francisco": "sf",
    "toronto": "to",
}


class Pipeline:
    def __init__(
        self,
        settings: Settings,
        vast: VastClient,
        yolo: YoloClient,
        cosmos: CosmosClient,
    ):
        self.settings = settings
        self.vast = vast
        self.yolo = yolo
        self.cosmos = cosmos
        self._moments: Dict[str, Moment] = {}
        self.services_snapshot: Dict[str, Any] = {}

    def store_moment(self, moment: Moment) -> None:
        self._moments[moment.id] = moment

    def get_moment(self, mid: str) -> Optional[Moment]:
        return self._moments.get(mid)

    async def refresh_services(self) -> Dict[str, Any]:
        vast_h = await self.vast.health()
        yolo_h = await self.yolo.health()
        cosmos_h = await self.cosmos.health()
        snap = {
            "vast": {"ok": vast_h.get("ok", False), "detail": vast_h.get("detail")},
            "yolo": {"ok": yolo_h.get("ok", False), "detail": yolo_h.get("detail")},
            "cosmos": {
                "ok": cosmos_h.get("ok", False),
                "detail": cosmos_h.get("detail"),
                "model": cosmos_h.get("model"),
            },
            # Only claim W&B / CoreWeave when a real successful call verifies them.
            # This pipeline does not call W&B; GPU host is workshop NIM, not asserted as CW.
        }
        self.services_snapshot = snap
        return snap

    def meta(self) -> Meta:
        return Meta(mode="live", services=dict(self.services_snapshot))

    async def search(self, query: str, dataset: str) -> Tuple[List[Moment], Meta]:
        await self.refresh_services()
        if not self.services_snapshot.get("vast", {}).get("ok"):
            raise VastError("VAST authentication failed; cannot run live search", 503)

        datasets = ["sf", "to"] if dataset == "both" else [dataset]
        raw_hits: List[Dict[str, Any]] = []
        for ds in datasets:
            payload = await self.vast.search(query, dataset=ds, top_k=10)
            for r in payload.get("results") or []:
                r["_dataset"] = ds
                raw_hits.append(r)

        # Prefer higher similarity; keep unique clip basenames.
        raw_hits.sort(key=lambda r: float(r.get("similarity_score") or 0), reverse=True)
        seen = set()
        unique: List[Dict[str, Any]] = []
        for r in raw_hits:
            cid = clip_basename(r.get("source") or r.get("filename") or "")
            if not cid or cid in seen:
                continue
            seen.add(cid)
            unique.append(r)

        moments: List[Moment] = []
        for hit in unique[:8]:
            try:
                moment = await self._build_moment(hit, query)
            except Exception as exc:
                logger.warning("skip hit %s: %s", hit.get("filename"), exc)
                continue
            if moment is None:
                continue
            # Exclude unreadable clothing from search results.
            if moment.readability == "unreadable":
                continue
            self.store_moment(moment)
            moments.append(moment)

        return moments, self.meta()

    async def _build_moment(self, hit: Dict[str, Any], query: str) -> Optional[Moment]:
        source = hit.get("source")
        if not source:
            return None
        clip_id = clip_basename(source)
        # Preserve exact VAST segment timestamp (start of segment in parent timeline).
        timestamp_seconds = float(hit.get("segment_start_sec") or 0.0)
        duration = float(hit.get("duration") or 0.0)
        mid = moment_id(clip_id, timestamp_seconds)

        clip_path = self.settings.clips_dir / clip_id
        if not clip_path.is_file() or clip_path.stat().st_size < 1000:
            await self.vast.download_stream(source, clip_path)

        # Frame at mid-clip (readable clothing); timestamp stays the VAST segment start.
        frame_t = min(0.5, max(0.0, duration / 2.0 if duration else 0.5))
        fpath = frame_path_for(self.settings, mid)
        if not extract_frame(clip_path, fpath, time_sec=frame_t):
            return None

        bbox_dict: Optional[Dict[str, float]] = None
        detection_src: Optional[str] = None

        # Real YOLO person boxes only — never invent. Prefer VAST YOLO sidecars
        # (already computed by the pipeline); fall back to live YOLO when healthy.
        dets = await self.vast.detections(source)
        if dets and dets.get("frames"):
            box, _ = pick_person_box(
                dets.get("frames") or [],
                target_time=frame_t,
                video_shape=dets.get("video_shape"),
            )
            if box:
                bbox_dict = box
                detection_src = "vast_detections_yolo"

        if bbox_dict is None and self.services_snapshot.get("yolo", {}).get("ok"):
            yolo_out = await self.yolo.infer_clip(clip_path, include_frames=True)
            if yolo_out:
                box, _ = pick_person_box(
                    yolo_out.get("frames") or [],
                    target_time=frame_t,
                    video_shape=yolo_out.get("video_shape"),
                )
                if box:
                    bbox_dict = box
                    detection_src = "yolo_live"

        # No invented boxes — leave null if we have no real person detection.
        bounding_box = BoundingBox(**bbox_dict) if bbox_dict else None

        analysis = None
        analysis_src = None
        analysis_image = fpath
        if bbox_dict:
            crop_path = self.settings.cache_dir / f"{mid}_crop.jpg"
            cropped = crop_person_frame(fpath, crop_path, bbox_dict)
            if cropped:
                analysis_image = cropped

        if self.services_snapshot.get("cosmos", {}).get("ok"):
            analysis, analysis_src = await self.cosmos.analyze_clothing(analysis_image)

        caption = (hit.get("reasoning_content") or "").strip()
        observed: List[ObservedItem] = []
        uncertain: List[UncertainItem] = []
        readability = "unreadable"

        if analysis:
            readability = analysis["readability"]
            observed = [ObservedItem(**o) for o in analysis["observed"]]
            uncertain = [UncertainItem(**u) for u in analysis["uncertain"]]
        else:
            # Caption alone is uncertain evidence, never observed.
            readability = "partial" if caption else "unreadable"
            if caption:
                uncertain.append(
                    UncertainItem(
                        kind="caption",
                        value=caption[:240],
                        note="from VAST segment caption only; Cosmos analysis unavailable",
                    )
                )

        location = (hit.get("location") or "").strip()
        collection = COLLECTION_BY_LOCATION.get(location) or hit.get("_dataset") or "sf"

        description = caption or "Street clothing moment from live VAST search."
        match_reason = (
            f"VAST hybrid search match for {query!r} "
            f"(similarity={float(hit.get('similarity_score') or 0):.3f}, "
            f"camera={hit.get('camera_id') or 'unknown'})"
        )

        return Moment(
            id=mid,
            collection=collection,
            clip_id=clip_id,
            timestamp_seconds=timestamp_seconds,
            duration=duration,
            frame_url=f"/frames/{mid}.jpg",
            video_url=f"/clips/{clip_id}",
            bounding_box=bounding_box,
            readability=readability,
            observed=observed,
            uncertain=uncertain,
            description=description,
            match_reason=match_reason,
            provenance=Provenance(
                source="vast_vss_search",
                detection=detection_src,
                analysis=analysis_src,
            ),
        )
