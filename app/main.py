from __future__ import annotations

import asyncio
import contextlib
import logging

from fastapi import Depends, FastAPI, HTTPException, Request, status
from kubernetes.client.rest import ApiException

from app.config import Settings, get_settings
from app.schemas import (
    ModelDeleteResponse,
    ModelDeploymentRequest,
    ModelDeploymentResponse,
    ModelListItem,
    ModelListResponse,
    ModelStatusResponse,
    ProjectPullRequest,
    build_image_reference,
    normalize_model_id,
)
from app.services import (
    DeployFailure,
    DeployRequest,
    DeployWorkflow,
    GHCRService,
    InMemoryProjectRegistry,
    KubernetesService,
    ModelLockManager,
    Project,
    ProjectRegistry,
    StateStore,
)

logger = logging.getLogger(__name__)


def get_kubernetes_service(request: Request) -> KubernetesService:
    service = getattr(request.app.state, "kubernetes_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Kubernetes service is not initialized")
    return service


def get_state_store(request: Request) -> StateStore:
    store = getattr(request.app.state, "state_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="State store is not initialized")
    return store


def get_ghcr_service(request: Request) -> GHCRService:
    service = getattr(request.app.state, "ghcr_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="GHCR service is not initialized")
    return service


def get_project_registry(request: Request) -> ProjectRegistry:
    registry = getattr(request.app.state, "project_registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="Project registry is not initialized")
    return registry


def get_deploy_workflow(request: Request) -> DeployWorkflow:
    workflow = getattr(request.app.state, "deploy_workflow", None)
    if workflow is None:
        raise HTTPException(status_code=503, detail="Deploy workflow is not initialized")
    return workflow


def _image_owner(image: str, registry: str) -> str | None:
    normalized_image = image.strip()
    registry_prefix = f"{registry.strip().rstrip('/')}/"
    if normalized_image.startswith(registry_prefix):
        normalized_image = normalized_image[len(registry_prefix) :]

    parts = normalized_image.split("/", 1)
    if len(parts) < 2:
        return None
    return parts[0]


def _derive_model_id_from_image(image: str) -> str:
    image_name = image.strip().rsplit("/", 1)[-1]
    image_name = image_name.split(":", 1)[0]
    return normalize_model_id(image_name)


def _stored_image_reference(
    *,
    model_id: str,
    settings: Settings,
    state_store: StateStore,
) -> str | None:
    model = state_store.get_model(model_id)
    if model is None:
        return None

    image = model.get("image")
    tag = model.get("tag")
    if not image or not tag:
        return None

    try:
        return build_image_reference(image=image, tag=tag, registry=settings.ghcr_registry)
    except ValueError:
        return None


def _deploy_model(
    *,
    request: ModelDeploymentRequest,
    settings: Settings,
    workflow: DeployWorkflow,
) -> ModelDeploymentResponse:
    model_id = normalize_model_id(request.model_id)
    image_reference = build_image_reference(
        image=request.image,
        tag=request.tag,
        registry=settings.ghcr_registry,
    )

    ghcr_username = settings.ghcr_username or _image_owner(request.image, settings.ghcr_registry)
    deploy_request = DeployRequest(
        model_id=model_id,
        image=request.image,
        tag=request.tag,
        image_reference=image_reference,
        replicas=request.replicas,
        container_port=request.container_port or settings.default_container_port,
        service_port=request.service_port or settings.default_service_port,
        env=dict(request.env),
        ghcr_username=ghcr_username,
        ghcr_token=settings.ghcr_token,
    )

    deployment_status = workflow.run(deploy_request)

    return ModelDeploymentResponse(
        model_id=model_id,
        deployment_name=deployment_status["deployment_name"],
        service_name=deployment_status["service_name"],
        image=image_reference,
        namespace=deployment_status["namespace"],
        service_type=deployment_status["service_type"],
        external_endpoints=deployment_status["external_endpoints"],
        message="Model deployment created or updated",
    )


def _deploy_from_project(
    *,
    project: Project,
    tag_override: str | None,
    settings: Settings,
    ghcr_service: GHCRService,
    workflow: DeployWorkflow,
) -> ModelDeploymentResponse:
    tag = tag_override or project.default_tag or "latest"
    if tag.lower() == "latest":
        resolved = ghcr_service.get_latest_tag(project.image)
        if resolved:
            tag = resolved

    model_id = normalize_model_id(project.project_id)
    image_reference = build_image_reference(
        image=project.image, tag=tag, registry=settings.ghcr_registry
    )

    deploy_request = DeployRequest(
        model_id=model_id,
        image=project.image,
        tag=tag,
        image_reference=image_reference,
        replicas=project.replicas,
        container_port=project.container_port or settings.default_container_port,
        service_port=project.service_port or settings.default_service_port,
        env=dict(project.env),
        ghcr_username=project.ghcr_username,
        ghcr_token=project.ghcr_token,
    )

    deployment_status = workflow.run(deploy_request)

    return ModelDeploymentResponse(
        model_id=model_id,
        deployment_name=deployment_status["deployment_name"],
        service_name=deployment_status["service_name"],
        image=image_reference,
        namespace=deployment_status["namespace"],
        service_type=deployment_status["service_type"],
        external_endpoints=deployment_status["external_endpoints"],
        message=f"Project '{project.project_id}' deployed at tag '{tag}'",
    )


def _poll_for_single_image(
    *,
    image: str,
    settings: Settings,
    ghcr_service: GHCRService,
    workflow: DeployWorkflow,
    state_store: StateStore,
) -> None:
    model_id = _derive_model_id_from_image(image)
    latest_tag = ghcr_service.get_latest_tag(image)
    if latest_tag is None:
        return

    previous_tag = state_store.get_tag(model_id)
    if previous_tag == latest_tag:
        return

    request = ModelDeploymentRequest(
        model_id=model_id,
        image=image,
        tag=latest_tag,
        replicas=1,
        container_port=settings.default_container_port,
        service_port=settings.default_service_port,
        env={},
    )
    _deploy_model(request=request, settings=settings, workflow=workflow)
    logger.info(
        "poll.updated model_id=%s from_tag=%s to_tag=%s",
        model_id,
        previous_tag,
        latest_tag,
    )


async def _poll_watched_images(app: FastAPI) -> None:
    settings: Settings = app.state.settings
    ghcr_service: GHCRService = app.state.ghcr_service
    workflow: DeployWorkflow = app.state.deploy_workflow
    state_store: StateStore = app.state.state_store

    while True:
        for image in settings.watched_images:
            try:
                await asyncio.to_thread(
                    _poll_for_single_image,
                    image=image,
                    settings=settings,
                    ghcr_service=ghcr_service,
                    workflow=workflow,
                    state_store=state_store,
                )
            except Exception:
                logger.exception("poll.failed image=%s", image)

        await asyncio.sleep(settings.poll_interval_seconds)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings
    app.state.kubernetes_service = KubernetesService(
        namespace=settings.k8s_namespace,
        service_type=settings.k8s_service_type,
        image_pull_secret_name=settings.image_pull_secret_name,
    )
    app.state.ghcr_service = GHCRService(
        registry=settings.ghcr_registry,
        username=settings.ghcr_username,
        token=settings.ghcr_token,
    )
    app.state.state_store = StateStore(settings.poll_state_file)
    app.state.project_registry = InMemoryProjectRegistry.from_environment()
    app.state.lock_manager = ModelLockManager()
    app.state.deploy_workflow = DeployWorkflow(
        kubernetes_service=app.state.kubernetes_service,
        state_store=app.state.state_store,
        lock_manager=app.state.lock_manager,
        registry=settings.ghcr_registry,
        rollout_timeout_seconds=settings.rollout_timeout_seconds,
        rollout_poll_interval=settings.rollout_poll_interval_seconds,
        max_attempts=settings.deploy_max_attempts,
    )

    poller_task: asyncio.Task[None] | None = None
    if settings.enable_polling and settings.watched_images:
        poller_task = asyncio.create_task(_poll_watched_images(app))
        logger.info(
            "poll.enabled images=%d interval=%ds",
            len(settings.watched_images),
            settings.poll_interval_seconds,
        )

    try:
        yield
    finally:
        if poller_task:
            poller_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await poller_task


app = FastAPI(
    title="MLAPI Supervisor",
    description="Supervisor controlling ML model instances",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "Welcome to MLAPI Supervisor"}


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


def _map_deploy_exception(exc: Exception) -> HTTPException:
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, DeployFailure):
        return HTTPException(status_code=502, detail=str(exc))
    if isinstance(exc, ApiException):
        return HTTPException(
            status_code=502,
            detail=f"Kubernetes API error ({exc.status}): {exc.reason}",
        )
    if isinstance(exc, RuntimeError):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(status_code=500, detail="Unexpected deploy error")


@app.post(
    "/models/deploy",
    response_model=ModelDeploymentResponse,
    status_code=status.HTTP_201_CREATED,
    description="Manual/admin deploy path. Prefer POST /projects/{project_id}/pull for CI triggers.",
)
def deploy_model(
    payload: ModelDeploymentRequest,
    settings: Settings = Depends(get_settings),
    workflow: DeployWorkflow = Depends(get_deploy_workflow),
) -> ModelDeploymentResponse:
    try:
        return _deploy_model(request=payload, settings=settings, workflow=workflow)
    except (ValueError, DeployFailure, ApiException, RuntimeError) as exc:
        raise _map_deploy_exception(exc) from exc


@app.post(
    "/projects/{project_id}/pull",
    response_model=ModelDeploymentResponse,
    status_code=status.HTTP_200_OK,
    description="CI-triggered pull-to-deploy for a registered project.",
)
def pull_project(
    project_id: str,
    payload: ProjectPullRequest | None = None,
    settings: Settings = Depends(get_settings),
    project_registry: ProjectRegistry = Depends(get_project_registry),
    ghcr_service: GHCRService = Depends(get_ghcr_service),
    workflow: DeployWorkflow = Depends(get_deploy_workflow),
) -> ModelDeploymentResponse:
    project = project_registry.get(project_id.strip())
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' is not registered")

    tag_override = payload.tag if payload else None
    try:
        return _deploy_from_project(
            project=project,
            tag_override=tag_override,
            settings=settings,
            ghcr_service=ghcr_service,
            workflow=workflow,
        )
    except (ValueError, DeployFailure, ApiException, RuntimeError) as exc:
        raise _map_deploy_exception(exc) from exc


@app.get("/models/{model_id}/status", response_model=ModelStatusResponse)
def model_status(
    model_id: str,
    settings: Settings = Depends(get_settings),
    kubernetes_service: KubernetesService = Depends(get_kubernetes_service),
    state_store: StateStore = Depends(get_state_store),
) -> ModelStatusResponse:
    normalized_model_id = normalize_model_id(model_id)

    try:
        status_payload = kubernetes_service.get_model_status(normalized_model_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ApiException as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Kubernetes API error ({exc.status}): {exc.reason}",
        ) from exc

    return ModelStatusResponse(
        model_id=normalized_model_id,
        deployment_name=status_payload["deployment_name"],
        service_name=status_payload["service_name"],
        image=_stored_image_reference(
            model_id=normalized_model_id,
            settings=settings,
            state_store=state_store,
        ),
        namespace=status_payload["namespace"],
        phase=status_payload["phase"],
        desired_replicas=status_payload["desired_replicas"],
        ready_replicas=status_payload["ready_replicas"],
        available_replicas=status_payload["available_replicas"],
        service_type=status_payload["service_type"],
        external_endpoints=status_payload["external_endpoints"],
    )


@app.get("/models", response_model=ModelListResponse)
def list_models(
    settings: Settings = Depends(get_settings),
    kubernetes_service: KubernetesService = Depends(get_kubernetes_service),
    state_store: StateStore = Depends(get_state_store),
) -> ModelListResponse:
    try:
        statuses = kubernetes_service.list_models()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ApiException as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Kubernetes API error ({exc.status}): {exc.reason}",
        ) from exc

    items: list[ModelListItem] = []
    for item in statuses:
        image_reference = _stored_image_reference(
            model_id=item["model_id"],
            settings=settings,
            state_store=state_store,
        )
        items.append(
            ModelListItem(
                model_id=item["model_id"],
                image=image_reference,
                deployment_name=item["deployment_name"],
                service_name=item["service_name"],
                phase=item["phase"],
                ready_replicas=item["ready_replicas"],
                available_replicas=item["available_replicas"],
                external_endpoints=item["external_endpoints"],
            )
        )

    return ModelListResponse(items=items)


@app.delete("/models/{model_id}", response_model=ModelDeleteResponse)
def delete_model(
    model_id: str,
    request: Request,
    kubernetes_service: KubernetesService = Depends(get_kubernetes_service),
    state_store: StateStore = Depends(get_state_store),
) -> ModelDeleteResponse:
    normalized_model_id = normalize_model_id(model_id)
    lock_manager: ModelLockManager = request.app.state.lock_manager

    with lock_manager.for_model(normalized_model_id):
        try:
            deleted = kubernetes_service.delete_model(normalized_model_id)
            state_store.delete_model(normalized_model_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ApiException as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Kubernetes API error ({exc.status}): {exc.reason}",
            ) from exc

    return ModelDeleteResponse(model_id=normalized_model_id, deleted=deleted)
