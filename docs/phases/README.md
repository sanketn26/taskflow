# Taskwire Phase Implementation Guide

This directory is the **authoritative implementation contract**. Each phase file
defines goals, deliverables, tests, and exit gates. The **Implementation Guide**
section at the bottom of each phase is the hands-on path: files to create, code
skeletons, commands, and a done checklist for review.

## Delivery order

```text
MVP (do in order)
  Phase 0  Repository, packaging, harness     ← baseline (largely done)
  Phase 1  Protocol + configuration           ← baseline (largely done)
  Phase 2  Go agent core (single node)        ← next major work
  Phase 3  Python worker + E2E
  Phase 4  Python SDK                         ← single-node pre-alpha gate

Post-MVP (independent gates; order recommended)
  Phase 5  Clustering
  Phase 6  Distributed storage (Postgres/S3)
  Phase 7  Kafka outbox
  Phase 8  Hardening and release
  Phase 9  Examples and adoption docs

Further runtimes / ops
  Phase 10 Node.js worker + SDK
  Phase 11 Go worker + SDK
  Phase 12 Admin console
```

If a vision or architecture doc disagrees with a phase, **the phase wins**.

## Status snapshot (repo as of this guide)

| Phase | Status | Notes |
|------:|--------|-------|
| 0 | **Baseline present** | Wheel, harness, version parity, stub agent lifecycle |
| 1 | **Baseline present** | Frames, Protobuf, session, config loaders, HELLO/STATUS stub |
| 2 | **Not started** | `stubserver` only; no real state/object stores or task IPC |
| 3–4 | **Not started** | Package roots exist; no worker/SDK implementation |
| 5–12 | **Not started** | Spec-only (Phase 12 is a detailed product contract) |

Confirm status yourself with:

```bash
make unit && make integration && make smoke-wheel
ls agent/internal/stubserver agent/internal/state 2>/dev/null
ls python/taskwire/worker python/taskwire/runtime.py 2>/dev/null
```

## How to implement a phase

1. **Read** the full phase doc (contract first), then the Implementation Guide.
2. **Branch** from `main`: `git checkout -b phase-N-<short-name>`.
3. **Implement** in the order listed in the guide (tests that fail first when practical).
4. **Self-check** against the phase’s Required Tests + Exit Gate + Done checklist.
5. **Commit** with a clear message (e.g. `phase-2: memory TaskStateStore + conformance`).
6. **Request review** (see below). Do not start the next phase until review passes
   or residual issues are explicitly deferred.

### Rules that apply to every phase

- Prefer small, reviewable commits over one giant dump.
- Do not invent wire fields, config keys, or storage semantics outside the phase.
- Keep Phases 0–1 green: `make format lint unit integration smoke-wheel`.
- No `callback_addr`, direct worker→app RESULT, Bolt/WAL queues, or Kafka as Future source.
- At-least-once only; app-level idempotency for side effects.
- Never log payloads, owner bearer IDs, DSNs with secrets, or encryption keys.

## After you finish a phase — how to ask for review

When you believe a phase is done, open a conversation and paste something like:

```text
Please review Phase N.

Branch: phase-N-...
What I implemented: <2–4 bullets>
Commands I ran:
  make format
  make lint
  make unit
  make integration
  make smoke-wheel
  <any phase-specific suites>

Known gaps / intentional deferrals: <none | list>
```

I will review against:

1. Phase exit gate and required tests  
2. Diff vs phase file map and forbidden patterns  
3. Failure-path / recovery / race coverage  
4. Packaging contracts (`__version__`, agent discovery, wheel smoke)  
5. Protocol/config parity (Python ↔ Go) where applicable  

Expect one of: **pass**, **pass with nits**, or **blockers** (must fix before Phase N+1).

## Recommended local loop

```bash
# edit → format → unit fast loop
make format && make unit

# after agent or harness changes
make build-agent && make integration

# before requesting review
make format lint unit integration smoke-wheel
```

## Where to start tomorrow

**Phase 2** is the next real product surface. Follow
[phase-2-sidecar-core.md](phase-2-sidecar-core.md) Implementation Guide end-to-end
(memory stores → SQLite/filesystem → IPC → replace stubserver).

Phases 0 and 1 Implementation Guides are residual-check / orientation only unless
your tree is missing those tests.
