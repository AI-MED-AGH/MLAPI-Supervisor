import time
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.registry.tables import Deployment, Model, ResourceRequest


async def get_model_by_name(session: AsyncSession, name: str) -> Model | None:
    return await session.scalar(select(Model).where(Model.name == name))


async def get_model(session: AsyncSession, model_id: int) -> Model | None:
    return await session.get(Model, model_id)


async def list_models(session: AsyncSession) -> Sequence[Model]:
    return (await session.scalars(select(Model).order_by(Model.name))).all()


async def get_request(session: AsyncSession, request_id: int) -> ResourceRequest | None:
    return await session.get(ResourceRequest, request_id)


async def list_requests(session: AsyncSession, status: str | None = None) -> Sequence[ResourceRequest]:
    query = select(ResourceRequest).order_by(ResourceRequest.id)
    if status is not None:
        query = query.where(ResourceRequest.status == status)
    return (await session.scalars(query)).all()


async def add_deployment(session: AsyncSession, model_id: int, digest: str) -> Deployment:
    dep = Deployment(model_id=model_id, digest=digest, status="started", started_at=int(time.time()))
    session.add(dep)
    await session.flush()
    return dep


async def list_deployments(session: AsyncSession, model_id: int, limit: int = 20) -> Sequence[Deployment]:
    query = (
        select(Deployment)
        .where(Deployment.model_id == model_id)
        .order_by(Deployment.id.desc())
        .limit(limit)
    )
    return (await session.scalars(query)).all()
