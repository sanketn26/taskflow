from __future__ import annotations

import pytest

from taskwire.protocol.errors import ProtocolError
from taskwire.protocol.messages import (
    TaskCapability,
    TaskRegistration,
    WorkerRegistration,
)
from taskwire.protocol.session import ConnectionState, OwnerRegistry, Session

OWNER = b"\x01" * 16
OTHER_OWNER = b"\x02" * 16


def worker_registration(
    *, worker_id="w", runtime="python", codecs=("msgpack",), generation=1, tasks=()
):
    return WorkerRegistration(
        worker_id=worker_id,
        runtime=runtime,
        runtime_version="1",
        sdk_version="0.1.0",
        codecs=list(codecs),
        tasks=TaskRegistration(
            worker_id=worker_id, generation=generation, tasks=list(tasks)
        ),
    )


def capability(name="a", version="1", invocation="value", codecs=("msgpack",)):
    return TaskCapability(
        task_name=name,
        task_version=version,
        invocation=invocation,
        codecs=list(codecs),
    )


def test_register_worker_establishes_capabilities():
    session = Session()
    state = ConnectionState()
    session.register_worker(state, worker_registration(tasks=[capability()]))

    assert state.registered is True
    assert state.worker_id == "w"
    assert state.capability_generation == 1
    assert state.codecs == frozenset({"msgpack"})


def test_register_tasks_before_registration_rejected():
    session = Session()
    state = ConnectionState()
    with pytest.raises(ProtocolError) as exc:
        session.register_tasks(state, TaskRegistration(worker_id="w", generation=1))
    assert exc.value.code == "not_registered"


def test_capability_fingerprint_is_derived_from_registration():
    session = Session()
    state = ConnectionState()
    tasks = [capability()]
    session.register_worker(state, worker_registration(runtime="nodejs", tasks=tasks))

    same = TaskRegistration(worker_id="w", generation=1, tasks=tasks)
    session.register_tasks(state, same)  # identical content is idempotent

    changed = TaskRegistration(
        worker_id="w", generation=1, tasks=[capability(name="b")]
    )
    with pytest.raises(ProtocolError) as exc:
        session.register_tasks(state, changed)
    assert exc.value.code == "task_conflict"


def test_stale_generation_rejected():
    session = Session()
    state = ConnectionState()
    session.register_worker(state, worker_registration(generation=5))

    with pytest.raises(ProtocolError) as exc:
        session.register_tasks(state, TaskRegistration(worker_id="w", generation=2))
    assert exc.value.code == "task_conflict"


def test_worker_id_must_match_registration():
    session = Session()
    state = ConnectionState()
    session.register_worker(state, worker_registration())

    with pytest.raises(ProtocolError) as exc:
        session.register_tasks(state, TaskRegistration(worker_id="other", generation=2))
    assert exc.value.code == "owner_mismatch"


def test_codec_absent_from_registration_rejected():
    session = Session()
    state = ConnectionState()
    with pytest.raises(ProtocolError) as exc:
        session.register_worker(
            state,
            worker_registration(
                codecs=["msgpack"], tasks=[capability(codecs=["bytes"])]
            ),
        )
    assert exc.value.code == "task_conflict"


@pytest.mark.parametrize("runtime", ["nodejs", "go"])
def test_python_only_capability_rejected_for_other_runtimes(runtime):
    session = Session()
    state = ConnectionState()
    with pytest.raises(ProtocolError) as exc:
        session.register_worker(
            state,
            worker_registration(
                runtime=runtime,
                codecs=["cloudpickle"],
                tasks=[capability(codecs=["cloudpickle"])],
            ),
        )
    assert exc.value.code == "task_conflict"


def test_owner_registry_promotes_newest_stream():
    registry = OwnerRegistry()
    first, second = object(), object()

    assert registry.promote(OWNER, first) is None
    assert registry.promote(OWNER, second) is first
    assert registry.is_primary(OWNER, first) is False
    assert registry.is_primary(OWNER, second) is True

    registry.remove(OWNER, second)
    assert registry.is_primary(OWNER, second) is False


def test_owner_registry_isolates_owners():
    registry = OwnerRegistry()
    stream = object()
    registry.promote(OWNER, stream)
    assert registry.is_primary(OTHER_OWNER, stream) is False
