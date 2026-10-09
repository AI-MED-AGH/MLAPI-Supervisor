import logging

import httpx

from app.poller.ghcr import GhcrClient, GhcrError, RateLimited, is_valid_package
from app.registry.errors import Conflict
from app.registry.labels import LabelError, NotAModel, parse_labels
from app.registry.queries import get_model_by_name

logger = logging.getLogger(__name__)


class Poller:
    """Looks for new `latest` digests of model images in a GHCR organisation."""

    def __init__(self, *, client: GhcrClient, sessionmaker, registry, deployer, org: str, events=None):
        self._client = client
        self._sm = sessionmaker
        self._registry = registry
        self._deployer = deployer
        self._org = org
        self._events = events
        self._seen: dict[str, str] = {}  # package -> last digest fully handled (including "not a model")

    async def aclose(self) -> None:
        await self._client.aclose()

    async def tick(self) -> None:
        try:
            packages = await self._client.list_packages()
        except RateLimited:
            logger.warning("GHCR rate limit hit while listing packages; retrying next tick")
            return
        except GhcrError as exc:
            logger.warning("Could not list packages: %s", exc)
            return
        for package in packages:
            if not is_valid_package(package):
                continue
            try:
                await self._poll_package(package)
            except RateLimited:
                logger.warning("GHCR rate limit hit; stopping this poll")
                return
            except GhcrError as exc:
                logger.warning("Skipping package %s for now: %s", package, exc)
            except Exception:
                logger.exception("Unexpected error while polling %s", package)

    def _fail(self, name: str, digest: str, reason: str) -> None:
        logger.warning("Image %s %s rejected: %s", name, digest, reason)
        if self._events is not None:
            self._events.emit("deploy.failed", name, digest=digest, status="failed", reason=reason, rolled_back=False)

    async def _poll_package(self, package: str) -> None:
        digest = await self._client.resolve_digest(package)
        if digest is None or self._seen.get(package) == digest:
            return
        labels = await self._client.read_labels(package, digest)
        try:
            parsed = parse_labels(labels, package)
        except NotAModel:
            self._seen[package] = digest
            return
        except LabelError as exc:
            self._fail(labels.get("mlapi.model.name") or package, digest, str(exc))
            self._seen[package] = digest
            return

        async with self._sm() as session:
            try:
                submission = await self._registry.submit_digest(
                    session,
                    name=parsed.name,
                    image=f"ghcr.io/{self._org}/{package}",
                    digest=digest,
                    labels=parsed,
                    source="ghcr",
                )
            except Conflict as exc:
                await session.rollback()
                self._fail(parsed.name, digest, str(exc))
                self._seen[package] = digest
                return
            if submission.action == "deploy":
                model = await get_model_by_name(session, parsed.name)
                try:
                    await self._deployer.begin(session, model, digest)
                except Conflict as exc:  # busy right now: leave it unseen so the next tick retries
                    await session.commit()
                    logger.info("Deploy of %s postponed: %s", parsed.name, exc)
                    return
            await session.commit()
            model_id = submission.model_id
        if submission.action == "deploy":
            self._deployer.spawn(model_id, digest)
        self._seen[package] = digest


def build_poller(services, state) -> Poller:
    settings = state.settings
    http = httpx.AsyncClient(timeout=20, follow_redirects=True)
    client = GhcrClient(token=settings.ghcr_token, org=settings.ghcr_org, http=http)
    return Poller(
        client=client,
        sessionmaker=state.sessionmaker,
        registry=services.registry,
        deployer=services.deployer,
        org=settings.ghcr_org,
        events=services.events,
    )
