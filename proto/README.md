# Taskwire protocol

The gRPC definitions for the controller API will live here, as
`proto/taskwire/v1/*.proto` ([Phase 2](../docs/phases/phase-2-controller-api.md)).
The controller and every library in [`clients/`](../clients/) generate code from
these files; CI checks that generated code has not drifted from them.

Empty until Phase 2. The build step that generates Go and Python code (and its
CI drift check) is added together with the first `.proto` file, because there is
nothing to generate yet.
