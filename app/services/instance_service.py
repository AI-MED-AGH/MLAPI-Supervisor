import threading
import uuid

from app.models.instance import Instance, InstanceCreate, InstanceStatus

_instances: dict[str, Instance] = {}
_lock = threading.Lock()


def list_instances() -> list[Instance]:
    with _lock:
        return list(_instances.values())


def get_instance(instance_id: str) -> Instance | None:
    with _lock:
        return _instances.get(instance_id)


def create_instance(data: InstanceCreate) -> Instance:
    instance_id = str(uuid.uuid4())
    instance = Instance(
        id=instance_id,
        name=data.name,
        status=InstanceStatus.running,
        model_name=data.model_name,
        endpoint=data.endpoint,
    )
    with _lock:
        _instances[instance_id] = instance
    return instance


def delete_instance(instance_id: str) -> bool:
    with _lock:
        if instance_id in _instances:
            del _instances[instance_id]
            return True
    return False


def clear_all_instances() -> None:
    with _lock:
        _instances.clear()
