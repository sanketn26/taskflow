# Client libraries

Each library speaks the Taskwire protocol ([`proto/`](../proto/)) to the
controller; none touches the database. The same library submits tasks and runs
them. See [Phase 2](../docs/phases/phase-2-controller-api.md) for the protocol
and its conformance rules.

| Directory | Language | Status |
|---|---|---|
| [`python/`](python/) | Python 3.11+ | First library; skeleton only ([Phase 3](../docs/phases/phase-3-python-library.md)) |
| [`go/`](go/) | Go | Planned |
| [`rust/`](rust/) | Rust | Planned |
| [`java/`](java/) | Java | Planned |

Each directory is self-contained: its own build files, tests and release
version. A new library is supported once it passes `taskwire library-check`
(Phase 6); until then it is experimental.

The Go library will be its own Go module (`clients/go/go.mod`), separate from
the controller's, so applications importing it do not pull in controller
dependencies.
