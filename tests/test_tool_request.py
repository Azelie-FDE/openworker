"""`request_tool` — the agent asks for a missing CLI instead of dropping the check (OPE-85).

Engine-intercepted like `request_directory`: it never goes through the permission path,
because the user's out-of-band decision IS the consent.
"""

from __future__ import annotations

import pytest

from coworker.engine import EventType, TurnEngine
from coworker.permissions import Mode, PermissionEngine
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from coworker.tools import ToolRegistry


class ScriptedProvider(ProviderClient):
    """One turn that calls request_tool, then a plain reply."""

    def __init__(self, tool: str = "gitleaks"):
        self.calls = 0
        self.tool = tool

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls += 1
        if self.calls == 1:
            return AssistantTurn(
                text="",
                tool_calls=[
                    ToolCall(
                        id="t1",
                        name="request_tool",
                        arguments={"name": self.tool, "reason": "scan history for secrets"},
                    )
                ],
            )
        return AssistantTurn(text="done", tool_calls=[])

    def capabilities(self, model):
        return ModelCapabilities(tools=True)


def _engine(tmp_path, requester, tool: str = "gitleaks"):
    return TurnEngine(
        provider=ScriptedProvider(tool),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path, mode=Mode.INTERACTIVE),
        model="m",
        tool_requester=requester,
    )


async def _run(engine) -> list:
    return [e async for e in engine.run("check this repo")]


@pytest.mark.asyncio
async def test_emits_tool_requested_and_reports_install(tmp_path):
    async def requester(args, tool_call_id=None):
        assert args["name"] == "gitleaks"
        return {"installed": True, "path": "/tmp/gitleaks", "version": "8.30.1"}

    events = await _run(_engine(tmp_path, requester))
    requested = [e for e in events if e.type is EventType.TOOL_REQUESTED]
    assert requested and requested[0].data["name"] == "gitleaks"
    finished = [e for e in events if e.type is EventType.TOOL_FINISHED]
    assert finished[0].data["status"] == "ok"


@pytest.mark.asyncio
async def test_declining_tells_the_agent_to_fall_back_openly(tmp_path):
    """A refusal must not read as 'check done'. The tool result has to push the agent
    toward a disclosed fallback, which is the whole point of the contract."""

    async def requester(args, tool_call_id=None):
        return {"installed": False, "reason": "the user declined to install it"}

    engine = _engine(tmp_path, requester)
    events = await _run(engine)
    assert [e for e in events if e.type is EventType.TOOL_REQUESTED]
    finished = [e for e in events if e.type is EventType.TOOL_FINISHED]
    assert finished[0].data["status"] == "denied"

    tool_msg = [m for m in engine.messages if m.get("role") == "tool"][-1]
    body = str(tool_msg["content"]).lower()
    assert "degraded" in body or "fallback" in body


@pytest.mark.asyncio
async def test_event_tells_the_truth_about_installability(tmp_path, monkeypatch):
    """Owner-hit 2026-08-14: the card offered Install for a tool with no pinned build —
    the surface guessed because the event said nothing. The event must carry the
    registry's verdict, and no metadata means NO."""
    from coworker import toolchain

    monkeypatch.setattr(toolchain, "_platform_key", lambda: "darwin_arm64")

    async def requester(args, tool_call_id=None):
        return {"installed": False, "reason": "declined"}

    events = await _run(_engine(tmp_path, requester, tool="gitleaks"))
    data = [e for e in events if e.type is EventType.TOOL_REQUESTED][0].data
    assert data["installable"] is True
    assert data["version"] == toolchain.MANAGED["gitleaks"].version
    assert data["summary"]
    assert data["source"] == "github.com/gitleaks"

    events = await _run(_engine(tmp_path, requester, tool="not-a-managed-tool"))
    data = [e for e in events if e.type is EventType.TOOL_REQUESTED][0].data
    assert data["installable"] is False
    assert data["version"] == "" and data["summary"] == ""


@pytest.mark.asyncio
async def test_no_requester_still_returns_guidance(tmp_path):
    """Headless surfaces have nobody to ask — the agent must still be told to disclose
    rather than assume the check passed."""
    engine = _engine(tmp_path, None)
    events = await _run(engine)
    assert not [e for e in events if e.type is EventType.TOOL_REQUESTED]
    tool_msg = [m for m in engine.messages if m.get("role") == "tool"][-1]
    assert "degraded" in str(tool_msg["content"]).lower()
