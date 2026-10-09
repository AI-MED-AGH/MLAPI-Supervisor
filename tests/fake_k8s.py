"""A small in-memory fake of the kubernetes_asyncio APIs that KubernetesBackend uses."""
from types import SimpleNamespace as NS


class ApiException(Exception):
    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.status = status


def _deployment(body, previous=None):
    generation = (previous.metadata.generation + 1) if previous else 1
    replicas = body["spec"]["replicas"]
    return NS(
        metadata=NS(name=body["metadata"]["name"], labels=body["metadata"]["labels"], generation=generation),
        spec=NS(replicas=replicas, body=body),
        status=NS(observed_generation=generation, updated_replicas=replicas, replicas=replicas,
                  available_replicas=replicas, ready_replicas=replicas),
    )


class FakeApps:
    def __init__(self):
        self.deployments: dict[str, NS] = {}
        self.calls = []

    async def create_namespaced_deployment(self, ns, body):
        name = body["metadata"]["name"]
        self.calls.append(("create_deployment", name))
        if name in self.deployments:
            raise ApiException(409)
        self.deployments[name] = _deployment(body)

    async def replace_namespaced_deployment(self, name, ns, body):
        self.calls.append(("replace_deployment", name))
        self.deployments[name] = _deployment(body, self.deployments[name])

    async def read_namespaced_deployment(self, name, ns):
        if name not in self.deployments:
            raise ApiException(404)
        return self.deployments[name]

    async def patch_namespaced_deployment_scale(self, name, ns, body):
        self.calls.append(("scale", name, body["spec"]["replicas"]))
        if name not in self.deployments:
            raise ApiException(404)
        d = self.deployments[name]
        d.spec.replicas = body["spec"]["replicas"]
        d.status.replicas = d.status.updated_replicas = d.status.available_replicas = d.status.ready_replicas = d.spec.replicas

    async def delete_namespaced_deployment(self, name, ns):
        self.calls.append(("delete_deployment", name))
        if self.deployments.pop(name, None) is None:
            raise ApiException(404)

    async def list_namespaced_deployment(self, ns, label_selector=None):
        return NS(items=list(self.deployments.values()))


class FakeCore:
    def __init__(self):
        self.services, self.pvcs, self.pods = {}, {}, []
        self.calls = []

    async def create_namespaced_service(self, ns, body):
        name = body["metadata"]["name"]
        if name in self.services:
            raise ApiException(409)
        self.services[name] = body

    async def replace_namespaced_service(self, name, ns, body):
        self.services[name] = body

    async def delete_namespaced_service(self, name, ns):
        if self.services.pop(name, None) is None:
            raise ApiException(404)

    async def create_namespaced_persistent_volume_claim(self, ns, body):
        name = body["metadata"]["name"]
        self.calls.append(("create_pvc", name))
        if name in self.pvcs:
            raise ApiException(409)
        self.pvcs[name] = body

    async def delete_namespaced_persistent_volume_claim(self, name, ns):
        self.calls.append(("delete_pvc", name))
        if self.pvcs.pop(name, None) is None:
            raise ApiException(404)

    async def list_namespaced_pod(self, ns, label_selector=None):
        return NS(items=self.pods)


class FakeNet:
    def __init__(self):
        self.policies = {}

    async def create_namespaced_network_policy(self, ns, body):
        name = body["metadata"]["name"]
        if name in self.policies:
            raise ApiException(409)
        self.policies[name] = body

    async def replace_namespaced_network_policy(self, name, ns, body):
        self.policies[name] = body

    async def delete_namespaced_network_policy(self, name, ns):
        if self.policies.pop(name, None) is None:
            raise ApiException(404)


def pod(role="main", phase="Running", restarts=0, unschedulable=None, waiting=None):
    conditions = []
    if unschedulable is not None:
        conditions = [NS(type="PodScheduled", status="False", message=unschedulable)]
    state = NS(waiting=NS(reason=waiting)) if waiting else NS(waiting=None)
    return NS(
        metadata=NS(labels={"mlapi/role": role}),
        status=NS(phase=phase, conditions=conditions, container_statuses=[NS(restart_count=restarts, state=state)]),
    )
