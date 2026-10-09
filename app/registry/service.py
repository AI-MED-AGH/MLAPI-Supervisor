import logging
import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.events import EventBus
from app.registry import states
from app.registry.approval import Decision, decide
from app.registry.errors import Conflict, Invalid, NotFound
from app.registry.labels import ModelLabels
from app.registry.queries import get_model, get_model_by_name, get_request
from app.registry.resources import Resources
from app.registry.tables import Model, ResourceRequest

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Submission:
    action: str  # "noop" | "deploy" | "approval"
    model_id: int
    digest: str | None = None


class RegistryService:
    """Registration of model images and the resource-approval workflow."""

    def __init__(self, events: EventBus | None):
        self._events = events

    def _emit(self, event: str, model: Model, digest: str | None, **details) -> None:
        if self._events is not None:
            self._events.emit(event, model.name, digest=digest, status=model.state, **details)

    async def submit_digest(
        self,
        session: AsyncSession,
        *,
        name: str,
        image: str,
        digest: str,
        labels: ModelLabels,
        source: str,
    ) -> Submission:
        model = await get_model_by_name(session, name)
        now = int(time.time())

        if model is None:
            model = Model(name=name, image=image, mode=labels.mode, source=source)
            session.add(model)
            await session.flush()
        elif model.state == states.REMOVED:
            model.image, model.mode, model.source = image, labels.mode, source
            model.state, model.state_detail = states.PENDING_APPROVAL, None
            model.current_digest = model.previous_digest = None
            model.pending_digest = model.failed_digest = None
            model.approved_resources = None
        else:
            # a name belongs to one image: another package must not be able to claim it (or file requests under it)
            if model.source != source or (source == "ghcr" and model.image != image):
                raise Conflict(f"model name {name!r} belongs to {model.image!r} ({model.source}); not {image!r} ({source})")
            if labels.mode != model.mode:
                raise Conflict(f"model {name!r} is {model.mode}-mode; mode changes need the model removed first")
            if model.image != image:  # a local model may move to another tag
                model.image = image

        model.last_checked_at = now
        if digest in (model.current_digest, model.pending_digest, model.failed_digest) or await self._was_rejected(
            session, model.id, digest
        ):
            return Submission("noop", model.id)

        requested = labels.resources
        model.requested_resources = requested.to_dict()
        approved = Resources.from_dict(model.approved_resources) if model.approved_resources else None

        # a newer digest always replaces whatever was waiting for approval: approving the older one later would roll back
        await self._supersede_pending(session, model)
        if decide(approved, requested) is Decision.AUTO:
            model.pending_digest = None
            return Submission("deploy", model.id, digest)

        # needs an admin: keep any running version untouched
        session.add(ResourceRequest(model_id=model.id, digest=digest, requested=requested.to_dict()))
        model.pending_digest = digest
        await session.flush()
        self._emit("approval.requested", model, digest, requested=requested.to_dict())
        return Submission("approval", model.id, digest)

    async def approve(
        self,
        session: AsyncSession,
        request_id: int,
        *,
        admin: str = "admin",
        override: Resources | None = None,
    ) -> tuple[Model, str]:
        request = await self._pending_request(session, request_id)
        requested = Resources.from_dict(request.requested)
        granted = override or requested
        if granted.exceeds(requested):
            raise Invalid("approved resources must not exceed the request")
        model = await get_model(session, request.model_id)
        model.approved_resources = granted.to_dict()
        request.status = states.REQ_APPROVED
        request.decided_by, request.decided_at = admin, int(time.time())
        await session.flush()
        self._emit("approval.approved", model, request.digest, approved=granted.to_dict())
        return model, request.digest

    async def reject(self, session: AsyncSession, request_id: int, *, note: str | None = None, admin: str = "admin") -> Model:
        request = await self._pending_request(session, request_id)
        model = await get_model(session, request.model_id)
        request.status = states.REQ_REJECTED
        request.decided_by, request.decided_at, request.note = admin, int(time.time()), note
        # not a "failed deploy": a rejected digest must not be redeployable, so it is remembered through its request row
        if model.pending_digest == request.digest:
            model.pending_digest = None
        if model.current_digest is None and model.state == states.PENDING_APPROVAL:
            model.state = states.FAILED
            model.state_detail = f"resource request rejected{': ' + note if note else ''}"
        await session.flush()
        self._emit("approval.rejected", model, request.digest, note=note)
        return model

    @staticmethod
    async def _supersede_pending(session: AsyncSession, model: Model) -> None:
        pending = await session.scalars(
            select(ResourceRequest).where(
                ResourceRequest.model_id == model.id, ResourceRequest.status == states.REQ_PENDING
            )
        )
        for request in pending.all():
            request.status = states.REQ_SUPERSEDED

    @staticmethod
    async def _was_rejected(session: AsyncSession, model_id: int, digest: str) -> bool:
        found = await session.scalar(
            select(ResourceRequest.id).where(
                ResourceRequest.model_id == model_id,
                ResourceRequest.digest == digest,
                ResourceRequest.status == states.REQ_REJECTED,
            )
        )
        return found is not None

    async def _pending_request(self, session: AsyncSession, request_id: int) -> ResourceRequest:
        request = await get_request(session, request_id)
        if request is None:
            raise NotFound(f"request {request_id} not found")
        if request.status != states.REQ_PENDING:
            raise Conflict(f"request {request_id} is already {request.status}")
        return request
