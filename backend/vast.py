"""Client for the workshop VAST archive.

Configuration comes only from the environment:

    VAST_INGRESS_URL   base URL of the workshop ingress (http or https)
    VAST_USERNAME
    VAST_PASSWORD
    VAST_TIMEOUT_S     optional, defaults to 10

The ingress URL, credentials, tokens and source URLs are never logged or placed
in exception messages. Responses are parsed strictly: a candidate without a
source clip id and timestamp is a schema error, never a defaulted value.

The request/response field names below have not been verified against a live
workshop deployment; they are collected in one place so they can be corrected.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)
# httpx/httpcore log full request URLs at INFO/DEBUG, which would expose the ingress URL.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

LOGIN_PATH = "/api/v1/auth/login"
SEARCH_PATH = "/api/v1/search"
EXPLORE_PATH = "/api/v1/videos/explore"
STREAM_PATH = "/api/v1/videos/stream"

TOKEN_KEYS = ("access_token", "token")
RESULT_LIST_KEYS = ("results", "items", "videos", "data")
CLIP_ID_KEYS = ("clip_id", "video_id", "filename", "file_name", "name")
TIMESTAMP_KEYS = ("timestamp_seconds", "start_time", "start", "timestamp")
DURATION_KEYS = ("duration", "duration_seconds")
SCORE_KEYS = ("score", "similarity")
COLLECTION_KEYS = ("collection", "dataset")
STREAM_CLIP_PARAM = "filename"
FORWARDED_STREAM_HEADERS = (
    "content-type",
    "content-length",
    "content-range",
    "accept-ranges",
)

ENV_URL = "VAST_INGRESS_URL"
ENV_USERNAME = "VAST_USERNAME"
ENV_PASSWORD = "VAST_PASSWORD"
ENV_TIMEOUT = "VAST_TIMEOUT_S"


class VastError(Exception):
    """A VAST failure safe to show to API clients.

    `detail` is a short machine-ish reason for `services.vast.detail`;
    `status_code` is the HTTP status the API should return.
    """

    def __init__(self, message: str, detail: str, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.detail = detail
        self.status_code = status_code


class VastNotConfigured(VastError):
    def __init__(self, missing: list[str]):
        super().__init__(
            f"VAST is not configured; set {', '.join(missing)}",
            "not configured",
            503,
        )
        self.missing = missing


class VastSchemaError(VastError):
    def __init__(self, message: str):
        super().__init__(message, "unexpected response schema", 502)


@dataclass(frozen=True)
class VastConfig:
    base_url: str = field(repr=False)
    username: str = field(repr=False)
    password: str = field(repr=False)
    timeout_s: float = 10.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "VastConfig":
        env = os.environ if env is None else env
        values = {name: (env.get(name) or "").strip() for name in (ENV_URL, ENV_USERNAME, ENV_PASSWORD)}
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise VastNotConfigured(missing)
        parts = urlsplit(values[ENV_URL])
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise VastError(f"{ENV_URL} must be an http(s) URL", "invalid configuration", 503)
        try:
            timeout_s = float(env.get(ENV_TIMEOUT) or 10)
        except ValueError:
            raise VastError(f"{ENV_TIMEOUT} must be a number", "invalid configuration", 503) from None
        if timeout_s <= 0:
            raise VastError(f"{ENV_TIMEOUT} must be positive", "invalid configuration", 503)
        return cls(values[ENV_URL].rstrip("/"), values[ENV_USERNAME], values[ENV_PASSWORD], timeout_s)


@dataclass(frozen=True)
class VastCandidate:
    """One search hit, holding only values VAST actually returned."""

    clip_id: str
    timestamp_seconds: float
    duration: float | None
    score: float | None
    collection: str | None


@dataclass(frozen=True)
class VastVideo:
    clip_id: str
    duration: float | None
    collection: str | None


@dataclass
class VastStream:
    status_code: int
    headers: dict[str, str]
    _response: httpx.Response = field(repr=False)

    def iter_bytes(self) -> Iterator[bytes]:
        try:
            yield from self._response.iter_bytes()
        finally:
            self._response.close()

    def close(self) -> None:
        self._response.close()


class VastClient:
    def __init__(self, config: VastConfig, transport: httpx.BaseTransport | None = None):
        self._config = config
        self._http = httpx.Client(
            base_url=config.base_url,
            timeout=config.timeout_s,
            transport=transport,
            follow_redirects=False,
        )
        self._token: str | None = None

    @classmethod
    def from_env(cls, transport: httpx.BaseTransport | None = None) -> "VastClient":
        return cls(VastConfig.from_env(), transport=transport)

    def __enter__(self) -> "VastClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def login(self) -> None:
        response = self._send(
            "POST",
            LOGIN_PATH,
            json={"username": self._config.username, "password": self._config.password},
            authenticated=False,
        )
        if response.status_code in (401, 403):
            raise VastError("VAST rejected the configured credentials", "authentication failed", 502)
        self._raise_for_status(response, "login")
        body = self._json(response, "login")
        token = next((body.get(key) for key in TOKEN_KEYS if isinstance(body, dict) and body.get(key)), None)
        if not isinstance(token, str):
            raise VastSchemaError("VAST login response did not include an access token")
        self._token = token

    def health(self) -> float:
        """Authenticate now and return the round-trip time in milliseconds."""
        started = perf_counter()
        self.login()
        return round((perf_counter() - started) * 1000, 1)

    def search(self, query: str, *, limit: int = 20, collection: str | None = None) -> list[VastCandidate]:
        payload: dict[str, Any] = {"query": query, "limit": limit}
        if collection:
            payload["collection"] = collection
        response = self._authed("POST", SEARCH_PATH, json=payload)
        self._raise_for_status(response, "search")
        return [_parse_candidate(item, index) for index, item in enumerate(_result_list(self._json(response, "search"), "search"))]

    def explore(self) -> list[VastVideo]:
        response = self._authed("GET", EXPLORE_PATH)
        self._raise_for_status(response, "explore")
        return [_parse_video(item, index) for index, item in enumerate(_result_list(self._json(response, "explore"), "explore"))]

    def stream(self, clip_id: str, range_header: str | None = None) -> VastStream:
        headers = {"Range": range_header} if range_header else {}
        response = self._authed("GET", STREAM_PATH, params={STREAM_CLIP_PARAM: clip_id}, headers=headers, stream=True)
        if response.status_code not in (200, 206):
            response.read()
            response.close()
            if response.status_code == 404:
                raise VastError("VAST has no clip with that id", "clip not found", 404)
            if response.status_code == 416:
                raise VastError("Requested range is not satisfiable", "range not satisfiable", 416)
            self._raise_for_status(response, "stream")
        forwarded = {name: response.headers[name] for name in FORWARDED_STREAM_HEADERS if name in response.headers}
        return VastStream(response.status_code, forwarded, response)

    def _authed(self, method: str, path: str, *, stream: bool = False, **kwargs: Any) -> httpx.Response:
        if self._token is None:
            self.login()
        response = self._send(method, path, stream=stream, **kwargs)
        if response.status_code == 401:
            response.close()
            self._token = None
            self.login()
            response = self._send(method, path, stream=stream, **kwargs)
        return response

    def _send(
        self,
        method: str,
        path: str,
        *,
        authenticated: bool = True,
        stream: bool = False,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        headers = dict(headers or {})
        if authenticated and self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        started = perf_counter()
        try:
            request = self._http.build_request(method, path, headers=headers, **kwargs)
            response = self._http.send(request, stream=stream)
        except httpx.TimeoutException:
            log.warning("VAST %s %s timed out", method, path)
            raise VastError(
                f"VAST request timed out after {self._config.timeout_s:g} s", "timeout", 504
            ) from None
        except httpx.HTTPError as exc:
            log.warning("VAST %s %s failed: %s", method, path, type(exc).__name__)
            raise VastError("Could not reach VAST", f"unreachable ({type(exc).__name__})", 503) from None
        log.info("VAST %s %s -> %s in %.0f ms", method, path, response.status_code, (perf_counter() - started) * 1000)
        return response

    @staticmethod
    def _raise_for_status(response: httpx.Response, operation: str) -> None:
        if response.is_success:
            return
        if response.status_code in (401, 403):
            raise VastError(f"VAST denied the {operation} request", "unauthorized", 502)
        raise VastError(f"VAST {operation} failed with HTTP {response.status_code}", f"http {response.status_code}", 502)

    @staticmethod
    def _json(response: httpx.Response, operation: str) -> Any:
        try:
            return response.json()
        except ValueError:
            raise VastSchemaError(f"VAST {operation} response was not JSON") from None


def _result_list(body: Any, operation: str) -> list[Any]:
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for key in RESULT_LIST_KEYS:
            if isinstance(body.get(key), list):
                return body[key]
    raise VastSchemaError(f"VAST {operation} response did not contain a result list")


def _first(item: dict[str, Any], keys: tuple[str, ...]) -> Any:
    return next((item[key] for key in keys if item.get(key) is not None), None)


def _number(value: Any, label: str, index: int, *, required: bool) -> float | None:
    if value is None:
        if required:
            raise VastSchemaError(f"VAST result {index} is missing {label}")
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise VastSchemaError(f"VAST result {index} has a non-numeric {label}")
    try:
        number = float(value)
    except ValueError:
        raise VastSchemaError(f"VAST result {index} has a non-numeric {label}") from None
    if number < 0:
        raise VastSchemaError(f"VAST result {index} has a negative {label}")
    return number


def _clip_id(item: Any, index: int) -> str:
    if not isinstance(item, dict):
        raise VastSchemaError(f"VAST result {index} is not an object")
    clip_id = _first(item, CLIP_ID_KEYS)
    if not isinstance(clip_id, str) or not clip_id.strip():
        raise VastSchemaError(f"VAST result {index} is missing a source clip id")
    return clip_id.strip()


def _collection(item: dict[str, Any]) -> str | None:
    value = _first(item, COLLECTION_KEYS)
    return value if isinstance(value, str) and value else None


def _parse_candidate(item: Any, index: int) -> VastCandidate:
    clip_id = _clip_id(item, index)
    duration = _number(_first(item, DURATION_KEYS), "duration", index, required=False)
    if duration == 0:
        duration = None
    return VastCandidate(
        clip_id=clip_id,
        timestamp_seconds=_number(_first(item, TIMESTAMP_KEYS), "timestamp", index, required=True),
        duration=duration,
        score=_number(_first(item, SCORE_KEYS), "score", index, required=False),
        collection=_collection(item),
    )


def _parse_video(item: Any, index: int) -> VastVideo:
    return VastVideo(
        clip_id=_clip_id(item, index),
        duration=_number(_first(item, DURATION_KEYS), "duration", index, required=False),
        collection=_collection(item),
    )
