# Phase 11 — Go Worker and SDK

## Goal

Implement an embeddable Go worker and submitting client against the unchanged
Phase 1 protocol. The SDK reuses protocol types where appropriate but has no
dependency on agent internals, storage backends, schedulers, or command packages.

This is a post-v0.1 feature. It does not block the Python-first release gate.

## Prerequisites

Phases 1–4 are complete and the Phase 3 language-independent worker conformance
suite is stable. Phase 10 is not a dependency: Node.js and Go bindings must both be
independent consumers of the same protocol rather than implementations of each
other.

## Public API

```go
worker := taskwire.NewWorker(taskwire.WorkerOptions{Socket: socketPath})
taskwire.Register(worker, "image.resize", "2",
    func(ctx context.Context, in ResizeInput) (ResizeResult, error) {
        return resize(ctx, in)
    })
if err := worker.Run(ctx); err != nil { /* handle shutdown/startup error */ }
```

The submitting client exposes `Client`, `Submit`, `Handle`, `Wait`, `Cancel`, and
`Reattach`. Submission uses explicit task name/version and one portable value.
Owner identity can be supplied by the caller for result replay after process
restart.

## Implementation Contract

- `sdk/go/taskwire` may import `agent/pkg/protocol` only if that package remains a
  dependency-light public protocol module. It must not import `agent/internal/*`.
- Generic registration derives a portable adapter for explicit supported field
  types. Unsupported Go kinds, architecture-sized overflow, cyclic values,
  non-string map keys, and implicit custom marshaling are rejected unless the
  application explicitly selects `bytes` and owns that schema.
- Load the complete registry, open the Work stream with generation 1 registration,
  then PULL. Reconnect republishes capabilities before claiming.
- Handler contexts are cancelled on shutdown, cancellation, or detected lease
  loss. Context cancellation is cooperative; the active lease still fences late
  completion.
- Panics are recovered at the task boundary and converted to bounded failures;
  agent/protocol failures remain ordinary SDK errors. Panic recovery must not hide
  process-corrupting startup or registry errors.
- The worker uses bounded object streaming, checksum validation, one serialized
  writer, request dispatch, heartbeat, and idempotent completion semantics from
  the shared conformance contract.
- The library never starts an embedded agent implicitly. Applications configure
  a socket or explicitly manage a separate `taskwire-agent` process.

## Files

```text
sdk/go/taskwire/{client,worker,registry,codec,errors}.go
sdk/go/taskwire/*_test.go
```

## Required Tests

- Reuse the generated Phase 1 Protobuf bindings and consume every portable-value vector.
- Register alongside synthetic Python and Node.js workers; capability and label
  filtering choose only compatible implementations.
- Execute success, returned error, panic, object-backed input/result, heartbeat,
  context cancellation, lost lease, worker crash/restart, and lost completion response.
- Cover signed/unsigned 64-bit boundaries, pointer/nil behavior, struct field
  mapping, binary values, and rejection of unsupported Go values.
- Run protocol/SDK unit tests with `go test -race`; malformed and oversized input
  must never panic or allocate beyond configured limits.
- Client result replay, reattach, cancellation races, and first-terminal-wins
  match Python and Node.js observable behavior.
- Test a clean external module importing the SDK so internal repository paths or
  replace directives cannot accidentally become packaging requirements.

## Exit Gate

Phase 11 is complete when an external Go module passes the shared protocol and
worker conformance suites, Go and Python implementations of one portable task are
interchangeable to the scheduler, client replay survives restart, race tests are
green, and no Phase 1 wire/configuration field or Go agent storage schema changes
were required.

---

## Implementation Guide

> **Post-v0.1.** Independent of Phase 10. SDK may import `agent/pkg/protocol`
> only if that package stays dependency-light; **never** import `agent/internal/*`.

### Module layout

```text
sdk/go/taskwire/
  go.mod                 # module path e.g. github.com/sanketn26/taskwire/sdk/go/taskwire
  client.go
  worker.go
  registry.go
  codec.go
  errors.go
  *_test.go
```

### Worker registration

```go
package main

import (
	"context"

	"github.com/sanketn26/taskwire/sdk/go/taskwire"
)

func main() {
	w := taskwire.NewWorker(taskwire.WorkerOptions{
		Socket: os.Getenv("TASKWIRE_SOCKET"),
	})
	taskwire.Register(w, "image.resize", "2",
		func(ctx context.Context, in ResizeInput) (ResizeResult, error) {
			return resize(ctx, in)
		},
	)
	if err := w.Run(context.Background()); err != nil {
		log.Fatal(err)
	}
}
```

### Client

```go
c, err := taskwire.Dial(taskwire.ClientOptions{Socket: socketPath, OwnerID: owner})
h, err := c.Submit(ctx, "image.resize", "2", ResizeInput{Path: p, Width: 256})
out, err := h.Wait(ctx) // or WaitTimeout
// Cancel, Reattach parallel to Python Runtime
```

### Codec rules

```go
// Map supported field types → portable msgpack.
// Reject: chan, func, unsafe pointers, non-string map keys, cycles,
// architecture-dependent int overflow, implicit encoding/json surprises.
// Explicit bytes codec for opaque application schemas.
```

### Worker loop (same as Phase 3 contract)

```go
// Work stream: WorkerRegistration(runtime=go) → register → pull
// heartbeat on lease; ctx cancel on shutdown/lease loss (cooperative)
// recover panic → Failure{code: task_exception} (do not hide registry/startup panics)
// COMPLETE fenced; stale_lease → stop
```

### External module test

```bash
# from a temp module outside the monorepo:
go mod init example.com/twtest
go get github.com/sanketn26/taskwire/sdk/go/taskwire@<commit>
# no replace directives required
go test ./...
```

### Race + conformance

```bash
cd sdk/go/taskwire && go test -race ./...
# portable vectors from testdata/portable
# multi-runtime capability filtering with synthetic python/nodejs workers
```

### Done checklist

- [ ] External module import works  
- [ ] Interchangeable portable task with Python  
- [ ] Client replay/reattach  
- [ ] `-race` green; no unbounded alloc on bad messages  
- [ ] No wire/schema changes  

### Review request

```text
Please review Phase 11.
Module: sdk/go/taskwire
Commands: go test -race ./...; external module smoke
Gaps: ...
```
