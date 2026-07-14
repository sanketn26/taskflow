# Implementation Plan Review — Resolved Decisions

This review is retained as a non-normative record. Its actionable corrections have been incorporated into the phase documents, so implementation does not need to consult this file.

## Resolved Corrections

- Added a packaging/build/test Phase 0 before protocol work.
- Defined the SUBMIT ACK as the durability boundary and bounded its wait.
- Replaced worker-to-application callbacks with agent-relayed owner/cursor result replay.
- Replaced Bolt/WAL-decorated queues with transactional task-state storage and immutable object storage.
- Made exact task name/version the production identity; inline functions are development-only.
- Added explicit object references and threshold-based inline values.
- Made lease completion fenced and cancellation an atomic queued-only transition.
- Required cluster authentication by default and durable forwarding ownership.
- Reframed Kafka as an origin-agent transactional outbox integration.
- Required clean-artifact, failure-path, race, fuzz, recovery, and seeded chaos gates.

## Scope Decision

Phases 0–4 form the single-node pre-alpha MVP. Phase 5 clustering and Phase 6 Kafka are independent post-MVP gates. Phase 7 releases only the features whose own exit gates pass. Phase 8 documents only shipped behavior.

Deferred items include exactly-once execution, cancellation of running/remotely owned work, automatic binary downloads, untested PostgreSQL/S3 adapters, and claims that a pure-Python heartbeat survives arbitrary GIL-holding native code.

The authoritative implementation sequence is the numbered set under `docs/phases/`.
