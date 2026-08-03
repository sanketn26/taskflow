from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class WorkerMessage(_message.Message):
    __slots__ = ("register", "pull", "heartbeat", "complete")
    REGISTER_FIELD_NUMBER: _ClassVar[int]
    PULL_FIELD_NUMBER: _ClassVar[int]
    HEARTBEAT_FIELD_NUMBER: _ClassVar[int]
    COMPLETE_FIELD_NUMBER: _ClassVar[int]
    register: WorkerRegistration
    pull: PullRequest
    heartbeat: HeartbeatRequest
    complete: Completion
    def __init__(self, register: _Optional[_Union[WorkerRegistration, _Mapping]] = ..., pull: _Optional[_Union[PullRequest, _Mapping]] = ..., heartbeat: _Optional[_Union[HeartbeatRequest, _Mapping]] = ..., complete: _Optional[_Union[Completion, _Mapping]] = ...) -> None: ...

class AgentMessage(_message.Message):
    __slots__ = ("task", "heartbeat_ack", "complete_ack", "registered")
    TASK_FIELD_NUMBER: _ClassVar[int]
    HEARTBEAT_ACK_FIELD_NUMBER: _ClassVar[int]
    COMPLETE_ACK_FIELD_NUMBER: _ClassVar[int]
    REGISTERED_FIELD_NUMBER: _ClassVar[int]
    task: LeasedTask
    heartbeat_ack: HeartbeatResponse
    complete_ack: CompletionResponse
    registered: WorkerRegistrationResponse
    def __init__(self, task: _Optional[_Union[LeasedTask, _Mapping]] = ..., heartbeat_ack: _Optional[_Union[HeartbeatResponse, _Mapping]] = ..., complete_ack: _Optional[_Union[CompletionResponse, _Mapping]] = ..., registered: _Optional[_Union[WorkerRegistrationResponse, _Mapping]] = ...) -> None: ...

class WorkerRegistration(_message.Message):
    __slots__ = ("worker_id", "runtime", "runtime_version", "sdk_version", "codecs", "tasks")
    WORKER_ID_FIELD_NUMBER: _ClassVar[int]
    RUNTIME_FIELD_NUMBER: _ClassVar[int]
    RUNTIME_VERSION_FIELD_NUMBER: _ClassVar[int]
    SDK_VERSION_FIELD_NUMBER: _ClassVar[int]
    CODECS_FIELD_NUMBER: _ClassVar[int]
    TASKS_FIELD_NUMBER: _ClassVar[int]
    worker_id: str
    runtime: str
    runtime_version: str
    sdk_version: str
    codecs: _containers.RepeatedScalarFieldContainer[str]
    tasks: TaskRegistration
    def __init__(self, worker_id: _Optional[str] = ..., runtime: _Optional[str] = ..., runtime_version: _Optional[str] = ..., sdk_version: _Optional[str] = ..., codecs: _Optional[_Iterable[str]] = ..., tasks: _Optional[_Union[TaskRegistration, _Mapping]] = ...) -> None: ...

class WorkerRegistrationResponse(_message.Message):
    __slots__ = ("worker_id", "generation", "accepted")
    WORKER_ID_FIELD_NUMBER: _ClassVar[int]
    GENERATION_FIELD_NUMBER: _ClassVar[int]
    ACCEPTED_FIELD_NUMBER: _ClassVar[int]
    worker_id: str
    generation: int
    accepted: int
    def __init__(self, worker_id: _Optional[str] = ..., generation: _Optional[int] = ..., accepted: _Optional[int] = ...) -> None: ...

class ObjectRef(_message.Message):
    __slots__ = ("store", "key", "size", "sha256", "codec")
    STORE_FIELD_NUMBER: _ClassVar[int]
    KEY_FIELD_NUMBER: _ClassVar[int]
    SIZE_FIELD_NUMBER: _ClassVar[int]
    SHA256_FIELD_NUMBER: _ClassVar[int]
    CODEC_FIELD_NUMBER: _ClassVar[int]
    store: str
    key: str
    size: int
    sha256: bytes
    codec: str
    def __init__(self, store: _Optional[str] = ..., key: _Optional[str] = ..., size: _Optional[int] = ..., sha256: _Optional[bytes] = ..., codec: _Optional[str] = ...) -> None: ...

class ValueRef(_message.Message):
    __slots__ = ("inline", "object", "codec")
    INLINE_FIELD_NUMBER: _ClassVar[int]
    OBJECT_FIELD_NUMBER: _ClassVar[int]
    CODEC_FIELD_NUMBER: _ClassVar[int]
    inline: bytes
    object: ObjectRef
    codec: str
    def __init__(self, inline: _Optional[bytes] = ..., object: _Optional[_Union[ObjectRef, _Mapping]] = ..., codec: _Optional[str] = ...) -> None: ...

class PullRequest(_message.Message):
    __slots__ = ("worker_id", "capability_generation")
    WORKER_ID_FIELD_NUMBER: _ClassVar[int]
    CAPABILITY_GENERATION_FIELD_NUMBER: _ClassVar[int]
    worker_id: str
    capability_generation: int
    def __init__(self, worker_id: _Optional[str] = ..., capability_generation: _Optional[int] = ...) -> None: ...

class TaskCapability(_message.Message):
    __slots__ = ("task_name", "task_version", "invocation", "codecs")
    TASK_NAME_FIELD_NUMBER: _ClassVar[int]
    TASK_VERSION_FIELD_NUMBER: _ClassVar[int]
    INVOCATION_FIELD_NUMBER: _ClassVar[int]
    CODECS_FIELD_NUMBER: _ClassVar[int]
    task_name: str
    task_version: str
    invocation: str
    codecs: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, task_name: _Optional[str] = ..., task_version: _Optional[str] = ..., invocation: _Optional[str] = ..., codecs: _Optional[_Iterable[str]] = ...) -> None: ...

class TaskRegistration(_message.Message):
    __slots__ = ("worker_id", "generation", "tasks")
    WORKER_ID_FIELD_NUMBER: _ClassVar[int]
    GENERATION_FIELD_NUMBER: _ClassVar[int]
    TASKS_FIELD_NUMBER: _ClassVar[int]
    worker_id: str
    generation: int
    tasks: _containers.RepeatedCompositeFieldContainer[TaskCapability]
    def __init__(self, worker_id: _Optional[str] = ..., generation: _Optional[int] = ..., tasks: _Optional[_Iterable[_Union[TaskCapability, _Mapping]]] = ...) -> None: ...

class RegisterTasksResponse(_message.Message):
    __slots__ = ("worker_id", "generation", "accepted")
    WORKER_ID_FIELD_NUMBER: _ClassVar[int]
    GENERATION_FIELD_NUMBER: _ClassVar[int]
    ACCEPTED_FIELD_NUMBER: _ClassVar[int]
    worker_id: str
    generation: int
    accepted: int
    def __init__(self, worker_id: _Optional[str] = ..., generation: _Optional[int] = ..., accepted: _Optional[int] = ...) -> None: ...

class TaskQuery(_message.Message):
    __slots__ = ("owner_id", "task_ids")
    OWNER_ID_FIELD_NUMBER: _ClassVar[int]
    TASK_IDS_FIELD_NUMBER: _ClassVar[int]
    owner_id: bytes
    task_ids: _containers.RepeatedScalarFieldContainer[bytes]
    def __init__(self, owner_id: _Optional[bytes] = ..., task_ids: _Optional[_Iterable[bytes]] = ...) -> None: ...

class TaskSnapshotEntry(_message.Message):
    __slots__ = ("task_id", "state", "cursor", "result", "failure")
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    CURSOR_FIELD_NUMBER: _ClassVar[int]
    RESULT_FIELD_NUMBER: _ClassVar[int]
    FAILURE_FIELD_NUMBER: _ClassVar[int]
    task_id: bytes
    state: str
    cursor: int
    result: ObjectRef
    failure: Failure
    def __init__(self, task_id: _Optional[bytes] = ..., state: _Optional[str] = ..., cursor: _Optional[int] = ..., result: _Optional[_Union[ObjectRef, _Mapping]] = ..., failure: _Optional[_Union[Failure, _Mapping]] = ...) -> None: ...

class TaskSnapshot(_message.Message):
    __slots__ = ("tasks",)
    TASKS_FIELD_NUMBER: _ClassVar[int]
    tasks: _containers.RepeatedCompositeFieldContainer[TaskSnapshotEntry]
    def __init__(self, tasks: _Optional[_Iterable[_Union[TaskSnapshotEntry, _Mapping]]] = ...) -> None: ...

class TaskEnvelope(_message.Message):
    __slots__ = ("owner_id", "task_name", "task_version", "invocation", "input", "labels", "idempotent", "submitted_at_unix_ms")
    class LabelsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    OWNER_ID_FIELD_NUMBER: _ClassVar[int]
    TASK_NAME_FIELD_NUMBER: _ClassVar[int]
    TASK_VERSION_FIELD_NUMBER: _ClassVar[int]
    INVOCATION_FIELD_NUMBER: _ClassVar[int]
    INPUT_FIELD_NUMBER: _ClassVar[int]
    LABELS_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENT_FIELD_NUMBER: _ClassVar[int]
    SUBMITTED_AT_UNIX_MS_FIELD_NUMBER: _ClassVar[int]
    owner_id: bytes
    task_name: str
    task_version: str
    invocation: str
    input: ValueRef
    labels: _containers.ScalarMap[str, str]
    idempotent: bool
    submitted_at_unix_ms: int
    def __init__(self, owner_id: _Optional[bytes] = ..., task_name: _Optional[str] = ..., task_version: _Optional[str] = ..., invocation: _Optional[str] = ..., input: _Optional[_Union[ValueRef, _Mapping]] = ..., labels: _Optional[_Mapping[str, str]] = ..., idempotent: _Optional[bool] = ..., submitted_at_unix_ms: _Optional[int] = ...) -> None: ...

class SubmitResponse(_message.Message):
    __slots__ = ("task_id",)
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    task_id: bytes
    def __init__(self, task_id: _Optional[bytes] = ...) -> None: ...

class LeasedTask(_message.Message):
    __slots__ = ("task", "task_id", "lease_id", "ttl_ms", "attempt")
    TASK_FIELD_NUMBER: _ClassVar[int]
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    LEASE_ID_FIELD_NUMBER: _ClassVar[int]
    TTL_MS_FIELD_NUMBER: _ClassVar[int]
    ATTEMPT_FIELD_NUMBER: _ClassVar[int]
    task: TaskEnvelope
    task_id: bytes
    lease_id: bytes
    ttl_ms: int
    attempt: int
    def __init__(self, task: _Optional[_Union[TaskEnvelope, _Mapping]] = ..., task_id: _Optional[bytes] = ..., lease_id: _Optional[bytes] = ..., ttl_ms: _Optional[int] = ..., attempt: _Optional[int] = ...) -> None: ...

class Completion(_message.Message):
    __slots__ = ("lease_id", "result", "failure")
    LEASE_ID_FIELD_NUMBER: _ClassVar[int]
    RESULT_FIELD_NUMBER: _ClassVar[int]
    FAILURE_FIELD_NUMBER: _ClassVar[int]
    lease_id: bytes
    result: ObjectRef
    failure: Failure
    def __init__(self, lease_id: _Optional[bytes] = ..., result: _Optional[_Union[ObjectRef, _Mapping]] = ..., failure: _Optional[_Union[Failure, _Mapping]] = ...) -> None: ...

class CompletionResponse(_message.Message):
    __slots__ = ("lease_id",)
    LEASE_ID_FIELD_NUMBER: _ClassVar[int]
    lease_id: bytes
    def __init__(self, lease_id: _Optional[bytes] = ...) -> None: ...

class ForwardedTask(_message.Message):
    __slots__ = ("transfer_id", "origin_node", "task")
    TRANSFER_ID_FIELD_NUMBER: _ClassVar[int]
    ORIGIN_NODE_FIELD_NUMBER: _ClassVar[int]
    TASK_FIELD_NUMBER: _ClassVar[int]
    transfer_id: bytes
    origin_node: str
    task: TaskEnvelope
    def __init__(self, transfer_id: _Optional[bytes] = ..., origin_node: _Optional[str] = ..., task: _Optional[_Union[TaskEnvelope, _Mapping]] = ...) -> None: ...

class ForwardTaskResponse(_message.Message):
    __slots__ = ("task_id", "transfer_id")
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    TRANSFER_ID_FIELD_NUMBER: _ClassVar[int]
    task_id: bytes
    transfer_id: bytes
    def __init__(self, task_id: _Optional[bytes] = ..., transfer_id: _Optional[bytes] = ...) -> None: ...

class ForwardedCompletion(_message.Message):
    __slots__ = ("transfer_id", "remote_node", "remote_attempt", "result", "failure")
    TRANSFER_ID_FIELD_NUMBER: _ClassVar[int]
    REMOTE_NODE_FIELD_NUMBER: _ClassVar[int]
    REMOTE_ATTEMPT_FIELD_NUMBER: _ClassVar[int]
    RESULT_FIELD_NUMBER: _ClassVar[int]
    FAILURE_FIELD_NUMBER: _ClassVar[int]
    transfer_id: bytes
    remote_node: str
    remote_attempt: int
    result: ObjectRef
    failure: Failure
    def __init__(self, transfer_id: _Optional[bytes] = ..., remote_node: _Optional[str] = ..., remote_attempt: _Optional[int] = ..., result: _Optional[_Union[ObjectRef, _Mapping]] = ..., failure: _Optional[_Union[Failure, _Mapping]] = ...) -> None: ...

class ForwardCompletionResponse(_message.Message):
    __slots__ = ("transfer_id",)
    TRANSFER_ID_FIELD_NUMBER: _ClassVar[int]
    transfer_id: bytes
    def __init__(self, transfer_id: _Optional[bytes] = ...) -> None: ...

class Failure(_message.Message):
    __slots__ = ("code", "message", "details", "retryable")
    CODE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    DETAILS_FIELD_NUMBER: _ClassVar[int]
    RETRYABLE_FIELD_NUMBER: _ClassVar[int]
    code: str
    message: str
    details: ValueRef
    retryable: bool
    def __init__(self, code: _Optional[str] = ..., message: _Optional[str] = ..., details: _Optional[_Union[ValueRef, _Mapping]] = ..., retryable: _Optional[bool] = ...) -> None: ...

class ResultNotification(_message.Message):
    __slots__ = ("owner_id", "task_id", "cursor", "state", "result", "failure")
    OWNER_ID_FIELD_NUMBER: _ClassVar[int]
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    CURSOR_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    RESULT_FIELD_NUMBER: _ClassVar[int]
    FAILURE_FIELD_NUMBER: _ClassVar[int]
    owner_id: bytes
    task_id: bytes
    cursor: int
    state: str
    result: ObjectRef
    failure: Failure
    def __init__(self, owner_id: _Optional[bytes] = ..., task_id: _Optional[bytes] = ..., cursor: _Optional[int] = ..., state: _Optional[str] = ..., result: _Optional[_Union[ObjectRef, _Mapping]] = ..., failure: _Optional[_Union[Failure, _Mapping]] = ...) -> None: ...

class WatchResultsRequest(_message.Message):
    __slots__ = ("owner_id", "after_cursor")
    OWNER_ID_FIELD_NUMBER: _ClassVar[int]
    AFTER_CURSOR_FIELD_NUMBER: _ClassVar[int]
    owner_id: bytes
    after_cursor: int
    def __init__(self, owner_id: _Optional[bytes] = ..., after_cursor: _Optional[int] = ...) -> None: ...

class AckResultRequest(_message.Message):
    __slots__ = ("owner_id", "task_id", "cursor")
    OWNER_ID_FIELD_NUMBER: _ClassVar[int]
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    CURSOR_FIELD_NUMBER: _ClassVar[int]
    owner_id: bytes
    task_id: bytes
    cursor: int
    def __init__(self, owner_id: _Optional[bytes] = ..., task_id: _Optional[bytes] = ..., cursor: _Optional[int] = ...) -> None: ...

class AckResultResponse(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class StatusSnapshot(_message.Message):
    __slots__ = ("version", "pid", "ready", "task_counts", "active_leases", "worker_pids", "worker_restarts", "storage_healthy", "cluster_members", "kafka_outbox_pending", "last_error_code")
    class TaskCountsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: int
        def __init__(self, key: _Optional[str] = ..., value: _Optional[int] = ...) -> None: ...
    VERSION_FIELD_NUMBER: _ClassVar[int]
    PID_FIELD_NUMBER: _ClassVar[int]
    READY_FIELD_NUMBER: _ClassVar[int]
    TASK_COUNTS_FIELD_NUMBER: _ClassVar[int]
    ACTIVE_LEASES_FIELD_NUMBER: _ClassVar[int]
    WORKER_PIDS_FIELD_NUMBER: _ClassVar[int]
    WORKER_RESTARTS_FIELD_NUMBER: _ClassVar[int]
    STORAGE_HEALTHY_FIELD_NUMBER: _ClassVar[int]
    CLUSTER_MEMBERS_FIELD_NUMBER: _ClassVar[int]
    KAFKA_OUTBOX_PENDING_FIELD_NUMBER: _ClassVar[int]
    LAST_ERROR_CODE_FIELD_NUMBER: _ClassVar[int]
    version: str
    pid: int
    ready: bool
    task_counts: _containers.ScalarMap[str, int]
    active_leases: int
    worker_pids: _containers.RepeatedScalarFieldContainer[int]
    worker_restarts: int
    storage_healthy: bool
    cluster_members: int
    kafka_outbox_pending: int
    last_error_code: str
    def __init__(self, version: _Optional[str] = ..., pid: _Optional[int] = ..., ready: _Optional[bool] = ..., task_counts: _Optional[_Mapping[str, int]] = ..., active_leases: _Optional[int] = ..., worker_pids: _Optional[_Iterable[int]] = ..., worker_restarts: _Optional[int] = ..., storage_healthy: _Optional[bool] = ..., cluster_members: _Optional[int] = ..., kafka_outbox_pending: _Optional[int] = ..., last_error_code: _Optional[str] = ...) -> None: ...

class Error(_message.Message):
    __slots__ = ("code", "message", "retryable", "details")
    class DetailsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    CODE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    RETRYABLE_FIELD_NUMBER: _ClassVar[int]
    DETAILS_FIELD_NUMBER: _ClassVar[int]
    code: str
    message: str
    retryable: bool
    details: _containers.ScalarMap[str, str]
    def __init__(self, code: _Optional[str] = ..., message: _Optional[str] = ..., retryable: _Optional[bool] = ..., details: _Optional[_Mapping[str, str]] = ...) -> None: ...

class HeartbeatRequest(_message.Message):
    __slots__ = ("lease_id",)
    LEASE_ID_FIELD_NUMBER: _ClassVar[int]
    lease_id: bytes
    def __init__(self, lease_id: _Optional[bytes] = ...) -> None: ...

class HeartbeatResponse(_message.Message):
    __slots__ = ("lease_id",)
    LEASE_ID_FIELD_NUMBER: _ClassVar[int]
    lease_id: bytes
    def __init__(self, lease_id: _Optional[bytes] = ...) -> None: ...

class CancelRequest(_message.Message):
    __slots__ = ("owner_id", "task_id")
    OWNER_ID_FIELD_NUMBER: _ClassVar[int]
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    owner_id: bytes
    task_id: bytes
    def __init__(self, owner_id: _Optional[bytes] = ..., task_id: _Optional[bytes] = ...) -> None: ...

class CancelResponse(_message.Message):
    __slots__ = ("task_id", "cancelled")
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    CANCELLED_FIELD_NUMBER: _ClassVar[int]
    task_id: bytes
    cancelled: bool
    def __init__(self, task_id: _Optional[bytes] = ..., cancelled: _Optional[bool] = ...) -> None: ...

class ObjectGetRequest(_message.Message):
    __slots__ = ("object",)
    OBJECT_FIELD_NUMBER: _ClassVar[int]
    object: ObjectRef
    def __init__(self, object: _Optional[_Union[ObjectRef, _Mapping]] = ...) -> None: ...

class PutObjectResponse(_message.Message):
    __slots__ = ("object",)
    OBJECT_FIELD_NUMBER: _ClassVar[int]
    object: ObjectRef
    def __init__(self, object: _Optional[_Union[ObjectRef, _Mapping]] = ...) -> None: ...

class ObjectChunk(_message.Message):
    __slots__ = ("data", "codec", "size", "sha256")
    DATA_FIELD_NUMBER: _ClassVar[int]
    CODEC_FIELD_NUMBER: _ClassVar[int]
    SIZE_FIELD_NUMBER: _ClassVar[int]
    SHA256_FIELD_NUMBER: _ClassVar[int]
    data: bytes
    codec: str
    size: int
    sha256: bytes
    def __init__(self, data: _Optional[bytes] = ..., codec: _Optional[str] = ..., size: _Optional[int] = ..., sha256: _Optional[bytes] = ...) -> None: ...

class StealRequest(_message.Message):
    __slots__ = ("requester_node", "labels", "limit")
    class LabelsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    REQUESTER_NODE_FIELD_NUMBER: _ClassVar[int]
    LABELS_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    requester_node: str
    labels: _containers.ScalarMap[str, str]
    limit: int
    def __init__(self, requester_node: _Optional[str] = ..., labels: _Optional[_Mapping[str, str]] = ..., limit: _Optional[int] = ...) -> None: ...

class StealResponse(_message.Message):
    __slots__ = ("transfer_id", "accepted")
    TRANSFER_ID_FIELD_NUMBER: _ClassVar[int]
    ACCEPTED_FIELD_NUMBER: _ClassVar[int]
    transfer_id: bytes
    accepted: int
    def __init__(self, transfer_id: _Optional[bytes] = ..., accepted: _Optional[int] = ...) -> None: ...

class StatusRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...
