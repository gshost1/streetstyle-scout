# StreetStyle Scout

A video agent that searches **visible clothing** in VAST's workshop archive (San Francisco, Toronto).
Flow: search → inspect a matching moment with timestamp and detection box → save it as an observation.

It describes what is worn. It does not identify people: no face recognition, identity tracking,
brand guesses or demographic labels — the data shape has no fields for them (see `API.md`).

## Run the frontend alone (demo fixtures)

```bash
python3 -m http.server 8000 -d web
```
Open http://localhost:8000. The page starts in **Demo fixtures** mode: every result is an invented,
clearly labeled `DEMO FIXTURE`. No sponsor service is contacted and none shows "ok".

## Run the local backend

```bash
uv sync --dev
uv run uvicorn server:app --reload --port 8000
```
Open the Status drawer (top right) → **Live backend**.

Without credentials or local data, searches return an honest `not_connected` error. For offline
development, set `SCOUT_CATALOG_PATH` to a JSON array of verified `Moment` objects (or an object with
a `moments` array) matching `API.md`. The backend validates privacy constraints and the complete
contract before serving or searching those records. It never imports the invented browser fixtures.

Run the offline contract suite with:

```bash
uv run pytest
```

## Demo states (Status drawer → Simulate)
- Slow search · Service error on next search · Empty results
- Unreadable footage: search `jacket` and scroll to the dimmed card
- No detection box: open the red/motion-blur moment

## Layout
- `web/index.html` — the UI, vanilla JS + inline CSS, no build step
- `web/api.js` — `ScoutAPI` adapter: `fixture` and `live` modes
- `web/fixtures.js` — labeled demo data (field names per `API.md`; `reviewedClips` is reference only)
- `server.py` — FastAPI stub serving `web/` and the `API.md` contract
- `TASKS.md` — who does what before 4:30 PM

## Credits
Frontend built with Claude Code. Backend and integration with Cursor on the workshop VM.
Hosting is credited only once verified.
