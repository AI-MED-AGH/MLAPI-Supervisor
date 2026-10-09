import base64
import logging
import re

import httpx

logger = logging.getLogger(__name__)

API = "https://api.github.com"
REGISTRY = "https://ghcr.io"
_MANIFEST_TYPES = ", ".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
])
_PACKAGE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_DIGEST_RE = re.compile(r"sha256:[a-f0-9]{64}")
_MAX_BODY = 5 * 1024 * 1024


class GhcrError(Exception):
    pass


class RateLimited(GhcrError):
    pass


def is_valid_package(name: str) -> bool:
    return bool(_PACKAGE_RE.fullmatch(name))


class GhcrClient:
    """Reads container packages of a GitHub organisation from the GitHub API and ghcr.io."""

    def __init__(self, *, token: str, org: str, http: httpx.AsyncClient):
        self._org = org
        self._http = http
        self._api = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self._registry = {
            "Authorization": "Bearer " + base64.b64encode(token.encode()).decode(),
            "Accept": _MANIFEST_TYPES,
        }

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, url: str, headers: dict, **kwargs) -> httpx.Response:
        try:
            # stream, so the size limit applies while reading instead of after the whole body is in memory
            async with self._http.stream(method, url, headers=headers, **kwargs) as response:
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body += chunk
                    if len(body) > _MAX_BODY:
                        raise GhcrError("response too large")
                result = httpx.Response(
                    response.status_code, headers=response.headers, content=bytes(body), request=response.request
                )
        except httpx.HTTPError as exc:
            raise GhcrError(f"request failed: {type(exc).__name__}") from exc
        if result.status_code == 429 or (
            result.status_code == 403
            and ("retry-after" in result.headers or result.headers.get("x-ratelimit-remaining") == "0")
        ):
            raise RateLimited("rate limited")
        return result

    async def list_packages(self) -> list[str]:
        names: list[str] = []
        url: str | None = f"{API}/orgs/{self._org}/packages"
        params: dict | None = {"package_type": "container", "per_page": 100}
        while url:
            response = await self._request("GET", url, self._api, params=params)
            if response.status_code != 200:
                raise GhcrError(f"listing packages failed: HTTP {response.status_code}")
            try:
                names.extend(item["name"] for item in response.json() if isinstance(item.get("name"), str))
            except (ValueError, TypeError, AttributeError) as exc:
                raise GhcrError("unexpected package listing") from exc
            url = (response.links.get("next") or {}).get("url")
            params = None
        return names

    async def resolve_digest(self, package: str, tag: str = "latest") -> str | None:
        if not is_valid_package(package):
            raise GhcrError("invalid package name")
        url = f"{REGISTRY}/v2/{self._org}/{package}/manifests/{tag}"
        response = await self._request("HEAD", url, self._registry)
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise GhcrError(f"resolving digest failed: HTTP {response.status_code}")
        digest = response.headers.get("Docker-Content-Digest", "")
        if not _DIGEST_RE.fullmatch(digest):
            raise GhcrError("registry returned no valid digest")
        return digest

    async def _json(self, url: str) -> dict:
        response = await self._request("GET", url, self._registry)
        if response.status_code != 200:
            raise GhcrError(f"HTTP {response.status_code} for registry object")
        try:
            body = response.json()
        except ValueError as exc:
            raise GhcrError("registry returned invalid JSON") from exc
        if not isinstance(body, dict):
            raise GhcrError("registry returned an unexpected object")
        return body

    async def read_labels(self, package: str, digest: str) -> dict[str, str]:
        if not is_valid_package(package) or not _DIGEST_RE.fullmatch(digest):
            raise GhcrError("invalid package or digest")
        base = f"{REGISTRY}/v2/{self._org}/{package}"
        manifest = await self._json(f"{base}/manifests/{digest}")
        if "manifests" in manifest:  # multi-arch index: use the linux/amd64 image
            chosen = next(
                (
                    m for m in manifest["manifests"]
                    if isinstance(m, dict)
                    and (m.get("platform") or {}).get("os") == "linux"
                    and (m.get("platform") or {}).get("architecture") == "amd64"
                ),
                None,
            )
            if chosen is None or not _DIGEST_RE.fullmatch(str(chosen.get("digest", ""))):
                raise GhcrError("no linux/amd64 image in index")
            manifest = await self._json(f"{base}/manifests/{chosen['digest']}")
        config_digest = (manifest.get("config") or {}).get("digest", "")
        if not _DIGEST_RE.fullmatch(str(config_digest)):
            raise GhcrError("manifest has no valid config digest")
        config = await self._json(f"{base}/blobs/{config_digest}")
        labels = (config.get("config") or {}).get("Labels") or {}
        return {str(k): str(v) for k, v in labels.items()} if isinstance(labels, dict) else {}
