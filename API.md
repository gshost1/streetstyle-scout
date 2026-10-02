# StreetStyle Scout — backend contract

The frontend (`web/`) talks only to `ScoutAPI` (`web/api.js`). In **live** mode it calls these endpoints
under a base URL (default `/api`, editable in the Status drawer). Serve `web/` as static files from the
same origin to avoid CORS, or enable CORS for the page's origin.

Sponsor status in the UI is derived **only** from `meta.services` on real responses. If the backend
doesn't report a service, it stays "not connected" in the UI. Don't report `ok` for anything that
didn't actually respond.

## `POST /api/search`

Request:
```json
{ "query": "bright jackets and backpacks", "dataset": "sf" | "to" | "both" }
```

Response `200`:
```json
{
  "results": [ Moment, ... ],
  "meta": {
    "mode": "live",
    "tookMs": 840,
    "services": {
      "vast":   { "state": "ok",    "ms": 210, "detail": "12 candidate clips" },
      "yolo":   { "state": "ok",    "ms": 95 },
      "cosmos": { "state": "ok",    "ms": 480 },
      "wandb":  { "state": "ok",    "detail": "run abc123" }
    }
  }
}
```

Error (any non-2xx):
```json
{ "error": "service_error", "service": "vast", "message": "VAST search timed out after 10 s",
  "services": { "vast": { "state": "error", "detail": "timeout" } } }
```
`service` is one of `vast | yolo | cosmos | wandb`. `services` is optional; include it when partial
results are known.

## `GET /api/moments/{id}`

Response `200`: `{ "moment": Moment, "meta": { "mode": "live", "services": { ... } } }`
Response `404`: `{ "error": "not_found", "message": "..." }`

## `GET /api/status`

Response `200`: `{ "services": { "vast": {...}, "yolo": {...}, "cosmos": {...}, "wandb": {...} } }`
Report the result of a real health check made now, not a config flag.

## `Moment`

Field names agreed in the Notion hub ("StreetStyle Scout — Claude & Cursor Build Hub", 1:59 PM Oct 2).

```jsonc
{
  "id": "sf-000123",
  "collection": "San Francisco",                          // "San Francisco" | "Toronto"
  "clip_id": "20261001_101025_sf4_chunk_0008.mp4",        // source file name in the VAST archive
  "timestamp_seconds": 83.5,                              // seconds into clip where the moment starts
  "duration": 4.0,                                        // seconds the moment spans
  "frame_url": "/frames/sf-000123.jpg",                   // extracted frame at timestamp_seconds, native resolution; null → labeled placeholder
  "video_url": "/clips/20261001_101025_sf4_chunk_0008.mp4", // playable clip, or null → still-frame-only state
  "bounding_box": { "x": 0.41, "y": 0.16, "w": 0.20, "h": 0.66 }, // normalized 0–1 relative to the frame; absent/null without a real detection
  "readability": "clear" | "partial" | "unreadable",
  "observed":  [ { "kind": "garment" | "color" | "accessory", "value": "puffer jacket" } ],
  "uncertain": [ { "kind": "garment", "value": "jeans or dark trousers", "note": "legs out of frame" } ],
  "description": "Cosmos text: what is visibly worn, nothing else.",
  "match_reason": "Matched “bright yellow” (color), “backpack” (accessory).",   // ONE sentence
  "provenance": { "source": "vast", "detection": "yolo" | null, "analysis": "cosmos" },
  "score": 0.91                                                                  // optional
}
```

Frames: serve at native footage resolution; the UI never upscales (it scales down only and keeps the
source aspect ratio inside a 16:9 container). Target same-origin `/frames` and `/clips`; the frontend
uses whatever URLs the backend returns.

### Hard rules for `observed` / `uncertain` / `description`
- Only garment type, color, accessories. **No brands** (even if a logo is visible), **no faces**,
  **no identity or re-identification across clips**, **no age/gender/ethnicity/body descriptors**.
- `observed` = read clearly. `uncertain` = plausible but not confirmed; always give a `note` with why.
- If nothing is readable, set `readability: "unreadable"`, `observed: []`, and say so in `description`.
- Enforce in the Cosmos prompt **and** with a post-check that strips/rejects disallowed terms.

### Fixtures
`web/fixtures.js` holds invented moments labeled `DEMO FIXTURE`. They are not real matches. Replace
`frame`/`video`/`timestamp` with verified VAST data only — never substitute stock imagery.
