#!/usr/bin/env python3
"""야간 디스패처: 30분마다 장부의 승인된 야간 항목을 훑어 한가한 허브에 보낼지 판정함. 1단계(STAGE=1)는 dry 모드만 동작.

모드 파일(기본 ~/.config/lrm-hubs/night.mode): off | dry | on. 파일이 없거나 값이 이상하면 off.
  off  아무것도 안 함.
  dry  전달 없이 '보냈다면' 만 night-dispatch.jsonl 에 기록하고 회차마다 자원 측정을 기록함(임계값을 정하는 근거). 장부·허브는 건드리지 않음.
  on   실전달 경로. STAGE < 2 인 동안은 파일이 on 이어도 거부하고 거부 기록만 남김(2단계 검토 전 실전달 차단).
판정 순서(항목마다 위에서 아래로, 하나라도 걸리면 거기서 멈춤. docs/night-queue-design.html 판정 순서도):
  0 밤 창(window_start~window_end) 밖이면 회차 종료
  1 정지 신호: 하룻밤 전달 상한(night_total_max) 도달이면 회차 종료
  2 후보: 야간 표시·상태 배정. gate=auto 가 아니거나 금지 키워드(forbid)가 있으면 결재함 대상, 사용자 승인(night.approved_at) 없으면 대기
  3 상한: 같은 항목 하룻밤 전달 상한(item_tries_max), dry 에서는 이미 '보냈다면' 기록한 항목 제외
  4 선행 조건: needs 전부 완료·after(pr 병합·결정 답·작업 완료) 전부 풀림
  5 허브 한가: 터미널 연결·마지막 출력이 idle_output_min 이상 전·동시 활성 워커 workers_active_max 미만·이번 회차에 이 허브에 보낸 항목 없음
  6 자원 여유: free_pct_min·load_max·disk_free_gb_min, uses 에 unity 가 있으면 Unity 슬롯 비어 있음
  → 전달(on) 또는 '보냈다면' 기록(dry)
확인하지 못하는 신호(UNCHECKED)는 기록에 남김: 확인 창 대기(화면 문구 검사 미구현)·허브별 워커 매핑(워커→허브 함수 위치 미확인). 2단계 전에 채움.
정책값은 night_policy.tsv 한 곳(코드에 숫자 없음). 기록 한 줄 = JSON 한 건: event(round|resources|item|refused), at, night, mode.

환경: LRM_TASKS 장부 · LRM_HUB_DIR 작업 폴더 · LRM_NIGHT_MODE 모드 파일 · LRM_NIGHT_POLICY 정책 표 · LRM_NIGHT_LOG 기록 · LRM_NIGHT_NOW 현재 시각 epoch 초(시험용)
      LRM_GH gh 실행 파일 · LRM_REPO 저장소 · LRM_UNITY_SLOT_SH unity_slot.sh 경로
종료 코드: 0 성공(모드 off·창 밖·거부 포함), 1 파일 읽기·쓰기 실패·정책 표 오류, 2 인자 오류
"""
import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))  # 링크·복사 설치에서도 같은 폴더 모듈을 찾음
import task_ledger as tl  # noqa: E402
from ledger import LedgerError  # noqa: E402

STAGE = 1  # 구현 단계. 1: dry 만. on 모드는 2단계 검토 뒤 2 로 올림
OFF, DRY, ON = "off", "dry", "on"
MODES = (OFF, DRY, ON)
UNCHECKED = ("confirm_dialog", "hub_workers")  # 아직 측정하지 못하는 신호. 모든 item 기록에 남김
NUMBER_KEYS = ("window_start", "window_end", "idle_output_min", "free_pct_min", "load_max", "disk_free_gb_min", "workers_active_max", "night_total_max", "item_tries_max")
QUEUED_STATE = tl.ASSIGNED  # 야간 대기 상태. 전달 뒤에는 진행(2단계)
DELIVERY_HEAD = "[야간 {id}]"
DELIVERY_RULES = "야간 규칙: 구현·draft PR·CI 수정·문서만. 병합·ready 전환·라이브·데이터 쓰기·새 결정은 하지 않고 결과는 장부에만 기록(아침 보고)"


def hub_dir() -> str:
    return os.environ.get("LRM_HUB_DIR") or os.path.expanduser("~/.config/lrm-hubs")


def now_ts() -> float:
    return float(os.environ.get("LRM_NIGHT_NOW") or time.time())


def env_path(var: str, name: str) -> str:
    return os.environ.get(var) or os.path.join(hub_dir(), name)


# ---------- 정책·모드 ----------

class Policy:
    """night_policy.tsv → 숫자 칸(NUMBER_KEYS)과 금지 키워드. 칸이 빠지거나 숫자가 아니면 LedgerError"""

    def __init__(self, path: str) -> None:
        vals, forbid = {}, []
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.read().splitlines()
        except OSError as err:
            raise LedgerError(f"정책 표 읽기 실패({err.strerror}) — LRM_NIGHT_POLICY·hubs/night_policy.tsv 설치 확인", tl.EXIT_IO)
        for ln in lines:
            cells = ln.split("\t")
            if not ln.strip() or ln.lstrip().startswith("#") or len(cells) < 2:
                continue
            key, val = cells[0].strip(), cells[1].strip()
            if key == "forbid" and val:
                forbid.append(val)
            else:
                vals[key] = val
        missing = [k for k in NUMBER_KEYS if k not in vals]
        if missing:
            raise LedgerError(f"정책 표에 없는 키: {', '.join(missing)} — hubs/night_policy.tsv 와 맞춤", tl.EXIT_IO)
        try:
            for k in NUMBER_KEYS:
                setattr(self, k, float(vals[k]) if k in ("free_pct_min", "load_max", "disk_free_gb_min") else int(vals[k]))
        except ValueError:
            raise LedgerError("정책 표 값이 숫자가 아님 — 키별 숫자로 고침", tl.EXIT_IO)
        self.forbid = [re.compile(r"(?<![A-Za-z])" + re.escape(w) + r"(?![A-Za-z])", re.I) for w in forbid]
        self.forbid_words = forbid

    def in_window(self, now: float) -> bool:
        hour = datetime.datetime.fromtimestamp(now).hour
        return hour >= self.window_start or hour < self.window_end

    def night_of(self, now: float) -> str:
        """밤 이름 = 창이 시작한 날짜. 자정 뒤 회차는 전날 밤에 속함"""
        t = datetime.datetime.fromtimestamp(now)
        return (t.date() if t.hour >= self.window_start else t.date() - datetime.timedelta(days=1)).isoformat()


def read_mode() -> str:
    try:
        with open(env_path("LRM_NIGHT_MODE", "night.mode"), encoding="utf-8") as f:
            val = f.read().strip().lower()
    except OSError:
        return OFF
    return val if val in MODES else OFF


# ---------- 측정(시험에서는 이 클래스를 갈아 끼움) ----------

class Probe:
    """실제 측정. 모든 메서드는 실패를 예외로 던지지 않고 None·사유로 돌려줌(판정은 모르면 보내지 않는 쪽)"""

    def __init__(self) -> None:
        self._run = self._conf = self._rows = None

    def _orca(self):
        if self._run is None:
            import intake  # 지연 가져오기: slack_push 등을 끌고 오므로 허브 측정 때만
            import main_terminal as mt
            self._conf = intake.Conf()
            self._run = lambda args, timeout: mt.run_orca(self._conf.orca, args, timeout)
            self._mt, self._intake = mt, intake
        return self._run

    def terminals(self):
        if self._rows is None:
            run = self._orca()
            self._rows = self._mt.list_rows(run) or False
        return self._rows or None

    def hub(self, slug: str, now: float) -> dict:
        """{found, connected, output_age_min, reason}"""
        rows = self.terminals()
        if rows is None:
            return {"found": False, "reason": "terminal_list_failed"}
        path = self._intake.hub_path(self._conf, slug)
        if not path:
            return {"found": False, "reason": "hub_not_in_table"}
        handle, why = self._intake.find_hub_terminal(self._run, path)
        if not handle:
            return {"found": False, "reason": why or "no_terminal"}
        t = next(r for r in rows if r["handle"] == handle)
        last = t.get("lastOutputAt")
        age = (now - last / 1000) / 60 if isinstance(last, (int, float)) else None
        return {"found": True, "connected": t.get("connected") is True, "output_age_min": age, "reason": ""}

    def workers_active(self, now: float, fresh_min: int):
        """연결된 워커 터미널 중 최근 fresh_min 분 안에 출력이 있는 수. 목록을 못 읽으면 None"""
        rows = self.terminals()
        if rows is None:
            return None
        return sum(1 for t in rows if self._intake.is_worker(t) and t.get("connected") is True and isinstance(t.get("lastOutputAt"), (int, float))
                   and (now - t["lastOutputAt"] / 1000) / 60 < fresh_min)

    def resources(self) -> dict:
        free = None
        try:
            out = subprocess.run(["memory_pressure"], capture_output=True, text=True, timeout=20).stdout
            m = re.search(r"free percentage:\s*(\d+)%", out)
            free = int(m.group(1)) if m else None
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            disk = round(shutil.disk_usage(hub_dir()).free / 1024 ** 3, 1)
        except OSError:
            disk = None
        return {"free_pct": free, "load1": round(os.getloadavg()[0], 2), "disk_free_gb": disk}

    def unity_busy(self):
        """슬롯 사용 중이거나 대기가 있으면 True. 조회 실패 None"""
        sh = os.environ.get("LRM_UNITY_SLOT_SH") or os.path.join(hub_dir(), "unity_slot.sh")
        try:
            out = subprocess.run(["bash", sh, "status"], capture_output=True, text=True, timeout=20).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        wait = re.search(r"대기:\s*(\d+)", out)
        return "사용 중" in out or bool(wait and int(wait.group(1)) > 0)

    def pr_merged(self, ref: str):
        """ref: '#12' 또는 'Owner/Repo#12'. 병합이면 True, 아니면 False, 조회 실패 None"""
        repo, _, num = ref.rpartition("#")
        cmd = [os.environ.get("LRM_GH") or "gh", "pr", "view", num, "-R", repo or os.environ.get("LRM_REPO") or "Basakansoft/LoveRevolutionMaker", "--json", "state", "-q", ".state"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            return None
        return res.stdout.strip() == "MERGED" if res.returncode == 0 else None

    def decision_answered(self, did: str) -> bool:
        import answer_store
        return any(c["by"] for c in answer_store.AnswerStore(hub_dir()).cards(did))

    def deliver(self, slug: str, text: str) -> str:
        """on 모드 전달. intake 의 허브 터미널 탐색·확인 전송을 그대로 씀. 'sent' | 'rejected' | 'unconfirmed' | 'unknown' | 사유 문자열"""
        self._orca()
        handle, why = self._intake.find_hub_terminal(self._run, self._intake.hub_path(self._conf, slug) or "")
        return self._intake.send_confirmed(self._run, handle, text, self._intake.hub_target) if handle else (why or "no_terminal")


# ---------- 판정 ----------

def forbidden_word(policy: Policy, row: dict) -> str:
    body = " ".join(str(row.get(k) or "") for k in ("text", "goal", "done_when"))
    return next((w for w, rx in zip(policy.forbid_words, policy.forbid) if rx.search(body)), "")


def after_pending(row: dict, rows: list, probe) -> str:
    """풀리지 않은 선행 조건 하나의 설명. 다 풀렸으면 빈 문자열. 조회 실패(None)는 풀리지 않은 것으로 봄"""
    state = {r["id"]: r["state"] for r in rows}
    for n in tl.split_ids(row.get("needs")):
        if state.get(n) != tl.DONE:
            return f"needs {n}"
    for a in row.get("after", []):
        ok = {"task": lambda: state.get(a["ref"]) == tl.DONE, "pr": lambda: probe.pr_merged(a["ref"]),
              "decision": lambda: probe.decision_answered(a["ref"])}[a["kind"]]()
        if ok is not True:
            return f"after {a['kind']}:{a['ref']}" + (" 조회 실패" if ok is None else "")
    return ""


def delivery_text(row: dict) -> str:
    """허브에 보낼 한 줄. 승인 근거와 야간 규칙을 붙임"""
    n = row["night"]
    goal = row.get("goal") or row["text"]
    return " ".join(f"{DELIVERY_HEAD.format(id=row['id'])} {goal} / 완료 기준: {row.get('done_when') or '미확인'} / 승인 {n.get('approved_by_card')} {n.get('approved_at')}. {DELIVERY_RULES}".split())


class Round:
    """한 회차. 판정 상태(허브 측정 캐시·이번 회차 보낸 허브·밤 전달 수)를 한 곳에 모음"""

    def __init__(self, path: str, policy: Policy, probe, now: float, mode: str, log: str) -> None:
        self.path, self.policy, self.probe, self.now, self.mode, self.log = path, policy, probe, now, mode, log
        self.night = policy.night_of(now)
        self.sent_tonight = self.read_sent()  # {task: 이번 밤 전달·'보냈다면' 횟수}
        self.hubs_used, self.hub_cache, self.res = set(), {}, None
        self.workers = None

    def read_sent(self) -> dict:
        out = {}
        try:
            with open(self.log, encoding="utf-8") as f:
                for ln in f:
                    try:
                        d = json.loads(ln)
                    except ValueError:
                        continue
                    if isinstance(d, dict) and d.get("event") == "item" and d.get("night") == self.night and d.get("verdict") in ("would_send", "sent"):
                        out[d["task"]] = out.get(d["task"], 0) + 1
        except OSError:
            pass
        return out

    def note(self, event: str, **kw) -> dict:
        rec = {"event": event, "at": int(self.now), "night": self.night, "mode": self.mode, **kw}
        os.makedirs(os.path.dirname(os.path.abspath(self.log)), exist_ok=True)
        with open(self.log, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def measure(self) -> dict:
        """회차 시작 자원 측정을 기록(dry 하룻밤 기록으로 임계값 확정). 판정은 이 값을 씀"""
        self.res = {**self.probe.resources(), "workers_active": self.probe.workers_active(self.now, self.policy.idle_output_min)}
        self.note("resources", **self.res, unchecked=list(UNCHECKED))
        return self.res

    def resource_why(self, row: dict) -> str:
        r, p = self.res, self.policy
        if r.get("free_pct") is None or r["free_pct"] < p.free_pct_min:
            return f"메모리 여유 {r.get('free_pct')}% < {p.free_pct_min}%"
        if r.get("load1") is None or r["load1"] > p.load_max:
            return f"부하 {r.get('load1')} > {p.load_max}"
        if r.get("disk_free_gb") is None or r["disk_free_gb"] < p.disk_free_gb_min:
            return f"디스크 여유 {r.get('disk_free_gb')}GB < {p.disk_free_gb_min}GB"
        if "unity" in row.get("uses", []) and self.probe.unity_busy() is not False:
            return "Unity 슬롯 사용 중이거나 조회 실패"
        return ""

    def judge(self, row: dict, rows: list) -> tuple:
        """(verdict, reason). verdict: would_send|sent 는 호출자가 정함, 여기서는 'go'|'skip'|'morning'|'wait'"""
        p = self.policy
        if row.get("gate") != "auto":
            return "morning", f"gate={row.get('gate') or '없음'}"
        if word := forbidden_word(p, row):
            return "morning", f"금지 키워드 '{word}'"
        if not tl.night_approved(row):
            return "wait", "사용자 승인 없음"
        if self.sent_tonight.get(row["id"], 0) >= p.item_tries_max or row["night"].get("tries", 0) >= p.item_tries_max:
            return "skip", "항목 전달 상한"
        if self.mode == DRY and self.sent_tonight.get(row["id"]):
            return "skip", "이미 '보냈다면' 기록함"
        if why := after_pending(row, rows, self.probe):
            return "wait", f"선행 조건 {why}"
        if row.get("hub") in self.hubs_used:
            return "skip", "이번 회차에 이 허브에 이미 보냄"
        hub = self.hub_cache.setdefault(row.get("hub"), self.probe.hub(row.get("hub") or "", self.now))
        if not hub.get("found"):
            return "skip", f"허브 터미널 없음({hub.get('reason')})"
        if hub.get("connected") is not True:
            return "skip", "허브 연결 끊김"
        if hub.get("output_age_min") is None or hub["output_age_min"] < p.idle_output_min:
            return "skip", f"허브 최근 출력 {hub.get('output_age_min')}분 전 < {p.idle_output_min}분"
        if self.res.get("workers_active") is None or self.res["workers_active"] >= p.workers_active_max:
            return "skip", f"활성 워커 {self.res.get('workers_active')} ≥ {p.workers_active_max}"
        if why := self.resource_why(row):
            return "skip", why
        return "go", ""

    def total_sent(self) -> int:
        return sum(self.sent_tonight.values())

    def run(self, rows: list) -> dict:
        p, out = self.policy, {"night": self.night, "items": [], "stopped": ""}
        self.measure()
        queue = [r for r in rows if tl.is_night(r) and r.get("state") == QUEUED_STATE]
        queue = [r for _, r in sorted(enumerate(queue), key=lambda x: (x[1].get("order", float("inf")), x[0]))]
        for row in queue:
            if self.total_sent() >= p.night_total_max:  # 정지 신호: 하룻밤 전달 상한
                out["stopped"] = f"하룻밤 전달 상한 {p.night_total_max}건"
                self.note("round", stopped=out["stopped"])
                break
            verdict, why = self.judge(row, rows)
            rec = {"task": row["id"], "hub": row.get("hub", ""), "verdict": verdict if verdict != "go" else None, "reason": why, "unchecked": list(UNCHECKED)}
            if verdict == "go":
                rec["verdict"], rec["text"] = ("would_send" if self.mode == DRY else "sent"), delivery_text(row)
                if self.mode == ON:
                    rec["verdict"], rec["result"] = self.deliver(row, rec["text"])
                self.hubs_used.add(row.get("hub"))
                self.sent_tonight[row["id"]] = self.sent_tonight.get(row["id"], 0) + 1
            self.note("item", **rec)
            out["items"].append(rec)
        return out

    def deliver(self, row: dict, text: str) -> tuple:
        """on 경로: 전달 직전 장부에 횟수를 먼저 기록한 뒤 허브에 전송. ('sent'|'send_failed', 결과)"""
        tl.record_night_send(self.path, row["id"], self.now)
        res = self.probe.deliver(row["hub"], text)
        return ("sent" if res == "sent" else "send_failed"), res


def dispatch(path: str, policy: Policy, probe, now: float, mode: str, log: str, ignore_window: bool = False) -> dict:
    """한 회차. mode 가 off 면 아무 기록 없이 종료, on 인데 STAGE < 2 면 거부 기록만"""
    if mode == OFF:
        return {"result": "mode_off"}
    rnd = Round(path, policy, probe, now, mode, log)
    if mode == ON and STAGE < 2:
        rnd.note("refused", why=f"on 모드는 2단계 구현 전 거부(STAGE={STAGE}) — night.mode 를 dry 로")
        return {"result": "refused_on_stage_1"}
    if not ignore_window and not policy.in_window(now):
        return {"result": "outside_window"}
    rows = tl.read_rows(path)
    res = rnd.run(rows)
    return {"result": "ok", **res}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="night_dispatch.py", formatter_class=argparse.RawDescriptionHelpFormatter, description="야간 디스패처 한 회차(30분마다 launchd 가 실행)", epilog=__doc__)
    p.add_argument("--once", action="store_true", required=True, help="한 회차 실행 후 종료(필수)")
    p.add_argument("--ignore-window", action="store_true", help="밤 창 밖에서도 판정(수동 시험용)")
    p.add_argument("--json", action="store_true", help="결과를 JSON 으로 출력")
    return p


def main(argv=None, probe=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        policy = Policy(env_path("LRM_NIGHT_POLICY", "night_policy.tsv"))
        res = dispatch(tl.tasks_path(), policy, probe or Probe(), now_ts(), read_mode(), env_path("LRM_NIGHT_LOG", "night-dispatch.jsonl"), args.ignore_window)
    except (LedgerError, OSError) as err:
        print(f"오류: {err}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        items = res.get("items", [])
        print(f"야간 디스패처: {res['result']}" + (f" · 후보 {len(items)}건 · 전달(예정) {sum(1 for i in items if i['verdict'] in ('would_send', 'sent'))}건" + (f" · 정지: {res['stopped']}" if res.get("stopped") else "") if items or res["result"] == "ok" else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
