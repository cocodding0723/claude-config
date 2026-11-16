#!/usr/bin/env python3
"""병합된 PR 로 작업 장부 행을 자동 완료함. 애매하면 완료하지 않고 후보로만 표시.

흐름: gh 로 병합 PR(대상 base 만)·열린 PR 조회 → 장부 행과 연결 판정 → dry-run 표 / --apply 로 task_ledger.set_row 완료.
쓰기는 task_ledger.set_row 경로만 씀(증거 형식 검사·잠금·원자적 교체 그대로). 장부 파일을 직접 고치지 않음.

연결 규칙(행 하나당 모든 조건 충족 시 자동 완료, 하나라도 어긋나면 후보):
  확실  a) 행 text·evidence·sources 에 같은 PR 번호(#n · pull/n · PR n · pr:n)
        b) 행의 티켓 키 LRM-n 이 병합 PR 제목·브랜치에 있고, 같은 키의 병합 PR 이 이 하나, 열린 PR 은 없음
  후보  키가 본문에만 · 같은 키 병합 PR 여러 개 · 같은 키 열린 PR 존재 · 미분류·버림 행 · needs 미완료
        · 행의 PR 번호·키 중 병합 확인 안 된 것이 있음(일부 병합) · 텍스트가 '병합' 이 아닌 후속 작업(화면 확인 등)
  건너뜀 이미 완료(증거 추가도 안 함) · PR 과 연결 없음 · base 가 dev·main 아닌 PR · 이미 처리한 PR

상태 파일(LRM_MERGED_STATE, 기본 ~/.config/lrm-hubs/ledger-merged.state.json): since(다음 조회 기준일)·processed(완료에 쓴 PR 번호)
  ·candidates(행 id → PR·사유). --apply 일 때만 씀. dry-run 은 어떤 파일도 바꾸지 않음. 같은 PR 은 다시 처리하지 않아 멱등.

환경: LRM_GH gh 실행 파일(기본 gh) · LRM_REPO 저장소(기본 Basakansoft/LoveRevolutionMaker) · LRM_TASKS 장부 · LRM_HUB_DIR 작업 폴더
종료 코드: 0 성공(dry-run 포함) · 1 gh 조회·파일 읽기·쓰기 실패 · 2 인자 오류(기준일 없음 포함) · 3 apply 중 장부에서 행이 사라짐
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))  # 링크·복사 설치에서도 같은 폴더의 장부 모듈을 찾음
import ledger  # noqa: E402
import task_ledger as tl  # noqa: E402
from ledger import LedgerError, EXIT_IO, EXIT_USAGE, EXIT_MISSING  # noqa: E402

# ---------- 설정(한 곳) ----------
REPO = os.environ.get("LRM_REPO") or "Basakansoft/LoveRevolutionMaker"
TARGET_BASES = ("dev", "main")
PR_LIMIT = 300
STATE_NAME = "ledger-merged.state.json"
PR_REF = re.compile(r"(?:#|pull/|\bPR\s*|\bpr:)(\d+)")
TICKET = re.compile(r"\bLRM-\d+\b")
MERGE_WORD = re.compile(r"병합|머지|merge", re.I)
NOTE = "병합 PR 자동 완료"
TEXT_CUT = 40
OVERLAP_DAYS = 1  # gh 검색은 날짜 단위라 기준일을 하루 겹쳐 잡고 processed 로 중복을 막음

REASONS = {
    "unsorted": "미분류 행", "dropped": "버림 행", "needs": "needs 미완료", "partial": "행의 PR·키 중 병합 확인 안 됨",
    "body_only": "키가 PR 본문에만 있음", "multi": "같은 키 병합 PR 여러 개", "open_pr": "같은 키 열린 PR 있음",
    "followup": "텍스트가 병합이 아닌 후속 작업",
}
SKIPS = {"already_done": "이미 완료 행", "no_link": "PR 과 연결 없음", "base_other": "base 가 dev·main 아닌 PR", "pr_processed": "이미 처리한 PR"}


def hub_dir() -> str:
    return os.environ.get("LRM_HUB_DIR") or os.path.expanduser("~/.config/lrm-hubs")


def state_path() -> str:
    return os.environ.get("LRM_MERGED_STATE") or os.path.join(hub_dir(), STATE_NAME)


# ---------- gh ----------

def gh_json(*args):
    """gh 실행 → JSON. 실행 파일 없음·실패·JSON 오류는 모두 exit 1(원인·조치 포함)"""
    cmd = [os.environ.get("LRM_GH") or "gh", *args]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as err:
        raise LedgerError(f"gh 실행 실패({err}) — gh 설치·로그인(gh auth status)·네트워크 확인", EXIT_IO)
    if res.returncode != 0:
        raise LedgerError(f"gh 조회 실패(종료 {res.returncode}: {res.stderr.strip()[:200]}) — gh auth status 로 로그인·저장소 권한 확인", EXIT_IO)
    try:
        return json.loads(res.stdout)
    except ValueError:
        raise LedgerError("gh 출력이 JSON 이 아님 — gh 버전 확인(gh --version)", EXIT_IO)


def fetch_merged(since: str) -> list:
    return gh_json("pr", "list", "-R", REPO, "--state", "merged", "--search", f"merged:>={since}", "--limit", str(PR_LIMIT),
                   "--json", "number,title,body,headRefName,mergedAt,baseRefName")


def fetch_open() -> list:
    return gh_json("pr", "list", "-R", REPO, "--state", "open", "--limit", str(PR_LIMIT), "--json", "number,title,headRefName,baseRefName")


def fetch_state(number: int) -> dict:
    return gh_json("pr", "view", str(number), "-R", REPO, "--json", "number,state,baseRefName")


# ---------- 판정 ----------

def keys_in(text: str) -> set:
    return set(TICKET.findall(text or ""))


def row_refs(row: dict) -> tuple:
    """행 → (PR 번호 집합, 티켓 키 집합). 번호는 text·evidence·sources·done_when, 키는 sources 의 context 를 뺀 같은 곳"""
    parts = [row.get("text", ""), row.get("done_when", ""), *row.get("evidence", []), *(str(s.get("ref", "")) for s in row.get("sources", []))]
    blob = "\n".join(map(str, parts))
    return {int(n) for n in PR_REF.findall(blob)}, keys_in(blob)


def short(text: str) -> str:
    return ledger.clean(text)[:TEXT_CUT]


class Judge:
    """한 번의 실행에서 쓰는 PR 조회 결과 모음과 행 판정"""

    def __init__(self, merged: list, opened: list, rows: list, processed: set, is_merged=None):
        self.skipped = {k: 0 for k in SKIPS}
        self.by_num, self.skip_pr = {}, set()
        for pr in merged:
            if pr["number"] in processed:
                self.skipped["pr_processed"] += 1
            elif pr.get("baseRefName") not in TARGET_BASES:
                self.skipped["base_other"] += 1
            else:
                self.by_num[pr["number"]] = pr
        self.processed = processed
        self.state = {r["id"]: r["state"] for r in rows}
        self.opened = opened
        self.is_merged = is_merged or (lambda n: False)

    def key_prs(self, key: str) -> tuple:
        """키 → (제목·브랜치에 키가 있는 병합 PR, 본문에만 있는 병합 PR)"""
        strong = [p for p in self.by_num.values() if key in keys_in(p["title"]) | keys_in(p["headRefName"])]
        weak = [p for p in self.by_num.values() if p not in strong and key in keys_in(p.get("body") or "")]
        return strong, weak

    def confirmed(self, number: int) -> bool:
        return number in self.by_num or number in self.processed or self.is_merged(number)

    def judge(self, row: dict) -> tuple:
        """행 → ('skip', 사유키) | ('auto', PR 번호 목록) | ('candidate', PR 번호 목록, 사유키 목록)"""
        if row["state"] == tl.DONE:
            return "skip", "already_done"
        nums, keys = row_refs(row)
        linked = {n for n in nums if n in self.by_num}
        reasons, per_key = [], {}
        for k in sorted(keys):
            per_key[k] = self.key_prs(k)
        if not linked and not any(s or w for s, w in per_key.values()):
            return "skip", "no_link"
        prs = set(linked)
        for k, (strong, weak) in per_key.items():
            prs |= {p["number"] for p in strong}
            if not strong and weak and not linked:
                reasons.append("body_only")
                prs |= {p["number"] for p in weak}
            if len(strong) > 1:
                reasons.append("multi")
            if any(k in keys_in(o["title"]) | keys_in(o["headRefName"]) for o in self.opened):
                reasons.append("open_pr")
        # 번호·키 어느 쪽이든 행이 가리키는 것이 전부 병합이어야 함. 번호는 조회 밖 PR 도 확인함
        if any(not self.confirmed(n) for n in nums) or any(not (s or w) for s, w in per_key.values()):
            reasons.append("partial")
        if row["state"] == tl.UNSORTED:
            reasons.append("unsorted")
        if row["state"] == tl.DROPPED:
            reasons.append("dropped")
        waiting = [n for n in tl.split_ids(row.get("needs")) if self.state.get(n) != tl.DONE]
        if waiting:
            reasons.append("needs")
        if not MERGE_WORD.search(row.get("text", "") + " " + row.get("done_when", "")):
            reasons.append("followup")
        pr_list = sorted(prs)
        if not reasons:
            return "auto", pr_list
        return "candidate", pr_list, list(dict.fromkeys(reasons))


def plan(rows: list, merged: list, opened: list, processed: set, is_merged=None) -> dict:
    j = Judge(merged, opened, rows, processed, is_merged)
    auto, cands = [], []
    for r in rows:
        res = j.judge(r)
        if res[0] == "skip":
            j.skipped[res[1]] += 1
        elif res[0] == "auto":
            auto.append({"id": r["id"], "text": short(r["text"]), "hub": r.get("hub", ""), "prs": res[1], "evidence": [f"PR #{n}" for n in res[1]]})
        else:
            cands.append({"id": r["id"], "text": short(r["text"]), "hub": r.get("hub", ""), "state": r["state"], "prs": res[1],
                          "reasons": [REASONS[k] for k in res[2]], "reason_keys": res[2]})
    return {"auto": auto, "candidates": cands, "skipped": j.skipped, "merged_seen": len(j.by_num)}


# ---------- 상태 파일 ----------

def load_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as err:
        raise LedgerError(f"상태 파일 읽기 실패({path}: {err}) — 깨졌으면 지우면 --since 부터 다시 시작(완료 행은 건드리지 않아 안전)", EXIT_IO)
    return data if isinstance(data, dict) else {}


def save_state(path: str, state: dict) -> None:
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    except OSError as err:
        raise LedgerError(f"상태 폴더 생성 실패({err.strerror}) — LRM_MERGED_STATE 경로 확인", EXIT_IO)
    ledger.write_atomic(path, json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def since_date(arg_since, state: dict, now: float) -> str:
    """기준일: --since 우선, 없으면 상태 파일의 since. 둘 다 없으면 exit 2"""
    raw = arg_since or state.get("since")
    if not raw:
        raise LedgerError("기준일 없음 — 첫 실행은 --since YYYY-MM-DD 지정(이후는 상태 파일이 기억)", EXIT_USAGE)
    try:
        datetime.date.fromisoformat(raw[:10])
    except ValueError:
        raise LedgerError(f"--since 형식 오류: {raw} — YYYY-MM-DD", EXIT_USAGE)
    return raw[:10]


# ---------- 실행 ----------

def run(since: str, apply: bool, now: float) -> dict:
    tasks = tl.tasks_path()
    sp = state_path()
    state = load_state(sp)
    processed = set(state.get("processed", []))
    rows = tl.read_rows(tasks)
    merged, opened = fetch_merged(since), fetch_open()
    cache = {}

    def is_merged(n: int) -> bool:
        if n not in cache:
            try:
                info = fetch_state(n)
                cache[n] = info.get("state") == "MERGED"
            except LedgerError:
                cache[n] = False  # 다른 저장소의 번호일 수 있음 → 병합 확인 불가로 보고 후보로 남김
        return cache[n]

    res = plan(rows, merged, opened, processed, is_merged)
    res.update(since=since, dry_run=not apply, applied=[])
    if apply:
        for item in res["auto"]:
            try:
                tl.set_row(tasks, item["id"], time.time(), False, state=tl.DONE, evidence=item["evidence"], note=NOTE)
            except LedgerError as err:
                if err.code == EXIT_MISSING:
                    raise
                raise LedgerError(f"{item['id']} 완료 실패 — {err}", err.code)
            res["applied"].append(item["id"])
            processed |= set(item["prs"])
        cutoff = datetime.datetime.fromtimestamp(now).date() - datetime.timedelta(days=OVERLAP_DAYS)
        state.update(since=cutoff.isoformat(), processed=sorted(processed), updated_at=tl.stamp(now),
                     candidates={c["id"]: {"prs": c["prs"], "reasons": c["reason_keys"]} for c in res["candidates"]})
        save_state(sp, state)
    return res


def render(res: dict) -> str:
    head = ("[dry-run] " if res["dry_run"] else "[apply] ") + f"기준일 {res['since']} · 병합 PR {res['merged_seen']}건 · 완료 {'예정' if res['dry_run'] else '처리'} {len(res['auto'])}건 · 후보 {len(res['candidates'])}건"
    lines = [head, "", "완료 예정 행:" if res["dry_run"] else "완료한 행:"]
    lines += [f"  {a['id']}\t{a['text']}\t{' '.join(f'PR #{n}' for n in a['prs'])}" for a in res["auto"]] or ["  없음"]
    lines += ["", "후보(자동 완료 안 함):"]
    lines += [f"  {c['id']}\t{c['state']}\t{c['text']}\tPR {' '.join('#%d' % n for n in c['prs'])}\t{' · '.join(c['reasons'])}" for c in res["candidates"]] or ["  없음"]
    lines += ["", "건너뜀: " + " · ".join(f"{SKIPS[k]} {v}" for k, v in res["skipped"].items())]
    return "\n".join(lines)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise LedgerError(f"{message} — --help 참고", EXIT_USAGE)


def build_parser() -> argparse.ArgumentParser:
    p = Parser(prog="ledger_merged.py", formatter_class=argparse.RawDescriptionHelpFormatter, description=__doc__.split("\n\n")[0],
               epilog="""예시:
  ledger_merged.py --since 2026-11-09              # dry-run(기본). 무엇을 완료·후보로 할지만 표로 봄
  ledger_merged.py --since 2026-11-09 --json       # 같은 내용 JSON
  ledger_merged.py --apply                         # 장부 완료 + 상태 파일 갱신(기준일은 상태 파일에서)
종료 코드: 0 성공 · 1 gh·파일 실패 · 2 인자 오류 · 3 apply 중 행 사라짐""")
    p.add_argument("--since", help="병합 조회 기준일 YYYY-MM-DD(없으면 상태 파일의 since)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="기본값. 아무것도 쓰지 않음")
    mode.add_argument("--apply", action="store_true", help="task_ledger set 경로로 완료 처리하고 상태 파일 갱신")
    p.add_argument("--json", action="store_true", help="JSON 으로 출력")
    return p


def main(argv=None) -> int:
    try:
        args = build_parser().parse_args(argv)
        now = time.time()
        since = since_date(args.since, load_state(state_path()), now)
        res = run(since, args.apply, now)
    except LedgerError as err:
        sys.stderr.write(f"오류: {err}\n")
        return err.code
    print(json.dumps(res, ensure_ascii=False, indent=2) if args.json else render(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
