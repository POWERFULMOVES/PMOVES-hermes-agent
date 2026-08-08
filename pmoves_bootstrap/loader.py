"""PMOVES bootstrap CGP loader for the PMOVES-hermes-agent fork.

The Hermes-side of the Mavis harness v0 (3-repo coordinated). Reads
a ``pmoves.bootstrap/v1`` CGP from file / env / default, validates it
against the vendored JSON Schema, and returns a typed ``Bootstrap``
object with the relevant fields exposed as attributes and as
``PMOVES_BOOTSTRAP_*`` env vars.

CGP profile: ``pmoves.bootstrap/v1``
Canonical spec (PMOVES.AI side):
    pmoves/docs/PMOVESCHIT/CGP_v1.0_SPECIFICATION.md

This module is the Hermes-side reader. The PMOVES.AI side has the
canonical writer at ``pmoves/tools/load_bootstrap.py``; the schema
``pmoves_bootstrap/cgp_schema/v1.schema.json`` is vendored from
``pmoves/contracts/schemas/pmoves-bootstrap/v1.schema.json``.

Hermes already has ``pyyaml`` and ``ruamel.yaml`` in its core
dependencies (see ``pyproject.toml``), so the loader accepts BOTH
YAML and JSON forms. The fork is non-breaking: ``load_bootstrap()``
always returns a Bootstrap. When the CGP is absent, the stub
Bootstrap is returned (safe defaults, all 6 constraints).
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

try:
    import yaml  # pyyaml (core dep)
except ImportError:  # pragma: no cover - core dep, should never happen
    yaml = None

LOG = logging.getLogger("pmoves_bootstrap.loader")

PROFILE = "pmoves.bootstrap/v1"
SCHEMA_PATH = Path(__file__).parent / "cgp_schema" / "v1.schema.json"
EXAMPLE_PATH = Path(__file__).parent / "cgp_schema" / "example.cgp.yaml"

REQUIRED_TOP_KEYS: tuple[str, ...] = (
    "spec", "meta", "identity", "tools", "mcps", "services", "routing", "constraints",
)

VALID_ROLES: tuple[str, ...] = (
    "implementer", "critic", "renderer", "curator", "operator", "dispatcher",
)
VALID_SOURCES: tuple[str, ...] = ("mavis", "hermes", "pinokio", "operator", "test")
VALID_CONSTRAINTS: tuple[str, ...] = (
    "no-override-existing-config",
    "tagged-services-are-advisory",
    "no-chit-bypass",
    "no-force-push",
    "no-ci-bypass",
    "preserve-existing-tools",
)


class BootstrapError(Exception):
    """Raised when a CGP can't be parsed or validated.

    The orchestrator / session-init code should catch this and either
    fall back to the stub Bootstrap (default behavior) or refuse the
    session (set ``strict=True`` in ``load_bootstrap``).
    """


@dataclass
class Identity:
    agent: str
    role: str
    skin: Optional[str] = None


@dataclass
class Meta:
    created_at: str
    operator: str
    source: str
    encoder_version: Optional[str] = None
    bootstrap_id: Optional[str] = None


@dataclass
class Bootstrap:
    spec: str
    meta: Meta
    identity: Identity
    tools: list[str] = field(default_factory=list)
    mcps: list[str] = field(default_factory=list)
    services: dict[str, Any] = field(default_factory=dict)
    routing: dict[str, Any] = field(default_factory=dict)
    constraints: list[str] = field(default_factory=list)
    super_nodes: list[Any] = field(default_factory=list)
    load_source: str = "unknown"  # where the CGP came from (file/env/raw/default). NOT to be confused with `meta.source` (the CGP producer).

    # --- typed accessors ----------------------------------------------------

    def has_tool(self, tool_id: str) -> bool:
        return tool_id in self.tools

    def has_mcp(self, mcp_id: str) -> bool:
        return mcp_id in self.mcps

    def has_constraint(self, constraint_id: str) -> bool:
        return constraint_id in self.constraints

    def service(self, name: str) -> Optional[dict]:
        return self.services.get(name)

    def route_for(self, agent: str) -> Optional[dict]:
        return self.routing.get(agent)

    def to_dict(self) -> dict:
        """Return the raw CGP as a dict (for env export + tests)."""
        return {
            "spec": self.spec,
            "meta": {
                "created_at": self.meta.created_at,
                "operator": self.meta.operator,
                "source": self.meta.source,
                "encoder_version": self.meta.encoder_version,
                "bootstrap_id": self.meta.bootstrap_id,
            },
            "identity": {
                "agent": self.identity.agent,
                "role": self.identity.role,
                "skin": self.identity.skin,
            },
            "tools": list(self.tools),
            "mcps": list(self.mcps),
            "services": self.services,
            "routing": self.routing,
            "constraints": list(self.constraints),
            "super_nodes": list(self.super_nodes),
        }


def _detect_yaml_or_json(raw: str) -> Any:
    """Parse raw text as YAML (preferred) or JSON (fallback).

    Hermes has pyyaml as a core dep, so YAML is the natural format.
    The PMOVES.AI side writes the canonical CGP in YAML for human
    editing; consumers can also receive JSON via env var.
    """
    stripped = raw.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        return json.loads(raw)
    if yaml is not None:
        return yaml.safe_load(raw)
    # Last-resort fallback: try JSON.parse
    return json.loads(raw)


def _read_path(path: str) -> tuple[dict, str]:
    if not os.path.exists(path):
        raise BootstrapError(f"CGP file not found: {path}")
    raw = Path(path).read_text(encoding="utf-8")
    obj = _detect_yaml_or_json(raw)
    if not isinstance(obj, dict):
        raise BootstrapError(f"CGP file did not parse to a dict: {path}")
    return obj, f"path:{path}"


def _read_source(raw: str) -> tuple[dict, str]:
    obj = _detect_yaml_or_json(raw)
    if not isinstance(obj, dict):
        raise BootstrapError("CGP source did not parse to a dict")
    return obj, "raw"


def _read_env() -> Optional[tuple[dict, str]]:
    raw = os.environ.get("PMOVES_BOOTSTRAP_CGP")
    if raw:
        obj = _detect_yaml_or_json(raw)
        if isinstance(obj, dict):
            return obj, "env:PMOVES_BOOTSTRAP_CGP"
    path = os.environ.get("PMOVES_BOOTSTRAP_CGP_PATH")
    if path:
        return _read_path(path)
    return None


def _read_default() -> Optional[tuple[dict, str]]:
    if EXAMPLE_PATH.exists():
        return _read_path(str(EXAMPLE_PATH))
    return None


def _validate_cgp(obj: dict) -> None:
    """Structural validation against the vendored v1 schema.

    The vendored ``v1.schema.json`` is the source of truth, but we
    don't pull ``jsonschema`` (a heavy dep) into Hermes. The thin
    structural check covers the 80% case with zero new deps:

    - all required top-level fields are present
    - spec is exactly the const (``pmoves.bootstrap/v1``)
    - meta.created_at / operator / source are non-empty strings
    - meta.source is one of the 5 enum values
    - identity.agent is a non-empty string
    - identity.role is one of the 6 enum values
    - tools, mcps, constraints are arrays
    - services, routing are objects
    - super_nodes is exactly ``[]`` (the bootstrap is metadata, not a data packet)
    """
    if not isinstance(obj, dict):
        raise BootstrapError("CGP must be a dict/object")

    # Spec check first - if the spec is wrong, the producer has bigger
    # problems than missing fields (the CGP is for a different profile),
    # so surface that error before complaining about structure.
    if obj.get("spec") != PROFILE:
        raise BootstrapError(
            f"CGP spec must be {PROFILE!r}, got {obj.get('spec')!r}"
        )

    for key in REQUIRED_TOP_KEYS:
        if key not in obj:
            raise BootstrapError(f"CGP missing required top-level field: {key}")

    meta = obj["meta"]
    if not isinstance(meta, dict):
        raise BootstrapError("CGP meta must be a dict")
    for mk in ("created_at", "operator", "source"):
        v = meta.get(mk)
        if not isinstance(v, str) or not v:
            raise BootstrapError(f"CGP meta.{mk} must be a non-empty string")
    if meta["source"] not in VALID_SOURCES:
        raise BootstrapError(
            f"CGP meta.source must be one of {VALID_SOURCES}, got {meta['source']!r}"
        )

    identity = obj["identity"]
    if not isinstance(identity, dict):
        raise BootstrapError("CGP identity must be a dict")
    if not identity.get("agent") or not isinstance(identity["agent"], str):
        raise BootstrapError("CGP identity.agent must be a non-empty string")
    if identity.get("role") not in VALID_ROLES:
        raise BootstrapError(
            f"CGP identity.role must be one of {VALID_ROLES}, got {identity.get('role')!r}"
        )

    for key in ("tools", "mcps", "constraints"):
        if not isinstance(obj[key], list):
            raise BootstrapError(f"CGP {key} must be a list")
    for key in ("services", "routing"):
        if not isinstance(obj[key], dict):
            raise BootstrapError(f"CGP {key} must be a dict")

    sn = obj["super_nodes"]
    if not isinstance(sn, list) or len(sn) != 0:
        raise BootstrapError(
            "CGP super_nodes must be an empty list (the bootstrap is metadata, not a data packet)"
        )


def _from_dict(obj: dict, source: str) -> Bootstrap:
    return Bootstrap(
        spec=obj["spec"],
        meta=Meta(
            created_at=obj["meta"]["created_at"],
            operator=obj["meta"]["operator"],
            source=obj["meta"]["source"],
            encoder_version=obj["meta"].get("encoder_version"),
            bootstrap_id=obj["meta"].get("bootstrap_id"),
        ),
        identity=Identity(
            agent=obj["identity"]["agent"],
            role=obj["identity"]["role"],
            skin=obj["identity"].get("skin"),
        ),
        tools=list(obj.get("tools", [])),
        mcps=list(obj.get("mcps", [])),
        services=dict(obj.get("services", {})),
        routing=dict(obj.get("routing", {})),
        constraints=list(obj.get("constraints", [])),
        super_nodes=list(obj.get("super_nodes", [])),
        load_source=source,
    )


def stub_bootstrap() -> Bootstrap:
    """Return a stub Bootstrap (the no-CGP fallback).

    The stub carries all 6 canonical constraints (so the consumer's
    non-breaking test pair still holds) but has empty tools, empty
    services, and an ``unknown`` identity. The orchestrator can detect
    the stub by checking ``bootstrap.load_source == "stub:no-cgp"``.
    """
    return Bootstrap(
        spec=PROFILE,
        meta=Meta(
            created_at="1970-01-01T00:00:00+00:00",
            operator="unknown",
            source="hermes",
            encoder_version="0.0.0",
        ),
        identity=Identity(agent="unknown", role="operator", skin="default"),
        tools=[],
        mcps=[],
        services={
            "tailscale": None,
            "rustdesk": {"devices": []},
            "hostinger": {"site": None, "status": "offline"},
            "cloudflare": {"account": None, "zones": []},
        },
        routing={},
        constraints=list(VALID_CONSTRAINTS),
        super_nodes=[],
        load_source="stub:no-cgp",
    )


def load_bootstrap(
    path: Optional[str] = None,
    source: Optional[str] = None,
    strict: bool = False,
) -> Bootstrap:
    """Load a pmoves.bootstrap/v1 CGP from one of 4 sources.

    Resolution order (in priority):

    1. ``path`` arg       - file path (YAML or JSON, detected by content)
    2. ``source`` arg     - raw YAML/JSON string
    3. env var            - PMOVES_BOOTSTRAP_CGP (raw) or PMOVES_BOOTSTRAP_CGP_PATH (file)
    4. default example    - the vendored example.cgp.yaml

    Returns a Bootstrap. If no CGP is found anywhere, returns the stub.
    Raises ``BootstrapError`` only on parse/validation failures of a
    CGP that was explicitly provided (path or source) AND ``strict=True``.
    With ``strict=False`` (default), explicit-source errors are caught
    and the loader falls through to the next source (matching the
    PMOVES.AI side behavior).
    """
    candidates: list[Optional[tuple[dict, str]]] = [
        _read_path(path) if path else None,
        _read_source(source) if source else None,
        _read_env(),
        _read_default(),
    ]
    last_err: Optional[Exception] = None
    for cand in candidates:
        if cand is None:
            continue
        obj, src = cand
        try:
            _validate_cgp(obj)
            return _from_dict(obj, src)
        except BootstrapError as err:
            last_err = err
            if strict and (path or source):
                raise
            LOG.debug("CGP from %s failed validation: %s", src, err)
            continue
    if last_err is not None:
        LOG.warning("No valid CGP found (last error: %s); using stub", last_err)
    return stub_bootstrap()


def export_env(bs: Bootstrap, env: Optional[dict] = None) -> dict:
    """Export the Bootstrap as PMOVES_BOOTSTRAP_* env vars.

    Mirrors the PMOVES.AI side's env export so any tool in the
    session can read the bootstrap via ``os.environ`` without
    re-parsing the CGP. The ``env`` arg defaults to ``os.environ``;
    pass a custom dict for tests.

    The same key set is exported regardless of which fork is doing
    the export - the env-var contract is part of the 3-repo CGP
    integration.
    """
    if env is None:
        env = os.environ
    env["PMOVES_BOOTSTRAP_AGENT"] = bs.identity.agent
    env["PMOVES_BOOTSTRAP_ROLE"] = bs.identity.role
    env["PMOVES_BOOTSTRAP_SKIN"] = bs.identity.skin or ""
    env["PMOVES_BOOTSTRAP_TOOLS"] = ",".join(bs.tools)
    env["PMOVES_BOOTSTRAP_MCPS"] = ",".join(bs.mcps)
    env["PMOVES_BOOTSTRAP_CONSTRAINTS"] = ",".join(bs.constraints)

    ts = bs.service("tailscale")
    if isinstance(ts, dict):
        if ts.get("host"):
            env["PMOVES_BOOTSTRAP_TAILSCALE_HOST"] = ts["host"]
        if ts.get("ip"):
            env["PMOVES_BOOTSTRAP_TAILSCALE_IP"] = ts["ip"]
    rd = bs.service("rustdesk")
    if isinstance(rd, dict) and isinstance(rd.get("devices"), list):
        env["PMOVES_BOOTSTRAP_RUSTDESK_DEVICES"] = ",".join(rd["devices"])
    hostinger = bs.service("hostinger")
    if isinstance(hostinger, dict):
        if hostinger.get("site"):
            env["PMOVES_BOOTSTRAP_HOSTINGER_SITE"] = hostinger["site"]
        if hostinger.get("status"):
            env["PMOVES_BOOTSTRAP_HOSTINGER_STATUS"] = hostinger["status"]
    cf = bs.service("cloudflare")
    if isinstance(cf, dict):
        if cf.get("account"):
            env["PMOVES_BOOTSTRAP_CLOUDFLARE_ACCOUNT"] = cf["account"]
        if isinstance(cf.get("zones"), list):
            env["PMOVES_BOOTSTRAP_CLOUDFLARE_ZONES"] = ",".join(cf["zones"])

    kiloclaw = bs.route_for("kiloclaw")
    if isinstance(kiloclaw, dict):
        env["PMOVES_BOOTSTRAP_TARGET_KILOCLAW"] = kiloclaw.get("target") or ""
    hermes = bs.route_for("hermes")
    if isinstance(hermes, dict):
        env["PMOVES_BOOTSTRAP_TARGET_HERMES"] = hermes.get("target") or ""
    return env


__all__ = [
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
    # exposed for tests:
    "_validate_cgp",
    "_from_dict",
    "_detect_yaml_or_json",
]
