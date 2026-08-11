"""Phase C (OPE-61) — the shipped security coworker bundles.

Three builtin bundles (security, cloud-posture, dep-audit) live as self-contained dirs
(manifest.md + skills/) under personas/builtin/. Each is code-family, drives OSS
scanners via the vetted catalog only, and its skills reach only its own sessions.
"""

from __future__ import annotations

from pathlib import Path

from coworker.personas.registry import PersonaRegistry
from coworker.providers import ModelCapabilities, ProviderClient
from coworker.server.manager import SessionManager
from coworker.sessions import SessionRecord

BUNDLES = {
    "security": {"semgrep-review", "secret-scan", "security-fix-pr"},
    "cloud-posture": {"iac-scan", "aws-posture"},
    "dep-audit": {"dependency-audit", "safe-upgrade-pr"},
}


class ScriptedProvider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        raise AssertionError("no turns expected")

    def capabilities(self, model):
        return ModelCapabilities()


def _reg(tmp_path) -> PersonaRegistry:
    return PersonaRegistry(state_path=tmp_path / "personas.json")


def test_bundles_register_as_enabled_code_builtins(tmp_path):
    reg = _reg(tmp_path)
    for pid in BUNDLES:
        entry = reg.get(pid)
        assert entry is not None and entry.builtin
        assert entry.family == "code"  # folder pick at send, like Code
        assert reg.is_enabled(pid) is True  # in the picker out of the box
        agent = reg.agent(pid)  # catalog-expanded tools materialize
        assert agent.family == "code" and agent.needs_workspace


def test_bundle_skill_folders_match_their_manifests(tmp_path):
    reg = _reg(tmp_path)
    for pid, expected in BUNDLES.items():
        m = reg.get(pid).manifest
        assert set(m.skills) == expected
        skills_dir = Path(m.source).parent / "skills"
        on_disk = {p.name for p in skills_dir.iterdir() if (p / "SKILL.md").is_file()}
        # Every listed skill exists; nothing ships unlisted.
        assert on_disk == expected


def test_bundle_skills_stay_with_their_persona(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    mgr = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    for i, (pid, expected) in enumerate(BUNDLES.items()):
        sid = f"s{i}"
        mgr.session_store.save(
            SessionRecord(session_id=sid, workspace="", model="m", mode="interactive", agent=pid)
        )
        names = mgr.effective_skill_names(sid)
        assert expected <= names
        # No leakage from the sibling bundles.
        others = set().union(*(v for k, v in BUNDLES.items() if k != pid))
        assert not (others & names)


def test_prompts_carry_the_positioning_guardrails(tmp_path):
    # "Drive scanners, never replace them" + safe-ops language is the product stance —
    # a reworded manifest that drops it should fail loudly, not ship quietly.
    reg = _reg(tmp_path)
    for pid in BUNDLES:
        prompt = reg.get(pid).manifest.system_prompt.lower()
        assert "todo_write" in prompt
        assert "drive" in prompt  # drives scanners; value is judgment/remediation
    assert "read-only" in reg.get("cloud-posture").manifest.system_prompt.lower()
    assert "never print a discovered secret" in reg.get("security").manifest.system_prompt.lower()
