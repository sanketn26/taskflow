# Testing Harness Summary

This file is a non-normative orientation guide. Harness construction, exact scenarios, assertions, and phase exit criteria are embedded in the phase documents; implementation does not depend on this file.

## Test Strategy

- Unit tests cover pure codecs, schemas, state machines, and helpers without processes.
- Integration tests run built artifacts through isolated temporary state/object paths.
- Race and conformance suites validate storage implementations and connection lifecycle.
- Fuzzers attack frames and envelopes with a committed seed corpus.
- Seeded chaos tests inject process, network, storage, and resource faults and retain a reproducible timeline.
- Release smoke tests install the final wheel/artifacts into clean environments.

## Shared Assertions

Every applicable scenario checks concrete outcomes rather than relying on shorthand invariant IDs:

- every acknowledged task is terminal or still accounted for under the configured durability guarantee;
- every Future reaches a result or documented exception within a deadline;
- duplicates occur only after a recorded lease/ownership event that permits them;
- agents survive malformed input and expected dependency failures;
- processes, sockets, connections, temporary objects, transfers, and outbox rows remain bounded;
- submitted work equals terminal plus queued/leased/in-transfer work.

## Harness Growth by Phase

- Phase 0 creates `AgentHarness`, isolated resources, deadline polling, logs, markers, and seeded timelines.
- Phase 1 adds generated Protobuf schema conformance, live cross-language
  compatibility checks, the shared configuration fixture, and compact in-code
  malformed-input/fuzz seeds.
- Phase 2 adds raw IPC, storage recovery, failpoints, and agent kill/restart.
- Phase 3 adds the append-only execution ledger and worker SIGSTOP/SIGKILL controls.
- Phase 4 adds Runtime disconnect/reconnect, duplicate/replay, cancellation, and shutdown stress.
- Phase 5 adds authenticated `ClusterHarness` plus directional network partitions and churn.
- Phase 6 adds PostgreSQL/S3 conformance suites and shared-backend, credential/connection-loss chaos.
- Phase 7 adds Kafka testcontainers and outbox outage/recovery.
- Phase 8 adds resource chaos, soak, upgrade, and final-artifact release gates.
- Phase 9 executes documentation examples from shipped artifacts.

No readiness or convergence test uses an unconditional sleep; all waits poll observable state with a deadline and attach logs plus the chaos timeline on failure.
