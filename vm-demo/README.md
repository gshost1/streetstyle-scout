# Workshop VM source snapshot

This runnable source tree was recovered from the reviewed workshop VM archive committed on 2026-10-02. The archive SHA-256 is `c8d07a2b6c940b73ada3283262ee6a73384155a5918cdaf7fccd9445c4377a1`. It is kept separate from the Mac backend and frontend at the repository root so neither implementation is overwritten.

## Run on the workshop VM

From this directory, with Python 3.11+ and the workshop services available:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
./run.sh
```

`run.sh` uses the single `/config/*.config` file on the workshop VM when present. Alternatively, supply the required environment variables listed in `app/config.py`. Keep credentials outside this repository. The server defaults to port 8080 and serves the UI at `/`, API at `/api`, and health check at `/healthz`. Set `SSS_HOST=127.0.0.1` to bind locally. Media created by the app goes under `data/` by default and is ignored by Git.

The code reports service status from actual responses. This source snapshot alone does not verify live sponsor connectivity or hosting.
