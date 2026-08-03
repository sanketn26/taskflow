# Protocol code generation

`taskwire/v1/control.proto` is the authoritative control-plane contract: it
defines both the `TaskwireControl` gRPC service and every message it carries.
Generated Go and Python bindings are committed so installing Taskwire does not
require `protoc`.

The checked-in Python bindings were generated with `grpcio-tools` 1.83.0 and
require `protobuf >= 6.33.5, < 7` and `grpcio >= 1.83`. The Go bindings use
`protoc-gen-go` 1.36.11 and `protoc-gen-go-grpc` 1.6.2.

Any language with a gRPC implementation can generate a working client from this
file alone — no hand-written framing code is involved.

## Go

```sh
protoc -I proto \
  --go_out=. \
  --go_opt=module=github.com/sanketn26/taskwire \
  --go-grpc_out=. \
  --go-grpc_opt=module=github.com/sanketn26/taskwire \
  proto/taskwire/v1/control.proto
```

## Python

`grpcio-tools` bundles its own `protoc`, so this needs no separate install:

```sh
python -m grpc_tools.protoc -I proto \
  --python_out=python/taskwire/protocol/pb \
  --pyi_out=python/taskwire/protocol/pb \
  --grpc_python_out=python/taskwire/protocol/pb \
  proto/taskwire/v1/control.proto
```

The Python generator creates a `taskwire/v1/` output prefix based on the proto
source path. Move `control_pb2.py`, `control_pb2.pyi`, and `control_pb2_grpc.py`
into `python/taskwire/protocol/pb/` after generation, then fix the import at the
top of `control_pb2_grpc.py` to match the flattened layout:

```python
from taskwire.protocol.pb import control_pb2 as taskwire_dot_v1_dot_control__pb2
```

Do not otherwise hand-edit generated files.
