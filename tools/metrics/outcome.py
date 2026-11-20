"""5단계: 결과 지표 — 카드 수명·기능당 사용자 접점·재작업률·검수 대기 수·메인 접점 → outcome.json
투입량(1~4단계)과 달리 사용자가 받은 결과를 잼. 카드 수명은 카드 생성→닫힘이라 사용자 지시부터의 시간이 아님(카드는 일을 시작할 때 만들어지는 경우가 많음). 재작업률과 같이 봄(한쪽만 좋아지면 개선 아님). 표본 10 미만은 판단 보류.
자료: 작업판(bd list·bd history), 작업 장부 tasks.jsonl, 허브 보고 hub-reports.jsonl, 답 전달 answer-delivery.jsonl, git log, 메인 세션 기록.
환경: METRICS_BOARD_DIR(기본 ~/orca/board) · METRICS_BD(bd) · LRM_HUB_DIR(기본 ~/.config/lrm-hubs) ·
      METRICS_REPOS(':' 구분, 기본 ~/orca/LoveRevolutionMaker:~/orca/claude-config) · METRICS_HISTORY_MAX(다시 연 카드 조회 상한 200)"""
import calendar
import collections as C
import datetime as D
import json
import os
import re
import statistics
import subprocess
import time

from common import KST, args, claude_base, kst

MIN_SAMPLE = 10
QUICK_CLOSE_H = 0.25  # 15분 안에 닫힌 카드: 일을 시작하며 만든 카드 비중을 가늠
HUMAN_KINDS = ("kind:decision", "kind:user-todo", "kind:approval")
APPROVAL_LABEL = "gate:approval"
ANSWER_REASON = "답:"  # board-answer 가 닫을 때 쓰는 사유 머리
REVERT = re.compile(r"^(Revert\b|revert[:(\s])")
REDO_FROM = "완료"  # 작업 장부: 완료 뒤 다른 상태로 돌아가면 재지시
MAIN_HEAD = re.compile(r"^\s*(<|\[[^\]]*→메인\])")  # 슬래시 명령·허브 붙여넣기는 사용자 입력이 아님


def env_path(name, default):
    return os.path.expanduser(os.environ.get(name) or default)


def utc_epoch(v):
    """bd RFC3339(UTC) → epoch 초"""
    if not isinstance(v, str) or len(v) < 19:
        return None
    try:
        return calendar.timegm(time.strptime(v[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return None


def iso_epoch(v):
    """시간대 붙은 ISO(작업 장부) → epoch 초"""
    try:
        return D.datetime.fromisoformat(v).timestamp() if v else None
    except (TypeError, ValueError):
        return None


def spread(hours):
    if not hours:
        return {"n": 0}
    hours = sorted(hours)
    p90 = hours[min(len(hours) - 1, int(round(0.9 * (len(hours) - 1))))]
    return {"n": len(hours), "median_h": round(statistics.median(hours), 1), "p90_h": round(p90, 1)}


def verdict(n):
    return "" if n >= MIN_SAMPLE else f" · 판단 보류(n<{MIN_SAMPLE})"


class Board:
    def __init__(self, path, bd):
        self.path, self.bd, self.rows, self.err, self.by_id = path, bd, [], None, {}
        if not os.path.isdir(path):
            self.err = "작업판 폴더 없음"
            return
        out, err = self.run("list", "--all", "--json", "--limit", "0")
        try:
            rows = json.loads(out or "[]") if out is not None else None
        except ValueError:
            rows = None
        if not isinstance(rows, list):
            self.err = err or "bd list 출력 이상"
            return
        self.rows = [r for r in rows if isinstance(r, dict)]
        self.by_id = {r.get("id"): r for r in self.rows}

    def run(self, *argv):
        try:
            p = subprocess.run([self.bd, *argv], cwd=self.path, capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as e:
            return None, f"bd 실행 실패({type(e).__name__})"
        if p.returncode != 0:
            return None, f"bd {argv[0]} 종료 {p.returncode}"
        return p.stdout, None

    def reopened(self, cid):
        """이력(최신 먼저)에서 닫힌 판 뒤에 열린 판이 오면 다시 연 것"""
        out, _ = self.run("history", cid, "--json")
        try:
            hist = json.loads(out or "[]")
        except ValueError:
            return False
        states = [((h.get("Issue") or {}).get("status")) for h in reversed(hist if isinstance(hist, list) else [])]
        seen_closed = False
        for s in states:
            if s == "closed":
                seen_closed = True
            elif seen_closed and s is not None:
                return True
        return False


def labels(r):
    return [x for x in r.get("labels") or [] if isinstance(x, str)]


def is_work(r):
    return r.get("issue_type") != "epic" and not any(x in HUMAN_KINDS for x in labels(r))


def feature_of(r, by_id):
    """기능 이름: 카드 또는 부모 epic 의 feature:<slug>, 없으면 부모 epic id"""
    for card in (r, by_id.get(r.get("parent")) or {}):
        for x in labels(card):
            if x.startswith("feature:"):
                return x
    return r.get("parent") or "(기능 없음)"


def board_part(b, lo, hi, hist_max):
    in_win = lambda t: t is not None and lo <= t < hi
    life, run, done, touch, redo_ids = [], [], 0, C.Counter(), []
    capped = False
    budget = hist_max
    for r in b.rows:
        closed_at = utc_epoch(r.get("closed_at"))
        if is_work(r):
            if r.get("status") == "closed" and in_win(closed_at):
                done += 1
                start = utc_epoch(r.get("created_at"))
                if start is not None:
                    life.append((closed_at - start) / 3600)
                began = utc_epoch(r.get("started_at"))
                if began is not None:
                    run.append((closed_at - began) / 3600)
            if in_win(utc_epoch(r.get("updated_at"))):
                if budget <= 0:
                    capped = True
                elif r.get("status") != "closed" or in_win(closed_at):
                    budget -= 1
                    if b.reopened(r["id"]):
                        redo_ids.append(r["id"])
        elif any(x in ("kind:decision", "kind:approval") for x in labels(r)):
            f = feature_of(r, b.by_id)
            if in_win(utc_epoch(r.get("created_at"))):
                touch[f] += 1
            if r.get("status") == "closed" and in_win(closed_at) and str(r.get("close_reason") or "").startswith(ANSWER_REASON):
                touch[f] += 1
    kids = C.defaultdict(list)
    for r in b.rows:
        if r.get("parent"):
            kids[r["parent"]].append(r)
    review = [r["id"] for r in b.rows if r.get("issue_type") == "epic" and r.get("status") != "closed"
              and kids.get(r["id"]) and all(k.get("status") == "closed" for k in kids[r["id"]])]
    gate = [r["id"] for r in b.rows if r.get("status") != "closed" and APPROVAL_LABEL in labels(r)]
    life_s = spread(life)
    life_s["within15m"] = sum(1 for h in life if h <= QUICK_CLOSE_H)
    return {"life": life_s, "run": spread(run), "done": done, "touch": dict(touch), "reopened": redo_ids, "historyCapped": capped,
            "review": review, "approvalWait": gate}


def ledger_part(hub_dir, lo, hi):
    """옛 작업 장부·허브 보고 기록. 기능 팀 이전 자료라 허브 단위로만 묶임"""
    lead, done, redo = [], 0, 0
    try:
        with open(os.path.join(hub_dir, "tasks.jsonl"), encoding="utf-8") as fh:
            rows = [json.loads(x) for x in fh if x.strip()]
    except (OSError, ValueError):
        rows = []
    for r in rows:
        end = iso_epoch(r.get("done_at"))
        if r.get("state") == REDO_FROM and end is not None and lo <= end < hi:
            done += 1
            start = iso_epoch(r.get("received_at"))
            if start is not None:
                lead.append((end - start) / 3600)
        hist = [h for h in r.get("history") or [] if isinstance(h, dict)]
        for prev, cur in zip(hist, hist[1:]):
            at = iso_epoch(cur.get("at"))
            if prev.get("state") == REDO_FROM and cur.get("state") not in (REDO_FROM, "버림") and at is not None and lo <= at < hi:
                redo += 1
    hub_of, touch = {}, C.Counter()
    for name in ("hub-reports.jsonl", "answer-delivery.jsonl"):
        try:
            with open(os.path.join(hub_dir, name), encoding="utf-8") as fh:
                for x in fh:
                    try:
                        r = json.loads(x)
                    except ValueError:
                        continue
                    at = r.get("at")
                    if name == "hub-reports.jsonl" and r.get("kind") == "decide":
                        hub_of[r.get("decision_id")] = r.get("hub") or "?"
                        if isinstance(at, (int, float)) and lo <= at < hi:
                            touch[r.get("hub") or "?"] += 1
                    elif name == "answer-delivery.jsonl" and r.get("state") != "pending" and isinstance(at, (int, float)) and lo <= at < hi:
                        touch[hub_of.get(r.get("decision_id"), "?")] += 1
        except OSError:
            continue
    return {"lead": spread(lead), "done": done, "redo": redo, "touch": dict(touch)}


def reverts(repos, a):
    n, seen = 0, set()
    since, until = f"{a.since} 00:00 +0900", (a.end).strftime("%Y-%m-%d 00:00 +0900")
    for repo in repos:
        if not os.path.isdir(repo):
            continue
        try:
            p = subprocess.run(["git", "-C", repo, "log", "--all", "--no-merges", f"--since={since}", f"--until={until}", "--format=%H %s"],
                               capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired):
            continue
        for line in p.stdout.splitlines():
            sha, _, subj = line.partition(" ")
            if sha not in seen and REVERT.search(subj):
                seen.add(sha)
                n += 1
    return n


def is_user_input(j):
    """사람이 친 입력만: 시스템 주입(isMeta)·작업 알림·예약·허브 붙여넣기·슬래시 명령 제외. 이어 연 세션의 같은 uuid 는 한 번"""
    content = (j.get("message") or {}).get("content")
    if j.get("type") != "user" or j.get("isMeta") or j.get("isCompactSummary") or not isinstance(content, str):
        return False
    if j.get("turnOrigin", "human") != "human" and j.get("promptSource") not in ("typed", "queued"):
        return False
    return not MAIN_HEAD.match(content)


def main_part(a):
    """메인 세션의 사용자 입력 수·자동 압축 수(일별). 2단계(sess2.json)의 main 분류를 씀"""
    try:
        with open(a.path("sess2.json")) as fh:
            S = json.load(fh)
    except (OSError, ValueError):
        return {"days": 0}
    users, compacts, seen = C.Counter(), C.Counter(), set()
    for x in S:
        if x.get("c") != "main" or x.get("src") != "claude":
            continue
        try:
            fh = open(f"{claude_base()}/{x['dir']}/{x['f']}", "rb")
        except OSError:
            continue
        with fh:
            for line in fh:
                if b'"type":"user"' not in line and b"compact_boundary" not in line:
                    continue
                try:
                    j = json.loads(line)
                except ValueError:
                    continue
                ts = j.get("timestamp") or ""
                if len(ts) < 19 or not (a.start <= kst(ts) < a.end):
                    continue
                day = kst(ts).strftime("%m-%d")
                if j.get("subtype") == "compact_boundary":
                    compacts[day] += 1
                elif is_user_input(j) and j.get("uuid") not in seen:
                    seen.add(j.get("uuid"))
                    users[day] += 1
    days = sorted(set(users) | set(compacts))
    per = lambda c: round(sum(c.values()) / len(days), 1) if days else 0
    return {"days": len(days), "userPerDay": per(users), "compactPerDay": per(compacts), "users": dict(users), "compacts": dict(compacts)}


def report(o):
    bd, lg = o["board"], o["ledger"]
    out = []
    if o.get("boardErr"):
        out.append(f"작업판 읽기 실패: {o['boardErr']} (작업판 값은 0 으로 둠)")
    def body(d):
        return f"n={d['n']}" + (f" 중앙 {d['median_h']}시간 p90 {d['p90_h']}시간" if d["n"] else "")

    life = bd["life"]
    out.append(f"카드 수명(카드 생성→닫힘, 사용자 지시부터가 아님) {body(life)} · 15분 안에 닫힌 카드 {life['within15m']}/{life['n']}{verdict(life['n'])}")
    out.append(f"시작→닫힘(started_at 있는 카드) {body(bd['run'])}{verdict(bd['run']['n'])}")
    out.append(f"접수→완료 작업 장부(옛, received_at→done_at) {body(lg['lead'])}{verdict(lg['lead']['n'])}")
    ft = bd["touch"]
    out.append(f"기능당 사용자 접점 기능 {len(ft)} 합 {sum(ft.values())}" + (f" 기능당 {round(sum(ft.values()) / len(ft), 1)}" if ft else "") + verdict(len(ft)))
    out += [f"  {k} {v}" for k, v in sorted(ft.items(), key=lambda z: -z[1])[:10]]
    lt = lg["touch"]
    out.append(f"허브당 사용자 접점(옛) 허브 {len(lt)} 합 {sum(lt.values())}")
    out += [f"  {k} {v}" for k, v in sorted(lt.items(), key=lambda z: -z[1])[:10]]
    r = o["rework"]
    rate = f"{r['rate']:.2f}" if r["rate"] is not None else "-"
    out.append(f"재작업률 {rate} = (되돌림 커밋 {r['reverts']} + 다시 연 카드 {r['reopened']} + 재지시 {r['redo']}) / 완료 {r['done']}"
               + (" · 다시 연 카드 조회 상한 도달" if bd["historyCapped"] else "") + verdict(r["done"]))
    out.append(f"검수 대기 {len(bd['review']) + len(bd['approvalWait'])} (지금 값: 하위 카드 다 닫힌 열린 epic {len(bd['review'])} · 승인 성격 결정 {len(bd['approvalWait'])})")
    m = o["main"]
    if m.get("days"):
        out.append(f"메인 접점 {m['days']}일 · 사용자→메인 메시지 일평균 {m['userPerDay']} · 자동 압축 일평균 {m['compactPerDay']}")
    else:
        out.append("메인 접점 메인 세션 기록 없음")
    return out


def main():
    a = args(__doc__)
    lo = (a.start - KST - D.datetime(1970, 1, 1)).total_seconds()
    hi = (a.end - KST - D.datetime(1970, 1, 1)).total_seconds()
    b = Board(env_path("METRICS_BOARD_DIR", "~/orca/board"), os.environ.get("METRICS_BD") or "bd")
    hist_max = int(os.environ.get("METRICS_HISTORY_MAX") or 200)
    bd = board_part(b, lo, hi, hist_max)
    lg = ledger_part(env_path("LRM_HUB_DIR", "~/.config/lrm-hubs"), lo, hi)
    repos = [os.path.expanduser(p) for p in (os.environ.get("METRICS_REPOS") or "~/orca/LoveRevolutionMaker:~/orca/claude-config").split(":") if p]
    rv = reverts(repos, a)
    done = bd["done"] + lg["done"]
    redo_n = rv + len(bd["reopened"]) + lg["redo"]
    o = {"board": bd, "boardErr": b.err, "ledger": lg, "main": main_part(a),
         "rework": {"reverts": rv, "reopened": len(bd["reopened"]), "redo": lg["redo"], "done": done,
                    "rate": round(redo_n / done, 3) if done else None}}
    with open(a.path("outcome.json"), "w") as fh:
        json.dump(o, fh, ensure_ascii=False)
    print("\n".join(report(o)))


if __name__ == "__main__":
    main()
