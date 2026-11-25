#!/usr/bin/env python3
"""claude-compat.py 시험. 임시 HOME 에서 가짜 훅 목록과 실제 훅 목록으로 각각 확인함."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
COMPAT = REPO / "codex/hooks/claude-compat.py"


class Case(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.home)
        self.log = self.home / "log.jsonl"
        (self.home / ".claude").mkdir()
        (self.home / ".claude/hooks").symlink_to(REPO / "claude/hooks")
        self.state = self.home / "state"

    def call(self, event, settings=None, **fields):
        env = {**os.environ, "HOME": str(self.home), "CLAUDE_HOOKS_STATE": str(self.state), "LOG": str(self.log)}
        for k in ("ORCA_ROLE", "CLAUDE_CONFIG_DIR", "LRM_HUB_DIR", "CODEX_COMPAT_SETTINGS"):
            env.pop(k, None)
        if settings is not None:
            f = self.home / "settings.json"
            f.write_text(json.dumps({"hooks": {event: settings}}))
            env["CODEX_COMPAT_SETTINGS"] = str(f)
        payload = {"hook_event_name": event, "session_id": "s1", "cwd": str(self.home), **fields}
        r = subprocess.run([sys.executable, str(COMPAT)], input=json.dumps(payload), text=True, capture_output=True, env=env, timeout=60)
        return r, (json.loads(r.stdout) if r.stdout.strip() else {})

    def seen(self):
        return [json.loads(x) for x in self.log.read_text().splitlines()] if self.log.exists() else []

    @staticmethod
    def group(cmd, matcher=None, timeout=5):
        return {**({"matcher": matcher} if matcher else {}), "hooks": [{"type": "command", "command": cmd, "timeout": timeout}]}

    @staticmethod
    def out(**spec):
        return "echo '" + json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", **spec}}) + "'"


SAVE = 'jq -c . >> "$LOG"'
PATCH = "*** Begin Patch\n*** Add File: a.py\n+x = 1\n+y = 2\n*** Update File: b.py\n@@\n-old\n+new\n*** Delete File: c.py\n*** End Patch"


class Fake(Case):
    def pre(self, groups, tool="Bash", inp=None):
        return self.call("PreToolUse", groups, tool_name=tool, tool_input=inp or {"command": "ls"})

    def decision(self, o):
        return o.get("hookSpecificOutput", {}).get("permissionDecision")

    def test_ask_becomes_deny(self):
        _, o = self.pre([self.group(self.out(permissionDecision="ask", permissionDecisionReason="확인"), "Bash")])
        self.assertEqual(self.decision(o), "deny")
        self.assertIn("확인", o["hookSpecificOutput"]["permissionDecisionReason"])

    def test_any_deny_wins(self):
        _, o = self.pre([self.group(self.out(permissionDecision="allow", updatedInput={"command": "x"}), "Bash"),
                         self.group(self.out(permissionDecision="deny", permissionDecisionReason="막음"), "Bash")])
        self.assertEqual(self.decision(o), "deny")
        self.assertNotIn("updatedInput", o["hookSpecificOutput"])

    def test_updated_input_kept_and_bare_allow_dropped(self):
        _, o = self.pre([self.group(self.out(permissionDecision="allow", updatedInput={"command": "y"}), "Bash")])
        self.assertEqual(o["hookSpecificOutput"]["updatedInput"], {"command": "y"})
        r, o = self.pre([self.group(self.out(permissionDecision="allow"), "Bash")])
        self.assertEqual((r.returncode, o), (0, {}))

    def test_exit2_denies(self):
        _, o = self.pre([self.group("echo 이유 >&2; exit 2", "Bash")])
        self.assertEqual((self.decision(o), o["hookSpecificOutput"]["permissionDecisionReason"]), ("deny", "이유"))

    def test_broken_guard_blocks(self):
        for cmd in ("exit 1", "echo 글자", "/없는/파일"):
            r, _ = self.pre([self.group(cmd, "Bash")])
            self.assertEqual(r.returncode, 2, cmd)
        r, _ = self.pre([self.group("sleep 5", "Bash", 1)])
        self.assertEqual(r.returncode, 2)

    def test_broken_hook_elsewhere_only_warns(self):
        r, _ = self.call("Stop", [self.group("exit 1")])
        self.assertEqual(r.returncode, 1)

    def test_matcher(self):
        self.pre([self.group(SAVE, "Write|Edit"), self.group("exit 2", "Bas")])
        self.assertEqual(self.seen(), [])
        r, _ = self.pre([self.group(SAVE, "mcp__(a|b)__(x|y)")], "mcp__b__y", {"k": 1})
        self.assertEqual(self.seen()[0]["tool_input"], {"k": 1})

    def test_patch_split(self):
        self.pre([self.group(SAVE, "Write|Edit|Bash")], "apply_patch", {"command": PATCH})
        got = [(v["tool_name"], Path(v["tool_input"]["file_path"]).name) for v in self.seen()]
        self.assertEqual(got, [("Write", "a.py"), ("Edit", "b.py"), ("Edit", "c.py")])
        self.assertEqual(self.seen()[0]["tool_input"]["content"], "x = 1\ny = 2\n")
        self.assertTrue(self.seen()[0]["tool_input"]["file_path"].startswith(str(self.home)))

    def test_patch_without_matcher_runs_once(self):
        self.call("PostToolUse", [self.group(SAVE)], tool_name="apply_patch", tool_input={"command": PATCH})
        self.assertEqual(len(self.seen()), 1)

    def test_patch_unreadable_blocks(self):
        r, _ = self.pre([self.group(SAVE, "Edit")], "apply_patch", {"command": "모르는 형식"})
        self.assertEqual(r.returncode, 2)

    def test_shell_patch_checks_files(self):
        self.pre([self.group(SAVE, "Write|Edit")], "Bash", {"command": "apply_patch <<'P'\n" + PATCH + "\nP"})
        self.assertEqual(len(self.seen()), 3)

    def test_cmd_key_and_session_prefix(self):
        self.pre([self.group(SAVE, "Bash")], "Bash", {"cmd": "ls -a"})
        self.assertEqual((self.seen()[0]["tool_input"]["command"], self.seen()[0]["session_id"]), ("ls -a", "codex-s1"))

    def test_spawn_agent(self):
        self.pre([self.group(SAVE, "Agent|Task")], "spawn_agent", {"agent_type": "explorer"})
        self.assertEqual(self.seen()[0]["tool_input"]["subagent_type"], "explorer")

    def test_scoped_test_only_after_edit(self):
        cmd = ": scoped-test.sh; " + SAVE
        self.call("Stop", [self.group(cmd)])
        self.assertEqual(self.seen(), [])
        (self.state / "sessions").mkdir(parents=True)
        (self.state / "sessions/codex-s1.files").write_text("a.py\n")
        self.call("Stop", [self.group(cmd)])
        self.assertEqual(len(self.seen()), 1)

    def test_bad_input_blocks(self):
        r = subprocess.run([sys.executable, str(COMPAT)], input="{깨짐", text=True, capture_output=True)
        self.assertEqual(r.returncode, 2)


class Real(Case):
    """실제 Claude 훅 목록. 이 파일에 훅 이름을 따로 적지 않아도 새 훅이 따라옴"""

    def decide(self, tool, inp):
        r, o = self.call("PreToolUse", tool_name=tool, tool_input=inp)
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        return o.get("hookSpecificOutput", {}).get("permissionDecision", "pass")

    def test_guards(self):
        plan = str(self.home / ".config/lrm-hubs/plan-approvals/x.json")
        for tool, inp, want in (
            ("Bash", {"command": "ls"}, "pass"),
            ("Bash", {"command": "git push --force origin x"}, "deny"),
            ("Bash", {"command": "find / -name a"}, "deny"),
            ("Bash", {"command": "gh pr create --title a --body b"}, "deny"),
            ("Bash", {"command": "~/.config/lrm-hubs/slack_notify.sh hi"}, "deny"),
            ("Bash", {"command": "git worktree add ../x"}, "deny"),
            ("apply_patch", {"command": f"*** Begin Patch\n*** Add File: {plan}\n+{{}}\n*** End Patch"}, "deny"),
            ("apply_patch", {"command": PATCH}, "pass"),
            ("mcp__atlassian__transitionJiraIssue", {"issueIdOrKey": "LRM-1", "transition": {"id": "31"}}, "deny"),
        ):
            with self.subTest(tool=tool, inp=inp):
                self.assertEqual(self.decide(tool, inp), want)

    def test_all_claude_events_known(self):
        base = json.loads((REPO / "claude/settings.base.json").read_text())["hooks"]
        ours = json.loads((REPO / "codex/hooks.base.json").read_text())["hooks"]
        no_codex = {"PreCompact", "Notification", "StopFailure"}  # Codex 에 없거나(뒤 둘) 요약 지시를 못 받는(PreCompact) 사건
        self.assertEqual(set(base) - no_codex, set(ours))


if __name__ == "__main__":
    unittest.main()
