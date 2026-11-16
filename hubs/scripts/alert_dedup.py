#!/usr/bin/env python3
"""자동 알림 하루 1회 판정. slack_notify.sh 가 전송 직전에 부르는 공용 판정 한 곳.

키 = (보낸 곳, 허브, 종류, 정규화 제목, 대상). 같은 키는 하루(현지 날짜)에 1회만 보내고, '상태'가 바뀌면 다시 보냄.
상태는 호출처가 --state 로 줌(없으면 종류 이름). 같은 대상(보낸 곳·허브·대상)의 마지막 전송 상태와도 비교해
실패→통과→실패처럼 중간에 상태가 한 번 바뀌었다 돌아온 경우도 다시 보냄.
막은 알림은 alert-dedup.jsonl 에 suppressed 로 남김. 사용자가 요청한 전송(결정 카드·답 회신·문서·스레드 답글)은
slack_notify.sh 가 이 판정을 부르지 않음.

사용:
  alert_dedup.py check    --from 보낸곳 --kind 종류 --title 제목 [--hub 허브] [--subject 대상] [--state 상태] [--dir 폴더]
      출력: 'send <rid>' 또는 'suppress'. 보낸 뒤 실패하면 rollback 으로 예약을 되돌림
  alert_dedup.py rollback --rid <rid> [--dir 폴더]
환경: LRM_ALERT_NOW 현재 시각(epoch 초, 시험용), LRM_ALERT_DIR 상태 폴더(기본 ~/.config/lrm-hubs)
종료 코드: 0 판정 완료, 2 인자 오류. 상태 파일을 못 읽거나 쓰면 'send' 로 판정(알림을 잃지 않는 쪽)
"""
import argparse
import fcntl
import json
import os
import re
import sys
import time
import uuid

STATE_FILE = "alert-dedup.json"
LOG_FILE = "alert-dedup.jsonl"
LOCK_FILE = "alert-dedup.lock"
KEEP_DAYS = 7  # 오래된 키 정리 기준

_EMOJI = re.compile(r":[^\s:]{1,40}:")
_CLOCK = re.compile(r"\d{1,2}:\d{2}(:\d{2})?|\d{4}-\d{2}-\d{2}|\d+\s*(분|시간|초)\s*전")
_EMPTY = re.compile(r"[(\[]\s*[)\]]|·\s*$")
_SPACE = re.compile(r"\s+")


def norm_title(title: str) -> str:
    """이모지 코드·시각·'N분 전' 을 빼고 공백을 모음. PR 번호 같은 식별 숫자는 남김"""
    t = _EMPTY.sub("", _CLOCK.sub("", _EMOJI.sub("", title or "")))
    return _SPACE.sub(" ", t).strip().lower()


def today_of(now: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(now))


def now_ts() -> float:
    v = os.environ.get("LRM_ALERT_NOW")
    return float(v) if v else time.time()


class Store:
    """상태 파일 + 잠금. with 블록 안에서 data 를 고치고 나가면 저장"""

    def __init__(self, folder: str):
        self.dir = folder
        self.path = os.path.join(folder, STATE_FILE)
        self.log = os.path.join(folder, LOG_FILE)
        self.data = {"keys": {}, "groups": {}}
        self._lock = None

    def __enter__(self):
        self._lock = open(os.path.join(self.dir, LOCK_FILE), "a")
        fcntl.flock(self._lock, fcntl.LOCK_EX)
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and isinstance(d.get("keys"), dict) and isinstance(d.get("groups"), dict):
                self.data = d
        except (OSError, ValueError):
            pass
        return self

    def __exit__(self, et, ev, tb):
        try:
            if et is None:
                tmp = self.path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self.data, f, ensure_ascii=False)
                os.replace(tmp, self.path)
        finally:
            fcntl.flock(self._lock, fcntl.LOCK_UN)
            self._lock.close()
        return False

    def note(self, row: dict):
        with open(self.log, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def decide(store: Store, sender: str, hub: str, kind: str, title: str, subject: str, state: str, now: float):
    """(보낼지, 이유, rid). 보낼 때만 예약을 기록함"""
    day = today_of(now)
    nt = norm_title(title)
    key = "|".join([sender, hub, kind, nt, subject])
    group = "|".join([sender, hub, subject or nt])
    keys, groups = store.data["keys"], store.data["groups"]
    entry, grp = keys.get(key), groups.get(group)
    if entry and entry.get("day") == day and entry.get("state") == state and grp and grp.get("state") == state:
        store.note({"at": int(now), "day": day, "action": "suppressed", "reason": "same_day_same_state", "key": key, "state": state})
        return False, "same_day_same_state", ""
    reason = "first" if not entry else "new_day" if entry.get("day") != day else "state_changed"
    rid = uuid.uuid4().hex[:12]
    keys[key] = {"day": day, "state": state, "rid": rid, "at": int(now), "prev": entry}
    groups[group] = {"state": state, "day": day, "rid": rid, "prev": grp}
    cutoff = today_of(now - KEEP_DAYS * 86400)
    for k in [k for k, v in keys.items() if v.get("day", "") < cutoff]:
        del keys[k]
    for k in [k for k, v in groups.items() if v.get("day", "") < cutoff]:
        del groups[k]
    store.note({"at": int(now), "day": day, "action": "sent", "reason": reason, "key": key, "state": state, "rid": rid})
    return True, reason, rid


def rollback(store: Store, rid: str, now: float) -> bool:
    """전송 실패 시 예약을 이전 값으로 되돌림. 그 사이 다른 전송이 덮었으면(rid 불일치) 건드리지 않음"""
    done = False
    for table in (store.data["keys"], store.data["groups"]):
        for k, v in list(table.items()):
            if v.get("rid") == rid:
                if v.get("prev"):
                    table[k] = v["prev"]
                else:
                    del table[k]
                done = True
    if done:
        store.note({"at": int(now), "day": today_of(now), "action": "rollback", "rid": rid})
    return done


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="alert_dedup.py", description="자동 알림 하루 1회 판정", epilog=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="보낼지 판정하고 보낼 때 예약 기록")
    c.add_argument("--from", dest="sender", required=True)
    c.add_argument("--kind", required=True)
    c.add_argument("--title", required=True)
    c.add_argument("--hub", default="")
    c.add_argument("--subject", default="")
    c.add_argument("--state", default="")
    r = sub.add_parser("rollback", help="전송 실패 시 예약 취소")
    r.add_argument("--rid", required=True)
    for s in (c, r):
        s.add_argument("--dir", default=os.environ.get("LRM_ALERT_DIR") or os.path.expanduser("~/.config/lrm-hubs"))
    a = p.parse_args(argv)
    now = now_ts()
    try:
        with Store(a.dir) as st:
            if a.cmd == "check":
                ok, _why, rid = decide(st, a.sender, a.hub, a.kind, a.title, a.subject, a.state or a.kind, now)
                print(f"send {rid}" if ok else "suppress")
            else:
                rollback(st, a.rid, now)
    except OSError as e:
        print(f"alert_dedup: 상태 파일 접근 실패({e}) — 전송으로 판정", file=sys.stderr)
        if a.cmd == "check":
            print("send -")
    return 0


if __name__ == "__main__":
    sys.exit(main())
