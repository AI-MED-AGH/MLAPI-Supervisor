from dataclasses import dataclass

from fastapi import FastAPI, Request

from app.deployer.probe import HttpProbe
from app.deployer.service import Deployer
from app.events import EventBus
from app.registry.service import RegistryService


@dataclass
class Services:
    backend: object
    probe: object
    inspector: object | None
    events: EventBus
    registry: RegistryService
    deployer: Deployer


def build_services(app: FastAPI) -> Services:
    from app.cluster.factory import make_backend

    state = app.state
    backend = state.backend or make_backend(state.settings)
    state.backend = backend
    probe = state.probe or HttpProbe()
    events = EventBus(state.sessionmaker)
    deployer = Deployer(
        sessionmaker=state.sessionmaker,
        backend=backend,
        redis=state.redis,
        events=events,
        probe=probe,
        settings=state.settings,
        queue_access=getattr(state, "queue_access", None),
    )
    return Services(
        backend=backend,
        probe=probe,
        inspector=state.inspector,
        events=events,
        registry=RegistryService(events),
        deployer=deployer,
    )


def get_services(request: Request) -> Services:
    state = request.app.state
    if state.services is None:
        state.services = build_services(request.app)
    return state.services
