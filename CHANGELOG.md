# Change Log
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](http://keepachangelog.com/)
and this project adheres to [Semantic Versioning](http://semver.org/).

## [Unreleased]

### Changed

- **Design reset.** Taskwire is now a Kubernetes-native library with PostgreSQL
  as the production backend and no agent on the task path. Task semantics are
  defined by a backend-neutral interface with a conformance suite and a
  start-up assertion.
  See `docs/decisions.md`.
- Vision, architecture, and phase documents rewritten for the new design. The
  plan is now nine phases (0–8).
- `Makefile`, CI, and `pyproject.toml` are Python-only. Runtime dependencies are
  empty until the phase that first needs them.

### Removed

- The Go agent, the gRPC control protocol and generated bindings, and the Go
  module.
- The Python gRPC client, protocol validation, YAML configuration loader,
  agent discovery, and native-acceleration seam, with their tests.
- The agent test harness. `harness.wait_until` and `harness.ChaosTimeline`
  remain.
- Phase documents for the agent core, clustering, distributed storage
  adapters, the Kafka outbox, Node.js and Go runtimes, and the admin console.
