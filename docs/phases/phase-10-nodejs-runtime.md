# Phase 10 — Node.js Worker and SDK

## Goal

Implement a TypeScript-first Node.js worker and submitting client against the
unchanged Phase 1 protocol. This phase proves that portable registered tasks can
be implemented or submitted outside Python without changing the Go agent's task,
lease, object, result, or cluster state models.

This is a post-v0.1 feature. It does not block the Python-first release gate.

## Prerequisites

Phases 1–4 are complete, and the Phase 3 worker conformance suite is published as
the shared Protobuf schema and behavioral scenarios. The agent already
supports named worker pools, capability registration, portable values, leases,
object transfer, and replayable Runtime results.

## Supported Surface

- Supported Node.js versions are declared from active LTS releases at
  implementation time; exact versions are pinned in CI and package metadata.
- TypeScript types and ESM are the primary interface. Published JavaScript and
  declarations must work without a TypeScript runtime dependency.
- The worker supports `invocation="value"`, `msgpack`, and `bytes` only.
  `python_args`, `cloudpickle`, and inline executable functions are rejected.
- Protocol uint64/int64 values use `bigint`. Conversion to JavaScript `number`
  performs a safe-integer range check and never loses precision silently.

## Public API

```typescript
const worker = new TaskwireWorker({ socket: "/run/taskwire/agent.sock" });

worker.task<ResizeInput, ResizeResult>(
  { name: "image.resize", version: "2" },
  async (input, context) => resize(input, context.signal),
);

await worker.run();
```

The submitting client exposes `TaskwireClient`, `submit`, `TaskHandle`, result
timeouts, cancellation, reattach, owner persistence, and result replay. Client
submission is by explicit name/version and one portable value; it does not require
the worker implementation package to be imported.

## Implementation Contract

- Load and validate the complete registry before connecting; send worker HELLO
  and generation 1 registration before PULL. Reconnect republishes the registry.
- Use one socket reader, one serialized writer, request-ID dispatch, bounded
  object transfers, deadlines, and checksum verification matching Phase 3.
- Begin heartbeat before handler invocation. Lease loss aborts the supplied
  `AbortSignal`; because arbitrary JavaScript may ignore it, any later completion
  remains fenced by the agent.
- Map thrown values and rejected promises to bounded language-neutral `Failure`
  data. Do not require JavaScript error reconstruction to inspect a failure.
- Preserve Buffer as msgpack binary and distinguish null from missing/undefined.
  Reject undefined, symbols, functions, unsafe integers, cyclic values, NaN,
  infinity, extension types, and non-string map keys before submission/completion.
- Package code must not spawn, download, or embed a second Go agent. An explicit
  socket is supported; optional local-agent discovery must be deterministic and
  separately documented from the Python wheel's bundled-binary behavior.

## Files

```text
sdk/nodejs/package.json
sdk/nodejs/src/{protocol,codec,client,worker,registry,errors}.ts
sdk/nodejs/test/{protocol,client,worker,conformance}.test.ts
```

## Required Tests

- Generate bindings from the Phase 1 `.proto` and consume every portable-value
  vector without a Node-generated alternate fixture.
- Register alongside synthetic Python and Go workers; only compatible task
  implementations receive leases, with labels applied after compatibility.
- Execute success, thrown Error, rejected non-Error, object-backed input/result,
  heartbeat, lost lease, cancellation, worker crash/restart, and lost COMPLETE ACK.
- Prove uint64 boundaries round-trip as bigint and unsafe number conversion fails.
- Prove malformed frames, duplicate keys, oversized lengths, unknown fields, and
  unsupported codecs fail closed without unbounded allocation or process exit.
- Client reconnect, owner/cursor replay, reattach, cancellation race, and
  first-terminal-wins match the Python SDK behavior.
- Test final npm package contents in a clean project using the supported Node.js
  version matrix and both generated JavaScript and type declarations.

## Exit Gate

Phase 10 is complete when the published-package candidate passes the shared worker
and protocol conformance suites, Node.js and Python implementations of the same
portable task are interchangeable to the scheduler, client replay survives
restart, and no Phase 1 wire/configuration field or Go storage schema changes were
required.

---

## Implementation Guide

> **Post-v0.1.** Requires Phases 1–4 + Phase 3 portable conformance fixtures.
> No `python_args` / `cloudpickle` / inline functions.

### Package skeleton

```text
sdk/nodejs/
  package.json
  tsconfig.json
  src/
    protocol/{frame,messages,session}.ts
    codec/portable.ts
    client.ts
    worker.ts
    registry.ts
    errors.ts
    index.ts
  test/
    protocol.test.ts
    worker.test.ts
    client.test.ts
    conformance.test.ts
```

### Worker API (implement to match)

```typescript
import { TaskwireWorker } from "@taskwire/sdk";

const worker = new TaskwireWorker({ socket: process.env.TASKWIRE_SOCKET! });

worker.task(
  { name: "image.resize", version: "2" },
  async (input: { path: string; width: number }, ctx) => {
    ctx.signal.throwIfAborted(); // cooperative lease-loss / shutdown
    return await resize(input);
  },
);

await worker.run();
```

### Client API

```typescript
import { TaskwireClient } from "@taskwire/sdk";

const client = await TaskwireClient.connect({ socket, ownerId? });
const handle = await client.submit("image.resize", "2", { path, width: 256 });
const result = await handle.result({ timeoutMs: 30_000 });
await client.close();
```

### Implementation loop (mirror Phase 3)

```typescript
// 1. HELLO worker (runtime: "nodejs", codecs: ["msgpack","bytes"])
// 2. REGISTER_TASKS generation 1
// 3. PULL → TASK | empty
// 4. start heartbeat (ttl/3)
// 5. OBJECT_GET input if needed; verify sha256
// 6. invoke handler
// 7. OBJECT_PUT result; COMPLETE
// 8. on reconnect: new capability namespace, full re-register
```

### Portable codec rules (strict)

```typescript
// Accept: null, boolean, bigint in int64 range, number finite (safe int check
// when converting from bigint), string, Buffer/Uint8Array, array, object with
// string keys only.
// Reject: undefined, symbol, function, Date, NaN, Infinity, non-string keys,
// cyclic structures, unsafe integer Number conversions.
```

### Agent pool config

```yaml
workers:
  pools:
    - name: node-default
      runtime: nodejs
      command: ["node", "dist/worker.js"]
      count: 2
      working_directory: "/app"
      labels: {workload: general}
```

### Tests

```bash
cd sdk/nodejs && npm test
# shared vectors:
#   consume testdata/portable/* without regenerating alternate fixtures
# multi-runtime: register Node + synthetic Python capabilities; only compatible leases
```

### Done checklist

- [ ] Published package works in clean project (no monorepo path hacks)  
- [ ] Interchangeable with Python for same portable `name@version`  
- [ ] bigint boundaries tested  
- [ ] No agent schema/wire changes  
- [ ] Fail closed on malformed frames (no process crash)  

### Review request

```text
Please review Phase 10.
Package: sdk/nodejs tarball / npm pack
Commands: npm test; multi-runtime lease tests; clean install
Gaps: ...
```
