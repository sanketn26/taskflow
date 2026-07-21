from __future__ import annotations

import pytest

from taskwire.protocol.errors import ProtocolDecodeError
from taskwire.protocol.frames import MessageType
from taskwire.protocol.messages import TaskCapability, TaskRegistration
from taskwire.protocol.session import ConnectionState, OwnerRegistry, Session

OWNER = b"\x01" * 16
OTHER_OWNER = b"\x02" * 16


def test_hello_first_required():
    session = Session()
    state = ConnectionState()
    with pytest.raises(ProtocolDecodeError) as exc:
        session.authorize(state, MessageType.SUBMIT)
    assert exc.value.code == "not_registered"

    # HELLO itself is always allowed, registered or not.
    session.authorize(state, MessageType.HELLO)


@pytest.mark.parametrize(
    "role,allowed,forbidden",
    [
        ("runtime", MessageType.SUBMIT, MessageType.PULL),
        ("worker", MessageType.REGISTER_TASKS, MessageType.SUBMIT),
        ("admin", MessageType.STATUS, MessageType.SUBMIT),
    ],
)
def test_role_matrix(role, allowed, forbidden):
    session = Session()
    state = ConnectionState()
    session.register(
        state,
        role=role,
        owner_id=OWNER if role == "runtime" else None,
        worker_id="w" if role == "worker" else None,
    )

    session.authorize(state, allowed)  # does not raise

    with pytest.raises(ProtocolDecodeError) as exc:
        session.authorize(state, forbidden)
    assert exc.value.code == "role_forbidden"


def test_duplicate_in_flight_request_rejected():
    session = Session()
    state = ConnectionState()
    session.begin_request(state, 5)
    with pytest.raises(ProtocolDecodeError) as exc:
        session.begin_request(state, 5)
    assert exc.value.code == "duplicate_request"


def test_completion_frees_request_id():
    session = Session()
    state = ConnectionState()
    session.begin_request(state, 5)
    session.complete_request(state, 5)
    session.begin_request(state, 5)  # does not raise


def test_zero_request_id_never_tracked():
    session = Session()
    state = ConnectionState()
    session.begin_request(state, 0)
    session.begin_request(state, 0)  # unsolicited notifications reuse 0 freely


def test_reconnect_resets_request_namespace():
    session = Session()
    state = ConnectionState()
    session.begin_request(state, 5)
    state.reset_request_namespace()
    session.begin_request(state, 5)  # does not raise after reset


def test_owner_mismatch_rejected():
    session = Session()
    state = ConnectionState()
    session.register(state, role="runtime", owner_id=OWNER, worker_id=None)
    with pytest.raises(ProtocolDecodeError) as exc:
        session.check_owner(state, OTHER_OWNER)
    assert exc.value.code == "owner_mismatch"
    session.check_owner(state, OWNER)  # does not raise


def test_worker_must_register_compatible_capabilities_before_pull():
    session = Session()
    state = ConnectionState()
    session.register(
        state,
        role="worker",
        owner_id=None,
        worker_id="w",
        runtime="nodejs",
        codecs=["msgpack", "bytes"],
    )
    with pytest.raises(ProtocolDecodeError) as exc:
        session.authorize(state, MessageType.PULL)
    assert exc.value.code == "not_registered"
    registration = TaskRegistration(
        worker_id="w",
        generation=1,
        tasks=[
            TaskCapability(
                task_name="task",
                task_version="1",
                invocation="value",
                codecs=["msgpack"],
            )
        ],
    )
    session.register_tasks(state, registration)
    session.authorize(state, MessageType.PULL)
    session.register_tasks(state, registration)
    with pytest.raises(ProtocolDecodeError) as exc:
        session.register_tasks(state, TaskRegistration(worker_id="w", generation=1))
    assert exc.value.code == "task_conflict"


def test_non_python_worker_rejects_python_only_capability():
    session = Session()
    state = ConnectionState()
    session.register(
        state,
        role="worker",
        owner_id=None,
        worker_id="w",
        runtime="go",
        codecs=["msgpack", "cloudpickle"],
    )
    with pytest.raises(ProtocolDecodeError) as exc:
        session.register_tasks(
            state,
            TaskRegistration(
                worker_id="w",
                generation=1,
                tasks=[
                    TaskCapability(
                        task_name="task",
                        task_version="1",
                        invocation="python_args",
                        codecs=["cloudpickle"],
                    )
                ],
            ),
        )
    assert exc.value.code == "task_conflict"


def test_notification_ack_correlation_via_new_request_id():
    # Unsolicited RESULT uses request id 0; the client's ACK back uses a
    # fresh nonzero id, tracked independently of the notification.
    session = Session()
    state = ConnectionState()
    session.begin_request(state, 0)  # RESULT notification itself
    session.begin_request(state, 7)  # the ACK the client sends back
    assert 7 in state.in_flight_request_ids
    assert 0 not in state.in_flight_request_ids  # zero is never tracked


def test_owner_registry_promotes_newest_connection():
    registry = OwnerRegistry()
    previous = registry.promote(OWNER, "conn-1")
    assert previous is None
    assert registry.is_primary(OWNER, "conn-1")

    previous = registry.promote(OWNER, "conn-2")
    assert previous == "conn-1"
    assert registry.is_primary(OWNER, "conn-2")
    assert not registry.is_primary(OWNER, "conn-1")


def test_owner_registry_remove_only_if_still_primary():
    registry = OwnerRegistry()
    registry.promote(OWNER, "conn-1")
    registry.promote(OWNER, "conn-2")
    registry.remove(OWNER, "conn-1")  # no-op: conn-1 isn't primary anymore
    assert registry.is_primary(OWNER, "conn-2")
    registry.remove(OWNER, "conn-2")
    assert not registry.is_primary(OWNER, "conn-2")
