"""PMOVES bootstrap CGP consumer for the PMOVES-hermes-agent fork.

The Hermes-side of the Mavis harness v0 (3-repo coordinated). A
non-breaking consumer of the ``pmoves.bootstrap/v1`` CGP that the
PMOVES.AI side writes (see ``pmoves/tools/load_bootstrap.py`` and
``pmoves/contracts/schemas/pmoves-bootstrap/v1.schema.json`` in the
PMOVES.AI repo).

This package is a NEW addition to the Hermes fork. It does not
modify any existing Hermes files (``cli.py``, ``run_agent.py``,
``toolsets.py``, etc.). The non-breaking test pair:

- No CGP present     -> ``load_bootstrap()`` returns the stub
  Bootstrap. ``register_pmoves_tools()`` is a no-op. ``subscribe()``
  is a no-op. Hermes runs as it does today.
- CGP present         -> the CGP is validated against the vendored
  schema, the PMOVES tools are registered alongside Hermes's native
  tools, the optional NATS subscriber can pick up Mavis-orchestrator
  tasks (when nats-py is in scope, future slice).

Public surface:

- ``load_bootstrap`` - the CGP reader (loader.py)
- ``stub_bootstrap`` - the no-CGP fallback Bootstrap
- ``export_env``     - export the Bootstrap as PMOVES_BOOTSTRAP_* env vars
- ``register_pmoves_tools`` - the tools_bridge (tools_bridge.py)
- ``subscribe``      - the optional NATS subscriber (subscriber.py)
- ``Bootstrap`` / ``Identity`` / ``Meta`` / ``BootstrapError`` - the typed shapes

CGP profile: ``pmoves.bootstrap/v1``
Canonical spec: PMOVES.AI's ``pmoves/docs/PMOVESCHIT/CGP_v1.0_SPECIFICATION.md``
Schema (vendored): ``pmoves_bootstrap/cgp_schema/v1.schema.json``
"""
from .loader import (
    PROFILE,
    SCHEMA_PATH,
    EXAMPLE_PATH,
    BootstrapError,
    Bootstrap,
    Identity,
    Meta,
    load_bootstrap,
    stub_bootstrap,
    export_env,
)
from .tools_bridge import (
    PMOVES_TOOL_REGISTRY,
    BridgeResult,
    register_pmoves_tools,
)
from .subscriber import (
    SUBJECT_TASK,
    SUBJECT_RESULT,
    SUBJECT_BPM_PHASE,
    SUBJECT_BPM_POMODORO,
    KNOWN_TARGETS,
    TaskEnvelope,
    ResultEnvelope,
    SubscriberStatus,
    subscribe,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "PROFILE",
    "SCHEMA_PATH",
    "EXAMPLE_PATH",
    "BootstrapError",
    "Bootstrap",
    "Identity",
    "Meta",
    "load_bootstrap",
    "stub_bootstrap",
    "export_env",
    "PMOVES_TOOL_REGISTRY",
    "BridgeResult",
    "register_pmoves_tools",
    "SUBJECT_TASK",
    "SUBJECT_RESULT",
    "SUBJECT_BPM_PHASE",
    "SUBJECT_BPM_POMODORO",
    "KNOWN_TARGETS",
    "TaskEnvelope",
    "ResultEnvelope",
    "SubscriberStatus",
    "subscribe",
]
