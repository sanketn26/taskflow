"""Canonical msgpack schemas for every protocol payload.

Encoding follows the canonical profile from the protocol doc: map keys in
ascending UTF-8 byte order, minimal headers for maps/arrays/strings/binary,
and fixed-width representations for every declared uint32/uint64/int64
field regardless of value. Decoding uses ``msgpack.unpackb`` with strict
settings and then re-validates every field by hand (required keys, types,
ranges, ID sizes, unknown keys) since the library alone does not enforce
the protocol's structural rules.
"""

from __future__ import annotations

import struct
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional

import msgpack

from taskwire.protocol.errors import (
    INVALID_MESSAGE,
    MALFORMED_PAYLOAD,
    ProtocolDecodeError,
)
from taskwire.protocol.frames import MessageType

# --------------------------------------------------------------------------
# Canonical encode primitives
# --------------------------------------------------------------------------


def _pack_nil() -> bytes:
    return b"\xc0"


def _pack_bool(v: bool) -> bytes:
    return b"\xc3" if v else b"\xc2"


def _pack_u32(v: int) -> bytes:
    return b"\xce" + struct.pack(">I", v)


def _pack_u64(v: int) -> bytes:
    return b"\xcf" + struct.pack(">Q", v)


def _pack_i64(v: int) -> bytes:
    return b"\xd3" + struct.pack(">q", v)


def _pack_float64(v: float) -> bytes:
    return b"\xcb" + struct.pack(">d", v)


def _pack_str(s: str) -> bytes:
    data = s.encode("utf-8")
    n = len(data)
    if n <= 31:
        return bytes([0xA0 | n]) + data
    if n <= 0xFF:
        return b"\xd9" + bytes([n]) + data
    if n <= 0xFFFF:
        return b"\xda" + struct.pack(">H", n) + data
    return b"\xdb" + struct.pack(">I", n) + data


def _pack_bin(b: bytes) -> bytes:
    n = len(b)
    if n <= 0xFF:
        return b"\xc4" + bytes([n]) + b
    if n <= 0xFFFF:
        return b"\xc5" + struct.pack(">H", n) + b
    return b"\xc6" + struct.pack(">I", n) + b


def _array_header(n: int) -> bytes:
    if n <= 15:
        return bytes([0x90 | n])
    if n <= 0xFFFF:
        return b"\xdc" + struct.pack(">H", n)
    return b"\xdd" + struct.pack(">I", n)


def _map_header(n: int) -> bytes:
    if n <= 15:
        return bytes([0x80 | n])
    if n <= 0xFFFF:
        return b"\xde" + struct.pack(">H", n)
    return b"\xdf" + struct.pack(">I", n)


def _pack_array(items: list[bytes]) -> bytes:
    return _array_header(len(items)) + b"".join(items)


def _pack_map(fields: Mapping[str, bytes]) -> bytes:
    ordered = sorted(fields.items(), key=lambda kv: kv[0].encode("utf-8"))
    out = [_map_header(len(ordered))]
    for k, v in ordered:
        out.append(_pack_str(k))
        out.append(v)
    return b"".join(out)


def _pack_str_map(d: Mapping[str, str]) -> bytes:
    return _pack_map({k: _pack_str(v) for k, v in d.items()})


def _pack_str_u64_map(d: Mapping[str, int]) -> bytes:
    return _pack_map({k: _pack_u64(v) for k, v in d.items()})


def _pack_opt(value: Optional[Any], packer: Callable[[Any], bytes]) -> bytes:
    return _pack_nil() if value is None else packer(value)


def _pack_portable(value: Any) -> bytes:
    if value is None:
        return _pack_nil()
    if isinstance(value, bool):
        return _pack_bool(value)
    if isinstance(value, int):
        if value < -(1 << 63) or value > (1 << 64) - 1:
            raise ProtocolDecodeError(
                INVALID_MESSAGE, "portable integer is outside 64-bit range"
            )
        return msgpack.packb(value, use_bin_type=True)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ProtocolDecodeError(INVALID_MESSAGE, "portable float must be finite")
        return _pack_float64(value)
    if isinstance(value, str):
        return _pack_str(value)
    if isinstance(value, bytes):
        return _pack_bin(value)
    if isinstance(value, list):
        return _pack_array([_pack_portable(v) for v in value])
    if isinstance(value, dict):
        if any(not isinstance(k, str) for k in value):
            raise ProtocolDecodeError(
                INVALID_MESSAGE, "portable map keys must be strings"
            )
        return _pack_map({k: _pack_portable(v) for k, v in value.items()})
    raise ProtocolDecodeError(
        INVALID_MESSAGE, f"unsupported portable value type {type(value).__name__}"
    )


def _validate_portable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, str, bytes)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        if -(1 << 63) <= value <= (1 << 64) - 1:
            return value
    elif isinstance(value, float):
        if math.isfinite(value):
            return value
    elif isinstance(value, list):
        return [_validate_portable(v) for v in value]
    elif isinstance(value, dict) and all(isinstance(k, str) for k in value):
        return {k: _validate_portable(v) for k, v in value.items()}
    raise ProtocolDecodeError(
        INVALID_MESSAGE, "value is outside the portable msgpack profile"
    )


def encode_portable_value(value: Any) -> bytes:
    """Encode one application value using the deterministic portable profile."""
    return _pack_portable(value)


def decode_portable_value(payload: bytes) -> Any:
    """Decode and validate one portable application value."""
    return _validate_portable(_unpack_strict(payload))


# --------------------------------------------------------------------------
# Strict decode primitives
# --------------------------------------------------------------------------


class _DuplicateKey(Exception):
    def __init__(self, key: object) -> None:
        self.key = key


def _pairs_hook(pairs: list[tuple[Any, Any]]) -> dict:
    out: dict = {}
    for k, v in pairs:
        if k in out:
            raise _DuplicateKey(k)
        out[k] = v
    return out


def _unpack_strict(payload: bytes) -> Any:
    try:
        return msgpack.unpackb(
            payload,
            raw=False,
            strict_map_key=True,
            object_pairs_hook=_pairs_hook,
        )
    except _DuplicateKey as exc:
        raise ProtocolDecodeError(
            INVALID_MESSAGE, f"duplicate key {exc.key!r}"
        ) from None
    except msgpack.exceptions.ExtraData:
        raise ProtocolDecodeError(
            INVALID_MESSAGE, "trailing bytes after payload"
        ) from None
    except (msgpack.exceptions.UnpackException, ValueError, TypeError) as exc:
        raise ProtocolDecodeError(MALFORMED_PAYLOAD, str(exc)) from None


def _as_map(v: Any, what: str = "payload") -> dict:
    if not isinstance(v, dict):
        raise ProtocolDecodeError(INVALID_MESSAGE, f"{what} must be a map")
    return v


def _req(d: dict, key: str) -> Any:
    if key not in d:
        raise ProtocolDecodeError(INVALID_MESSAGE, f"missing required key {key!r}")
    return d[key]


def _no_unknown(
    d: dict, allowed: frozenset[str], *, forward_compatible: bool = False
) -> None:
    if forward_compatible:
        return
    extra = set(d) - allowed
    if extra:
        raise ProtocolDecodeError(INVALID_MESSAGE, f"unknown keys {sorted(extra)!r}")


def _bin(v: Any, n: int, field_name: str) -> bytes:
    if not isinstance(v, bytes) or len(v) != n:
        raise ProtocolDecodeError(
            INVALID_MESSAGE, f"{field_name} must be {n}-byte binary"
        )
    return v


def _str(v: Any, field_name: str) -> str:
    if not isinstance(v, str):
        raise ProtocolDecodeError(INVALID_MESSAGE, f"{field_name} must be a string")
    return v


def _bool(v: Any, field_name: str) -> bool:
    if not isinstance(v, bool):
        raise ProtocolDecodeError(INVALID_MESSAGE, f"{field_name} must be a bool")
    return v


def _uint(v: Any, bits: int, field_name: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise ProtocolDecodeError(INVALID_MESSAGE, f"{field_name} must be an integer")
    if v < 0 or v >= (1 << bits):
        raise ProtocolDecodeError(
            INVALID_MESSAGE, f"{field_name} out of range for uint{bits}"
        )
    return v


def _int(v: Any, bits: int, field_name: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise ProtocolDecodeError(INVALID_MESSAGE, f"{field_name} must be an integer")
    lo, hi = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
    if not (lo <= v <= hi):
        raise ProtocolDecodeError(
            INVALID_MESSAGE, f"{field_name} out of range for int{bits}"
        )
    return v


def _str_map(v: Any, field_name: str) -> dict[str, str]:
    if not isinstance(v, dict):
        raise ProtocolDecodeError(INVALID_MESSAGE, f"{field_name} must be a map")
    for k, val in v.items():
        if not isinstance(k, str) or not isinstance(val, str):
            raise ProtocolDecodeError(
                INVALID_MESSAGE, f"{field_name} must be map<string,string>"
            )
    return v


def _array(v: Any, field_name: str) -> list:
    if not isinstance(v, list):
        raise ProtocolDecodeError(INVALID_MESSAGE, f"{field_name} must be an array")
    return v


def _opt(v: Any, decoder: Callable[[Any], Any]) -> Any:
    return None if v is None else decoder(v)


def _enum(v: Any, allowed: frozenset[str], field_name: str) -> str:
    s = _str(v, field_name)
    if s not in allowed:
        raise ProtocolDecodeError(
            INVALID_MESSAGE, f"{field_name} has unknown value {s!r}"
        )
    return s


# --------------------------------------------------------------------------
# ObjectRef / ValueRef
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ObjectRef:
    store: str
    key: str
    size: int
    sha256: bytes
    codec: str

    def __post_init__(self) -> None:
        if not self.store or not self.key or not self.codec:
            raise ProtocolDecodeError(
                INVALID_MESSAGE, "ObjectRef strings must be non-empty"
            )
        if isinstance(self.size, bool) or not 0 <= self.size < (1 << 64):
            raise ProtocolDecodeError(INVALID_MESSAGE, "ObjectRef.size out of range")
        if not isinstance(self.sha256, bytes) or len(self.sha256) != 32:
            raise ProtocolDecodeError(
                INVALID_MESSAGE, "ObjectRef.sha256 must be 32 bytes"
            )

    def to_bytes(self) -> bytes:
        return _pack_map(
            {
                "store": _pack_str(self.store),
                "key": _pack_str(self.key),
                "size": _pack_u64(self.size),
                "sha256": _pack_bin(self.sha256),
                "codec": _pack_str(self.codec),
            }
        )

    @staticmethod
    def from_dict(d: dict) -> "ObjectRef":
        allowed = frozenset({"store", "key", "size", "sha256", "codec"})
        _no_unknown(d, allowed)
        return ObjectRef(
            store=_str(_req(d, "store"), "store"),
            key=_str(_req(d, "key"), "key"),
            size=_uint(_req(d, "size"), 64, "size"),
            sha256=_bin(_req(d, "sha256"), 32, "sha256"),
            codec=_str(_req(d, "codec"), "codec"),
        )


_OBJECT_REF_KEYS = frozenset({"store", "key", "size", "sha256", "codec"})


@dataclass(frozen=True)
class ValueRef:
    inline: Optional[bytes] = None
    codec: Optional[str] = None
    object: Optional[ObjectRef] = None

    def __post_init__(self) -> None:
        has_inline = self.inline is not None
        has_object = self.object is not None
        if has_inline == has_object:
            raise ProtocolDecodeError(
                INVALID_MESSAGE, "ValueRef requires exactly one of inline or object"
            )
        if has_inline and self.codec is None:
            raise ProtocolDecodeError(INVALID_MESSAGE, "ValueRef.inline requires codec")

    @staticmethod
    def from_inline(data: bytes, codec: str) -> "ValueRef":
        return ValueRef(inline=data, codec=codec)

    @staticmethod
    def from_object(obj: ObjectRef) -> "ValueRef":
        return ValueRef(object=obj)

    def to_bytes(self) -> bytes:
        if self.object is not None:
            return _pack_map({"object": self.object.to_bytes()})
        assert self.inline is not None and self.codec is not None
        return _pack_map(
            {"inline": _pack_bin(self.inline), "codec": _pack_str(self.codec)}
        )

    @staticmethod
    def from_dict(d: dict) -> "ValueRef":
        keys = frozenset(d)
        if keys == frozenset({"object"}):
            return ValueRef.from_object(
                ObjectRef.from_dict(_as_map(d["object"], "object"))
            )
        if keys == frozenset({"inline", "codec"}):
            return ValueRef.from_inline(
                _bin_any(_req(d, "inline"), "inline"), _str(_req(d, "codec"), "codec")
            )
        raise ProtocolDecodeError(INVALID_MESSAGE, "ValueRef has invalid key set")


def _bin_any(v: Any, field_name: str) -> bytes:
    if not isinstance(v, bytes):
        raise ProtocolDecodeError(INVALID_MESSAGE, f"{field_name} must be binary")
    return v


# --------------------------------------------------------------------------
# Connection / request schemas
# --------------------------------------------------------------------------

_HELLO_ROLES = frozenset({"runtime", "worker", "admin"})
_WORKER_RUNTIMES = frozenset({"python", "nodejs", "go"})
_INVOCATIONS = frozenset({"value", "python_args"})


@dataclass(frozen=True)
class Hello:
    role: str
    owner_id: Optional[bytes] = None
    worker_id: Optional[str] = None
    runtime: Optional[str] = None
    runtime_version: Optional[str] = None
    sdk_version: Optional[str] = None
    codecs: Optional[list[str]] = None

    def __post_init__(self) -> None:
        if self.role not in _HELLO_ROLES:
            raise ProtocolDecodeError(
                INVALID_MESSAGE, f"unknown Hello role {self.role!r}"
            )
        worker_fields = (
            self.runtime,
            self.runtime_version,
            self.sdk_version,
            self.codecs,
        )
        if self.role == "runtime" and (
            self.owner_id is None
            or not isinstance(self.owner_id, bytes)
            or len(self.owner_id) != 16
            or self.worker_id is not None
            or any(v is not None for v in worker_fields)
        ):
            raise ProtocolDecodeError(
                INVALID_MESSAGE, "runtime Hello requires only owner_id"
            )
        if self.role == "worker" and (
            self.worker_id is None
            or not self.worker_id
            or self.owner_id is not None
            or self.runtime not in _WORKER_RUNTIMES
            or not self.runtime_version
            or not self.sdk_version
            or not self.codecs
            or any(not isinstance(codec, str) or not codec for codec in self.codecs)
            or len(set(self.codecs)) != len(self.codecs)
        ):
            raise ProtocolDecodeError(
                INVALID_MESSAGE,
                "worker Hello requires identity, runtime, versions, and unique codecs",
            )
        if self.role == "admin" and (
            self.owner_id is not None
            or self.worker_id is not None
            or any(v is not None for v in worker_fields)
        ):
            raise ProtocolDecodeError(
                INVALID_MESSAGE, "admin Hello takes no extra fields"
            )

    def to_bytes(self) -> bytes:
        fields: dict[str, bytes] = {"role": _pack_str(self.role)}
        if self.owner_id is not None:
            fields["owner_id"] = _pack_bin(self.owner_id)
        if self.worker_id is not None:
            fields["worker_id"] = _pack_str(self.worker_id)
            fields["runtime"] = _pack_str(self.runtime or "")
            fields["runtime_version"] = _pack_str(self.runtime_version or "")
            fields["sdk_version"] = _pack_str(self.sdk_version or "")
            fields["codecs"] = _pack_array([_pack_str(v) for v in self.codecs or []])
        return _pack_map(fields)

    @staticmethod
    def from_dict(d: dict) -> "Hello":
        role = _enum(_req(d, "role"), _HELLO_ROLES, "role")
        if role == "runtime":
            _no_unknown(d, frozenset({"role", "owner_id"}))
            return Hello(role=role, owner_id=_bin(_req(d, "owner_id"), 16, "owner_id"))
        if role == "worker":
            _no_unknown(
                d,
                frozenset(
                    {
                        "role",
                        "worker_id",
                        "runtime",
                        "runtime_version",
                        "sdk_version",
                        "codecs",
                    }
                ),
            )
            return Hello(
                role=role,
                worker_id=_str(_req(d, "worker_id"), "worker_id"),
                runtime=_enum(_req(d, "runtime"), _WORKER_RUNTIMES, "runtime"),
                runtime_version=_str(_req(d, "runtime_version"), "runtime_version"),
                sdk_version=_str(_req(d, "sdk_version"), "sdk_version"),
                codecs=[
                    _str(v, "codecs[]") for v in _array(_req(d, "codecs"), "codecs")
                ],
            )
        _no_unknown(d, frozenset({"role"}))
        return Hello(role=role)


@dataclass(frozen=True)
class PullRequest:
    worker_id: str
    capability_generation: int

    def __post_init__(self) -> None:
        if (
            not self.worker_id
            or isinstance(self.capability_generation, bool)
            or not 0 <= self.capability_generation < (1 << 64)
        ):
            raise ProtocolDecodeError(INVALID_MESSAGE, "invalid PullRequest")

    def to_bytes(self) -> bytes:
        return _pack_map(
            {
                "worker_id": _pack_str(self.worker_id),
                "capability_generation": _pack_u64(self.capability_generation),
            }
        )

    @staticmethod
    def from_dict(d: dict) -> "PullRequest":
        _no_unknown(d, frozenset({"worker_id", "capability_generation"}))
        return PullRequest(
            worker_id=_str(_req(d, "worker_id"), "worker_id"),
            capability_generation=_uint(
                _req(d, "capability_generation"), 64, "capability_generation"
            ),
        )


@dataclass(frozen=True)
class TaskCapability:
    task_name: str
    task_version: str
    invocation: str
    codecs: list[str]

    def __post_init__(self) -> None:
        if not self.task_name or not self.task_version:
            raise ProtocolDecodeError(
                INVALID_MESSAGE, "task capability identity must be non-empty"
            )
        if (
            self.invocation not in _INVOCATIONS
            or not self.codecs
            or len(set(self.codecs)) != len(self.codecs)
        ):
            raise ProtocolDecodeError(
                INVALID_MESSAGE, "invalid task capability invocation/codecs"
            )
        if any(not isinstance(v, str) or not v for v in self.codecs):
            raise ProtocolDecodeError(
                INVALID_MESSAGE, "task capability codecs must be non-empty strings"
            )

    def to_bytes(self) -> bytes:
        return _pack_map(
            {
                "task_name": _pack_str(self.task_name),
                "task_version": _pack_str(self.task_version),
                "invocation": _pack_str(self.invocation),
                "codecs": _pack_array([_pack_str(v) for v in self.codecs]),
            }
        )

    @staticmethod
    def from_dict(d: dict) -> "TaskCapability":
        _no_unknown(d, frozenset({"task_name", "task_version", "invocation", "codecs"}))
        return TaskCapability(
            task_name=_str(_req(d, "task_name"), "task_name"),
            task_version=_str(_req(d, "task_version"), "task_version"),
            invocation=_enum(_req(d, "invocation"), _INVOCATIONS, "invocation"),
            codecs=[_str(v, "codecs[]") for v in _array(_req(d, "codecs"), "codecs")],
        )


@dataclass(frozen=True)
class TaskRegistration:
    worker_id: str
    generation: int
    tasks: list[TaskCapability]

    def __post_init__(self) -> None:
        if (
            not self.worker_id
            or isinstance(self.generation, bool)
            or not 1 <= self.generation < (1 << 64)
        ):
            raise ProtocolDecodeError(
                INVALID_MESSAGE, "registration generation must be positive"
            )
        identities = [(t.task_name, t.task_version) for t in self.tasks]
        if len(set(identities)) != len(identities):
            raise ProtocolDecodeError(
                INVALID_MESSAGE, "duplicate task capability identity"
            )

    def to_bytes(self) -> bytes:
        return _pack_map(
            {
                "worker_id": _pack_str(self.worker_id),
                "generation": _pack_u64(self.generation),
                "tasks": _pack_array([v.to_bytes() for v in self.tasks]),
            }
        )

    @staticmethod
    def from_dict(d: dict) -> "TaskRegistration":
        _no_unknown(d, frozenset({"worker_id", "generation", "tasks"}))
        return TaskRegistration(
            worker_id=_str(_req(d, "worker_id"), "worker_id"),
            generation=_uint(_req(d, "generation"), 64, "generation"),
            tasks=[
                TaskCapability.from_dict(_as_map(v, "tasks[]"))
                for v in _array(_req(d, "tasks"), "tasks")
            ],
        )


@dataclass(frozen=True)
class TaskQuery:
    owner_id: bytes
    task_ids: list[bytes]

    def to_bytes(self) -> bytes:
        return _pack_map(
            {
                "owner_id": _pack_bin(self.owner_id),
                "task_ids": _pack_array([_pack_bin(t) for t in self.task_ids]),
            }
        )

    @staticmethod
    def from_dict(d: dict) -> "TaskQuery":
        _no_unknown(d, frozenset({"owner_id", "task_ids"}))
        raw_ids = _array(_req(d, "task_ids"), "task_ids")
        if len(raw_ids) < 1:
            raise ProtocolDecodeError(INVALID_MESSAGE, "task_ids must be non-empty")
        return TaskQuery(
            owner_id=_bin(_req(d, "owner_id"), 16, "owner_id"),
            task_ids=[_bin(t, 16, "task_ids[]") for t in raw_ids],
        )


_TASK_STATES = frozenset(
    {"queued", "leased", "succeeded", "failed", "cancelled", "dead_lettered", "unknown"}
)


@dataclass(frozen=True)
class TaskSnapshotEntry:
    task_id: bytes
    state: str
    cursor: Optional[int]
    result: Optional[ObjectRef]
    failure: Optional["Failure"]

    def to_bytes(self) -> bytes:
        return _pack_map(
            {
                "task_id": _pack_bin(self.task_id),
                "state": _pack_str(self.state),
                "cursor": _pack_opt(self.cursor, _pack_u64),
                "result": _pack_opt(self.result, lambda o: o.to_bytes()),
                "failure": _pack_opt(self.failure, lambda f: f.to_bytes()),
            }
        )

    @staticmethod
    def from_dict(d: dict) -> "TaskSnapshotEntry":
        _no_unknown(d, frozenset({"task_id", "state", "cursor", "result", "failure"}))
        return TaskSnapshotEntry(
            task_id=_bin(_req(d, "task_id"), 16, "task_id"),
            state=_enum(_req(d, "state"), _TASK_STATES, "state"),
            cursor=_opt(_req(d, "cursor"), lambda v: _uint(v, 64, "cursor")),
            result=_opt(
                _req(d, "result"), lambda v: ObjectRef.from_dict(_as_map(v, "result"))
            ),
            failure=_opt(
                _req(d, "failure"), lambda v: Failure.from_dict(_as_map(v, "failure"))
            ),
        )


@dataclass(frozen=True)
class TaskSnapshot:
    tasks: list[TaskSnapshotEntry]

    def to_bytes(self) -> bytes:
        return _pack_map({"tasks": _pack_array([t.to_bytes() for t in self.tasks])})

    @staticmethod
    def from_dict(d: dict) -> "TaskSnapshot":
        _no_unknown(d, frozenset({"tasks"}))
        raw = _array(_req(d, "tasks"), "tasks")
        return TaskSnapshot(
            tasks=[TaskSnapshotEntry.from_dict(_as_map(t, "tasks[]")) for t in raw]
        )


# --------------------------------------------------------------------------
# Task and result envelopes
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskEnvelope:
    owner_id: bytes
    task_name: str
    task_version: str
    invocation: str
    input: ValueRef
    labels: dict[str, str]
    idempotent: bool
    submitted_at_unix_ms: int

    def __post_init__(self) -> None:
        if not isinstance(self.owner_id, bytes) or len(self.owner_id) != 16:
            raise ProtocolDecodeError(
                INVALID_MESSAGE, "TaskEnvelope.owner_id must be 16 bytes"
            )
        if (
            not self.task_name
            or not self.task_version
            or self.invocation not in _INVOCATIONS
        ):
            raise ProtocolDecodeError(
                INVALID_MESSAGE, "invalid TaskEnvelope identity or invocation"
            )
        if isinstance(self.submitted_at_unix_ms, bool) or not -(
            1 << 63
        ) <= self.submitted_at_unix_ms < (1 << 63):
            raise ProtocolDecodeError(
                INVALID_MESSAGE, "submitted_at_unix_ms out of range"
            )

    def to_bytes(self) -> bytes:
        return _pack_map(
            {
                "owner_id": _pack_bin(self.owner_id),
                "task_name": _pack_str(self.task_name),
                "task_version": _pack_str(self.task_version),
                "invocation": _pack_str(self.invocation),
                "input": self.input.to_bytes(),
                "labels": _pack_str_map(self.labels),
                "idempotent": _pack_bool(self.idempotent),
                "submitted_at_unix_ms": _pack_i64(self.submitted_at_unix_ms),
            }
        )

    @staticmethod
    def from_dict(d: dict) -> "TaskEnvelope":
        _no_unknown(d, _TASK_ENVELOPE_KEYS)
        return TaskEnvelope(
            owner_id=_bin(_req(d, "owner_id"), 16, "owner_id"),
            task_name=_str(_req(d, "task_name"), "task_name"),
            task_version=_str(_req(d, "task_version"), "task_version"),
            invocation=_enum(_req(d, "invocation"), _INVOCATIONS, "invocation"),
            input=ValueRef.from_dict(_as_map(_req(d, "input"), "input")),
            labels=_str_map(_req(d, "labels"), "labels"),
            idempotent=_bool(_req(d, "idempotent"), "idempotent"),
            submitted_at_unix_ms=_int(
                _req(d, "submitted_at_unix_ms"), 64, "submitted_at_unix_ms"
            ),
        )


_TASK_ENVELOPE_KEYS = frozenset(
    {
        "owner_id",
        "task_name",
        "task_version",
        "invocation",
        "input",
        "labels",
        "idempotent",
        "submitted_at_unix_ms",
    }
)


@dataclass(frozen=True)
class LeasedTask:
    task: TaskEnvelope
    lease_id: bytes
    ttl_ms: int
    attempt: int

    def to_bytes(self) -> bytes:
        return _pack_map(
            {
                "task": self.task.to_bytes(),
                "lease_id": _pack_bin(self.lease_id),
                "ttl_ms": _pack_u32(self.ttl_ms),
                "attempt": _pack_u32(self.attempt),
            }
        )

    @staticmethod
    def from_dict(d: dict) -> "LeasedTask":
        _no_unknown(d, frozenset({"task", "lease_id", "ttl_ms", "attempt"}))
        return LeasedTask(
            task=TaskEnvelope.from_dict(_as_map(_req(d, "task"), "task")),
            lease_id=_bin(_req(d, "lease_id"), 16, "lease_id"),
            ttl_ms=_uint(_req(d, "ttl_ms"), 32, "ttl_ms"),
            attempt=_uint(_req(d, "attempt"), 32, "attempt"),
        )


def _validate_result_xor_failure(
    result: Optional[ObjectRef], failure: Optional["Failure"]
) -> None:
    if (result is None) == (failure is None):
        raise ProtocolDecodeError(
            INVALID_MESSAGE, "exactly one of result or failure is required"
        )


@dataclass(frozen=True)
class Completion:
    lease_id: bytes
    result: Optional[ObjectRef]
    failure: Optional["Failure"]

    def __post_init__(self) -> None:
        _validate_result_xor_failure(self.result, self.failure)

    def to_bytes(self) -> bytes:
        return _pack_map(
            {
                "lease_id": _pack_bin(self.lease_id),
                "result": _pack_opt(self.result, lambda o: o.to_bytes()),
                "failure": _pack_opt(self.failure, lambda f: f.to_bytes()),
            }
        )

    @staticmethod
    def from_dict(d: dict) -> "Completion":
        _no_unknown(d, frozenset({"lease_id", "result", "failure"}))
        return Completion(
            lease_id=_bin(_req(d, "lease_id"), 16, "lease_id"),
            result=_opt(
                _req(d, "result"), lambda v: ObjectRef.from_dict(_as_map(v, "result"))
            ),
            failure=_opt(
                _req(d, "failure"), lambda v: Failure.from_dict(_as_map(v, "failure"))
            ),
        )


@dataclass(frozen=True)
class ForwardedTask:
    transfer_id: bytes
    origin_node: str
    task: TaskEnvelope

    def to_bytes(self) -> bytes:
        return _pack_map(
            {
                "transfer_id": _pack_bin(self.transfer_id),
                "origin_node": _pack_str(self.origin_node),
                "task": self.task.to_bytes(),
            }
        )

    @staticmethod
    def from_dict(d: dict) -> "ForwardedTask":
        _no_unknown(d, frozenset({"transfer_id", "origin_node", "task"}))
        return ForwardedTask(
            transfer_id=_bin(_req(d, "transfer_id"), 16, "transfer_id"),
            origin_node=_str(_req(d, "origin_node"), "origin_node"),
            task=TaskEnvelope.from_dict(_as_map(_req(d, "task"), "task")),
        )


@dataclass(frozen=True)
class ForwardedCompletion:
    transfer_id: bytes
    remote_node: str
    remote_attempt: int
    result: Optional[ObjectRef]
    failure: Optional["Failure"]

    def __post_init__(self) -> None:
        _validate_result_xor_failure(self.result, self.failure)

    def to_bytes(self) -> bytes:
        return _pack_map(
            {
                "transfer_id": _pack_bin(self.transfer_id),
                "remote_node": _pack_str(self.remote_node),
                "remote_attempt": _pack_u32(self.remote_attempt),
                "result": _pack_opt(self.result, lambda o: o.to_bytes()),
                "failure": _pack_opt(self.failure, lambda f: f.to_bytes()),
            }
        )

    @staticmethod
    def from_dict(d: dict) -> "ForwardedCompletion":
        _no_unknown(
            d,
            frozenset(
                {"transfer_id", "remote_node", "remote_attempt", "result", "failure"}
            ),
        )
        return ForwardedCompletion(
            transfer_id=_bin(_req(d, "transfer_id"), 16, "transfer_id"),
            remote_node=_str(_req(d, "remote_node"), "remote_node"),
            remote_attempt=_uint(_req(d, "remote_attempt"), 32, "remote_attempt"),
            result=_opt(
                _req(d, "result"), lambda v: ObjectRef.from_dict(_as_map(v, "result"))
            ),
            failure=_opt(
                _req(d, "failure"), lambda v: Failure.from_dict(_as_map(v, "failure"))
            ),
        )


@dataclass(frozen=True)
class Failure:
    code: str
    message: str
    details: Optional[ValueRef]
    retryable: bool

    def to_bytes(self) -> bytes:
        return _pack_map(
            {
                "code": _pack_str(self.code),
                "message": _pack_str(self.message),
                "details": _pack_opt(self.details, lambda v: v.to_bytes()),
                "retryable": _pack_bool(self.retryable),
            }
        )

    @staticmethod
    def from_dict(d: dict) -> "Failure":
        _no_unknown(d, frozenset({"code", "message", "details", "retryable"}))
        return Failure(
            code=_str(_req(d, "code"), "code"),
            message=_str(_req(d, "message"), "message"),
            details=_opt(
                _req(d, "details"), lambda v: ValueRef.from_dict(_as_map(v, "details"))
            ),
            retryable=_bool(_req(d, "retryable"), "retryable"),
        )


_RESULT_STATES = frozenset({"succeeded", "failed", "cancelled"})


@dataclass(frozen=True)
class ResultNotification:
    owner_id: bytes
    cursor: int
    state: str
    result: Optional[ObjectRef]
    failure: Optional[Failure]

    def to_bytes(self) -> bytes:
        return _pack_map(
            {
                "owner_id": _pack_bin(self.owner_id),
                "cursor": _pack_u64(self.cursor),
                "state": _pack_str(self.state),
                "result": _pack_opt(self.result, lambda o: o.to_bytes()),
                "failure": _pack_opt(self.failure, lambda f: f.to_bytes()),
            }
        )

    @staticmethod
    def from_dict(d: dict) -> "ResultNotification":
        _no_unknown(d, frozenset({"owner_id", "cursor", "state", "result", "failure"}))
        return ResultNotification(
            owner_id=_bin(_req(d, "owner_id"), 16, "owner_id"),
            cursor=_uint(_req(d, "cursor"), 64, "cursor"),
            state=_enum(_req(d, "state"), _RESULT_STATES, "state"),
            result=_opt(
                _req(d, "result"), lambda v: ObjectRef.from_dict(_as_map(v, "result"))
            ),
            failure=_opt(
                _req(d, "failure"), lambda v: Failure.from_dict(_as_map(v, "failure"))
            ),
        )


@dataclass(frozen=True)
class StatusSnapshot:
    version: str
    pid: int
    ready: bool
    task_counts: dict[str, int]
    active_leases: int
    worker_pids: list[int]
    worker_restarts: int
    storage_healthy: bool
    cluster_members: int
    kafka_outbox_pending: int
    last_error_code: Optional[str]

    def to_bytes(self) -> bytes:
        return _pack_map(
            {
                "version": _pack_str(self.version),
                "pid": _pack_u64(self.pid),
                "ready": _pack_bool(self.ready),
                "task_counts": _pack_str_u64_map(self.task_counts),
                "active_leases": _pack_u64(self.active_leases),
                "worker_pids": _pack_array([_pack_u64(p) for p in self.worker_pids]),
                "worker_restarts": _pack_u64(self.worker_restarts),
                "storage_healthy": _pack_bool(self.storage_healthy),
                "cluster_members": _pack_u64(self.cluster_members),
                "kafka_outbox_pending": _pack_u64(self.kafka_outbox_pending),
                "last_error_code": _pack_opt(self.last_error_code, _pack_str),
            }
        )

    @staticmethod
    def from_dict(d: dict) -> "StatusSnapshot":
        # Forward-compatible: unknown keys are ignored rather than rejected.
        return StatusSnapshot(
            version=_str(_req(d, "version"), "version"),
            pid=_uint(_req(d, "pid"), 64, "pid"),
            ready=_bool(_req(d, "ready"), "ready"),
            task_counts={
                _str(k, "task_counts key"): _uint(v, 64, "task_counts value")
                for k, v in _as_map(_req(d, "task_counts"), "task_counts").items()
            },
            active_leases=_uint(_req(d, "active_leases"), 64, "active_leases"),
            worker_pids=[
                _uint(p, 64, "worker_pids[]")
                for p in _array(_req(d, "worker_pids"), "worker_pids")
            ],
            worker_restarts=_uint(_req(d, "worker_restarts"), 64, "worker_restarts"),
            storage_healthy=_bool(_req(d, "storage_healthy"), "storage_healthy"),
            cluster_members=_uint(_req(d, "cluster_members"), 64, "cluster_members"),
            kafka_outbox_pending=_uint(
                _req(d, "kafka_outbox_pending"), 64, "kafka_outbox_pending"
            ),
            last_error_code=_opt(
                _req(d, "last_error_code"), lambda v: _str(v, "last_error_code")
            ),
        )


_STATUS_SNAPSHOT_KEYS = frozenset(
    {
        "version",
        "pid",
        "ready",
        "task_counts",
        "active_leases",
        "worker_pids",
        "worker_restarts",
        "storage_healthy",
        "cluster_members",
        "kafka_outbox_pending",
        "last_error_code",
    }
)


# --------------------------------------------------------------------------
# ACK and Error
# --------------------------------------------------------------------------

# field -> (packer, decoder) for every field that appears in some Ack kind.
_ACK_FIELD_CODECS: dict[
    str, tuple[Callable[[Any], bytes], Callable[[Any, str], Any]]
] = {
    "task_id": (_pack_bin, lambda v, n: _bin(v, 16, n)),
    "transfer_id": (_pack_bin, lambda v, n: _bin(v, 16, n)),
    "lease_id": (_pack_bin, lambda v, n: _bin(v, 16, n)),
    "owner_id": (_pack_bin, lambda v, n: _bin(v, 16, n)),
    "cancelled": (_pack_bool, _bool),
    "cursor": (_pack_u64, lambda v, n: _uint(v, 64, n)),
    "object": (lambda o: o.to_bytes(), lambda v, n: ObjectRef.from_dict(_as_map(v, n))),
    "next_cursor": (_pack_u64, lambda v, n: _uint(v, 64, n)),
    "more": (_pack_bool, _bool),
    "accepted": (_pack_u32, lambda v, n: _uint(v, 32, n)),
    "worker_id": (_pack_str, _str),
    "generation": (_pack_u64, lambda v, n: _uint(v, 64, n)),
}

_ACK_KIND_FIELDS: dict[str, tuple[str, ...]] = {
    "hello": (),
    "submit": ("task_id",),
    "forward": ("task_id", "transfer_id"),
    "heartbeat": ("lease_id",),
    "complete": ("lease_id",),
    "cancel": ("task_id", "cancelled"),
    "result": ("owner_id", "task_id", "cursor"),
    "empty_pull": (),
    "object_put": ("transfer_id", "object"),
    "object_get": ("transfer_id",),
    "resume": ("owner_id", "next_cursor", "more"),
    "steal": ("transfer_id", "accepted"),
    "register_tasks": ("worker_id", "generation", "accepted"),
}


@dataclass(frozen=True)
class Ack:
    kind: str
    fields: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in _ACK_KIND_FIELDS:
            raise ProtocolDecodeError(
                INVALID_MESSAGE, f"unknown Ack kind {self.kind!r}"
            )
        expected = frozenset(_ACK_KIND_FIELDS[self.kind])
        got = frozenset(self.fields)
        if expected != got:
            raise ProtocolDecodeError(
                INVALID_MESSAGE,
                f"Ack(kind={self.kind!r}) requires fields {sorted(expected)!r}",
            )

    def __getattr__(self, name: str) -> Any:
        fields = object.__getattribute__(self, "fields")
        if name in fields:
            return fields[name]
        raise AttributeError(name)

    def to_bytes(self) -> bytes:
        out: dict[str, bytes] = {"kind": _pack_str(self.kind)}
        for name, value in self.fields.items():
            packer, _ = _ACK_FIELD_CODECS[name]
            out[name] = packer(value)
        return _pack_map(out)

    @staticmethod
    def from_dict(d: dict) -> "Ack":
        kind = _enum(_req(d, "kind"), frozenset(_ACK_KIND_FIELDS), "kind")
        expected = frozenset(_ACK_KIND_FIELDS[kind])
        _no_unknown(d, expected | {"kind"})
        fields = {}
        for name in expected:
            _, decoder = _ACK_FIELD_CODECS[name]
            fields[name] = decoder(_req(d, name), name)
        return Ack(kind=kind, fields=fields)


def make_ack(kind: str, **fields: Any) -> Ack:
    return Ack(kind=kind, fields=fields)


@dataclass(frozen=True)
class Error:
    code: str
    message: str
    retryable: bool
    details: dict[str, str]

    def to_bytes(self) -> bytes:
        return _pack_map(
            {
                "code": _pack_str(self.code),
                "message": _pack_str(self.message),
                "retryable": _pack_bool(self.retryable),
                "details": _pack_str_map(self.details),
            }
        )

    @staticmethod
    def from_dict(d: dict) -> "Error":
        _no_unknown(d, frozenset({"code", "message", "retryable", "details"}))
        return Error(
            code=_str(_req(d, "code"), "code"),
            message=_str(_req(d, "message"), "message"),
            retryable=_bool(_req(d, "retryable"), "retryable"),
            details=_str_map(_req(d, "details"), "details"),
        )


# --------------------------------------------------------------------------
# Remaining small request payloads
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class HeartbeatRequest:
    lease_id: bytes

    def to_bytes(self) -> bytes:
        return _pack_map({"lease_id": _pack_bin(self.lease_id)})

    @staticmethod
    def from_dict(d: dict) -> "HeartbeatRequest":
        _no_unknown(d, frozenset({"lease_id"}))
        return HeartbeatRequest(lease_id=_bin(_req(d, "lease_id"), 16, "lease_id"))


@dataclass(frozen=True)
class CancelRequest:
    owner_id: bytes

    def to_bytes(self) -> bytes:
        return _pack_map({"owner_id": _pack_bin(self.owner_id)})

    @staticmethod
    def from_dict(d: dict) -> "CancelRequest":
        _no_unknown(d, frozenset({"owner_id"}))
        return CancelRequest(owner_id=_bin(_req(d, "owner_id"), 16, "owner_id"))


@dataclass(frozen=True)
class ResumeResultsRequest:
    owner_id: bytes
    after_cursor: int
    limit: int

    def to_bytes(self) -> bytes:
        return _pack_map(
            {
                "owner_id": _pack_bin(self.owner_id),
                "after_cursor": _pack_u64(self.after_cursor),
                "limit": _pack_u32(self.limit),
            }
        )

    @staticmethod
    def from_dict(d: dict) -> "ResumeResultsRequest":
        _no_unknown(d, frozenset({"owner_id", "after_cursor", "limit"}))
        return ResumeResultsRequest(
            owner_id=_bin(_req(d, "owner_id"), 16, "owner_id"),
            after_cursor=_uint(_req(d, "after_cursor"), 64, "after_cursor"),
            limit=_uint(_req(d, "limit"), 32, "limit"),
        )


@dataclass(frozen=True)
class ObjectPutRequest:
    transfer_id: bytes
    codec: str
    size: int
    sha256: bytes

    def to_bytes(self) -> bytes:
        return _pack_map(
            {
                "transfer_id": _pack_bin(self.transfer_id),
                "codec": _pack_str(self.codec),
                "size": _pack_u64(self.size),
                "sha256": _pack_bin(self.sha256),
            }
        )

    @staticmethod
    def from_dict(d: dict) -> "ObjectPutRequest":
        _no_unknown(d, frozenset({"transfer_id", "codec", "size", "sha256"}))
        return ObjectPutRequest(
            transfer_id=_bin(_req(d, "transfer_id"), 16, "transfer_id"),
            codec=_str(_req(d, "codec"), "codec"),
            size=_uint(_req(d, "size"), 64, "size"),
            sha256=_bin(_req(d, "sha256"), 32, "sha256"),
        )


@dataclass(frozen=True)
class ObjectGetRequest:
    transfer_id: bytes
    object: ObjectRef

    def to_bytes(self) -> bytes:
        return _pack_map(
            {
                "transfer_id": _pack_bin(self.transfer_id),
                "object": self.object.to_bytes(),
            }
        )

    @staticmethod
    def from_dict(d: dict) -> "ObjectGetRequest":
        _no_unknown(d, frozenset({"transfer_id", "object"}))
        return ObjectGetRequest(
            transfer_id=_bin(_req(d, "transfer_id"), 16, "transfer_id"),
            object=ObjectRef.from_dict(_as_map(_req(d, "object"), "object")),
        )


@dataclass(frozen=True)
class ObjectChunk:
    transfer_id: bytes
    sequence: int
    data: bytes
    eof: bool

    def to_bytes(self) -> bytes:
        return _pack_map(
            {
                "transfer_id": _pack_bin(self.transfer_id),
                "sequence": _pack_u64(self.sequence),
                "data": _pack_bin(self.data),
                "eof": _pack_bool(self.eof),
            }
        )

    @staticmethod
    def from_dict(d: dict) -> "ObjectChunk":
        _no_unknown(d, frozenset({"transfer_id", "sequence", "data", "eof"}))
        return ObjectChunk(
            transfer_id=_bin(_req(d, "transfer_id"), 16, "transfer_id"),
            sequence=_uint(_req(d, "sequence"), 64, "sequence"),
            data=_bin_any(_req(d, "data"), "data"),
            eof=_bool(_req(d, "eof"), "eof"),
        )


@dataclass(frozen=True)
class StealRequest:
    requester_node: str
    labels: dict[str, str]
    limit: int

    def to_bytes(self) -> bytes:
        return _pack_map(
            {
                "requester_node": _pack_str(self.requester_node),
                "labels": _pack_str_map(self.labels),
                "limit": _pack_u32(self.limit),
            }
        )

    @staticmethod
    def from_dict(d: dict) -> "StealRequest":
        _no_unknown(d, frozenset({"requester_node", "labels", "limit"}))
        return StealRequest(
            requester_node=_str(_req(d, "requester_node"), "requester_node"),
            labels=_str_map(_req(d, "labels"), "labels"),
            limit=_uint(_req(d, "limit"), 32, "limit"),
        )


@dataclass(frozen=True)
class StatusRequest:
    def to_bytes(self) -> bytes:
        return _pack_map({})

    @staticmethod
    def from_dict(d: dict) -> "StatusRequest":
        _no_unknown(d, frozenset())
        return StatusRequest()


# --------------------------------------------------------------------------
# encode_payload / decode_payload dispatch
# --------------------------------------------------------------------------

_ALLOWED_TYPES: dict[MessageType, tuple[type, ...]] = {
    MessageType.SUBMIT: (TaskEnvelope, ForwardedTask),
    MessageType.PULL: (PullRequest,),
    MessageType.TASK: (LeasedTask,),
    MessageType.HEARTBEAT: (HeartbeatRequest,),
    MessageType.RESULT: (ResultNotification,),
    MessageType.CANCEL: (CancelRequest,),
    MessageType.COMPLETE: (Completion, ForwardedCompletion),
    MessageType.STEAL: (StealRequest,),
    MessageType.ACK: (Ack,),
    MessageType.STATUS: (StatusRequest, StatusSnapshot),
    MessageType.RESUME_RESULTS: (ResumeResultsRequest,),
    MessageType.ERROR: (Error,),
    MessageType.OBJECT_PUT: (ObjectPutRequest,),
    MessageType.OBJECT_GET: (ObjectGetRequest,),
    MessageType.OBJECT_CHUNK: (ObjectChunk,),
    MessageType.HELLO: (Hello,),
    MessageType.TASK_QUERY: (TaskQuery, TaskSnapshot),
    MessageType.REGISTER_TASKS: (TaskRegistration,),
}


def encode_payload(message_type: MessageType, value: Any) -> bytes:
    allowed = _ALLOWED_TYPES.get(message_type)
    if allowed is None or not isinstance(value, allowed):
        raise ProtocolDecodeError(
            INVALID_MESSAGE,
            f"{type(value).__name__} is not valid for {message_type.name}",
        )
    payload = value.to_bytes()
    # The encoder and decoder share the same strict schema contract. A
    # round-trip validation prevents locally constructed dataclasses from
    # emitting bytes that the peer would reject.
    decode_payload(message_type, payload)
    return payload


_DECODE_KEYSETS: dict[
    MessageType, tuple[tuple[frozenset[str], Callable[[dict], Any]], ...]
] = {
    MessageType.SUBMIT: (
        (_TASK_ENVELOPE_KEYS, TaskEnvelope.from_dict),
        (frozenset({"transfer_id", "origin_node", "task"}), ForwardedTask.from_dict),
    ),
    MessageType.COMPLETE: (
        (frozenset({"lease_id", "result", "failure"}), Completion.from_dict),
        (
            frozenset(
                {"transfer_id", "remote_node", "remote_attempt", "result", "failure"}
            ),
            ForwardedCompletion.from_dict,
        ),
    ),
    MessageType.TASK_QUERY: (
        (frozenset({"owner_id", "task_ids"}), TaskQuery.from_dict),
        (frozenset({"tasks"}), TaskSnapshot.from_dict),
    ),
}

_SIMPLE_DECODERS: dict[MessageType, Callable[[dict], Any]] = {
    MessageType.PULL: PullRequest.from_dict,
    MessageType.TASK: LeasedTask.from_dict,
    MessageType.HEARTBEAT: HeartbeatRequest.from_dict,
    MessageType.RESULT: ResultNotification.from_dict,
    MessageType.CANCEL: CancelRequest.from_dict,
    MessageType.STEAL: StealRequest.from_dict,
    MessageType.ACK: Ack.from_dict,
    MessageType.RESUME_RESULTS: ResumeResultsRequest.from_dict,
    MessageType.ERROR: Error.from_dict,
    MessageType.OBJECT_PUT: ObjectPutRequest.from_dict,
    MessageType.OBJECT_GET: ObjectGetRequest.from_dict,
    MessageType.OBJECT_CHUNK: ObjectChunk.from_dict,
    MessageType.HELLO: Hello.from_dict,
    MessageType.REGISTER_TASKS: TaskRegistration.from_dict,
}


def decode_payload(message_type: MessageType, payload: bytes) -> Any:
    raw = _as_map(_unpack_strict(payload), "payload")

    if message_type == MessageType.STATUS:
        # StatusSnapshot is the only forward-compatible schema (extra keys
        # allowed); disambiguate from the empty StatusRequest by presence
        # of its required keys rather than an exact key-set match.
        if not raw:
            return StatusRequest.from_dict(raw)
        if _STATUS_SNAPSHOT_KEYS.issubset(raw.keys()):
            return StatusSnapshot.from_dict(raw)
        raise ProtocolDecodeError(
            INVALID_MESSAGE, "payload does not match STATUS request or response"
        )

    keyset_table = _DECODE_KEYSETS.get(message_type)
    if keyset_table is not None:
        for required_keys, decoder in keyset_table:
            if frozenset(raw) == required_keys:
                return decoder(raw)
        raise ProtocolDecodeError(
            INVALID_MESSAGE,
            f"payload key set does not match any {message_type.name} variant",
        )

    decoder = _SIMPLE_DECODERS.get(message_type)
    if decoder is not None:
        return decoder(raw)

    raise ProtocolDecodeError(
        INVALID_MESSAGE, f"no decoder registered for {message_type.name}"
    )
