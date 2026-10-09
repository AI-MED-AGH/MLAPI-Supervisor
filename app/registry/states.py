PENDING_APPROVAL = "pending_approval"
DEPLOYING = "deploying"
STARTING = "starting"
READY = "ready"
SLEEPING = "sleeping"
WAITING_FOR_GPU = "waiting_for_gpu"
FAILED = "failed"
REMOVED = "removed"

ALL_STATES = (
    PENDING_APPROVAL, DEPLOYING, STARTING, READY, SLEEPING, WAITING_FOR_GPU, FAILED, REMOVED,
)

# request statuses
REQ_PENDING = "pending"
REQ_APPROVED = "approved"
REQ_REJECTED = "rejected"
REQ_SUPERSEDED = "superseded"

# deployment history statuses
DEP_SUCCEEDED = "succeeded"
DEP_FAILED = "failed"
DEP_ROLLED_BACK = "rolled_back"
