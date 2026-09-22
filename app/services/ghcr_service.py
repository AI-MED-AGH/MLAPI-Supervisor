from __future__ import annotations

import re

import httpx

_SEMVER_PATTERN = re.compile(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-+].*)?$")


class GHCRService:
    def __init__(
        self,
        *,
        registry: str,
        username: str | None,
        token: str | None,
        timeout_seconds: int = 10,
    ) -> None:
        self.registry = registry.strip().rstrip("/")
        self.username = username
        self.token = token
        self.timeout_seconds = timeout_seconds

    def list_tags(self, image: str) -> list[str]:
        normalized_image = self._normalize_image(image)
        url = f"https://{self.registry}/v2/{normalized_image}/tags/list"

        auth = None
        if self.token:
            owner = normalized_image.split("/", 1)[0]
            username = self.username or owner
            auth = (username, self.token)

        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.get(url, auth=auth)
                if response.status_code == 404:
                    return []
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Failed to fetch tags from GHCR for {image}: {exc}") from exc

        payload = response.json()
        tags = payload.get("tags")
        if not isinstance(tags, list):
            return []
        return [str(tag).strip() for tag in tags if str(tag).strip()]

    def get_latest_tag(self, image: str) -> str | None:
        tags = self.list_tags(image)
        if not tags:
            return None
        return max(tags, key=self._tag_sort_key)

    def _normalize_image(self, image: str) -> str:
        cleaned_image = image.strip()
        if not cleaned_image:
            raise ValueError("image cannot be empty")

        registry_prefix = f"{self.registry}/"
        if cleaned_image.startswith(registry_prefix):
            return cleaned_image[len(registry_prefix) :]
        return cleaned_image

    @staticmethod
    def _tag_sort_key(tag: str) -> tuple[int, int, int, int, str]:
        if tag == "latest":
            return (1, 0, 0, 0, tag)

        match = _SEMVER_PATTERN.fullmatch(tag)
        if not match:
            return (0, 0, 0, 0, tag)

        major = int(match.group(1))
        minor = int(match.group(2) or 0)
        patch = int(match.group(3) or 0)
        return (2, major, minor, patch, tag)
