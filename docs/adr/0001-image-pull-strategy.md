# ADR 0001 — Image Pull Strategy

- Status: Accepted
- Date: 2026-04-18
- Scope: Orchestrator track (issue #6)

## Context

The supervisor must fetch private container images from GHCR and run them on a local Kubernetes cluster. Issue #6 framed the choice of "how to pull" as open research between:

1. `docker-py` — supervisor talks to a local Docker daemon and `docker pull`s the image itself.
2. `subprocess` → `ctr` / `crictl` — supervisor shells out to a CRI-level tool on the node.
3. Kubelet-native pull via an `imagePullSecrets`-referenced Secret on the Deployment.

## Decision

**Use option 3: kubelet-native pull.**

The supervisor never pulls images itself. Instead, it:

1. Maintains a `kubernetes.io/dockerconfigjson` Secret named `ghcr-pull-secret` in the target namespace (`KubernetesService.ensure_registry_pull_secret`).
2. Emits Deployments whose PodSpec references that Secret via `imagePullSecrets`.
3. Lets every node's kubelet pull the image from GHCR using the credentials in the Secret.

The GHCR HTTP API (`GHCRService`) is still used, but only for *metadata* — listing tags, finding the latest version — never for blob download.

## Rationale

- **Idiomatic Kubernetes.** `imagePullSecrets` is the contract the platform expects. Tooling like `kubectl describe pod`, events, and image-pull backoff handling all assume the kubelet is the puller.
- **No runtime coupling.** The supervisor does not need access to the Docker or containerd socket on the host, and does not need to ship a Docker/CRI client library. It only needs the Kubernetes API.
- **Portability.** Works on k3s, k3d, kind, EKS, GKE, bare-metal kubeadm — anywhere kubelet runs. Options 1 and 2 assume a specific container runtime and a specific socket path.
- **Smaller attack surface.** The supervisor container does not mount host sockets and does not need `privileged` or `hostPath` mounts to pull.
- **Layer reuse across nodes.** Kubelet image caches are per-node; letting the kubelet pull lets the scheduler place pods on nodes that already have the layers, which an external puller cannot coordinate.

## Rejected alternatives

### docker-py

- Requires a local Docker daemon accessible from the supervisor pod.
- Does not help a multi-node cluster: pulling on the supervisor's node does not load the image onto whichever node kubelet schedules the pod on.
- Adds a hard dependency on the Docker runtime at a time when most distros ship containerd.

### subprocess → `ctr` / `crictl`

- Requires mounting the CRI socket (`/run/containerd/containerd.sock` or similar) into the supervisor pod.
- Same multi-node limitation as docker-py.
- Shell-invocation error handling is brittle; we would be re-inventing what the kubelet already does.

## Consequences

- Credential rotation is a Secret update — no supervisor restart required.
- The supervisor can run unprivileged and does not need host mounts.
- If a pull fails, the failure signal is `ImagePullBackOff` on the Pod; the Watchman track (#10–#12) is responsible for surfacing it. The orchestrator's own rollout-wait logic (`DeployWorkflow.wait_for_rollout`, added in step 4 of the implementation plan) times out and triggers rollback on persistent pull failures.
- `GHCRService` is deliberately scoped to registry metadata. It must never grow a "download image" method.
