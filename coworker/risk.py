"""Risk classes for tools — the intrinsic side-effect category that drives permission
gating (and, later in Phase 2, unattended Inbox routing).

This replaces the hardcoded ``WRITE_TOOLS`` / ``SHELL_TOOL`` name sets the permission engine
used to carry inline: risk is now a declared property a single ``classify`` reads.

A tool's *effective* risk = an optional user-local override (Phase 2) ?? the base
classification here. Built-in vetted tools are classified by name; anything else falls back
to its aisuite metadata (``requires_approval`` → external) or is treated as read.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Callable, Optional


class RiskClass(str, Enum):
    READ = "read"  # no side effects — always allowed
    EGRESS = "egress"  # reaches the network — the request itself can carry data off-machine
    WRITE_LOCAL = "write_local"  # mutates the workspace — path-scoped + mode-gated
    EXEC = "exec"  # runs commands — mode-gated
    EXTERNAL = "external"  # side effects off the machine — the unattended Inbox hook


# Built-in tools whose risk is fixed by name (the old WRITE_TOOLS / SHELL_TOOL, as data).
WRITE_TOOLS = {"write_file", "replace_in_file", "apply_patch", "apply_unified_diff"}
SHELL_TOOL = "run_shell"
# Model-chosen network egress. `web_fetch` takes a URL straight from the model and the
# URL's path/query can carry data outbound, so it is NOT a pure read — it must reach the
# gate. `web_search` reaches a FIXED destination (the configured provider), but its query
# is model-chosen free text — the same outbound channel — so it gates too (spec §2.2).
EGRESS_TOOLS = {"web_fetch", "web_search"}

_BASE: dict[str, RiskClass] = {
    **{name: RiskClass.WRITE_LOCAL for name in WRITE_TOOLS},
    SHELL_TOOL: RiskClass.EXEC,
    **{name: RiskClass.EGRESS for name in EGRESS_TOOLS},
}

# How much attention each class demands, for the override-tightening rule below. Higher =
# stricter. EXEC and WRITE_LOCAL are the crown jewels (path scoping / command gating).
_STRICTNESS: dict[RiskClass, int] = {
    RiskClass.READ: 0,
    RiskClass.EGRESS: 1,
    RiskClass.EXTERNAL: 2,
    RiskClass.WRITE_LOCAL: 3,
    RiskClass.EXEC: 3,
}

# A user-local override resolver: tool name -> RiskClass (or None to defer to the base).
RiskOverrides = Callable[[str], Optional["RiskClass"]]


def classify(
    tool_name: str, metadata: Any = None, overrides: Optional[RiskOverrides] = None
) -> RiskClass:
    """Effective risk of a tool call. A user override may *relax* a metadata/MCP tool (the
    intended use — quieting an over-cautious plug-in), but may only ever **tighten** a
    built-in write/exec/egress tool, never loosen it. Downgrading a built-in write to a read
    would switch off path scoping AND the read-only gate at once, so it is refused here.
    Precedence otherwise: the by-name base table, then aisuite metadata
    (`requires_approval` → external), else read."""
    base = _BASE.get(tool_name)
    if overrides is not None:
        ov = overrides(tool_name)
        if ov is not None:
            if base is None or _STRICTNESS[ov] >= _STRICTNESS[base]:
                return ov
            # A loosening override on a built-in is ignored: fall through to the base class.
    if base is not None:
        return base
    if bool(getattr(metadata, "requires_approval", False)):
        return RiskClass.EXTERNAL
    return RiskClass.READ


def is_consequential(risk: RiskClass) -> bool:
    """Anything but a pure read needs the permission engine's attention."""
    return risk is not RiskClass.READ
