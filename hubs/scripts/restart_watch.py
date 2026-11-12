#!/usr/bin/env python3
"""Orca 재시작 감지 → Slack 결정 카드 1장. ✅ 이어받기를 누르면 진행 작업이 있던 허브만 resume_all.sh 로 재기동함.

판정: `orca status --json` 의 runtimeId·app pid 와 ps 시작 시각을 저장값과 비교. 첫 실행은 기록만, 조회 실패는 '판정 불가'로 기록만(재시작 아님).
같은 재시작은 키(runtimeId:pid:시작 시각)로 한 번만 카드를 보냄. 카드 전송이 실패하면 저장값을 바꾸지 않아 다음 주기에 다시 시도함.
상태 파일: $LRM_HUB_DIR/restart-watch.json. 호출은 slack_socket.py 의 감시 루프가 주기적으로 함(새 launchd 없음).
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import card_actions  # noqa: E402
import hub_status  # noqa: E402
import main_terminal as mt  # noqa: E402

DIR = card_actions.DIR
STATE = os.path.join(DIR, "restart-watch.json")
LOCK = os.path.join(DIR, ".restart-watch.lock")
ORCA = os.environ.get("LRM_ORCA") or "orca"
NOTIFY = os.environ.get("LRM_NOTIFY") or os.path.join(DIR, "slack_notify.sh")
TSV = os.environ.get("HUBS_TSV") or next((p for p in (os.path.join(HERE, "..", "hubs.tsv"), os.path.join(DIR, "hubs.tsv")) if os.path.isfile(p)), "")
ORCA_TIMEOUT_S = 20
NOTIFY_TIMEOUT_S = 60
LINE_MAX = 60  # 카드의 허브 한 줄 작업 문구 상한
DID_PREFIX = "D-RS"
CARD_TITLE = "Orca 재시작 감지"
UNSAFE_RE = re.compile(r"[<>&`]")  # Slack 멘션·링크·코드 서식으로 해석될 글자 제거

EPILOG = """종료 코드: 0 정상(첫 실행·판정 불가·재시작 없음·대상 없음 포함), 1 카드 전송 실패, 2 인자 오류
환경: LRM_HUB_DIR 작업 폴더, LRM_ORCA orca 경로, LRM_NOTIFY slack_notify.sh 경로, HUBS_TSV 허브 목록,
      LRM_HUB_STATUS_DIR·LRM_HUB_STATUS_STALE_HOURS 허브 상태 파일 위치·낡음 기준(기본 24시간)
예:
  restart_watch.py check --dry-run     # 판정·카드 문구만 출력, 전송·저장 없음
  restart_watch.py check --json
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def probe() -> dict | None:
    """현재 Orca 신원 {runtimeId,pid,startedAt}. 조회 실패·미실행·응답 이상이면 None(판정 불가)"""
    rc, d = mt.run_orca(ORCA, ["status", "--json"], ORCA_TIMEOUT_S)
    res = (d or {}).get("result") if rc == 0 and isinstance(d, dict) else None
    if not isinstance(res, dict):
        return None
    app, rt = res.get("app") or {}, res.get("runtime") or {}
    pid, rid = app.get("pid"), rt.get("runtimeId")
    if app.get("running") is not True or not isinstance(pid, int) or not isinstance(rid, str) or not rid:
        return None
    started = ""
    try:
        p = subprocess.run(["ps", "-p", str(pid), "-o", "lstart="], capture_output=True, text=True, timeout=5, env={**os.environ, "LC_ALL": "C"})
        started = " ".join(p.stdout.split()) if p.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        pass
    return {"runtimeId": rid, "pid": pid, "startedAt": started}


def changed(old: dict, new: dict) -> bool:
    """신원이 바뀌었는지. 시작 시각은 양쪽 다 있을 때만 비교(ps 일시 실패로 오판하지 않음)"""
    if old.get("runtimeId") != new["runtimeId"] or old.get("pid") != new["pid"]:
        return True
    return bool(old.get("startedAt") and new["startedAt"] and old["startedAt"] != new["startedAt"])


def key_of(ident: dict) -> str:
    return f"{ident['runtimeId']}:{ident['pid']}:{ident['startedAt']}"


def load_state_at(path: str) -> dict:
    """상태 파일 읽기. 없음·손상은 빈 dict(stall_watch.py 도 공용)"""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state_at(path: str, d: dict) -> None:
    """같은 폴더 임시 파일 → fsync → os.replace 원자 교체"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_state() -> dict:
    return load_state_at(STATE)


def save_state(d: dict) -> None:
    save_state_at(STATE, d)


def hub_rows() -> list[tuple[str, str]]:
    """hubs.tsv 의 (slug, 표시 이름). 목록이 없으면 빈 목록"""
    rows = []
    try:
        with open(TSV, encoding="utf-8") as f:
            for ln in f:
                cols = ln.rstrip("\n").split("\t")
                if cols[0] and not cols[0].lstrip().startswith("#") and len(cols) >= 2:
                    rows.append((cols[0], cols[1]))
    except OSError:
        pass
    return rows


def clean(text: str) -> str:
    return UNSAFE_RE.sub("", hub_status.one_line(text))


def targets() -> list[dict]:
    """이어받을 허브: 상태 파일에 진행 작업이 있고 낡음 기준 안에 갱신된 허브"""
    out = []
    limit = timedelta(hours=hub_status.stale_hours())
    for slug, name in hub_rows():
        data, _path, err = hub_status.load(slug)
        if err or not data or not data.get("tasks"):
            continue
        at = hub_status.parse_iso(data.get("updatedAt"))
        if at is None or datetime.now(timezone.utc) - at > limit:
            continue
        tasks = [clean(t) for t in data["tasks"]]
        out.append({"slug": slug, "name": clean(name), "tasks": tasks})
    return out


def card_text(ident: dict, hubs: list[dict]) -> str:
    started = ident["startedAt"] or "시각 불명"
    lines = [CARD_TITLE, "",
             f"• 무엇: Orca 가 다시 시작됨(시작 {started}), 진행 작업이 있던 허브 {len(hubs)}개",
             "• 결과: 이어받기를 누르면 아래 허브만 코디네이터를 다시 띄움. 보류는 아무것도 안 함", "", "참고:"]
    for h in hubs:
        more = f" 외 {len(h['tasks']) - 1}건" if len(h["tasks"]) > 1 else ""
        lines.append(f"• {h['slug']} · {h['name']} — {h['tasks'][0][:LINE_MAX]}{more}")
    return "\n".join(lines)


def send_card(did: str, text: str, options: str = "1=이어받기,2=보류") -> bool:
    cmd = [NOTIFY, "--kind", "decide", "--options", options, "--recommend", "1", "--decision-id", did, "--reversible", "쉬움", text]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=NOTIFY_TIMEOUT_S, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return p.returncode == 0


def check(dry: bool) -> tuple[int, dict]:
    """조회·판정·전송·저장 전체를 프로세스 간 잠금으로 직렬화. 잠금을 못 잡으면(다른 check 실행 중) 기다리지 않고 건너뜀.
    결과 status 에 busy 추가"""
    with card_actions.flock(LOCK, wait=False) as got:
        return _check(dry) if got else (0, {"status": "busy"})


def _check(dry: bool) -> tuple[int, dict]:
    """(종료 코드, 결과 요약). 결과 status: first_run|unavailable|same|carded|no_targets|dup|send_failed"""
    ident = probe()
    st = load_state()
    if ident is None:  # 판정 불가: 저장값 유지, 기록만
        if not dry:
            save_state({**st, "lastProbe": "unavailable", "lastProbeAt": now_iso()})
        return 0, {"status": "unavailable"}
    stored = st.get("identity")
    keep = {"lastProbe": "ok", "lastProbeAt": now_iso()}
    if not isinstance(stored, dict) or not stored.get("runtimeId"):
        if not dry:
            save_state({**st, "identity": ident, **keep})
        return 0, {"status": "first_run"}
    if not changed(stored, ident):
        if not dry:
            save_state({**st, "identity": {**ident, "startedAt": ident["startedAt"] or stored.get("startedAt", "")}, **keep})
        return 0, {"status": "same"}
    key = key_of(ident)
    if st.get("cardedKey") == key:  # 같은 재시작에 이미 처리함
        if not dry:
            save_state({**st, "identity": ident, **keep})
        return 0, {"status": "dup", "key": key}
    hubs = targets()
    if not hubs:
        if not dry:
            save_state({**st, "identity": ident, "cardedKey": key, **keep})
        return 0, {"status": "no_targets", "key": key}
    did = DID_PREFIX + hashlib.sha1(key.encode()).hexdigest()[:10]
    text = card_text(ident, hubs)
    summary = {"key": key, "did": did, "hubs": [h["slug"] for h in hubs], "card": text}
    if dry:
        return 0, {"status": "would_card", **summary}
    card_actions.register(did, "resume", {"slugs": summary["hubs"]})
    if not send_card(did, text):
        card_actions.take(did)  # 전송 실패 시 등록 철회. 저장값은 그대로라 다음 주기에 다시 시도
        return 1, {"status": "send_failed", **summary}
    save_state({**st, "identity": ident, "cardedKey": key, "cardedAt": now_iso(), **keep})
    return 0, {"status": "carded", **summary}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="restart_watch.py", description="Orca 재시작 감지 카드. 대화형 입력 없음.",
                                epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True, metavar="{check}")
    c = sub.add_parser("check", help="재시작 여부 판정, 재시작이면 카드 1장 전송", epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    c.add_argument("--dry-run", action="store_true", help="전송·저장 없이 판정과 카드 문구만 출력")
    c.add_argument("--json", action="store_true", help="결과를 JSON 으로 출력")
    a = p.parse_args(argv)
    rc, res = check(a.dry_run)
    if a.json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        print(f"restart_watch: {res['status']}" + (f" 허브={','.join(res['hubs'])}" if res.get("hubs") else ""))
        if a.dry_run and res.get("card"):
            print(res["card"])
    if rc:
        print("ERROR: 카드 전송 실패 — slack_notify.sh·slack-bot.env 확인. 저장값은 유지돼 다음 주기에 재시도", file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
