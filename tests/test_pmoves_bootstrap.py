"""Tests for pmoves_bootstrap (the Hermes-side consumer of the PMOVES
CGP). Mirrors the test count of the PMOVES.AI side load_bootstrap.py
(22 tests for the loader) + 12 for the orchestrator + 22 for bpm_cron.
Hermes-side breakdown:

- A. LoadFromExampleTests     (5) - the vendored example loads + validates
- B. LoadFromSourceTests      (4) - raw YAML, raw JSON, env var, path
- C. ValidationFailureTests   (5) - wrong spec, missing fields, bad values
- D. StubFallbackTests        (2) - missing CGP returns the stub
- E. ExportEnvTests           (3) - export_env sets the right vars
- F. TypedAccessorTests       (3) - has_tool/has_mcp/has_constraint/route_for
- G. ToolsBridgeTests         (6) - register_pmoves_tools with real + stub CGPs
- H. SubscriberTests          (3) - subscribe() is a safe no-op in v0

Total: 33 tests, organized into 9 groups (A through H + I).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pmoves_bootstrap import (
    Bootstrap,
    BootstrapError,
    BridgeResult,
    EXAMPLE_PATH,
    Identity,
    Meta,
    PROFILE,
    ResultEnvelope,
    SCHEMA_PATH,
    SUBJECT_BPM_PHASE,
    SUBJECT_BPM_POMODORO,
    SUBJECT_RESULT,
    SUBJECT_TASK,
    SCHEMA_PATH as _SCHEMA_PATH,
    SubscriberStatus,
    TaskEnvelope,
    export_env,
    load_bootstrap,
    register_pmoves_tools,
    stub_bootstrap,
    subscribe,
)
from pmoves_bootstrap.loader import _validate_cgp
from pmoves_bootstrap.subscriber import _enabled, _nats_available
from pmoves_bootstrap.tools_bridge import PMOVES_TOOL_REGISTRY, _parse_disable_list


# === Fixtures =================================================================

@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Strip PMOVES_BOOTSTRAP_* and PMOVES_SUBSCRIBER_ENABLED before every test."""
    for k in list(os.environ):
        if k.startswith("PMOVES_BOOTSTRAP_") or k.startswith("PMOVES_SUBSCRIBER_") or k.startswith("PMOVES_TOOLS_"):
            monkeypatch.delenv(k, raising=False)
    yield


def _write_tmp_cgp(tmp_path: Path, obj: dict) -> Path:
    p = tmp_path / "test.cgp.yaml"
    import yaml
    p.write_text(yaml.safe_dump(obj), encoding="utf-8")
    return p


def _make_minimal_cgp(**overrides) -> dict:
    base = {
        "spec": PROFILE,
        "meta": {
            "created_at": "2026-08-08T00:00:00+00:00",
            "operator": "test",
            "source": "test",
        },
        "identity": {"agent": "test-agent", "role": "operator"},
        "tools": [],
        "mcps": [],
        "services": {},
        "routing": {},
        "constraints": [],
        "super_nodes": [],
    }
    base.update(overrides)
    return base


# === A. LoadFromExampleTests =================================================

def test_A1_example_file_exists():
    assert SCHEMA_PATH.exists(), "vendored schema should exist"
    assert EXAMPLE_PATH.exists(), "vendored example should exist"


def test_A2_load_with_no_args_loads_example():
    bs = load_bootstrap()
    assert isinstance(bs, Bootstrap)
    assert bs.spec == PROFILE
    assert bs.load_source.startswith("path:")
    assert str(EXAMPLE_PATH) in bs.load_source


def test_A3_example_identity():
    bs = load_bootstrap()
    assert bs.identity.agent == "minimax"
    assert bs.identity.role == "implementer"
    assert bs.identity.skin == "dimensional"


def test_A4_example_services_and_routing():
    bs = load_bootstrap()
    ts = bs.service("tailscale")
    assert ts is not None
    assert ts["host"] == "powerfullmoves.tail.ts.net"
    kiloclaw = bs.route_for("kiloclaw")
    assert kiloclaw["target"] == "glm-5.1"
    assert kiloclaw["node"] == "5090"
    hermes = bs.route_for("hermes")
    assert hermes["target"] == "hermes-3"
    assert hermes["node"] == "TBD"


def test_A5_example_has_all_six_canonical_constraints():
    bs = load_bootstrap()
    for c in (
        "no-override-existing-config",
        "tagged-services-are-advisory",
        "no-chit-bypass",
        "no-force-push",
        "no-ci-bypass",
        "preserve-existing-tools",
    ):
        assert bs.has_constraint(c), f"missing constraint: {c}"


# === B. LoadFromSourceTests ==================================================

def test_B1_raw_yaml_string(monkeypatch):
    import yaml
    bs = load_bootstrap(source=yaml.safe_dump(_make_minimal_cgp(identity={"agent": "yaml-agent", "role": "critic"})))
    assert bs.load_source == "raw"
    assert bs.identity.agent == "yaml-agent"
    assert bs.identity.role == "critic"


def test_B2_raw_json_string():
    bs = load_bootstrap(source=json.dumps(_make_minimal_cgp(identity={"agent": "json-agent", "role": "renderer"})))
    assert bs.identity.agent == "json-agent"
    assert bs.identity.role == "renderer"


def test_B3_env_var_PMOVES_BOOTSTRAP_CGP(monkeypatch):
    monkeypatch.setenv(
        "PMOVES_BOOTSTRAP_CGP",
        json.dumps(_make_minimal_cgp(meta={"created_at": "2026-08-08T00:00:00+00:00", "operator": "env", "source": "test"})),
    )
    bs = load_bootstrap()
    assert bs.meta.operator == "env"
    assert bs.load_source == "env:PMOVES_BOOTSTRAP_CGP"


def test_B4_env_var_PMOVES_BOOTSTRAP_CGP_PATH(tmp_path, monkeypatch):
    p = _write_tmp_cgp(tmp_path, _make_minimal_cgp(meta={"created_at": "2026-08-08T00:00:00+00:00", "operator": "path-env", "source": "test"}))
    monkeypatch.setenv("PMOVES_BOOTSTRAP_CGP_PATH", str(p))
    bs = load_bootstrap()
    assert bs.meta.operator == "path-env"


# === C. ValidationFailureTests ===============================================

def test_C1_wrong_spec_rejected():
    bad = _make_minimal_cgp()
    bad["spec"] = "wrong.profile/v9"
    with pytest.raises(BootstrapError, match=r"spec must be"):
        load_bootstrap(source=json.dumps(bad), strict=True)


def test_C2_missing_top_level_field(tmp_path):
    bad = _make_minimal_cgp()
    del bad["constraints"]
    p = _write_tmp_cgp(tmp_path, bad)
    with pytest.raises(BootstrapError, match=r"missing required top-level field: constraints"):
        load_bootstrap(path=str(p), strict=True)


def test_C3_missing_identity_agent():
    bad = _make_minimal_cgp(identity={"role": "operator"})
    with pytest.raises(BootstrapError, match=r"identity\.agent"):
        load_bootstrap(source=json.dumps(bad), strict=True)


def test_C4_bad_role():
    bad = _make_minimal_cgp(identity={"agent": "x", "role": "super-wizard"})
    with pytest.raises(BootstrapError, match=r"identity\.role must be one of"):
        load_bootstrap(source=json.dumps(bad), strict=True)


def test_C5_non_empty_super_nodes_rejected():
    bad = _make_minimal_cgp()
    bad["super_nodes"] = [{"id": "s0"}]
    with pytest.raises(BootstrapError, match=r"super_nodes must be an empty list"):
        load_bootstrap(source=json.dumps(bad), strict=True)


# === D. StubFallbackTests ====================================================

def test_D1_stub_when_no_cgp(monkeypatch):
    # Delete the default example path so the loader can't fall through
    # to it. Then ensure the loader returns the stub.
    import pmoves_bootstrap.loader as loader_mod
    monkeypatch.setattr(loader_mod, "EXAMPLE_PATH", Path("/nonexistent/path/example.cgp.yaml"))
    bs = load_bootstrap()
    assert bs.load_source == "stub:no-cgp"
    assert bs.identity.agent == "unknown"


def test_D2_stub_has_all_six_constraints(monkeypatch):
    import pmoves_bootstrap.loader as loader_mod
    monkeypatch.setattr(loader_mod, "EXAMPLE_PATH", Path("/nonexistent/path/example.cgp.yaml"))
    bs = load_bootstrap()
    for c in (
        "no-override-existing-config",
        "tagged-services-are-advisory",
        "no-chit-bypass",
        "no-force-push",
        "no-ci-bypass",
        "preserve-existing-tools",
    ):
        assert bs.has_constraint(c)


# === E. ExportEnvTests =======================================================

def test_E1_export_identity_vars(monkeypatch):
    bs = load_bootstrap()
    export_env(bs)
    assert os.environ["PMOVES_BOOTSTRAP_AGENT"] == "minimax"
    assert os.environ["PMOVES_BOOTSTRAP_ROLE"] == "implementer"
    assert os.environ["PMOVES_BOOTSTRAP_SKIN"] == "dimensional"


def test_E2_export_services_and_routing_vars(monkeypatch):
    bs = load_bootstrap()
    export_env(bs)
    assert os.environ["PMOVES_BOOTSTRAP_TAILSCALE_HOST"] == "powerfullmoves.tail.ts.net"
    assert os.environ["PMOVES_BOOTSTRAP_HOSTINGER_SITE"] == "powerfullmoves.com"
    assert os.environ["PMOVES_BOOTSTRAP_HOSTINGER_STATUS"] == "pending-mgmt"
    assert os.environ["PMOVES_BOOTSTRAP_CLOUDFLARE_ACCOUNT"] == "powerfullmoves"
    assert os.environ["PMOVES_BOOTSTRAP_TARGET_KILOCLAW"] == "glm-5.1"
    assert os.environ["PMOVES_BOOTSTRAP_TARGET_HERMES"] == "hermes-3"


def test_E3_export_takes_custom_env_dict():
    bs = load_bootstrap()
    env = {}
    export_env(bs, env)
    assert env["PMOVES_BOOTSTRAP_AGENT"] == "minimax"
    assert isinstance(env["PMOVES_BOOTSTRAP_TOOLS"], str)
    # The real process env is unchanged when a custom dict is passed.
    assert "PMOVES_BOOTSTRAP_AGENT" not in os.environ


# === F. TypedAccessorTests ===================================================

def test_F1_has_tool_has_mcp_has_constraint():
    bs = load_bootstrap()
    assert bs.has_tool("comfyui_client")
    assert not bs.has_tool("definitely_not_real")
    assert bs.has_mcp("pmoves-nats-mcp")
    assert bs.has_constraint("no-chit-bypass")


def test_F2_service_returns_none_for_missing():
    bs = load_bootstrap()
    assert bs.service("nope") is None


def test_F3_route_for_returns_none_for_missing():
    bs = load_bootstrap()
    assert bs.route_for("not_in_fleet") is None


# === G. ToolsBridgeTests =====================================================

def test_G1_register_with_stub_returns_empty():
    bs = stub_bootstrap()
    result = register_pmoves_tools(bs=bs)
    assert isinstance(result, BridgeResult)
    assert result.registered == []
    assert result.skipped == []
    assert result.disabled == []
    assert not result.has_any


def test_G2_register_with_real_cgp_registers_known_tools():
    bs = load_bootstrap()  # the example has the canonical tool list
    result = register_pmoves_tools(bs=bs)
    # The example CGP has gh, web_search, web_fetch in the registry.
    # The others (comfyui_client, render_skin, etc.) require the
    # PMOVES.AI repo on PYTHONPATH - they're in the registry but
    # subprocess.run will fail at call time, not at registration.
    assert "gh" in result.registered
    assert "web_search" in result.registered
    assert "web_fetch" in result.registered


def test_G3_disable_list_excludes_tools(monkeypatch):
    monkeypatch.setenv("PMOVES_TOOLS_DISABLE", "gh, web_search")
    bs = load_bootstrap()
    result = register_pmoves_tools(bs=bs)
    assert "gh" in result.disabled
    assert "web_search" in result.disabled
    assert "gh" not in result.registered


def test_G4_unknown_tools_go_to_skipped():
    cgp = _make_minimal_cgp(tools=["gh", "completely_made_up_tool", "also_fake"])
    bs = load_bootstrap(source=json.dumps(cgp))
    result = register_pmoves_tools(bs=bs)
    assert "gh" in result.registered
    assert "completely_made_up_tool" in result.skipped
    assert "also_fake" in result.skipped


def test_G5_callables_are_invokable():
    """The registered callables are real functions (not just IDs)."""
    bs = load_bootstrap()
    result = register_pmoves_tools(bs=bs)
    assert "gh" in result.callables
    assert callable(result.callables["gh"])


def test_G6_registry_is_populated_at_import():
    assert "gh" in PMOVES_TOOL_REGISTRY
    assert "comfyui_client" in PMOVES_TOOL_REGISTRY
    assert "render_skin" in PMOVES_TOOL_REGISTRY


def test_G7_non_string_tool_id_does_not_crash_bridge():
    """Defense-in-depth: a malformed CGP might have non-string entries
    in the tools array (e.g. an object {"inject": "evil"} or an int 42).
    The bridge used to crash with TypeError on the `in disable` check.
    After the fix, non-string entries are skipped silently."""
    cgp = _make_minimal_cgp(tools=["gh", {"inject": "evil"}, 42, None])
    bs = load_bootstrap(source=json.dumps(cgp))
    # This should not raise TypeError anymore
    result = register_pmoves_tools(bs=bs)
    assert "gh" in result.registered
    # The non-string entries land in skipped
    skipped_strs = [str(s) for s in result.skipped]
    assert any("inject" in s for s in skipped_strs)
    assert any("42" in s for s in skipped_strs)


# === H. SubscriberTests ======================================================

def test_H1_subscribe_is_safe_noop_when_disabled():
    """No nats-py + PMOVES_SUBSCRIBER_ENABLED unset -> safe disabled status."""
    status = subscribe()
    assert isinstance(status, SubscriberStatus)
    assert status.enabled is False
    assert status.subject == SUBJECT_TASK
    # The reason is either "nats-py not importable" or "PMOVES_SUBSCRIBER_ENABLED is not set"
    assert "not importable" in status.reason or "not set" in status.reason


def test_H2_task_envelope_round_trip():
    """The TaskEnvelope dataclass matches the orchestrator's payload shape."""
    raw = {
        "task_id": "task-abc",
        "target": "hermes",
        "prompt": "render the cyber.png as the Pillar 4 visual",
        "requested_by": "mavis",
        "phase": "execute",
        "context": {"skin": "dimensional"},
        "timestamp": "2026-08-08T00:30:00+00:00",
    }
    env = TaskEnvelope.from_json(json.dumps(raw))
    assert env.task_id == "task-abc"
    assert env.target == "hermes"
    assert env.prompt.startswith("render")
    assert env.context == {"skin": "dimensional"}


def test_H3_result_envelope_round_trip():
    """The ResultEnvelope serializes to the wire shape."""
    env = ResultEnvelope(
        task_id="task-abc",
        target="hermes",
        success=True,
        output="Pillar 4 visual rendered at pmoves/design/skins/pillar4.json",
        timestamp="2026-08-08T00:35:00+00:00",
    )
    raw = env.to_json()
    obj = json.loads(raw)
    assert obj["task_id"] == "task-abc"
    assert obj["success"] is True
    assert "Pillar 4" in obj["output"]


# === I. Constants and subject surfaces ========================================

def test_I1_subjects_match_orchestrator():
    """The subscriber's subject constants match the orchestrator's."""
    from pmoves_bootstrap import SUBJECT_TASK as A, SUBJECT_RESULT as B
    from pmoves_bootstrap import SUBJECT_BPM_PHASE as C, SUBJECT_BPM_POMODORO as D
    assert A == "pmoves.agent.task.v1"
    assert B == "pmoves.agent.result.v1"
    assert C == "pmoves.bpm.phase.v1"
    assert D == "pmoves.bpm.pomodoro.v1"


def test_I2_known_targets_contains_expected():
    from pmoves_bootstrap import KNOWN_TARGETS
    assert "mavis" in KNOWN_TARGETS
    assert "kiloclaw" in KNOWN_TARGETS
    assert "hermes" in KNOWN_TARGETS
