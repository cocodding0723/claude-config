#!/usr/bin/env python3
"""PR CI 결론을 Slack 봇 DM 으로 1회 알림. launchd(ai.lrm.cinotify) 5분 주기.
실패는 warn + 검사별 원인 한 줄, 감시 검사가 전부 통과하면 done 한 줄. 진행 중은 보내지 않음.

감시 PR: ci-watch.txt(한 줄에 PR 번호, # 주석) — 없거나 비면 열린 PR 중 내 것.
감시 검사: PR Test Pipeline 워크플로 전체 + 'AI Review Gate (n/5)'. 검사가 새로 실패하거나 PR 전체가 통과로 정해질 때 보냄.
같은 결론은 상태 파일(ci-watch.state.json)로 다시 보내지 않음. 첫 실행은 기준만 기록하고 보내지 않음.
사용: ci_watch.py [--dry-run]   (--dry-run: 보낼 메시지를 출력만, 상태 파일 갱신 안 함)
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

DIR = Path(os.environ.get("LRM_HUB_DIR", Path.home() / ".config/lrm-hubs"))
REPO = os.environ.get("LRM_CI_REPO", "Basakansoft/LoveRevolutionMaker")
WATCH_FILE = DIR / "ci-watch.txt"
STATE = DIR / "ci-watch.state.json"
GH = os.environ.get("LRM_GH", "gh")
NOTIFY = os.environ.get("LRM_NOTIFY", str(DIR / "slack_notify.sh"))
DONE = {"pass", "fail", "cancel"}
VERDICT = "_verdict"  # PR 단위로 마지막에 보낸 결론(pass|fail)
# 실패 로그에서 원인 한 줄로 쓸 줄. 앞쪽 패턴일수록 우선
CAUSE = [re.compile(p) for p in (r"not ok \d+.*", r"[^\s].*없음.*", r"##\[error\].*", r"(?i)\berror\b[: ].*", r"(?i)failed.*")]
TS_PREFIX = re.compile(r"^.*?\d{4}-\d\d-\d\dT[\d:.]+Z ?")  # gh --log-failed 줄머리(잡·단계·시각) 제거
TICKET = re.compile(r"\[?LRM-\d+\]?\s*")


def watched(check):
    return check.get("workflow") == "PR Test Pipeline" or check.get("name", "").startswith("AI Review Gate")


def key(check):
    return re.sub(r"\s*\(\d+/\d+\)$", "", check["name"])  # 'AI Review Gate (4/5)' 의 진행 수는 매번 바뀜


def gh(*args):
    out = subprocess.run([GH, *args], capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])} 실패: {out.stderr.strip()[:200]}")
    return out.stdout


def pr_list():
    try:
        nums = [ln.split("#")[0].strip() for ln in WATCH_FILE.read_text().splitlines()]
        nums = [n for n in nums if n.isdigit()]
        if nums:
            return [{"number": int(n), "title": ""} for n in nums]
    except OSError:
        pass
    return json.loads(gh("pr", "list", "-R", REPO, "--author", "@me", "--state", "open", "--json", "number,title"))


def cause(link):
    """실패 잡 로그에서 원인 한 줄. 못 찾으면 빈 문자열"""
    m = re.search(r"/runs/(\d+)/job/(\d+)", link or "")
    if not m:
        return ""
    try:
        log = gh("run", "view", m.group(1), "-R", REPO, "--job", m.group(2), "--log-failed")
    except (RuntimeError, subprocess.TimeoutExpired):
        return ""
    lines = [TS_PREFIX.sub("", ln).strip() for ln in log.splitlines()]
    for rx in CAUSE:
        for ln in lines:
            hit = rx.search(ln)
            if hit:
                return hit.group(0).strip()[:120]
    return ""


def message(pr, fails):
    lines = [f"PR #{pr['number']} CI 실패 원인", ""]
    lines += [f"• {c['key']}: {c.get('cause') or '원인 줄 못 찾음 — 로그 확인'}" for c in fails]
    lines += ["", "👉 답: 고칠 곳 확인 뒤 다시 실행"]
    ref = [TICKET.sub("", pr.get("title", "")).strip()] + [c["link"] for c in fails if c.get("link")]
    lines += ["", "참고: " + " · ".join(x for x in ref if x)]  # 제목의 티켓 번호는 양식 검사 대상이라 뺌
    return "warn", "\n".join(lines)


def passed_message(pr):
    ref = " · ".join(x for x in (f"PR #{pr['number']}", TICKET.sub("", pr.get("title", "")).strip()) if x)
    return "done", f"CI 통과\n\n참고: {ref}"


def main():
    dry = "--dry-run" in sys.argv[1:]
    try:
        state = json.loads(STATE.read_text())
    except (OSError, ValueError):
        state = {}
    first = not state
    sent = 0
    for pr in pr_list():
        n = str(pr["number"])
        try:
            checks = json.loads(gh("pr", "checks", n, "-R", REPO, "--json", "name,bucket,link,workflow"))
        except (RuntimeError, ValueError) as err:
            print(f"PR {n}: {err}", file=sys.stderr)
            continue
        before = dict(state.get(n, {}))  # 전송 실패 시 되돌릴 값. prev 는 아래에서 바뀜
        prev = dict(before)
        mine = list(filter(watched, checks))
        fails = []
        for c in mine:
            k = key(c)
            if c["bucket"] == "fail" and prev.get(k) != "fail":
                fails.append({"key": k, "bucket": "fail", "link": c.get("link", "")})
            if c["bucket"] in DONE:
                prev[k] = c["bucket"]
        # 통과 판정: 감시 검사가 있고 전부 끝났으며 실패·취소 없음. 건너뜀(skipping)은 통과로 봄
        passed = bool(mine) and all(c["bucket"] in ("pass", "skipping") for c in mine)
        verdict = "fail" if fails else "pass" if passed and prev.get(VERDICT) != "pass" else None
        if verdict:
            prev[VERDICT] = verdict
        state[n] = prev
        if not verdict or first:
            continue
        if fails:
            for c in fails:
                c["cause"] = cause(c["link"])
            kind, msg = message(pr, fails)
        else:
            kind, msg = passed_message(pr)
        if dry:
            print(f"--- {kind}\n{msg}")
            continue
        rc = subprocess.run([NOTIFY, "--kind", kind, "--alive", "--tag", "ci-watch", msg]).returncode
        if rc == 0:
            sent += 1
        else:  # 실패하면 다음 주기에 다시 보내도록 이번 결론을 기록하지 않음
            state[n] = before
    if not dry:
        tmp = STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False))
        tmp.replace(STATE)
    print(f"감시 {len(state)}건 · 전송 {sent}건" + (" · 첫 실행이라 기준만 기록" if first else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
