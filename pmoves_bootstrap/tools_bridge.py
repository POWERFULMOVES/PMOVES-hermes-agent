"""PMOVES tools bridge for Hermes.

The bridge between the PMOVES CGP ``tools`` list and Hermes's native
tool registry. When a CGP is loaded, the bridge looks at each tool ID
in ``bootstrap.tools`` and, for the ones that match a known PMOVES
tool, registers a wrapper that the session can call alongside Hermes's
own tools.

Non-breaking contract:

- No CGP present     -> ``register_pmoves_tools()`` is a no-op. The
  session's toolset is unchanged.
- CGP present         -> PMOVES tools are added alongside Hermes's
  native tools. The operator can opt out per-tool via the
  ``PMOVES_TOOLS_DISABLE`` env var (a comma-separated deny-list).

The bridge does NOT touch Hermes's ``toolsets.py`` directly (that's
load-bearing). Instead, it returns a ``BridgeResult`` with the
list of registered tool IDs and a callable for each. The session
init code (a future slice) merges the bridge's tools into Hermes's
active toolset.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .loader import Bootstrap, load_bootstrap

LOG = logging.getLogger("pmoves_bootstrap.tools_bridge")

# A small, explicit set of PMOVES tools that the v0 bridge knows how
# to call. Each entry maps a PMOVES tool ID (as it appears in
# ``bootstrap.tools``) to a callable that invokes the underlying
# PMOVES tool. Tools not in this registry are silently skipped (per
# the ``tagged-services-are-advisory`` constraint).
#
# A real bridge would resolve these against the PMOVES MCP server
# (``pmoves-nats-mcp``); v0 calls the underlying CLI / Python script
# directly for the tools that have local implementations.
PMOVES_TOOL_REGISTRY: dict[str, Callable[..., Any]] = {}


def _resolve_python_tool(script_relpath: str):
    """Return a callable that invokes a PMOVES Python tool by relative path.

    ``script_relpath`` is relative to the PMOVES repo root (e.g.
    ``pmoves/tools/render_skin.py``). The callable shells out to
    ``python -m <module>`` so the operator's working directory
    (typically the PMOVES.AI repo) is the implicit CWD.

    A future slice will swap this for an MCP-RPC call so the tools
    don't need a local PMOVES checkout on every node.
    """
    module = script_relpath.replace("/", ".").removesuffix(".py")

    def _call(*args: str, stdin: Optional[str] = None, env: Optional[dict] = None) -> subprocess.CompletedProcess:
        cmd = ["python", "-m", module, *args]
        return subprocess.run(
            cmd,
            input=stdin,
            capture_output=True,
            text=True,
            env={**os.environ, **(env or {})},
        )

    return _call


def _resolve_cli_tool(cli_name: str):
    """Return a callable that invokes a CLI binary by name.

    The binary is resolved against ``PATH`` (via ``shutil.which``).
    The callable fails fast with a clear error if the binary is
    missing - the operator can install it or set ``PMOVES_TOOLS_DISABLE``
    to skip it.
    """
    def _call(*args: str, stdin: Optional[str] = None) -> subprocess.CompletedProcess:
        binary = shutil.which(cli_name)
        if not binary:
            raise RuntimeError(
                f"PMOVES tool {cli_name!r} not found on PATH; "
                f"install it or set PMOVES_TOOLS_DISABLE to skip"
            )
        return subprocess.run(
            [binary, *args],
            input=stdin,
            capture_output=True,
            text=True,
        )

    return _call


def _register_default_registry() -> None:
    """Populate ``PMOVES_TOOL_REGISTRY`` with the v0 set of PMOVES tools.

    Called at module import (idempotent). A future slice will move
    this to a config-driven registry loaded from the PMOVES tools
    catalog (per ``pmoves/tools/``).
    """
    if PMOVES_TOOL_REGISTRY:
        return
    # PMOVES Python tools - pmoves.tools.* modules
    PMOVES_TOOL_REGISTRY["comfyui_client"] = _resolve_python_tool("pmoves.tools.comfyui_client")
    PMOVES_TOOL_REGISTRY["render_skin"] = _resolve_python_tool("pmoves.tools.render_skin")
    PMOVES_TOOL_REGISTRY["pinokio_launch"] = _resolve_python_tool("pmoves.tools.pinokio_launch")
    # PMOVES shell tool (pinokio_launch.sh) - alternative entry
    PMOVES_TOOL_REGISTRY["pinokio_launch_sh"] = _resolve_cli_tool("bash")
    # Generic CLI tools
    PMOVES_TOOL_REGISTRY["gh"] = _resolve_cli_tool("gh")
    PMOVES_TOOL_REGISTRY["web_search"] = _resolve_cli_tool("web_search")  # notional
    PMOVES_TOOL_REGISTRY["web_fetch"] = _resolve_cli_tool("web_fetch")  # notional
    # Mavis (PMOVES) skill tools - the ``mavis__<group>__<name>`` convention
    # from the CGP example. These resolve against the Mavis skill surface
    # (a future slice wires them in via the mavis tool family).
    for tool_id in (
        "mavis__agent__create",
        "mavis__cron__self",
        "mavis__cron__list",
        "mavis__cron__create",
    ):
        PMOVES_TOOL_REGISTRY[tool_id] = _resolve_cli_tool("mavis")


_register_default_registry()


@dataclass
class BridgeResult:
    """The result of ``register_pmoves_tools``.

    ``registered`` is the list of tool IDs that were successfully
    registered (i.e. in the CGP, not in the deny-list, and have a
    callable in the registry).

    ``skipped`` is the list of tool IDs that were in the CGP but
    NOT registered (missing, denied, or unknown). Per the
    ``tagged-services-are-advisory`` constraint, skipped tools are
    warnings, not errors.

    ``disabled`` is the set of tool IDs in the deny-list (from
    ``PMOVES_TOOLS_DISABLE``) - these are reported separately from
    skipped so the session init code can distinguish "operator
    chose to disable" from "we don't know this tool".
    """

    registered: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    disabled: list[str] = field(default_factory=list)
    callables: dict[str, Callable[..., Any]] = field(default_factory=dict)

    @property
    def has_any(self) -> bool:
        return bool(self.registered)


def _parse_disable_list() -> set[str]:
    raw = os.environ.get("PMOVES_TOOLS_DISABLE", "")
    return {tok.strip() for tok in raw.split(",") if tok.strip()}


def register_pmoves_tools(
    bs: Optional[Bootstrap] = None,
    registry: Optional[dict[str, Callable[..., Any]]] = None,
) -> BridgeResult:
    """Register PMOVES tools for the current session.

    ``bs`` defaults to ``load_bootstrap()`` (the canonical CGP). Pass
    an explicit Bootstrap for tests or for a session that already
    loaded the CGP elsewhere.

    ``registry`` defaults to ``PMOVES_TOOL_REGISTRY``. Pass a custom
    dict to override (tests, plugins).

    The non-breaking test pair:

    - ``bs is None`` and no CGP anywhere -> ``BridgeResult(registered=[], ...))``
    - ``bs is stub_bootstrap()`` (CGP missing) -> same as above
    - ``bs`` is a real CGP with ``tools=[...]`` -> PMOVES tools are
      registered alongside Hermes's native tools
    """
    if bs is None:
        bs = load_bootstrap()

    reg = registry if registry is not None else PMOVES_TOOL_REGISTRY
    disable = _parse_disable_list()
    result = BridgeResult()

    for tool_id in bs.tools:
        if tool_id in disable:
            result.disabled.append(tool_id)
            continue
        if tool_id in reg:
            result.registered.append(tool_id)
            result.callables[tool_id] = reg[tool_id]
        else:
            result.skipped.append(tool_id)
            LOG.debug("PMOVES tool %r in CGP but not in registry; skipping", tool_id)

    if result.disabled:
        LOG.info("PMOVES tools disabled via PMOVES_TOOLS_DISABLE: %s", sorted(result.disabled))
    if result.skipped:
        LOG.info(
            "PMOVES tools skipped (unknown to this bridge): %s",
            sorted(result.skipped),
        )
    LOG.info(
        "PMOVES tools registered: %d of %d in CGP",
        len(result.registered),
        len(bs.tools),
    )
    return result


__all__ = [
    "PMOVES_TOOL_REGISTRY",
    "BridgeResult",
    "register_pmoves_tools",
    # exposed for tests:
    "_parse_disable_list",
    "_resolve_python_tool",
    "_resolve_cli_tool",
]
