from fastapi import APIRouter, HTTPException

from app.models.instance import Instance, InstanceCreate
from app.services import instance_service

router = APIRouter(prefix="/instances", tags=["instances"])


@router.get("/", response_model=list[Instance])
def list_instances() -> list[Instance]:
    return instance_service.list_instances()


@router.get("/{instance_id}", response_model=Instance)
def get_instance(instance_id: str) -> Instance:
    instance = instance_service.get_instance(instance_id)
    if instance is None:
        raise HTTPException(status_code=404, detail="Instance not found")
    return instance


@router.post("/", response_model=Instance, status_code=201)
def create_instance(data: InstanceCreate) -> Instance:
    return instance_service.create_instance(data)


@router.delete("/{instance_id}", status_code=204)
def delete_instance(instance_id: str) -> None:
    deleted = instance_service.delete_instance(instance_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Instance not found")
