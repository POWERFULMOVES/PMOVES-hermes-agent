# PMOVES Bootstrap Consumer for Hermes (`pmoves_bootstrap/`)

The Hermes-side of the Mavis harness v0 (3-repo coordinated slice). A
non-breaking consumer of the `pmoves.bootstrap/v1` CGP that the
PMOVES.AI side writes (see `pmoves/tools/load_bootstrap.py` and
`pmoves/contracts/schemas/pmoves-bootstrap/v1.schema.json` in the
PMOVES.AI repo).

## Why this exists

The harness v0 (PMOVES.AI PR #2477) is a 3-repo coordinated slice:

1. **PMOVES.AI** — the writer, owns the CGP schema + example, the
   orchestrator, the BPM cron, and the canonical `load_bootstrap.py`.
2. **PMOVES-hermes-agent** (this fork) — the agent runtime. Loads
   the CGP at session init, registers PMOVES tools alongside
   Hermes's native tools, and (in a future slice) subscribes to
   `pmoves.agent.task.v1` to pick up Mavis-orchestrator tasks.
3. **PMOVES-pinokio** — the app launcher. Reads the CGP when
   launching a PMOVES-tagged Pinokio app.

This fork's role is the heaviest of the three: read the CGP, expose
PMOVES tools as first-class in the agent's toolset, and (optionally)
participate in the multi-agent orchestrator loop via NATS.

## What this slice ships

- `pmoves_bootstrap/__init__.py` — public surface
- `pmoves_bootstrap/loader.py` — CGP reader (YAML + JSON, validates
  against the vendored JSON Schema, returns a typed `Bootstrap`).
  Mirrors the PMOVES.AI side's `load_bootstrap.py` API.
- `pmoves_bootstrap/tools_bridge.py` — registers PMOVES tools in
  the session. Resolves each `bootstrap.tools` entry against the
  v0 tool registry (Python scripts + CLI binaries). The session init
  code merges these into Hermes's active toolset.
- `pmoves_bootstrap/subscriber.py` — the optional NATS subscriber
  stub. v0 is a no-op (no `nats-py` in Hermes's core deps); the
  dataclasses (`TaskEnvelope`, `ResultEnvelope`) document the wire
  contract so a future slice can wire it in without changing the
  surface.
- `pmoves_bootstrap/cgp_schema/v1.schema.json` — vendored copy of
  the PMOVES.AI schema
- `pmoves_bootstrap/cgp_schema/example.cgp.yaml` — vendored YAML
  example (Hermes has `pyyaml` in its core deps, so YAML is the
  natural format)
- `tests/test_pmoves_bootstrap.py` — 31 tests, 9 test groups
- `pmoves_bootstrap/README.md` — this file

## Non-breaking contract

The CGP is a **manifest**, not a config replacement. The consumer
fork is required to honor the 6 constraints baked into the CGP:

| Constraint | What it means for Hermes |
|------------|-------------------------|
| `no-override-existing-config` | Hermes's own config (`cli-config.yaml`, `hermes_state`) is never replaced by the CGP |
| `tagged-services-are-advisory` | The `services` block (Tailscale, RustDesk, Hostinger, Cloudflare) is a hint — missing services are skipped, not failed |
| `no-chit-bypass` | State-changing actions still go through `pmoves-chit-sign`, not directly through the CGP |
| `no-force-push` | Lane rule (this fork's PRs use rebase, never raw `--force`) |
| `no-ci-bypass` | Lane rule (no `--admin` to skip CI; admin merge override is OK for already-green PRs) |
| `preserve-existing-tools` | Hermes's existing toolset (`toolsets.py`) is preserved; the bridge adds PMOVES tools alongside, not in place of |

The non-breaking test pair:

- **No CGP present** → `load_bootstrap()` returns the stub Bootstrap
  (empty tools, empty services, all 6 constraints). `register_pmoves_tools()`
  is a no-op (no tools added). `subscribe()` is a no-op. Hermes runs
  as it does today.
- **CGP present** → the CGP is validated against the vendored schema,
  the PMOVES tools are registered alongside Hermes's native tools,
  the optional NATS subscriber can pick up Mavis-orchestrator tasks.

## Public API

```python
from pmoves_bootstrap import (
    load_bootstrap,          # the CGP reader
    stub_bootstrap,          # the no-CGP fallback Bootstrap
    export_env,              # export PMOVES_BOOTSTRAP_* env vars
    register_pmoves_tools,   # the tools_bridge
    subscribe,               # the optional NATS subscriber
    Bootstrap, Identity, Meta, BootstrapError,
)

bs = load_bootstrap()                       # real or stub
export_env(bs)                              # process.env gets PMOVES_BOOTSTRAP_*
result = register_pmoves_tools(bs=bs)       # BridgeResult(registered, skipped, disabled)
status = subscribe(target="hermes")         # SubscriberStatus (always safe; disabled in v0)
```

## Resolution order (4 sources, 1 default)

In priority order, the first one that yields a parseable CGP wins:

1. `path` arg (file path, YAML or JSON detected by content)
2. `source` arg (raw YAML/JSON string)
3. `PMOVES_BOOTSTRAP_CGP` env var (raw) or `PMOVES_BOOTSTRAP_CGP_PATH` env var (file path)
4. The vendored example at `pmoves_bootstrap/cgp_schema/example.cgp.yaml`

If none of the above yield a CGP, the stub Bootstrap is returned.

## Why YAML (in addition to JSON)

Hermes has `pyyaml==6.0.3` and `ruamel.yaml==0.18.17` already in its
core dependencies (see `pyproject.toml`). The loader accepts both
YAML and JSON, with YAML preferred when the content is YAML-shaped
(no leading `{` or `[`). The PMOVES.AI side writes the canonical
CGP in YAML for human editing; the Hermes side reads YAML natively
without a conversion step. JSON is supported for `PMOVES_BOOTSTRAP_CGP`
raw-string env vars (env vars are awkward for multi-line YAML).

## Why no `nats-py` in the v0 subscriber

`nats-py` is not in Hermes's core dependencies. Adding it would be a
meaningful change to `pyproject.toml` (the deps list warns against
adding new packages — see the "Mini Shai-Hulud" comment on the
existing `dependencies` list).

The v0 subscriber is a STUB: `subscribe()` always returns a
`SubscriberStatus` with `enabled=False` and a clear `reason`. The
dataclasses (`TaskEnvelope`, `ResultEnvelope`) document the wire
contract so a future slice that adds `nats-py` can wire it in
without changing the public surface.

To enable the real subscriber in a future slice:

1. Add `nats-py` to `pyproject.toml`'s `dependencies` list
   (exact-pinned per the existing pattern, e.g. `nats-py==2.10.0`).
2. Implement the `subscribe()` body with a real nats-py loop.
3. Set `PMOVES_SUBSCRIBER_ENABLED=true` in the session env.

The wire contract:

- Tasks arrive on `pmoves.agent.task.v1`, filtered by
  `TaskEnvelope.target == "hermes"`.
- Results go out on `pmoves.agent.result.v1` as a `ResultEnvelope`.
- BPM events arrive on `pmoves.bpm.phase.v1` and
  `pmoves.bpm.pomodoro.v1` (the orchestrator publishes these for
  observability; the subscriber doesn't respond to them, but a
  future slice might).

## Tests

```bash
pytest tests/test_pmoves_bootstrap.py -v
```

33 tests, 9 test groups:

- A. LoadFromExampleTests (5)
- B. LoadFromSourceTests (4)
- C. ValidationFailureTests (5)
- D. StubFallbackTests (2)
- E. ExportEnvTests (3)
- F. TypedAccessorTests (3)
- G. ToolsBridgeTests (6)
- H. SubscriberTests (3)
- I. Constants and subject surfaces (2)

## What this slice does NOT do (intentional, follow-up)

- **Wiring into `run_agent.py` / `cli.py`** — the actual integration
  point in Hermes's session lifecycle is a follow-up. v0 ships the
  package; the operator (or a future slice) wires `load_bootstrap()`,
  `export_env()`, and `register_pmoves_tools()` into the session
  init code.
- **Real `nats-py` subscriber** — see "Why no `nats-py` in the v0
  subscriber" above.
- **CHIT trail signing** — the `no-chit-bypass` constraint is
  honored by the loader's behavior (the stub carries it, the real
  CGP carries the operator's set), but no actual CHIT signing code
  lives in this package. The Mavis orchestrator side does the
  signing.
- **Per-session tool allow-list** — a future slice can extend
  `register_pmoves_tools()` with a per-identity allow-list (e.g.
  `minimax` gets all 10 tools, `critic` gets only `web_search` and
  `web_fetch`). v0 trusts the operator's CGP as-is.

## Cross-fork plan

This is the Hermes-side of the 3-repo Mavis harness v0 slice.
The other two PRs are:

1. `POWERFULMOVES/PMOVES.AI` PR #2477 — the writer (load_bootstrap.py
   + orchestrator.py + bpm_cron.py + 56/56 tests)
2. `POWERFULMOVES/PMOVES-pinokio` PR `feat/pmoves-app-launcher` —
   the app launcher (pmoves_loader.js + example app + 24/24 tests)

All three read the same `v1.schema.json`; the schema is the contract
that ties the three forks together.

## License

Same as the upstream NousResearch hermes-agent fork (MIT, per LICENSE
in repo root).
