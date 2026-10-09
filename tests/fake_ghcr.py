"""An in-process fake of the GitHub packages API and the GHCR registry for the poller tests."""
import base64
import hashlib
import json

import httpx

MANIFEST = "application/vnd.oci.image.manifest.v1+json"
INDEX = "application/vnd.oci.image.index.v1+json"


def _digest(obj) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


class FakeGhcr:
    def __init__(self, org="org", token="pat-123"):
        self.org, self.token = org, token
        self.packages: dict[str, dict] = {}
        self.requests: list[tuple[str, str]] = []   # (method, path)
        self.fail: dict[str, int] = {}               # path substring -> status to return
        self.page_size = 100

    def add(self, name, labels, *, multiarch=False, tag="latest"):
        config = {"architecture": "amd64", "os": "linux", "config": {"Labels": labels}}
        config_digest = _digest(config)
        manifest = {"schemaVersion": 2, "mediaType": MANIFEST, "config": {"digest": config_digest}}
        manifest_digest = _digest(manifest)
        entry = {"config": config, "config_digest": config_digest,
                 "manifest": manifest, "manifest_digest": manifest_digest, "labels": labels}
        if multiarch:
            arm_config = {"architecture": "arm64", "os": "linux", "config": {"Labels": {"mlapi.model": "false"}}}
            arm_manifest = {"schemaVersion": 2, "mediaType": MANIFEST, "config": {"digest": _digest(arm_config)}}
            entry["arm"] = (arm_config, arm_manifest)
            index = {"schemaVersion": 2, "mediaType": INDEX, "manifests": [
                {"digest": _digest(arm_manifest), "mediaType": MANIFEST, "platform": {"os": "linux", "architecture": "arm64"}},
                {"digest": manifest_digest, "mediaType": MANIFEST, "platform": {"os": "linux", "architecture": "amd64"}},
            ]}
            entry["index"], entry["index_digest"] = index, _digest(index)
        self.packages[name] = entry
        return entry

    def top_digest(self, name):
        e = self.packages[name]
        return e.get("index_digest", e["manifest_digest"])

    def handler(self, request: httpx.Request) -> httpx.Response:
        url, path = request.url, request.url.path
        self.requests.append((request.method, f"{url.host}{path}"))
        for needle, status in self.fail.items():
            if needle in path:
                return httpx.Response(status, headers={"Retry-After": "30"} if status in (403, 429) else {})
        auth = request.headers.get("authorization", "")
        if url.host == "api.github.com":
            assert auth == f"Bearer {self.token}", auth
            assert path == f"/orgs/{self.org}/packages"
            names = sorted(self.packages)
            page = int(url.params.get("page", "1"))
            chunk = names[(page - 1) * self.page_size: page * self.page_size]
            headers = {}
            if page * self.page_size < len(names):
                headers["Link"] = f'<https://api.github.com/orgs/{self.org}/packages?page={page + 1}>; rel="next"'
            return httpx.Response(200, json=[{"name": n, "package_type": "container"} for n in chunk], headers=headers)
        assert url.host == "ghcr.io"
        assert auth == "Bearer " + base64.b64encode(self.token.encode()).decode(), auth
        parts = path.split("/")            # ['', 'v2', org, pkg, 'manifests'|'blobs', ref]
        pkg, kind, ref = parts[3], parts[4], parts[5]
        entry = self.packages.get(pkg)
        if entry is None:
            return httpx.Response(404)
        if kind == "manifests":
            if ref == "latest":
                body, digest, ctype = (entry.get("index"), entry.get("index_digest"), INDEX) if "index" in entry \
                    else (entry["manifest"], entry["manifest_digest"], MANIFEST)
            elif ref == entry.get("index_digest"):
                body, digest, ctype = entry["index"], ref, INDEX
            elif ref == entry["manifest_digest"]:
                body, digest, ctype = entry["manifest"], ref, MANIFEST
            else:
                return httpx.Response(404)
            headers = {"Docker-Content-Digest": digest, "Content-Type": ctype}
            if request.method == "HEAD":
                return httpx.Response(200, headers=headers)
            return httpx.Response(200, json=body, headers=headers)
        if kind == "blobs" and ref == entry["config_digest"]:
            return httpx.Response(200, json=entry["config"])
        return httpx.Response(404)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler), follow_redirects=True)
