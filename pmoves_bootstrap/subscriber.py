"""PMOVES NATS subscriber stub for Hermes.

The optional NATS subscriber that lets a Hermes session pick up
``pmoves.agent.task.v1`` messages from the Mavis orchestrator and
respond on ``pmoves.agent.result.v1``.

In v0, this is a STUB. The reason: ``nats-py`` (the official Python
NATS client) is not a Hermes core dep, and adding it to the
``dependencies`` list in ``pyproject.toml`` is a meaningful blast-
radius change (the comment on the deps list in pyproject.toml
warns against adding ranges; ``nats-py`` would be exact-pinned, but
the exact pin is a decision that belongs in a follow-up PR with its
own review).

What v0 ships:

- ``TaskEnvelope`` / ``ResultEnvelope`` dataclasses matching the
  NATS payload shape (so the wire contract is documented + can be
  validated offline)
- ``subscribe()`` is a no-op stub that logs a clear "not yet
  implemented" message and returns a ``SubscriberStatus.disabled``
- The PMOVES.AI orchestrator's exact subject + payload shape
  (see ``pmoves/tools/orchestrator.py``) is mirrored in the
  dataclasses, so a future slice that adds ``nats-py`` can wire it
  in without changing the wire contract.

The non-breaking test pair:

- ``nats-py`` not installed (default in v0) -> ``subscribe()`` is a
  no-op. The session still loads the CGP, the tools_bridge still
  registers PMOVES tools, the orchestrator's tasks are not picked up
  (this is the v0 limitation; the orchestrator publishes anyway and
  a future Hermes-side subscriber picks them up).
- ``nats-py`` installed + ``PMOVES_SUBSCRIBER_ENABLED=true`` -> a
  real subscriber runs (future slice).
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Optional

LOG = logging.getLogger("pmoves_bootstrap.subscriber")

# The PMOVES.AI orchestrator publishes to these subjects. The shape
# is mirrored in the dataclasses below so the wire contract is
# explicit and testable offline.
SUBJECT_TASK = "pmoves.agent.task.v1"
SUBJECT_RESULT = "pmoves.agent.result.v1"
SUBJECT_BPM_PHASE = "pmoves.bpm.phase.v1"
SUBJECT_BPM_POMODORO = "pmoves.bpm.pomodoro.v1"

# The set of agents the orchestrator can target (kept here for
# validation; the routing block of the CGP is the source of truth
# for which agents are wired in any given session).
KNOWN_TARGETS: frozenset[str] = frozenset({"mavis", "kiloclaw", "hermes"})


@dataclass
class TaskEnvelope:
    """The shape of a ``pmoves.agent.task.v1`` message.

    Mirrors the PMOVES.AI side's ``orchestrator.dispatch()`` payload.
    The ``target`` is the agent handle; the matching subscriber
    filters by target before processing.
    """

    task_id: str
    target: str
    prompt: str
    requested_by: str = "mavis"
    phase: str = "execute"
    context: dict = field(default_factory=dict)
    timestamp: str = ""

    @classmethod
    def from_json(cls, raw: str) -> "TaskEnvelope":
        obj = json.loads(raw)
        return cls(
            task_id=obj.get("task_id", ""),
            target=obj.get("target", ""),
            prompt=obj.get("prompt", ""),
            requested_by=obj.get("requested_by", "mavis"),
            phase=obj.get("phase", "execute"),
            context=obj.get("context", {}),
            timestamp=obj.get("timestamp", ""),
        )


@dataclass
class ResultEnvelope:
    """The shape of a ``pmoves.agent.result.v1`` message.

    The subscriber publishes a ResultEnvelope for each TaskEnvelope
    it processes. The PMOVES.AI orchestrator collects these on the
    bus and merges the outputs per phase.
    """

    task_id: str
    target: str
    success: bool
    output: str = ""
    error: str = ""
    timestamp: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self))


@dataclass
class SubscriberStatus:
    """The status returned by ``subscribe()``.

    ``enabled`` is True only when nats-py is importable AND
    ``PMOVES_SUBSCRIBER_ENABLED=true``. In v0, nats-py is not a
    core dep, so ``enabled`` is always False until the operator
    adds nats-py + sets the env var.
    """

    enabled: bool
    reason: str
    subject: str = SUBJECT_TASK
    tasks_received: int = 0
    tasks_published: int = 0
    last_task_id: str = ""


def _nats_available() -> bool:
    """Return True if nats-py is importable.

    Hermes does not list ``nats-py`` as a core dep (it lives in the
    harness v0 follow-ups). This helper lets the v0 subscriber
    detect the import cleanly and surface a useful log message
    instead of crashing on import.
    """
    try:
        import nats  # noqa: F401
        return True
    except ImportError:
        return False


def _enabled() -> bool:
    return os.environ.get("PMOVES_SUBSCRIBER_ENABLED", "false").lower() in ("1", "true", "yes")


def subscribe(
    target: str = "hermes",
    on_task: Optional[Callable[[TaskEnvelope], ResultEnvelope]] = None,
    bootstrap: Optional[Any] = None,
) -> SubscriberStatus:
    """Subscribe to ``pmoves.agent.task.v1`` and respond on ``pmoves.agent.result.v1``.

    v0 STUB: when nats-py is not available OR the env var is not set,
    this logs a clear message and returns ``SubscriberStatus(enabled=False)``.
    The non-breaking test pair is preserved: the call is always safe,
    the session never crashes, the orchestrator can publish tasks
    even if no one is subscribed yet.

    ``target`` is the agent handle this session acts as. The
    subscriber filters incoming tasks to those targeting this handle.
    Defaults to ``"hermes"``.

    ``on_task`` is the handler callback. If None, the subscriber
    uses a default handler that echoes the prompt back as the result
    (useful for development + the "no real agent attached yet" case
    that the operator's memory flagged: 'TBD' for the hermes node).

    ``bootstrap`` is the loaded PMOVES CGP. If None, ``load_bootstrap()``
    is called. The subscriber uses the CGP's routing block to find
    the NATS server URL (future slice) and the constraints to
    enforce (e.g. ``no-chit-bypass`` means a task that requires a
    CHIT-signed action is rejected).
    """
    status = SubscriberStatus(
        enabled=False,
        reason="not-yet-implemented (v0 stub)",
        subject=SUBJECT_TASK,
    )

    if not _nats_available():
        status.reason = "nats-py not importable; subscriber disabled"
        LOG.info(
            "PMOVES subscriber disabled: %s. "
            "Install nats-py and set PMOVES_SUBSCRIBER_ENABLED=true to enable.",
            status.reason,
        )
        return status

    if not _enabled():
        status.reason = "PMOVES_SUBSCRIBER_ENABLED is not set to true"
        LOG.info("PMOVES subscriber disabled: %s.", status.reason)
        return status

    # Future slice: when nats-py is in scope, this is where the real
    # subscription loop lives. The v0 stub is a no-op so the import
    # surface is stable; flipping PMOVES_SUBSCRIBER_ENABLED to true
    # without nats-py installed still returns a clean status.
    status.reason = "v0 stub: real nats-py subscription is a follow-up slice"
    LOG.info("PMOVES subscriber: %s", status.reason)
    return status


__all__ = [
    "SUBJECT_TASK",
    "SUBJECT_RESULT",
    "SUBJECT_BPM_PHASE",
    "SUBJECT_BPM_POMODORO",
    "KNOWN_TARGETS",
    "TaskEnvelope",
    "ResultEnvelope",
    "SubscriberStatus",
    "subscribe",
    # exposed for tests:
    "_nats_available",
    "_enabled",
]
