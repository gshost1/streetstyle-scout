"""NVIDIA Cosmos clothing analysis — only use values when the endpoint responds."""
from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

CLOTHING_PROMPT = (
    "Analyze ONLY clearly visible clothing in this street-camera image. "
    "Return JSON only with this schema:\n"
    '{"readability":"readable|partial|unreadable",'
    '"observed":[{"kind":"garment|color|accessory","value":"..."}],'
    '"uncertain":[{"kind":"garment|color|accessory","value":"...","note":"..."}]}\n'
    "Rules: no brands, logos, identities, demographics, ages, genders, race, or trends. "
    "Put guesses in uncertain, not observed. If clothing is too small/blurry, "
    'set readability to "unreadable" and leave observed empty.'
)


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _clean_items(items: Any, *, with_note: bool) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    seen = set()
    if not isinstance(items, list):
        return out
    for it in items:
        if not isinstance(it, dict):
            continue
        kind = str(it.get("kind") or "garment").strip().lower()
        value = str(it.get("value") or "").strip().lower()
        if not value:
            continue
        # Hard filter: never keep brand/identity-ish keys the model may invent.
        banned = ("brand", "logo", "nike", "adidas", "gucci", "age", "gender", "race", "ethnicity")
        if any(b in value for b in banned) or kind in ("brand", "identity", "demographic", "trend"):
            continue
        key = (kind, value)
        if key in seen:
            continue
        seen.add(key)
        if with_note:
            note = str(it.get("note") or "model uncertain").strip()
            out.append({"kind": kind, "value": value, "note": note})
        else:
            out.append({"kind": kind, "value": value})
        if len(out) >= 12:
            break
    return out


class CosmosClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=15.0))
        self._model_id: Optional[str] = None
        self.last_ok = False
        self.last_detail: Optional[str] = None

    async def aclose(self) -> None:
        await self._client.aclose()

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.settings.gpu_bearer_token}"}

    async def health(self) -> Dict[str, Any]:
        try:
            resp = await self._client.get(
                f"{self.settings.cosmos_reason_url}/v1/models", headers=self._headers()
            )
            if resp.status_code != 200:
                self.last_ok = False
                self.last_detail = f"models HTTP {resp.status_code}"
                return {"ok": False, "detail": self.last_detail}
            data = resp.json()
            models = data.get("data") or []
            if not models:
                self.last_ok = False
                self.last_detail = "no models"
                return {"ok": False, "detail": self.last_detail}
            self._model_id = models[0].get("id")
            self.last_ok = True
            self.last_detail = f"model={self._model_id}"
            return {"ok": True, "detail": self.last_detail, "model": self._model_id}
        except Exception as exc:
            self.last_ok = False
            self.last_detail = str(exc)
            return {"ok": False, "detail": self.last_detail}

    async def _ensure_model(self) -> Optional[str]:
        if self._model_id:
            return self._model_id
        h = await self.health()
        return self._model_id if h.get("ok") else None

    async def analyze_clothing(
        self, image_path: Path
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Returns (analysis_dict, analysis_source_label).
        analysis_dict keys: readability, observed, uncertain — only if endpoint responded.
        """
        model = await self._ensure_model()
        if not model:
            return None, None
        try:
            b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
            payload = {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": CLOTHING_PROMPT},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{b64}"
                                },
                            },
                        ],
                    }
                ],
                "max_tokens": 500,
                "temperature": 0,
            }
            resp = await self._client.post(
                f"{self.settings.cosmos_reason_url}/v1/chat/completions",
                headers={**self._headers(), "Content-Type": "application/json"},
                json=payload,
            )
            if resp.status_code != 200:
                self.last_ok = False
                self.last_detail = f"chat HTTP {resp.status_code}"
                logger.warning("Cosmos analysis failed: %s", self.last_detail)
                return None, None
            data = resp.json()
            content = (
                ((data.get("choices") or [{}])[0].get("message") or {}).get("content")
                or ""
            )
            parsed = _extract_json(content)
            if not parsed:
                self.last_ok = False
                self.last_detail = "unparseable clothing JSON"
                return None, None
            readability = str(parsed.get("readability") or "unreadable").strip().lower()
            if readability not in ("readable", "partial", "unreadable"):
                readability = "unreadable"
            result = {
                "readability": readability,
                "observed": _clean_items(parsed.get("observed"), with_note=False),
                "uncertain": _clean_items(parsed.get("uncertain"), with_note=True),
            }
            self.last_ok = True
            self.last_detail = f"analyzed readability={readability}"
            return result, f"cosmos:{model}"
        except Exception as exc:
            self.last_ok = False
            self.last_detail = str(exc)
            logger.warning("Cosmos analysis error: %s", exc)
            return None, None
