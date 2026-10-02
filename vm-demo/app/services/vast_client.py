"""VAST VSS retrieval client — login, search, detections, stream."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

LOCATION_BY_DATASET = {
    "sf": "san_francisco",
    "to": "toronto",
}


class VastError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class VastClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._token: Optional[str] = None
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=20.0))

    async def aclose(self) -> None:
        await self._client.aclose()

    async def login(self, force: bool = False) -> str:
        if self._token and not force:
            return self._token
        url = f"{self.settings.ingress_url}/api/v1/auth/login"
        try:
            resp = await self._client.post(
                url,
                json={
                    "username": self.settings.username,
                    "password": self.settings.password,
                },
            )
        except httpx.HTTPError as exc:
            raise VastError(f"VAST login network error: {exc}", 503) from exc
        if resp.status_code != 200:
            raise VastError(
                f"VAST login failed (HTTP {resp.status_code})",
                resp.status_code if resp.status_code >= 400 else 502,
            )
        data = resp.json()
        token = data.get("access_token")
        if not token:
            raise VastError("VAST login response missing access_token", 502)
        self._token = token
        return token

    async def _auth_headers(self) -> Dict[str, str]:
        token = await self.login()
        return {"Authorization": f"Bearer {token}"}

    async def search(
        self,
        query: str,
        *,
        dataset: str,
        top_k: int = 12,
        min_similarity: float = 0.2,
    ) -> Dict[str, Any]:
        token = await self.login()
        body: Dict[str, Any] = {
            "query": query,
            "top_k": top_k,
            "llm_top_n": 1,
            "min_similarity": min_similarity,
            "include_public": True,
            "time_filter": "all",
        }
        if dataset in LOCATION_BY_DATASET:
            body["metadata_filters"] = {"location": LOCATION_BY_DATASET[dataset]}

        url = f"{self.settings.ingress_url}/api/v1/search"
        try:
            resp = await self._client.post(
                url,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=body,
            )
        except httpx.HTTPError as exc:
            raise VastError(f"VAST search network error: {exc}", 503) from exc

        if resp.status_code == 401:
            token = await self.login(force=True)
            resp = await self._client.post(
                url,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=body,
            )

        if resp.status_code != 200:
            detail = None
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text[:300]
            raise VastError(
                f"VAST search failed (HTTP {resp.status_code}): {detail}",
                resp.status_code if resp.status_code >= 400 else 502,
            )
        return resp.json()

    async def detections(self, source: str) -> Optional[Dict[str, Any]]:
        token = await self.login()
        url = f"{self.settings.ingress_url}/api/v1/videos/detections"
        try:
            resp = await self._client.get(
                url,
                params={"source": source},
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            logger.warning("VAST detections network error: %s", exc)
            return None
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            logger.warning("VAST detections HTTP %s", resp.status_code)
            return None
        return resp.json()

    async def download_stream(self, source: str, dest_path) -> int:
        """Download segment MP4 via VAST Range-capable stream proxy."""
        token = await self.login()
        url = f"{self.settings.ingress_url}/api/v1/videos/stream"
        try:
            async with self._client.stream(
                "GET",
                url,
                params={"source": source, "token": token},
            ) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread())[:300]
                    raise VastError(
                        f"VAST stream failed (HTTP {resp.status_code}): {body!r}",
                        resp.status_code if resp.status_code >= 400 else 502,
                    )
                n = 0
                with open(dest_path, "wb") as f:
                    async for chunk in resp.aiter_bytes():
                        f.write(chunk)
                        n += len(chunk)
                return n
        except VastError:
            raise
        except httpx.HTTPError as exc:
            raise VastError(f"VAST stream network error: {exc}", 503) from exc

    async def health(self) -> Dict[str, Any]:
        try:
            await self.login(force=True)
            me = await self._client.get(
                f"{self.settings.ingress_url}/api/v1/auth/me",
                headers=await self._auth_headers(),
            )
            ok = me.status_code == 200
            return {
                "ok": ok,
                "detail": "authenticated" if ok else f"auth/me HTTP {me.status_code}",
            }
        except Exception as exc:
            return {"ok": False, "detail": str(exc)}
