from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ControlMessage(_message.Message):
    __slots__ = ("submit", "forwarded_submit", "pull", "task", "heartbeat", "result", "cancel", "complete", "forwarded_complete", "steal", "ack", "status_request", "status_snapshot", "resume_results", "error", "object_put", "object_get", "object_chunk", "hello", "task_query", "task_snapshot", "register_tasks")
    SUBMIT_FIELD_NUMBER: _ClassVar[int]
    FORWARDED_SUBMIT_FIELD_NUMBER: _ClassVar[int]
    PULL_FIELD_NUMBER: _ClassVar[int]
    TASK_FIELD_NUMBER: _ClassVar[int]
    HEARTBEAT_FIELD_NUMBER: _ClassVar[int]
    RESULT_FIELD_NUMBER: _ClassVar[int]
    CANCEL_FIELD_NUMBER: _ClassVar[int]
    COMPLETE_FIELD_NUMBER: _ClassVar[int]
    FORWARDED_COMPLETE_FIELD_NUMBER: _ClassVar[int]
    STEAL_FIELD_NUMBER: _ClassVar[int]
    ACK_FIELD_NUMBER: _ClassVar[int]
    STATUS_REQUEST_FIELD_NUMBER: _ClassVar[int]
    STATUS_SNAPSHOT_FIELD_NUMBER: _ClassVar[int]
    RESUME_RESULTS_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    OBJECT_PUT_FIELD_NUMBER: _ClassVar[int]
    OBJECT_GET_FIELD_NUMBER: _ClassVar[int]
    OBJECT_CHUNK_FIELD_NUMBER: _ClassVar[int]
    HELLO_FIELD_NUMBER: _ClassVar[int]
    TASK_QUERY_FIELD_NUMBER: _ClassVar[int]
    TASK_SNAPSHOT_FIELD_NUMBER: _ClassVar[int]
    REGISTER_TASKS_FIELD_NUMBER: _ClassVar[int]
    submit: TaskEnvelope
    forwarded_submit: ForwardedTask
    pull: PullRequest
    task: LeasedTask
    heartbeat: HeartbeatRequest
    result: ResultNotification
    cancel: CancelRequest
    complete: Completion
    forwarded_complete: ForwardedCompletion
    steal: StealRequest
    ack: Ack
    status_request: StatusRequest
    status_snapshot: StatusSnapshot
    resume_results: ResumeResultsRequest
    error: Error
    object_put: ObjectPutRequest
    object_get: ObjectGetRequest
    object_chunk: ObjectChunk
    hello: Hello
    task_query: TaskQuery
    task_snapshot: TaskSnapshot
    register_tasks: TaskRegistration
    def __init__(self, submit: _Optional[_Union[TaskEnvelope, _Mapping]] = ..., forwarded_submit: _Optional[_Union[ForwardedTask, _Mapping]] = ..., pull: _Optional[_Union[PullRequest, _Mapping]] = ..., task: _Optional[_Union[LeasedTask, _Mapping]] = ..., heartbeat: _Optional[_Union[HeartbeatRequest, _Mapping]] = ..., result: _Optional[_Union[ResultNotification, _Mapping]] = ..., cancel: _Optional[_Union[CancelRequest, _Mapping]] = ..., complete: _Optional[_Union[Completion, _Mapping]] = ..., forwarded_complete: _Optional[_Union[ForwardedCompletion, _Mapping]] = ..., steal: _Optional[_Union[StealRequest, _Mapping]] = ..., ack: _Optional[_Union[Ack, _Mapping]] = ..., status_request: _Optional[_Union[StatusRequest, _Mapping]] = ..., status_snapshot: _Optional[_Union[StatusSnapshot, _Mapping]] = ..., resume_results: _Optional[_Union[ResumeResultsRequest, _Mapping]] = ..., error: _Optional[_Union[Error, _Mapping]] = ..., object_put: _Optional[_Union[ObjectPutRequest, _Mapping]] = ..., object_get: _Optional[_Union[ObjectGetRequest, _Mapping]] = ..., object_chunk: _Optional[_Union[ObjectChunk, _Mapping]] = ..., hello: _Optional[_Union[Hello, _Mapping]] = ..., task_query: _Optional[_Union[TaskQuery, _Mapping]] = ..., task_snapshot: _Optional[_Union[TaskSnapshot, _Mapping]] = ..., register_tasks: _Optional[_Union[TaskRegistration, _Mapping]] = ...) -> None: ...

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

class Hello(_message.Message):
    __slots__ = ("role", "owner_id", "worker_id", "runtime", "runtime_version", "sdk_version", "codecs")
    ROLE_FIELD_NUMBER: _ClassVar[int]
    OWNER_ID_FIELD_NUMBER: _ClassVar[int]
    WORKER_ID_FIELD_NUMBER: _ClassVar[int]
    RUNTIME_FIELD_NUMBER: _ClassVar[int]
    RUNTIME_VERSION_FIELD_NUMBER: _ClassVar[int]
    SDK_VERSION_FIELD_NUMBER: _ClassVar[int]
    CODECS_FIELD_NUMBER: _ClassVar[int]
    role: str
    owner_id: bytes
    worker_id: str
    runtime: str
    runtime_version: str
    sdk_version: str
    codecs: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, role: _Optional[str] = ..., owner_id: _Optional[bytes] = ..., worker_id: _Optional[str] = ..., runtime: _Optional[str] = ..., runtime_version: _Optional[str] = ..., sdk_version: _Optional[str] = ..., codecs: _Optional[_Iterable[str]] = ...) -> None: ...

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

class LeasedTask(_message.Message):
    __slots__ = ("task", "lease_id", "ttl_ms", "attempt")
    TASK_FIELD_NUMBER: _ClassVar[int]
    LEASE_ID_FIELD_NUMBER: _ClassVar[int]
    TTL_MS_FIELD_NUMBER: _ClassVar[int]
    ATTEMPT_FIELD_NUMBER: _ClassVar[int]
    task: TaskEnvelope
    lease_id: bytes
    ttl_ms: int
    attempt: int
    def __init__(self, task: _Optional[_Union[TaskEnvelope, _Mapping]] = ..., lease_id: _Optional[bytes] = ..., ttl_ms: _Optional[int] = ..., attempt: _Optional[int] = ...) -> None: ...

class Completion(_message.Message):
    __slots__ = ("lease_id", "result", "failure")
    LEASE_ID_FIELD_NUMBER: _ClassVar[int]
    RESULT_FIELD_NUMBER: _ClassVar[int]
    FAILURE_FIELD_NUMBER: _ClassVar[int]
    lease_id: bytes
    result: ObjectRef
    failure: Failure
    def __init__(self, lease_id: _Optional[bytes] = ..., result: _Optional[_Union[ObjectRef, _Mapping]] = ..., failure: _Optional[_Union[Failure, _Mapping]] = ...) -> None: ...

class ForwardedTask(_message.Message):
    __slots__ = ("transfer_id", "origin_node", "task")
    TRANSFER_ID_FIELD_NUMBER: _ClassVar[int]
    ORIGIN_NODE_FIELD_NUMBER: _ClassVar[int]
    TASK_FIELD_NUMBER: _ClassVar[int]
    transfer_id: bytes
    origin_node: str
    task: TaskEnvelope
    def __init__(self, transfer_id: _Optional[bytes] = ..., origin_node: _Optional[str] = ..., task: _Optional[_Union[TaskEnvelope, _Mapping]] = ...) -> None: ...

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
    __slots__ = ("owner_id", "cursor", "state", "result", "failure")
    OWNER_ID_FIELD_NUMBER: _ClassVar[int]
    CURSOR_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    RESULT_FIELD_NUMBER: _ClassVar[int]
    FAILURE_FIELD_NUMBER: _ClassVar[int]
    owner_id: bytes
    cursor: int
    state: str
    result: ObjectRef
    failure: Failure
    def __init__(self, owner_id: _Optional[bytes] = ..., cursor: _Optional[int] = ..., state: _Optional[str] = ..., result: _Optional[_Union[ObjectRef, _Mapping]] = ..., failure: _Optional[_Union[Failure, _Mapping]] = ...) -> None: ...

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

class Ack(_message.Message):
    __slots__ = ("kind", "task_id", "transfer_id", "lease_id", "owner_id", "cursor", "cancelled", "object", "next_cursor", "more", "accepted", "worker_id", "generation")
    KIND_FIELD_NUMBER: _ClassVar[int]
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    TRANSFER_ID_FIELD_NUMBER: _ClassVar[int]
    LEASE_ID_FIELD_NUMBER: _ClassVar[int]
    OWNER_ID_FIELD_NUMBER: _ClassVar[int]
    CURSOR_FIELD_NUMBER: _ClassVar[int]
    CANCELLED_FIELD_NUMBER: _ClassVar[int]
    OBJECT_FIELD_NUMBER: _ClassVar[int]
    NEXT_CURSOR_FIELD_NUMBER: _ClassVar[int]
    MORE_FIELD_NUMBER: _ClassVar[int]
    ACCEPTED_FIELD_NUMBER: _ClassVar[int]
    WORKER_ID_FIELD_NUMBER: _ClassVar[int]
    GENERATION_FIELD_NUMBER: _ClassVar[int]
    kind: str
    task_id: bytes
    transfer_id: bytes
    lease_id: bytes
    owner_id: bytes
    cursor: int
    cancelled: bool
    object: ObjectRef
    next_cursor: int
    more: bool
    accepted: int
    worker_id: str
    generation: int
    def __init__(self, kind: _Optional[str] = ..., task_id: _Optional[bytes] = ..., transfer_id: _Optional[bytes] = ..., lease_id: _Optional[bytes] = ..., owner_id: _Optional[bytes] = ..., cursor: _Optional[int] = ..., cancelled: _Optional[bool] = ..., object: _Optional[_Union[ObjectRef, _Mapping]] = ..., next_cursor: _Optional[int] = ..., more: _Optional[bool] = ..., accepted: _Optional[int] = ..., worker_id: _Optional[str] = ..., generation: _Optional[int] = ...) -> None: ...

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

class CancelRequest(_message.Message):
    __slots__ = ("owner_id",)
    OWNER_ID_FIELD_NUMBER: _ClassVar[int]
    owner_id: bytes
    def __init__(self, owner_id: _Optional[bytes] = ...) -> None: ...

class ResumeResultsRequest(_message.Message):
    __slots__ = ("owner_id", "after_cursor", "limit")
    OWNER_ID_FIELD_NUMBER: _ClassVar[int]
    AFTER_CURSOR_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    owner_id: bytes
    after_cursor: int
    limit: int
    def __init__(self, owner_id: _Optional[bytes] = ..., after_cursor: _Optional[int] = ..., limit: _Optional[int] = ...) -> None: ...

class ObjectPutRequest(_message.Message):
    __slots__ = ("transfer_id", "codec", "size", "sha256")
    TRANSFER_ID_FIELD_NUMBER: _ClassVar[int]
    CODEC_FIELD_NUMBER: _ClassVar[int]
    SIZE_FIELD_NUMBER: _ClassVar[int]
    SHA256_FIELD_NUMBER: _ClassVar[int]
    transfer_id: bytes
    codec: str
    size: int
    sha256: bytes
    def __init__(self, transfer_id: _Optional[bytes] = ..., codec: _Optional[str] = ..., size: _Optional[int] = ..., sha256: _Optional[bytes] = ...) -> None: ...

class ObjectGetRequest(_message.Message):
    __slots__ = ("transfer_id", "object")
    TRANSFER_ID_FIELD_NUMBER: _ClassVar[int]
    OBJECT_FIELD_NUMBER: _ClassVar[int]
    transfer_id: bytes
    object: ObjectRef
    def __init__(self, transfer_id: _Optional[bytes] = ..., object: _Optional[_Union[ObjectRef, _Mapping]] = ...) -> None: ...

class ObjectChunk(_message.Message):
    __slots__ = ("transfer_id", "sequence", "data", "eof")
    TRANSFER_ID_FIELD_NUMBER: _ClassVar[int]
    SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    EOF_FIELD_NUMBER: _ClassVar[int]
    transfer_id: bytes
    sequence: int
    data: bytes
    eof: bool
    def __init__(self, transfer_id: _Optional[bytes] = ..., sequence: _Optional[int] = ..., data: _Optional[bytes] = ..., eof: _Optional[bool] = ...) -> None: ...

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

class StatusRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...
