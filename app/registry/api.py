import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin
from app.db import get_session
from app.deps import get_redis
from app.registry import states
from app.registry.errors import Conflict, NotFound
from app.registry.labels import LabelError, NotAModel, parse_labels
from app.registry.queries import (
    get_model_by_name,
    get_request,
    list_deployments,
    list_models,
    list_requests,
)
from app.registry.resources import ResourceError, Resources, parse_cpu, parse_quantity
from app.registry.tables import Model, ResourceRequest
from app.services import Services, get_services

logger = logging.getLogger(__name__)

models_router = APIRouter(prefix="/v1/models", tags=["models"], dependencies=[Depends(require_admin)])
approvals_router = APIRouter(prefix="/v1/approvals", tags=["approvals"], dependencies=[Depends(require_admin)])


def model_out(model: Model) -> dict:
    return {
        "name": model.name,
        "image": model.image,
        "mode": model.mode,
        "source": model.source,
        "state": model.state,
        "state_detail": model.state_detail,
        "current_digest": model.current_digest,
        "previous_digest": model.previous_digest,
        "pending_digest": model.pending_digest,
        "failed_digest": model.failed_digest,
        "approved_resources": model.approved_resources,
        "requested_resources": model.requested_resources,
        "config": model.config or {},
        "created_at": model.created_at,
        "updated_at": model.updated_at,
        "last_checked_at": model.last_checked_at,
    }


async def _model_or_404(session: AsyncSession, name: str) -> Model:
    model = await get_model_by_name(session, name)
    if model is None:
        raise NotFound(f"model {name!r} not found")
    return model


class RegisterBody(BaseModel):
    image: str = Field(min_length=1, max_length=300)
    name: str | None = Field(default=None, max_length=80)
    source: Literal["local", "ghcr"] = "local"


@models_router.post("", status_code=status.HTTP_202_ACCEPTED)
async def register_model(
    body: RegisterBody, session: AsyncSession = Depends(get_session), svc: Services = Depends(get_services)
):
    if svc.inspector is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "image inspection is not configured")
    try:
        info = await svc.inspector.inspect(body.image, body.source)
    except LookupError:
        raise NotFound(f"image {body.image!r} not found")
    try:
        labels = parse_labels(info.labels, body.name or info.package_name)
    except NotAModel:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "image is not an MLAPI model (missing label mlapi.model=true)")
    except LabelError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    submission = await svc.registry.submit_digest(
        session, name=labels.name, image=body.image, digest=info.digest, labels=labels, source=body.source
    )
    model = await get_model_by_name(session, labels.name)
    if submission.action == "deploy":
        await svc.deployer.begin(session, model, submission.digest)
    await session.commit()
    if submission.action == "deploy":
        svc.deployer.spawn(model.id, submission.digest)
    return {"action": submission.action, "model": model_out(model)}


@models_router.get("")
async def get_models(session: AsyncSession = Depends(get_session)):
    return [model_out(m) for m in await list_models(session)]


@models_router.get("/{name}")
async def get_model_detail(
    name: str,
    session: AsyncSession = Depends(get_session),
    svc: Services = Depends(get_services),
    redis=Depends(get_redis),
):
    import json

    model = await _model_or_404(session, name)
    out = model_out(model)
    out["deployments"] = [
        {
            "digest": d.digest, "status": d.status, "reason": d.reason,
            "started_at": d.started_at, "finished_at": d.finished_at,
        }
        for d in await list_deployments(session, model.id)
    ]
    raw_schema = await redis.get(f"schema:{name}")
    out["schema"] = json.loads(raw_schema) if raw_schema else None
    try:
        runtime = await svc.backend.status(name)
        out["runtime"] = {
            "exists": runtime.exists, "ready": runtime.ready, "replicas": runtime.replicas,
            "worker_replicas": runtime.worker_replicas, "pending_reason": runtime.pending_reason,
            "restarts": runtime.restarts,
        }
    except Exception:
        logger.warning("Could not read runtime status for %s", name, exc_info=True)
        out["runtime"] = None
    return out


@models_router.patch("/{name}/config")
async def patch_config(
    name: str,
    body: dict[str, Any],
    session: AsyncSession = Depends(get_session),
    svc: Services = Depends(get_services),
):
    model = await _model_or_404(session, name)
    redeploy = await svc.deployer.update_config(session, model, body)
    digest = model.current_digest
    await session.commit()
    if redeploy:
        svc.deployer.spawn(model.id, digest)
    return {"redeploy": redeploy, "model": model_out(model)}


@models_router.post("/{name}/redeploy", status_code=status.HTTP_202_ACCEPTED)
async def redeploy(name: str, session: AsyncSession = Depends(get_session), svc: Services = Depends(get_services)):
    model = await _model_or_404(session, name)
    digest = await svc.deployer.redeploy(session, model)
    await session.commit()
    svc.deployer.spawn(model.id, digest)
    return model_out(model)


@models_router.post("/{name}/rollback", status_code=status.HTTP_202_ACCEPTED)
async def rollback(name: str, session: AsyncSession = Depends(get_session), svc: Services = Depends(get_services)):
    model = await _model_or_404(session, name)
    digest = await svc.deployer.rollback(session, model)
    await session.commit()
    svc.deployer.spawn(model.id, digest)
    return model_out(model)


@models_router.post("/{name}/sleep")
async def sleep(name: str, session: AsyncSession = Depends(get_session), svc: Services = Depends(get_services)):
    model = await _model_or_404(session, name)
    await svc.deployer.sleep(session, model)
    await session.commit()
    return model_out(model)


@models_router.post("/{name}/wake", status_code=status.HTTP_202_ACCEPTED)
async def wake(name: str, session: AsyncSession = Depends(get_session), svc: Services = Depends(get_services)):
    model = await _model_or_404(session, name)
    await svc.deployer.begin_wake(session, model)
    await session.commit()
    svc.deployer.spawn_wake(model.id)
    return model_out(model)


@models_router.delete("/{name}", status_code=status.HTTP_200_OK)
async def delete_model(
    name: str,
    keep_cache: bool = False,
    session: AsyncSession = Depends(get_session),
    svc: Services = Depends(get_services),
):
    model = await _model_or_404(session, name)
    await svc.deployer.remove(session, model, keep_cache=keep_cache)
    await session.commit()
    return model_out(model)


# ───────────── approvals ─────────────
class ApproveBody(BaseModel):
    cpu: str | None = None
    memory: str | None = None
    gpu: bool | None = None
    disk: str | None = None


class RejectBody(BaseModel):
    note: str | None = Field(default=None, max_length=500)


async def _approval_out(session: AsyncSession, req: ResourceRequest) -> dict:
    from app.registry.queries import get_model

    model = await get_model(session, req.model_id)
    return {
        "id": req.id,
        "model": model.name,
        "digest": req.digest,
        "status": req.status,
        "requested": req.requested,
        "currently_approved": model.approved_resources,
        "decided_by": req.decided_by,
        "decided_at": req.decided_at,
        "note": req.note,
        "created_at": req.created_at,
    }


@approvals_router.get("")
async def list_approvals(status_filter: str = states.REQ_PENDING, session: AsyncSession = Depends(get_session)):
    status_arg = None if status_filter == "all" else status_filter
    return [await _approval_out(session, r) for r in await list_requests(session, status=status_arg)]


@approvals_router.post("/{request_id}/approve")
async def approve(
    request_id: int,
    body: ApproveBody | None = None,
    session: AsyncSession = Depends(get_session),
    svc: Services = Depends(get_services),
):
    override = None
    body = body or ApproveBody()
    if any(v is not None for v in (body.cpu, body.memory, body.gpu, body.disk)):
        req = await get_request(session, request_id)
        if req is None:
            raise NotFound(f"request {request_id} not found")
        asked = Resources.from_dict(req.requested)
        try:
            override = Resources(
                cpu_m=parse_cpu(body.cpu) if body.cpu is not None else asked.cpu_m,
                memory_bytes=parse_quantity(body.memory) if body.memory is not None else asked.memory_bytes,
                gpu=body.gpu if body.gpu is not None else asked.gpu,
                disk_bytes=parse_quantity(body.disk) if body.disk is not None else asked.disk_bytes,
            )
        except ResourceError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    model, digest = await svc.registry.approve(session, request_id, override=override)
    started = True
    reason = None
    try:
        await svc.deployer.begin(session, model, digest)
    except Conflict as exc:
        started, reason = False, str(exc)
    await session.commit()
    if started:
        svc.deployer.spawn(model.id, digest)
    return {"deploy_started": started, "reason": reason, "model": model_out(model)}


@approvals_router.post("/{request_id}/reject")
async def reject(
    request_id: int,
    body: RejectBody | None = None,
    session: AsyncSession = Depends(get_session),
    svc: Services = Depends(get_services),
):
    model = await svc.registry.reject(session, request_id, note=(body.note if body else None))
    await session.commit()
    return model_out(model)
