"""시험용 원료 파일 생성. 사용: fixtures.py <가짜 orca 폴더> <작업 폴더> <화면 표식> <토큰 모양 문자열>
화면 표식·토큰 문자열은 preview 와 Slack 기록에 심어 두고, 시험이 산출물에 새지 않는지 확인함."""
import json
import os
import sys
import time

fake, hub, mark, tok = sys.argv[1:5]
now_ms = int(time.time() * 1000)
B = "─" * 40


def term(path, agent, preview, quiet_min, connected=True, orphaned=False):
    return {"handle": "term_" + os.urandom(4).hex(), "worktreePath": path, "agentIdentity": agent,
            "preview": preview, "lastOutputAt": now_ms - quiet_min * 60000 if quiet_min is not None else None,
            "connected": connected, "orphaned": orphaned, "writable": True}


W = "/ws/proj"
terms = [
    term(f"{W}/coord-busy-hub", "claude", f"{mark} 작업 내용\n✻ Thinking… (12s)\n{B}\n❯ \n{B}", 0),
    term(f"{W}/coord-esc-hub", "claude", f"{mark} 진행\n  esc to interrupt\n", 0),
    term(f"{W}/coord-idle-hub", "claude", f"{mark} 끝났다\n✻ Worked for 3m\n{B}\n❯ \n{B}\n  ? for shortcuts", 0),
    term(f"{W}/coord-ask-hub", "claude", f"{mark}\nDo you want to proceed?\n❯ 1. Yes\n  2. No", 1),
    term(f"{W}/coord-unk-hub", "claude", f"{mark} 무언가 출력 중\n진행 로그 한 줄\n· 설치 정리 중…", 0),
    term(f"{W}/coord-quiet-hub", "claude", f"{mark} 마지막 보고 줄", 12),
    term(f"{W}/coord-gone-hub", "claude", f"{mark}\n✻ Thinking…", 3, connected=False),
    term(f"{W}/coord-dup-hub", "claude", f"{mark}\n{B}\n❯ \n{B}", 5),
    term(f"{W}/coord-dup-hub", "claude", f"{mark}\n{B}\n❯ \n{B}", 40),
    term(f"{W}/coord-noagent-hub", "", f"{mark} 셸", 1),
    term(f"{W}/feature-branch", "claude", f"{mark} 워커", 0),
    term(f"/other/coord-elsewhere-hub", "codex", f"{mark}\n✻ Thinking…", 0),
]
for i, t in enumerate(terms[:3]):
    t["worktreeId"] = f"wt_{i}"
json.dump({"ok": True, "result": {"terminals": terms}}, open(os.path.join(fake, "terms.json"), "w"), ensure_ascii=False)
json.dump({"ok": True, "result": {"terminals": terms, "totalCount": 99, "truncated": True}},
          open(os.path.join(fake, "terms_alt.json"), "w"), ensure_ascii=False)


def page(rows, cursor, alt=None):
    """실제 worker-list 모양: 최신이 앞, page.hasMore·nextCursor, counts(터미널 상태별 전체 총계, 첫 쪽부터 같은 값)"""
    pg = {"limit": 100, "total": 5, "hasMore": cursor is not None, **({"nextCursor": cursor} if cursor else {})}
    return {"ok": True, "result": {"workers": rows, "page": pg, "scope": {"source": "all"},
                                   "counts": {"active": 1, "retained": 1, "reclaimable": 1, "released": 3}, "warnings": []}}


# 3쪽. 종료 뒤 남은 터미널(ctx_left·ctx_aband)이 둘째·셋째 쪽에만 있어 첫 쪽만 세면 0 이 됨. ctx_t1 은 쪽 사이 겹침(한 번만 셈)
W1 = {"taskId": "task_1", "runId": "run_a", "dispatchId": "ctx_t1", "workerState": "succeeded", "terminalState": "released",
      "projection": {"liveness": {"verdict": "exited"}}, "resource": {"releaseCompletedAt": "2026-11-10 05:04:08"}}
pages = {
    "workers": page([
        {"taskId": "task_live", "runId": "run_a", "dispatchId": "ctx_live", "workerState": "ready", "terminalState": "active",
         "projection": {"liveness": {"verdict": "live"}}, "resource": {}},
        W1], "c2"),
    "workers_c2": page([
        W1,
        {"taskId": "task_2", "runId": "run_b", "dispatchId": "ctx_left", "workerState": "failed", "terminalState": "retained",
         "projection": {"liveness": {"verdict": "exited"}}, "resource": {"releaseCompletedAt": None}}], "c3"),
    "workers_c3": page([
        {"taskId": "task_3", "runId": "run_a", "dispatchId": "ctx_t3", "workerState": "stopped", "terminalState": "released",
         "projection": {"liveness": {"verdict": "exited"}}, "resource": None},
        {"taskId": "task_4", "runId": "run_c", "dispatchId": "ctx_aband", "workerState": "abandoned", "terminalState": "reclaimable",
         "projection": {"liveness": {"verdict": "exited"}}, "resource": {}}], None),
}
# 끝나지 않는 커서(셋째 쪽 뒤로 빈 쪽이 끝없이 이어짐, fake_orca.sh 의 loopN) → 쪽 수 상한에서 멈춰야 함
pages["workers_c3_alt"] = page(pages["workers_c3"]["result"]["workers"], "loop1")
for name, body in pages.items():
    json.dump(body, open(os.path.join(fake, name + ".json"), "w"))
tasks = [{"id": "task_1", "status": "completed", "title": "수집기 만들기 " + "가" * 60},
         {"id": "task_2", "status": "failed", "title": "설치 정리"},
         {"id": "task_3", "status": "blocked", "title": "문서"},
         {"id": "task_4", "status": "failed", "title": "버려진 작업"}]
json.dump({"ok": True, "result": {"tasks": tasks, "runId": "run_a"}}, open(os.path.join(fake, "tasks.json"), "w"), ensure_ascii=False)
# 예상 밖 모양(tasks 가 숫자) → 수집기 안에서 TypeError. 그 원료만 실패해야 함
json.dump({"ok": True, "result": {"tasks": 5}}, open(os.path.join(fake, "tasks_alt.json"), "w"))

prs = [{"number": 1, "title": f"{mark}-gh", "statusCheckRollup": [{"conclusion": "SUCCESS"}]},
       {"number": 2, "statusCheckRollup": [{"conclusion": "SUCCESS"}, {"conclusion": "FAILURE"}]},
       {"number": 3, "statusCheckRollup": [{"state": "ERROR"}]},
       {"number": 4, "statusCheckRollup": []}]
json.dump(prs, open(os.path.join(fake, "prs.json"), "w"), ensure_ascii=False)
json.dump([{"number": n, "statusCheckRollup": []} for n in range(100)], open(os.path.join(fake, "prs_many.json"), "w"))

now = int(time.time())
sent = [
    {"at": now - 600, "channel": "C1", "ts": "1.1", "thread_ts": "", "title": "🙋 결정 A 고르기", "kind": "decide"},
    {"at": now - 300, "channel": "C1", "ts": "1.2", "thread_ts": "", "title": "🙋 결정 B 반응함", "kind": "decide"},
    {"at": now - 200, "channel": "C1", "ts": "1.3", "thread_ts": "", "title": "🙋 결정 C 완료 기록", "kind": "decide"},
    {"at": now - 100, "channel": "C1", "ts": "1.4", "thread_ts": "1.1", "title": "결정 답글", "kind": "text"},
    {"at": now - 90, "channel": "C1", "ts": "1.5", "thread_ts": "", "title": "📄 문서 공유", "kind": "doc"},
    {"at": now - 80, "channel": "C1", "ts": "1.6", "thread_ts": "", "title": "🙋 결정 A 고르기", "kind": "decide"},
    {"at": now - 60, "channel": "C1", "ts": "1.7", "thread_ts": "", "title": "🙋 결정 D " + "나" * 60, "kind": "decide"},
]
with open(os.path.join(hub, "slack-sent.jsonl"), "w", encoding="utf-8") as f:
    for s in sent:
        f.write(json.dumps(s, ensure_ascii=False) + "\n")
    f.write("깨진 줄\n")
with open(os.path.join(hub, "slack-inbox.jsonl"), "w", encoding="utf-8") as f:
    f.write(json.dumps({"type": "reaction", "parent_ts": "1.2", "text": f"{mark} 반응 본문"}, ensure_ascii=False) + "\n")
    f.write(json.dumps({"type": "note", "text": f"{mark} 사용자 원문 {tok}"}, ensure_ascii=False) + "\n")
open(os.path.join(hub, "slack-done.txt"), "w").write("1.3\n")
with open(os.path.join(hub, "slack-push.log"), "w", encoding="utf-8") as f:
    for i, line in enumerate([
        "push ok n=1 lines=1-1 mark=1",
        f"push hold reason=dialog lines=2-2 handle=term_abcdef {tok}",
        "push off reason=off_file",
        "push fail reason=send_timeout attempts=3 lines=3-3",
        "push ok n=2 lines=3-4 mark=4 confirm=receipt",
        "push hold reason=no_idle_marker lines=5-5",
    ]):
        f.write(f"2026-11-10 10:0{i}:00 {line}\n")
    f.write("형식이 다른 줄\n")

# 워크스페이스 25개(+메인 1개). wt_0~2 는 터미널이 있음 → 터미널 없는 것 22개. 번호가 작을수록 오래 쉼
home = os.environ["FIX_HOME"]
wts = [{"id": "wt_main", "path": f"{home}/repo", "isMainWorktree": True, "lastActivityAt": now_ms, "branch": "refs/heads/main"}]
for i in range(25):
    wts.append({"id": f"wt_{i}", "displayName": f"ws-{i:02d}", "path": f"{home}/ws/ws-{i:02d}" + ("-merged" if i == 3 else ""),
                "isMainWorktree": False, "branch": f"refs/heads/feat/w{i}", "baseRef": "" if i == 4 else "origin/main",
                "lastActivityAt": now_ms - (100 - i) * 86400000 if i != 5 else 0})
json.dump({"ok": True, "result": {"worktrees": wts, "truncated": False}}, open(os.path.join(fake, "worktrees.json"), "w"))
