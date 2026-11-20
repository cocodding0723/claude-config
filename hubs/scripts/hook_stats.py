#!/usr/bin/env python3
"""보호 훅 거부 집계: 최근 N일 세션 기록에서 훅이 거부한 도구 호출을 사유별로 셈.

왜: 훅이 일을 얼마나 막는지 숫자 없이는 풀 훅과 둘 훅을 가릴 수 없음. 거부는 많은데 막은 사고가 없는 사유를 주 1회 보고 좁힘(사용자 11-20).
세는 것: 도구 결과가 오류이고 본문이 'PreToolUse:<도구> hook error:' 로 시작하는 줄(훅 거부가 모델에게 돌아간 꼴). 사유는 첫 구절로 묶음.
세지 않는 것: 확인 창(ask)을 띄운 경우·훅 실행 시간. 세션 기록에 남지 않아 여기서는 알 수 없음.
읽기 전용: 세션 기록·장부를 고치지 않음. --report 일 때만 hub_report.py 로 보고 1건을 outbox 에 추가(전송은 report_sender).

사용: hook_stats.py [--days 7] [--top 8] [--json] [--report [--dry-run]]
예:   hook_stats.py --days 7
      hook_stats.py --days 30 --json | jq '.reasons[:5]'
      hook_stats.py --report --dry-run     # 보고 양식 검사까지만
환경: LRM_TRANSCRIPT_DIRS 세션 기록 폴더들(':' 구분, 기본 ~/.claude/projects 와 Orca 프로필) · LRM_HOOK_STATS_NOW 현재 시각 epoch 초(시험용)
      LRM_HUB_REPORT hub_report.py 경로(시험용)
종료 코드: 0 성공(거부 0건 포함), 1 세션 기록 폴더를 하나도 못 읽음·보고 기록 실패, 2 인자 오류
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

DENY_RE = re.compile(r"PreToolUse:(\w+) hook (?:blocking )?error[^:]*:\s*(.+)", re.S)
REASON_CUT = re.compile(r"\s+—\s+|[(:\n]")  # 사유의 첫 구절까지만(뒤는 경로·번호처럼 건마다 다른 값)
REASON_MAX = 40
DEFAULT_DIRS = ("~/.claude/projects", "~/Library/Application Support/orca/claude-profiles")
REPORT_HUB, REPORT_TASK, REPORT_KIND = "main", "없음", "info"
HUB_REPORT = os.path.join(os.path.dirname(os.path.realpath(__file__)), "hub_report.py")


def now_ts() -> float:
    return float(os.environ.get("LRM_HOOK_STATS_NOW") or time.time())


def transcript_dirs() -> list:
    raw = os.environ.get("LRM_TRANSCRIPT_DIRS")
    return [d for d in raw.split(":") if d] if raw else [os.path.expanduser(d) for d in DEFAULT_DIRS]


def transcripts(dirs: list, since: float) -> list:
    """since 뒤에 바뀐 세션 기록(.jsonl). 링크로 겹친 폴더는 실제 경로로 한 번만"""
    out = set()
    for top in dirs:
        for dp, _, names in os.walk(top):
            for n in names:
                if not n.endswith(".jsonl"):
                    continue
                p = os.path.join(dp, n)
                try:
                    if os.path.getmtime(p) >= since:
                        out.add(os.path.realpath(p))
                except OSError:
                    pass
    return sorted(out)


def reason_key(text: str) -> str:
    head = REASON_CUT.split(text.strip(), 1)[0].strip()
    return re.sub(r"\d+", "N", head)[:REASON_MAX] or "(사유 없음)"


def result_text(block: dict) -> str:
    c = block.get("content")
    if isinstance(c, list):
        return " ".join(str(x.get("text") or "") for x in c if isinstance(x, dict))
    return str(c or "")


def scan(path: str, since: float) -> tuple:
    """(도구 호출 수, [(사유, 도구, 원문 앞부분)]). 줄 시각이 since 전이면 건너뜀(시각 없는 줄은 셈)"""
    calls, denies = 0, []
    try:
        f = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return 0, []
    with f:
        for ln in f:
            if '"tool_use"' not in ln and "hook" not in ln:
                continue
            try:
                d = json.loads(ln)
            except ValueError:
                continue
            content = (d.get("message") or {}).get("content") if isinstance(d, dict) else None
            if not isinstance(content, list) or not recent(d.get("timestamp"), since):
                continue
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use":
                    calls += 1
                elif b.get("type") == "tool_result" and b.get("is_error"):
                    m = DENY_RE.match(result_text(b).strip())
                    if m:
                        denies.append((reason_key(m.group(2)), m.group(1), " ".join(m.group(2).split())[:160]))
    return calls, denies


def recent(stamp, since: float) -> bool:
    if not isinstance(stamp, str):
        return True
    try:
        import datetime
        return datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() >= since
    except ValueError:
        return True


def collect(days: int) -> dict:
    since = now_ts() - days * 86400
    dirs = [d for d in transcript_dirs() if os.path.isdir(d)]
    if not dirs:
        raise OSError("세션 기록 폴더 없음 — LRM_TRANSCRIPT_DIRS 또는 ~/.claude/projects 확인")
    calls, by, sessions = 0, {}, 0
    files = transcripts(dirs, since)
    for p in files:
        c, denies = scan(p, since)
        calls += c
        sessions += bool(denies)
        for key, tool, sample in denies:
            row = by.setdefault(key, {"reason": key, "count": 0, "tools": set(), "sample": sample})
            row["count"] += 1
            row["tools"].add(tool)
    reasons = sorted(({**r, "tools": sorted(r["tools"])} for r in by.values()), key=lambda r: (-r["count"], r["reason"]))
    total = sum(r["count"] for r in reasons)
    return {"days": days, "files": len(files), "calls": calls, "denies": total, "sessions_with_deny": sessions,
            "rate_pct": round(100 * total / calls, 2) if calls else 0.0, "reasons": reasons}


def text_report(res: dict, top: int) -> str:
    lines = [f"훅 거부 집계(최근 {res['days']}일): 도구 호출 {res['calls']}번 중 거부 {res['denies']}번({res['rate_pct']}%) · 거부 있는 세션 {res['sessions_with_deny']}개 · 세션 기록 {res['files']}개"]
    for r in res["reasons"][:top]:
        lines.append(f"  {r['count']:>4}  {r['reason']}  [{'·'.join(r['tools'])}]")
    if len(res["reasons"]) > top:
        lines.append(f"  … 사유 {len(res['reasons']) - top}개 더(--top 으로 늘림)")
    lines.append("확인 창(ask)과 훅 실행 시간은 세션 기록에 없어 세지 않음")
    return "\n".join(lines)


def slack_text(res: dict) -> str:
    """보고 본문. hub_report 양식(3줄, 한 줄에 사실 하나)에 맞춰 가장 많은 사유 하나만 싣고 나머지는 명령으로 안내"""
    first = res["reasons"][0] if res["reasons"] else None
    most = f"{first['reason']} {first['count']}번" if first else "없음"
    return (f"훅 거부 주간 집계: 최근 {res['days']}일 도구 호출 {res['calls']}번 중 {res['denies']}번 거부({res['rate_pct']}%)\n"
            f"- 가장 많은 사유: {most}\n- 볼 것: hook_stats.py --days {res['days']} 의 사유별 목록에서 막은 사고가 없는 훅을 좁힘")


def report(res: dict, dry: bool) -> int:
    cmd = [sys.executable, os.environ.get("LRM_HUB_REPORT") or HUB_REPORT, "add", "--hub", REPORT_HUB, "--task", REPORT_TASK,
           "--kind", REPORT_KIND, "--text", slack_text(res)] + (["--dry-run"] if dry else [])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as err:
        print(f"오류: 보고 기록 실패({err}) — hub_report.py 위치·LRM_HUB_REPORT 확인", file=sys.stderr)
        return 1
    if r.returncode != 0:
        print(f"오류: 보고 기록 실패 — {(r.stderr or r.stdout).strip()[:300]}", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hook_stats.py", formatter_class=argparse.RawDescriptionHelpFormatter, description="보호 훅 거부 집계(읽기 전용)", epilog=__doc__)
    p.add_argument("--days", type=int, default=7, help="볼 기간(일, 기본 7)")
    p.add_argument("--top", type=int, default=8, help="글 출력에 보일 사유 수(기본 8)")
    p.add_argument("--json", action="store_true", help="결과를 JSON 으로 출력(사유 전부)")
    p.add_argument("--report", action="store_true", help="요약 1건을 hub_report.py 로 outbox 에 추가(주간 예약 실행용)")
    p.add_argument("--dry-run", action="store_true", help="--report 와 함께: 보고 양식 검사까지만 하고 기록하지 않음")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.days < 1 or args.top < 1:
        print("오류: --days·--top 은 1 이상", file=sys.stderr)
        return 2
    try:
        res = collect(args.days)
    except OSError as err:
        print(f"오류: {err}", file=sys.stderr)
        return 1
    print(json.dumps(res, ensure_ascii=False) if args.json else text_report(res, args.top))
    return report(res, args.dry_run) if args.report else 0


if __name__ == "__main__":
    sys.exit(main())
