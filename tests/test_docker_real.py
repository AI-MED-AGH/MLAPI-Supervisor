"""Runs the supervisor against the real local Docker engine. Opt in with RUN_DOCKER_TESTS=1."""
import asyncio
import io
import json
import os
import tarfile

import httpx
import pytest

pytestmark = pytest.mark.skipif(os.environ.get("RUN_DOCKER_TESTS") != "1", reason="set RUN_DOCKER_TESTS=1 to run")

SERVER = '''
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

class H(BaseHTTPRequestHandler):
    def _send(self, body, status=200):
        raw = json.dumps(body).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def do_GET(self):
        if self.path == "/health": self._send({"status": "healthy", "model_loaded": True, "model_name": "smoke-model"})
        elif self.path == "/info": self._send({"name": "smoke-model", "version": "1.0.0"})
        elif self.path == "/schema": self._send({"model_name": "smoke-model", "model_version": "1.0.0", "request_schema": {"type": "object"}, "response_schema": {"type": "object"}, "example": {"data": 1}})
        else: self._send({"error": "nope"}, 404)
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0)); body = json.loads(self.rfile.read(n) or b"{}")
        self._send({"success": True, "prediction": body.get("data")})
    def log_message(self, *a): pass

HTTPServer(("0.0.0.0", 8000), H).serve_forever()
'''
DOCKERFILE = '''FROM python:3.12-slim
LABEL mlapi.model=true mlapi.model.name=smoke-model mlapi.cpu=0.5 mlapi.memory=256Mi mlapi.disk=1Gi
COPY server.py /server.py
CMD ["python", "/server.py"]
'''
TAG = "mlapi-smoke-model:test"


def _context() -> io.BytesIO:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data in (("Dockerfile", DOCKERFILE), ("server.py", SERVER)):
            raw = data.encode()
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            tar.addfile(info, io.BytesIO(raw))
    buf.seek(0)
    return buf


@pytest.fixture
def docker_client():
    import docker

    try:
        client = docker.from_env()
        client.ping()
    except Exception:
        pytest.skip("no Docker engine reachable")
    client.images.build(fileobj=_context(), custom_context=True, tag=TAG, rm=True)
    yield client
    for c in client.containers.list(all=True, filters={"label": ["mlapi.model=smoke-model"]}):
        c.remove(force=True)
    for v in client.volumes.list(filters={"label": ["mlapi.model=smoke-model"]}):
        v.remove(force=True)
    try:
        client.images.remove(TAG, force=True)
    except Exception:
        pass


async def test_register_approve_serve_sleep_wake_delete_on_real_docker(docker_client, settings, redis, sessionmaker):
    from app.cluster.docker import DockerBackend, DockerInspector
    from app.db import get_session
    from app.deployer.probe import HttpProbe
    from app.main import create_app
    from tests.conftest import ADMIN_HEADERS as H

    settings.docker_publish_ports = True
    settings.deploy_timeout = 60
    settings.deploy_poll_interval = 0.5
    backend = DockerBackend(settings, client=docker_client)
    app = create_app(settings, redis=redis, sessionmaker=sessionmaker, backend=backend,
                     probe=HttpProbe(), inspector=DockerInspector(docker_client))

    async def override():
        async with sessionmaker() as s:
            yield s

    app.dependency_overrides[get_session] = override
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://sup") as api:
        r = await api.post("/v1/models", headers=H, json={"image": TAG, "source": "local"})
        assert r.status_code == 202 and r.json()["action"] == "approval", r.text
        pending = (await api.get("/v1/approvals", headers=H)).json()[0]
        assert pending["requested"]["cpu_m"] == 500 and pending["requested"]["memory_bytes"] == 256 * 1024 * 1024
        await api.post(f"/v1/approvals/{pending['id']}/approve", headers=H)
        await app.state.services.deployer.drain()

        detail = (await api.get("/v1/models/smoke-model", headers=H)).json()
        assert detail["state"] == "ready", detail
        route = json.loads(await redis.get("route:smoke-model"))
        assert route["state"] == "ready"
        async with httpx.AsyncClient() as http:
            predicted = (await http.post(route["url"] + "/predict", json={"data": 42})).json()
        assert predicted == {"success": True, "prediction": 42}

        container = docker_client.containers.get("mlapi-m-smoke-model")
        host = container.attrs["HostConfig"]
        assert host["NanoCpus"] == 500_000_000 and host["Memory"] == 256 * 1024 * 1024
        assert host["CapDrop"] == ["ALL"] and "no-new-privileges" in host["SecurityOpt"]

        assert (await api.post("/v1/models/smoke-model/sleep", headers=H)).json()["state"] == "sleeping"
        assert docker_client.containers.get("mlapi-m-smoke-model").status in ("exited", "stopping")
        assert (await api.post("/v1/models/smoke-model/wake", headers=H)).status_code == 202
        await app.state.services.deployer.drain()
        assert (await api.get("/v1/models/smoke-model", headers=H)).json()["state"] == "ready"

        assert (await api.delete("/v1/models/smoke-model", headers=H)).json()["state"] == "removed"
        assert await redis.exists("route:smoke-model") == 0
        with pytest.raises(Exception):
            docker_client.containers.get("mlapi-m-smoke-model")
