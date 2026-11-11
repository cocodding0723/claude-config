#!/usr/bin/env python3
"""PR CI 결론을 Slack 봇 DM 으로 1회 알림 + AI 리뷰 새 지적을 메인 터미널에 전달. launchd(ai.lrm.cinotify) 5분 주기.
실패는 warn + 검사별 원인 한 줄, 감시 검사가 전부 통과하면 done 한 줄. 진행 중은 보내지 않음.

감시 PR: ci-watch.txt(한 줄에 PR 번호, # 주석) — 없거나 비면 열린 PR 중 내 것.
감시 검사: PR Test Pipeline 워크플로 전체 + 'AI Review Gate (n/5)'. 검사가 새로 실패하거나 PR 전체가 통과로 정해질 때 보냄.
같은 결론은 상태 파일(ci-watch.state.json)로 다시 보내지 않음. 첫 실행은 기준만 기록하고 보내지 않음.
사용: ci_watch.py [--dry-run]   (--dry-run: 보낼 메시지를 출력만, 상태 파일 갱신 안 함)
"""
import json
import os
import tempfile
import xml.etree.ElementTree as ET
import re
import subprocess
import sys
from pathlib import Path

import ci_review as review

DIR = Path(os.environ.get("LRM_HUB_DIR", Path.home() / ".config/lrm-hubs"))
REPO = os.environ.get("LRM_CI_REPO", "Basakansoft/LoveRevolutionMaker")
WATCH_FILE = DIR / "ci-watch.txt"
STATE = DIR / "ci-watch.state.json"
GH = os.environ.get("LRM_GH", "gh")
NOTIFY = os.environ.get("LRM_NOTIFY", str(DIR / "slack_notify.sh"))
ORCA = os.environ.get("LRM_ORCA", "orca")
MAIN_ENV = os.environ.get("LRM_MAIN_ENV", str(DIR / "main.env"))
HUB_TABLE = DIR / "ci-review-hubs.tsv"  # 정규식<TAB>허브. 브랜치·제목에 위에서부터 맞춤
REVIEW_OUT = DIR / "ci-review"
REVIEW_INIT = "_review_init"
SEEN = "_seen"
REVIEWER_ERROR = "리뷰어 오류(재실행 대상)"
HEAD = "_head"  # 마지막으로 본 PR 머리 커밋
FAIL_SENT = "_fail_sent"
ADV_HEAD = "_adv_head"  # 적대적 리뷰를 띄운 head 커밋. 묶음 PR 당 1회(fixup 마다 재실행 안 함)
ADV_BRANCH = re.compile(os.environ.get("LRM_ADV_BRANCH", r"(^|/)bundle-"))  # 묶음 PR 브랜치
ADV_LABEL = os.environ.get("LRM_ADV_LABEL", "bundle")
def adv_scope():
    """all: 모든 ready PR · bundle: 묶음 PR 만. 환경 변수 > adv-review.env 의 ADV_SCOPE > all"""
    if os.environ.get("LRM_ADV_SCOPE"):
        return os.environ["LRM_ADV_SCOPE"]
    try:
        for ln in (DIR / "adv-review.env").read_text(encoding="utf-8").splitlines():
            if ln.strip().startswith("ADV_SCOPE="):
                return ln.split("=", 1)[1].strip()
    except OSError:
        pass
    return "all"
ADV_INIT = "_adv_init"
ADV = os.environ.get("LRM_ADV", str(Path(__file__).resolve().parent / "adv_review.py"))  # 메인에 실패를 전달한 커밋(같은 커밋 실패는 1회)  # PR 별 이미 전달한 AI 리뷰 지적 id
DONE = {"pass", "fail", "cancel"}
VERDICT = "_verdict"  # PR 단위로 마지막에 보낸 결론(pass|fail)
# 실패 로그에서 원인 한 줄로 쓸 줄. 앞쪽 패턴일수록 우선
CAUSE = [re.compile(p) for p in (r"Shader error.*", r"not ok \d+.*", r"[^\s].*없음.*", r"##\[error\].*", r"(?i)\berror\b[: ].*", r"(?i)failed(?! 0\b).*")]  # 'FAILED 0' 요약 줄은 원인 아님
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
            return [json.loads(gh("pr", "view", n, "-R", REPO, "--json", "number,title,headRefName,headRefOid,isDraft,labels")) for n in nums]
    except OSError:
        pass
    return json.loads(gh("pr", "list", "-R", REPO, "--author", "@me", "--state", "open", "--json", "number,title,headRefName,headRefOid,isDraft,labels"))


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
    return xml_cause(m.group(1))


def xml_cause(run_id):
    """로그에 원인 줄이 없을 때 테스트 결과 아티팩트(xml)의 첫 실패 메시지. 로그 요약이 'FAILED 0' 으로 틀릴 때 대비"""
    with tempfile.TemporaryDirectory() as tmp:
        try:
            gh("run", "download", run_id, "-R", REPO, "-p", "*[Tt]est*", "-D", tmp)
        except (RuntimeError, subprocess.TimeoutExpired):
            return ""
        for x in sorted(Path(tmp).rglob("*.xml")):
            try:
                root = ET.parse(x).getroot()
            except ET.ParseError:
                continue
            for case in root.iter("test-case"):
                if case.get("result") == "Failed":
                    msg = case.find("./failure/message")
                    text = " ".join((msg.text or "").split()) if msg is not None else ""
                    return f"{case.get('name', '')}: {text}"[:120]
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


def relay_review(pr, checks, prev, baseline, dry):
    """AI 리뷰가 끝났으면 새 지적을 메인 터미널에 전달. 첫 실행은 기존 지적을 본 것으로만 기록"""
    if not review.review_finished(checks):
        return
    try:
        finds = [f for c in review.comments(gh, REPO, pr["number"]) for f in review.parse(c)]
    except (RuntimeError, ValueError) as err:
        print(f"PR {pr['number']} 리뷰 조회 실패: {err}", file=sys.stderr)
        return
    seen = set(prev.get(SEEN, []))
    new = [f for f in finds if f["id"] not in seen]
    if not new:
        return
    errs = [f for f in new if f["kind"] == "오류"]
    if errs and not baseline:
        # 리뷰어 실패는 지적이 아니므로 허브에 배정하지 않고 재실행 대상으로만 알림
        handle = review.main_handle(MAIN_ENV)
        if dry:
            print(f"--- review-error\n{review.error_text(pr, errs)}")
        elif not (handle and review.send_main(ORCA, handle, review.error_text(pr, errs))):
            return
        new = [f for f in new if f["kind"] != "오류"]
        if not new:
            if not dry:
                prev[SEEN] = sorted(seen | {e["id"] for e in errs})
            return
        seen |= {e["id"] for e in errs}
    if not baseline:
        hub = review.hub_for(pr.get("headRefName", ""), pr.get("title", ""), review.load_table(HUB_TABLE))
        if dry:
            print(f"--- review\n{review.relay_text(pr, hub, len(new), '(dry-run)')}")
            for f in new:
                print(f"  [{f['kind']}·{f['role']}] {f['text'][:100]}")
            return
        path = review.write_summary(REVIEW_OUT, pr, hub, new)
        handle = review.main_handle(MAIN_ENV)
        if not (handle and review.send_main(ORCA, handle, review.relay_text(pr, hub, len(new), path))):
            # 메인 전달 실패: Slack 경고로 알리고 다음 주기에 다시 전달
            subprocess.run([NOTIFY, "--kind", "warn", "--tag", "ci-review", f"AI 리뷰 지적 메인 전달 실패\n\n• 무엇: PR #{pr['number']} 지적 {len(new)}건\n• 왜: 메인 터미널 전송 실패\n• 결과: 다음 주기에 다시 보냄\n\n👉 답: 메인 세션에서 set_main_terminal.sh 로 핸들 확인\n\n참고: {path}"])
            return
    prev[SEEN] = sorted(seen | {f["id"] for f in new})


def reviewer_errors(pr):
    """이 PR 의 AI 리뷰 코멘트 중 리뷰어 오류(ERROR) 수. 조회 실패는 0"""
    try:
        return sum(1 for c in review.comments(gh, REPO, pr["number"]) for f in review.parse(c) if f["kind"] == "오류")
    except (RuntimeError, ValueError):
        return 0


def relay_fail(pr, fails, prev, slack_ts=""):
    """CI 실패를 메인 터미널에 전달(허브가 고치도록 메인이 배정). 같은 커밋은 1회. 전송 실패는 Slack warn 이 대신함
    리뷰어 오류로만 실패한 경우는 'AI 리뷰 오류' 알림이 이미 갔으므로 보내지 않음"""
    fails = [c for c in fails if not c.get("reviewer_error")]
    if not fails:
        return
    sha = pr.get("headRefOid") or "unknown"
    if prev.get(FAIL_SENT) == sha:
        return
    hub = review.hub_for(pr.get("headRefName", ""), pr.get("title", ""), review.load_table(HUB_TABLE))
    path = review.write_fail(REVIEW_OUT, pr, hub, sha, fails, slack_ts)
    first = fails[0]
    why = first.get("cause") or "원인 줄 못 찾음"
    handle = review.main_handle(MAIN_ENV)
    text = f"[CI→메인] CI 실패 PR#{pr['number']} · 담당 허브 {hub} · 원인 {first['key']}: {why} · 파일 {path}"
    if handle and review.send_main(ORCA, handle, text):
        prev[FAIL_SENT] = sha


def is_bundle(pr):
    return bool(ADV_BRANCH.search(pr.get("headRefName", ""))) or any(l.get("name") == ADV_LABEL for l in pr.get("labels") or [])


def start_adv(pr, prev, baseline, dry):
    """PR 이 draft 아닌 상태(ready)로 처음 보이면 적대적 리뷰를 백그라운드로 1회. fixup 커밋마다 재실행 안 함.
    ADV_SCOPE=bundle 이면 묶음 PR 만. 비용 상한은 adv_review.py 의 ADV_DAILY_MAX. 다시 돌리려면 adv_review.py --pr N"""
    head = pr.get("headRefOid")
    if not head or pr.get("isDraft") or (adv_scope() == "bundle" and not is_bundle(pr)) or prev.get(ADV_HEAD):
        return
    if not baseline:
        if dry:
            print(f"--- adv\nPR #{pr['number']} 적대적 리뷰 시작 예정 ({head[:12]})")
            return
        subprocess.Popen([sys.executable, ADV, "--pr", str(pr["number"]), "--head", head], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=open(DIR / "adv-review.log", "a"), start_new_session=True)
    prev[ADV_HEAD] = head


def main():
    dry = "--dry-run" in sys.argv[1:]
    try:
        state = json.loads(STATE.read_text())
    except (OSError, ValueError):
        state = {}
    first = not state
    # 리뷰 전달 기능을 처음 켠 실행은 이미 달린 지적을 본 것으로만 기록(옛 지적이 한꺼번에 가지 않게)
    review_base = not state.get(REVIEW_INIT)
    adv_base = not state.get(ADV_INIT)  # 처음 켠 실행은 지금 head 들을 본 것으로만 기록(일괄 실행 방지)
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
        sha = pr.get("headRefOid")
        if sha and prev.get(HEAD) not in (None, sha):
            # 새 커밋: 검사별 결론·PR 결론을 비워 같은 검사의 재실패도 다시 알림. 리뷰 지적·실패 전달 기록은 유지
            prev = {k: v for k, v in prev.items() if k in (SEEN, FAIL_SENT, ADV_HEAD)}  # 적대적 리뷰는 PR 당 1회라 유지
        if sha:
            prev[HEAD] = sha
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
        relay_review(pr, checks, prev, review_base, dry)
        start_adv(pr, prev, adv_base, dry)
        state[n] = prev
        if not verdict or first:
            continue
        if fails:
            gate_err = None
            for c in fails:
                if c["key"].startswith("AI Review Gate"):
                    gate_err = reviewer_errors(pr) if gate_err is None else gate_err
                    if gate_err:
                        # 게이트 실패가 리뷰어 오류 때문이면 고칠 코드가 없음. 오류 알림이 이미 메인에 가므로 원인만 한 줄로
                        c["cause"], c["reviewer_error"] = REVIEWER_ERROR, True
                        continue
                c["cause"] = cause(c["link"])
            kind, msg = message(pr, fails)
        else:
            kind, msg = passed_message(pr)
        if dry:
            print(f"--- {kind}\n{msg}")
            continue
        out = subprocess.run([NOTIFY, "--kind", kind, "--alive", "--tag", "ci-watch", msg], capture_output=True, text=True)
        if fails:
            # warn 의 ts 를 실패 파일에 남겨 허브가 같은 스레드에 원인·고칠 방법을 답하게 함
            ts = out.stdout.split()[1] if out.returncode == 0 and out.stdout.startswith("ok ") and len(out.stdout.split()) > 1 else ""
            relay_fail(pr, fails, prev, ts)
        rc = out.returncode
        if rc == 0:
            sent += 1
        else:  # 실패하면 다음 주기에 다시 보내도록 이번 결론을 기록하지 않음
            state[n] = before
    state[REVIEW_INIT] = True
    state[ADV_INIT] = True
    if not dry:
        tmp = STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False))
        tmp.replace(STATE)
    print(f"감시 {sum(1 for k in state if not k.startswith('_'))}건 · 전송 {sent}건" + (" · 첫 실행이라 기준만 기록" if first else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
