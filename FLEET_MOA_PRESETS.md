# Fleet MoA Presets — EMHERMES (PMOVES fleet configuration record)

Documentation, not runtime code. The Mixture-of-Agents preset `EMHERMES` lives
in each node operator's Hermes profile `config.yaml` under `moa.presets`;
this file records the fleet-canonical seed shape and how the harness resolves
it. Hermes itself never reads `HYPERAGINT_*` variables (verified: zero
references in this tree at `8493c4f7`); the HyPeRAGInT registry tooling
injects per-node configuration into the agent environment at ACP handshake.

## EMHERMES preset (verbatim, profile `pmoves-hermes-elder`, read 2026-10-04)

```yaml
moa:
  presets:
    EMHERMES:
      enabled: true
      reference_models:
        - provider: zai
          model: glm-5.3-flash
          enabled: true
      aggregator:
        provider: zai
        model: glm-5.3-flash
      degraded_reference_policy: loud
      fanout: per_iteration
```

`moa.max_tokens: 4096` is set at the top-level `moa:` scope, so the preset
inherits it; it is not preset-specific.

## Resolution semantics

- `agent/moa_loop.py` resolves presets via
  `hermes_cli/moa_config.resolve_moa_preset` — config-layer; there is no
  hardcoded preset registry in code.
- Preset resolution is validated and cached per config-file signature;
  malformed preset blocks are rejected at resolve time (`moa_config.py`).
- `degraded_reference_policy: loud` — a failed reference call is surfaced,
  never silently skipped.
- `fanout: per_iteration` — reference context is gathered before every model
  iteration.

## Per-node variants

Fleet per-node model variants (z890 / 5090 / spark local-Ollama suits, elder
cloud-first) are owned by the HyPeRAGInT registry entry:
`hyperagint-acp/env/` in POWERFULMOVES/PMOVES-registry (`env.shared` +
one `suit-<node>.env` overlay per node, overlay wins). That directory is the
source of truth for node deltas. EMHERMES is the seed preset with zero model
deltas; elder is its reference deployment.

## Provenance

- Authored: 2026-10-04, elder chat session (EMHERMES via moa).
- Source: live read of profile `pmoves-hermes-elder` `config.yaml`.
- Fleet registry: POWERFULMOVES/PMOVES-registry `hyperagint-acp` entry
  (grounding commit `4b05abb`).
