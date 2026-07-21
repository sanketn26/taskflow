# Protocol code generation

`taskwire/v1/control.proto` is the authoritative control-plane schema. Generated
Go and Python bindings are committed so installing Taskwire does not require
`protoc`.

The checked-in Python bindings were generated with Protobuf compiler 33.5 and
require `protobuf >= 6.33.5, < 7`. The Go bindings use `protoc-gen-go` 1.36.11.

```sh
protoc -I proto \
  --go_out=. \
  --go_opt=module=github.com/sanketn26/taskwire \
  proto/taskwire/v1/control.proto

protoc -I proto \
  --python_out=python/taskwire/protocol/pb \
  --pyi_out=python/taskwire/protocol/pb \
  proto/taskwire/v1/control.proto
```

The Python generator creates a `taskwire/v1/` output prefix based on the proto
source path. Move `control_pb2.py` and `control_pb2.pyi` into
`python/taskwire/protocol/pb/` after generation. Do not hand-edit generated files.
