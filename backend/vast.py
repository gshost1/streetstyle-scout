"""Client for the workshop VAST archive.

Configuration comes only from the environment (first name wins):

    ingress URL   VAST_INGRESS_URL | INGRESS_URL
    username      VAST_USERNAME    | USERNAME
    password      VAST_PASSWORD    | PASSWORD
    timeout       VAST_TIMEOUT_S   (optional, seconds, default 10)

The ingress URL, credentials, tokens and S3 source URIs are never logged or
placed in exception messages.

Request shapes follow the workshop archive: login returns `access_token`;
search takes query/top_k/llm_top_n/include_public; explore takes
scope/limit/offset/location; stream takes `source`, an s3:// URI from Explore.
Response item fields beyond `access_token` are not verified, so parsing is
strict: anything without the expected keys is a schema error, never a default.
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
# httpx/httpcore log full request URLs at INFO/DEBUG, which would expose the ingress URL and S3 sources.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

LOGIN_PATH = "/api/v1/auth/login"
SEARCH_PATH = "/api/v1/search"
EXPLORE_PATH = "/api/v1/videos/explore"
STREAM_PATH = "/api/v1/videos/stream"

SEARCH_LIST_KEY = "results"
EXPLORE_LIST_KEYS = ("videos", "items", "results")
SOURCE_KEY = "source"
FORWARDED_STREAM_HEADERS = (
    "content-type",
    "content-length",
    "content-range",
    "accept-ranges",
)

ENV_URL = ("VAST_INGRESS_URL", "INGRESS_URL")
ENV_USERNAME = ("VAST_USERNAME", "USERNAME")
ENV_PASSWORD = ("VAST_PASSWORD", "PASSWORD")
ENV_TIMEOUT = "VAST_TIMEOUT_S"
ALL_ENV_NAMES = (*ENV_URL, *ENV_USERNAME, *ENV_PASSWORD, ENV_TIMEOUT)


class VastError(Exception):
    """A VAST failure safe to show to API clients.

    `detail` is a short reason for `services.vast.detail`; `status_code` is
    the HTTP status the API should return.
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

        def pick(names: tuple[str, ...]) -> str:
            return next((env[name].strip() for name in names if (env.get(name) or "").strip()), "")

        url, username, password = pick(ENV_URL), pick(ENV_USERNAME), pick(ENV_PASSWORD)
        missing = [
            " or ".join(names)
            for names, value in ((ENV_URL, url), (ENV_USERNAME, username), (ENV_PASSWORD, password))
            if not value
        ]
        if missing:
            raise VastNotConfigured(missing)
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise VastError("The VAST ingress URL must be an http(s) URL", "invalid configuration", 503)
        try:
            timeout_s = float(env.get(ENV_TIMEOUT) or 10)
        except ValueError:
            raise VastError(f"{ENV_TIMEOUT} must be a number", "invalid configuration", 503) from None
        if timeout_s <= 0:
            raise VastError(f"{ENV_TIMEOUT} must be positive", "invalid configuration", 503)
        return cls(url.rstrip("/"), username, password, timeout_s)


@dataclass(frozen=True)
class VastHit:
    """One search hit. `source` is the archive's s3:// URI; never shown to clients."""

    source: str = field(repr=False)
    clip_id: str


@dataclass(frozen=True)
class VastVideo:
    source: str = field(repr=False)
    clip_id: str


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
        token = body.get("access_token") if isinstance(body, dict) else None
        if not isinstance(token, str) or not token:
            raise VastSchemaError("VAST login response did not include an access token")
        self._token = token

    def health(self) -> float:
        """Authenticate now and return the round-trip time in milliseconds."""
        started = perf_counter()
        self.login()
        return round((perf_counter() - started) * 1000, 1)

    def search(self, query: str, *, top_k: int = 15) -> list[VastHit]:
        response = self._authed(
            "POST",
            SEARCH_PATH,
            json={"query": query, "top_k": top_k, "llm_top_n": 3, "include_public": True},
        )
        self._raise_for_status(response, "search")
        body = self._json(response, "search")
        items = body.get(SEARCH_LIST_KEY) if isinstance(body, dict) else body
        if not isinstance(items, list):
            raise VastSchemaError("VAST search response did not contain a result list")
        hits = []
        for index, item in enumerate(items):
            source = _source(item, "search", index)
            hits.append(VastHit(source=source, clip_id=clip_id_from_source(source)))
        return hits

    def explore(self, *, limit: int = 48, offset: int = 0, location: str = "") -> list[VastVideo]:
        params: dict[str, str | int] = {"scope": "all", "limit": limit, "offset": offset}
        if location:
            params["location"] = location
        response = self._authed("GET", EXPLORE_PATH, params=params)
        self._raise_for_status(response, "explore")
        body = self._json(response, "explore")
        items = body if isinstance(body, list) else next(
            (body[key] for key in EXPLORE_LIST_KEYS if isinstance(body, dict) and isinstance(body.get(key), list)),
            None,
        )
        if items is None:
            raise VastSchemaError("VAST explore response did not contain a video list")
        videos = []
        for index, item in enumerate(items):
            source = _source(item, "explore", index)
            videos.append(VastVideo(source=source, clip_id=clip_id_from_source(source)))
        return videos

    def find_source(self, clip_id: str, *, location: str = "", page_size: int = 100, max_pages: int = 20) -> str:
        """Resolve a clip file name to its s3:// source by paging through Explore."""
        for page in range(max_pages):
            videos = self.explore(limit=page_size, offset=page * page_size, location=location)
            for video in videos:
                if video.clip_id == clip_id:
                    return video.source
            if len(videos) < page_size:
                break
        raise VastError("VAST has no clip with that id", "clip not found", 404)

    def stream(self, source: str, range_header: str | None = None) -> VastStream:
        if not source.startswith("s3://"):
            raise VastError("VAST video source must be an S3 URI from Explore", "invalid source", 400)
        headers = {"Range": range_header} if range_header else {}
        response = self._authed("GET", STREAM_PATH, params={"source": source}, headers=headers, stream=True)
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


def clip_id_from_source(source: str) -> str:
    return source.rstrip("/").rsplit("/", 1)[-1]


def _source(item: Any, operation: str, index: int) -> str:
    value = item.get(SOURCE_KEY) if isinstance(item, dict) else None
    if not isinstance(value, str) or not value.startswith("s3://") or not clip_id_from_source(value):
        raise VastSchemaError(f"VAST {operation} item {index} has no s3:// {SOURCE_KEY}")
    return value
