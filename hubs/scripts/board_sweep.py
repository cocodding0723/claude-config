#!/usr/bin/env python3
"""작업판(bd) 주기 정리기. launchd(ai.lrm.boardsweep)가 30분마다 board_sweep.sh 로 호출.

규칙(순서·값은 board_sweep.tsv 한 곳, 코드에 숫자·라벨 이름 없음):
  pr_done    결정·할 일 카드가 가리키는 PR(라벨 pr:N, 제목·본문 #N)이 전부 병합·닫힘이고, 전부 카드를 만든 뒤에 끝났으면 근거 댓글 + 닫기.
             카드를 만들 때 이미 끝난 PR 은 '참고로 적은 번호'로 보고 닫지 않음
  slack_answered  결정 카드 자신의 결정 번호(라벨 slack:·decision:<번호>, 제목, 본문 '결정 번호:' 줄 — 본문 인용 번호는 무시)에
             카드를 만든 뒤 온 Slack 버튼 답이 있으면 답 원문 댓글 + 닫기.
             답 기록은 answer_store(slack-inbox.jsonl)를 읽기만 함. 같은 번호의 버튼 답이 여럿이면 가장 늦은 것
  expired    라벨 expires:<ISO> 가 지난 카드 근거 댓글 + 닫기
  stale      만든 지 stale_hours 넘은 결정 카드에 라벨 stale 추가(닫지 않음)
  duplicate  같은 제목의 열린 카드가 여럿이면 가장 새 것만 두고 나머지에 댓글 + 닫기 후보 라벨
  orphan     담당(assignee) 없는 진행 중 카드가 orphan_hours 넘게 갱신·댓글이 없으면 open 으로 되돌리고 댓글
  ws_gone    작업 공간 라벨(ws:·repo:<경로>)이 가리키는 폴더가 사라진 작업 카드: 브랜치(라벨 branch:)의 PR 이 전부 병합이면 닫고,
             아니면 진행 중에서 내려놓음(open + 담당 비움). 기능 카드(epic)·작업 공간 폴더(ws_root) 밖 경로·ws_gone_hours 안에 활동한 카드는 둠.
             작업 공간 폴더 자체가 없으면(디스크 미연결) 아무것도 하지 않음
닫기가 bd 훅·게이트로 막히면 우회(--force)하지 않고 닫기 후보 라벨만 붙임.
규칙 하나가 실패해도 다른 규칙은 계속함. gh 호출은 실행당 gh_calls_max 까지(넘으면 남은 PR 은 다음 회차).
동작마다 sweep.log 에 한 줄(시각·모드·규칙·카드·동작·근거, 탭 구분), 회차 끝에 요약 한 줄. --dry-run 은 쓰기 없이 같은 줄을 mode=dry 로 남김.

환경: BOARD_DIR 작업판(기본 ~/orca/board) · BOARD_BD bd 실행 파일 · BOARD_GH gh 실행 파일
      LRM_HUB_DIR 설정 폴더(기본 ~/.config/lrm-hubs) · LRM_BOARD_SWEEP_POLICY 규칙 표 · LRM_BOARD_SWEEP_LOCAL 로컬 덮어쓰기 표
      BOARD_SWEEP_NOW 현재 시각 ISO(시험용)
종료 코드: 0 정상(일부 규칙 실패 포함) · 2 인자·규칙 표 오류 · 3 bd 목록 조회 실패 · 4 다른 실행이 잠금 보유(건너뜀)
"""
import argparse
import datetime as dt
import fcntl
import json
import os
import re
import subprocess
import sys

EXIT_OK, EXIT_CONFIG, EXIT_BD, EXIT_LOCKED = 0, 2, 3, 4
DONE_STATES = ("MERGED", "CLOSED")
REQUIRED = ("rules", "kinds", "kind_prefix", "pr_repo", "pr_label_prefix", "pr_sources", "pr_min_digits",
            "gh_calls_max", "gh_timeout_sec", "expires_label_prefix", "stale_kinds", "stale_hours", "stale_label",
            "dup_kinds", "slack_kinds", "slack_id_pattern", "slack_label_prefix", "slack_body_line", "candidate_label", "orphan_hours", "skip_label", "protect_ids", "bd_timeout_sec",
            "ws_root", "ws_label_prefix", "branch_label_prefix", "ws_gone_hours",
            "log_path", "lock_path")

EPILOG = """예:
  board_sweep.sh --dry-run        바뀔 동작만 기록(mode=dry)
  board_sweep.sh                  실제 정리
  board_sweep.sh --json           끝에 요약 JSON 한 줄
로컬 덮어쓰기: ~/.config/lrm-hubs/board_sweep.local.tsv 에 같은 형식으로 키만 적으면 그 값이 우선(예: protect_ids).
"""


class ConfigError(Exception):
    pass


def hub_dir():
    return os.path.expanduser(os.environ.get("LRM_HUB_DIR") or "~/.config/lrm-hubs")


def read_table(path):
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = line.split("\t")
            out[parts[0].strip()] = parts[1].strip() if len(parts) > 1 else ""
    return out


def load_config():
    main = os.environ.get("LRM_BOARD_SWEEP_POLICY") or os.path.join(hub_dir(), "board_sweep.tsv")
    local = os.environ.get("LRM_BOARD_SWEEP_LOCAL") or os.path.join(hub_dir(), "board_sweep.local.tsv")
    try:
        cfg = read_table(main)
    except OSError as e:
        raise ConfigError(f"규칙 표 읽기 실패({e.strerror}): {main}. 조치: install.sh 실행 또는 LRM_BOARD_SWEEP_POLICY 지정")
    if os.path.exists(local):
        try:
            cfg.update(read_table(local))
        except OSError as e:
            raise ConfigError(f"로컬 덮어쓰기 표 읽기 실패({e.strerror}): {local}")
    missing = [k for k in REQUIRED if k not in cfg]
    if missing:
        raise ConfigError(f"규칙 표에 없는 키: {', '.join(missing)}. 조치: hubs/board_sweep.tsv 와 맞춤")
    for k in ("pr_min_digits", "gh_calls_max", "gh_timeout_sec", "bd_timeout_sec"):
        if not cfg[k].isdigit():
            raise ConfigError(f"규칙 표 {k} 가 정수가 아님: '{cfg[k]}'")
    for k in ("stale_hours", "orphan_hours", "ws_gone_hours"):
        try:
            float(cfg[k])
        except ValueError:
            raise ConfigError(f"규칙 표 {k} 가 숫자가 아님: '{cfg[k]}'")
    return cfg


def as_list(value):
    return [v.strip() for v in value.split(",") if v.strip()]


def parse_time(text):
    """ISO 문자열 → 시간대 있는 datetime. 'Z'·시간대 없음(로컬)·날짜만(그날 끝, 로컬) 허용. 실패면 None"""
    if not text:
        return None
    text = text.strip().replace("Z", "+00:00")
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            d = dt.datetime.fromisoformat(text) + dt.timedelta(days=1) - dt.timedelta(seconds=1)
        else:
            d = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return d if d.tzinfo else d.astimezone()


def now():
    forced = parse_time(os.environ.get("BOARD_SWEEP_NOW", ""))
    return forced or dt.datetime.now(dt.timezone.utc).astimezone()


class Sweeper:
    def __init__(self, cfg, dry):
        self.cfg = cfg
        self.dry = dry
        self.now = now()
        self.board = os.path.expanduser(os.environ.get("BOARD_DIR") or "~/orca/board")
        self.bd_bin = os.environ.get("BOARD_BD", "bd")
        self.gh_bin = os.environ.get("BOARD_GH", "gh")
        self.gh_calls = 0
        self.gh_capped = False
        self.pr_cache = {}
        self.handled = set()  # 이번 실행에서 닫았거나 되돌린 카드. 뒤 규칙이 다시 건드리지 않음
        self.counts = {"close": 0, "label": 0, "candidate": 0, "reopen": 0, "blocked": 0, "error": 0}
        self.log_path = os.path.expanduser(cfg["log_path"])
        self.kind_prefix = cfg["kind_prefix"]
        self.protect = as_list(cfg["protect_ids"])

    # ---------- 바깥 명령 ----------

    def run(self, cmd, timeout):
        try:
            p = subprocess.run(cmd, cwd=self.board, capture_output=True, text=True, timeout=timeout,
                               stdin=subprocess.DEVNULL, env={**os.environ, "BD_ACTOR": "board-sweep"})
        except (OSError, subprocess.TimeoutExpired) as e:
            return 127, "", type(e).__name__
        return p.returncode, p.stdout, p.stderr

    def bd(self, *args):
        return self.run([self.bd_bin, *args], int(self.cfg["bd_timeout_sec"]))

    def bd_write(self, *args):
        """쓰기 명령. dry-run 이면 실행하지 않고 성공으로 봄"""
        if self.dry:
            return 0, "", ""
        return self.bd(*args)

    def pr_state(self, number):
        """(state, 끝난 시각) | None(상한 도달·조회 실패). 실행 안에서는 같은 번호를 다시 묻지 않음"""
        if number in self.pr_cache:
            return self.pr_cache[number]
        if self.gh_calls >= int(self.cfg["gh_calls_max"]):
            if not self.gh_capped:
                self.gh_capped = True
                self.log("pr_done", "-", "gh-cap", f"gh 호출 상한 {self.cfg['gh_calls_max']}회 도달 — 남은 PR 은 다음 회차")
            return None
        self.gh_calls += 1
        rc, out, err = self.run([self.gh_bin, "pr", "view", str(number), "--repo", self.cfg["pr_repo"],
                                 "--json", "state,mergedAt,closedAt"], int(self.cfg["gh_timeout_sec"]))
        result = None
        if rc == 0:
            try:
                d = json.loads(out)
                result = (d.get("state"), parse_time(d.get("mergedAt") or d.get("closedAt") or ""))
            except (json.JSONDecodeError, AttributeError):
                result = None
        if result is None:
            self.count("error")
            self.log("pr_done", f"#{number}", "gh-error", (err or out).strip().replace("\n", " ")[:160] or f"종료 {rc}")
        self.pr_cache[number] = result
        return result

    # ---------- 기록 ----------

    def log(self, rule, card_id, action, reason):
        line = "\t".join([self.now.isoformat(timespec="seconds"), "dry" if self.dry else "apply",
                          rule, card_id, action, reason.replace("\t", " ").replace("\n", " ")])
        print(line)
        try:
            os.makedirs(os.path.dirname(self.log_path) or ".", exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError as e:
            print(f"기록 실패({e.strerror}): {self.log_path}", file=sys.stderr)

    def count(self, key):
        self.counts[key] += 1

    # ---------- 카드 ----------

    def cards(self):
        rc, out, err = self.bd("list", "--json", "-n", "0")
        if rc != 0:
            raise RuntimeError(f"bd list 실패: {(err or out).strip()[:200]}. 조치: BOARD_DIR·bd 설치 확인")
        try:
            cards = json.loads(out or "[]")
        except json.JSONDecodeError:
            raise RuntimeError("bd list 출력이 JSON 아님. 조치: bd 버전 확인")
        return [c for c in cards if c.get("status") != "closed" and not self.excluded(c)]

    def excluded(self, card):
        if self.cfg["skip_label"] in (card.get("labels") or []):
            return True
        cid = card.get("id", "")
        return any(cid.startswith(p[:-1]) if p.endswith("*") else cid == p for p in self.protect)

    def kinds(self, card):
        p = self.kind_prefix
        return {lab[len(p):] for lab in card.get("labels") or [] if lab.startswith(p)}

    def comment(self, card_id, text):
        return self.bd_write("comment", card_id, text)

    def close(self, rule, card, reason):
        cid = card["id"]
        if self.cfg["candidate_label"] in (card.get("labels") or []):
            return  # 이미 닫기 후보(앞 회차에서 닫기가 막힘). 회차마다 같은 댓글을 쌓지 않음
        rc, _, err = self.comment(cid, f"정리(자동): {reason}")
        if rc != 0:
            self.count("error")
            self.log(rule, cid, "comment-failed", err.strip()[:160])
            return
        rc, out, err = self.bd_write("close", cid, "--reason", reason)
        if rc == 0:
            self.handled.add(cid)
            self.count("close")
            self.log(rule, cid, "close", reason)
            return
        # 훅·게이트가 막은 닫기는 우회하지 않음. 사람이 보게 닫기 후보 라벨만 붙임
        self.count("blocked")
        self.log(rule, cid, "close-blocked", f"{reason} / 막힌 이유: {(err or out).strip()[:120]}")
        self.bd_write("label", "add", cid, self.cfg["candidate_label"])

    # ---------- 규칙 ----------

    def rule_pr_done(self, cards):
        kinds = set(as_list(self.cfg["kinds"]))
        sources = set(as_list(self.cfg["pr_sources"]))
        pat = re.compile(r"#(\d{%d,})\b" % int(self.cfg["pr_min_digits"]))
        lp = self.cfg["pr_label_prefix"]
        for card in cards:
            if card["id"] in self.handled or not (self.kinds(card) & kinds):
                continue
            refs = set()
            if "label" in sources:
                refs |= {int(lab[len(lp):]) for lab in card.get("labels") or []
                         if lab.startswith(lp) and lab[len(lp):].isdigit()}
            if "title" in sources:
                refs |= {int(n) for n in pat.findall(card.get("title") or "")}
            if "body" in sources:
                refs |= {int(n) for n in pat.findall(card.get("description") or "")}
            if not refs:
                continue
            created = parse_time(card.get("created_at"))
            states = {}
            for n in sorted(refs):
                st = self.pr_state(n)
                if st is None:
                    break
                states[n] = st
            if len(states) != len(refs):
                continue
            if not all(s in DONE_STATES and t and created and t > created for s, t in states.values()):
                continue
            reason = ", ".join(f"PR #{n} {'병합' if s == 'MERGED' else '닫힘'}({t.isoformat(timespec='minutes')})"
                               for n, (s, t) in sorted(states.items()))
            self.close("pr_done", card, reason)

    def slack_answers(self):
        """결정 번호 → 가장 늦은 버튼 답(inbox 줄). 읽기만 함. answer_store 는 규칙이 돌 때만 불러옴(다른 규칙·launchd 짧은 PATH 에 부담 없게)"""
        sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
        from answer_store import AnswerStore
        store = AnswerStore(hub_dir())
        inbox, done = store.read_inbox(), store.read_done()
        picks = {}
        for rec in inbox:
            did, ts = rec.get("decision_id"), rec.get("parent_ts")
            if rec.get("type") != "button" or rec.get("action") != "pick" or not did or not ts or not rec.get("option_text"):
                continue
            by, win = store.judge(inbox, done, did, ts)  # 같은 카드는 첫 버튼 답만 인정
            if by and win is rec and (did not in picks or (rec.get("received_at") or 0) >= (picks[did].get("received_at") or 0)):
                picks[did] = rec
        return picks

    def own_decision_ids(self, card):
        """카드 자신의 결정 번호. 라벨(slack:·decision:)·제목·본문의 전용 줄('결정 번호: D-…')만 인정함.
        본문에서 근거로 인용한 다른 결정 번호는 그 카드의 것이 아니라 무시(옛 답으로 엉뚱한 카드가 닫히는 것 방지)"""
        base = self.cfg["slack_id_pattern"]
        pat = re.compile(r"(?<![0-9A-Za-z])(?:%s)(?![0-9A-Za-z])" % base)  # 'ID-1234'·'PROD-1' 속 번호 오인 방지
        ids = []
        for lab in card.get("labels") or []:
            for lp in as_list(self.cfg["slack_label_prefix"]):
                if lab.startswith(lp) and pat.fullmatch(lab[len(lp):]):
                    ids.append(lab[len(lp):])
        ids += pat.findall(card.get("title") or "")
        head = self.cfg["slack_body_line"]
        for line in (card.get("description") or "").splitlines():
            if line.strip().startswith(head):
                ids += pat.findall(line.strip()[len(head):])
        return ids

    def rule_slack_answered(self, cards):
        """답 시각이 카드를 만든 뒤인 버튼 답만 인정(pr_done 의 '카드를 만든 뒤에 끝난 것만' 과 같은 기준)"""
        kinds = set(as_list(self.cfg["slack_kinds"]))
        answers = None
        for card in cards:
            if card["id"] in self.handled or not (self.kinds(card) & kinds):
                continue
            ids = self.own_decision_ids(card)
            if not ids:
                continue
            if answers is None:
                answers = self.slack_answers()
            created = parse_time(card.get("created_at"))
            hit = next((i for i in ids if i in answers and answers[i].get("received_at") and created
                        and dt.datetime.fromtimestamp(answers[i]["received_at"]).astimezone() > created), None)
            if hit is None:
                continue
            rec = answers[hit]
            when = dt.datetime.fromtimestamp(rec["received_at"]).astimezone().isoformat(timespec="minutes")
            self.close("slack_answered", card, f"Slack 에서 답함({hit}, {when}) — 사용자 답: {rec['option_text']}")

    def rule_expired(self, cards):
        p = self.cfg["expires_label_prefix"]
        for card in cards:
            if card["id"] in self.handled:
                continue
            for lab in card.get("labels") or []:
                if not lab.startswith(p):
                    continue
                when = parse_time(lab[len(p):])
                if when is None:
                    self.log("expired", card["id"], "bad-label", f"만료 라벨 해석 실패: {lab}")
                    continue
                if self.now > when:
                    self.close("expired", card, f"만료 라벨 {lab} 지남")
                    break

    def rule_stale(self, cards):
        kinds = set(as_list(self.cfg["stale_kinds"]))
        label = self.cfg["stale_label"]
        limit = dt.timedelta(hours=float(self.cfg["stale_hours"]))
        for card in cards:
            if card["id"] in self.handled or not (self.kinds(card) & kinds) or label in (card.get("labels") or []):
                continue
            created = parse_time(card.get("created_at"))
            if created and self.now - created > limit:
                rc, _, err = self.bd_write("label", "add", card["id"], label)
                if rc == 0:
                    self.count("label")
                    self.log("stale", card["id"], "label", f"{label} — 만든 지 {self.cfg['stale_hours']}시간 넘음")
                else:
                    self.count("error")
                    self.log("stale", card["id"], "label-failed", err.strip()[:160])

    def rule_duplicate(self, cards):
        kinds = set(as_list(self.cfg["dup_kinds"]))
        label = self.cfg["candidate_label"]
        groups = {}
        for card in cards:
            if card["id"] in self.handled or not (self.kinds(card) & kinds):
                continue
            key = re.sub(r"\s+", " ", (card.get("title") or "").strip()).lower()
            if key:
                groups.setdefault(key, []).append(card)
        for group in groups.values():
            if len(group) < 2:
                continue
            group.sort(key=lambda c: (c.get("created_at") or "", c["id"]))
            keep = group[-1]
            for card in group[:-1]:
                if label in (card.get("labels") or []):
                    continue
                reason = f"같은 제목의 새 카드 {keep['id']} 가 있음 — 닫기 후보"
                rc, _, err = self.comment(card["id"], f"정리(자동): {reason}")
                if rc == 0:
                    rc, _, err = self.bd_write("label", "add", card["id"], label)
                if rc == 0:
                    self.count("candidate")
                    self.log("duplicate", card["id"], "candidate", reason)
                else:
                    self.count("error")
                    self.log("duplicate", card["id"], "candidate-failed", err.strip()[:160])

    def last_activity(self, card):
        times = [parse_time(card.get(k)) for k in ("updated_at", "started_at", "created_at")]
        if card.get("comment_count"):
            rc, out, _ = self.bd("comments", card["id"], "--json")
            if rc != 0:
                return None  # 댓글을 못 읽으면 활동을 모르는 것. 되돌리지 않음
            try:
                times += [parse_time(c.get("created_at")) for c in json.loads(out or "[]")]
            except (json.JSONDecodeError, AttributeError):
                return None
        times = [t for t in times if t]
        return max(times) if times else None

    def rule_orphan(self, cards):
        limit = dt.timedelta(hours=float(self.cfg["orphan_hours"]))
        for card in cards:
            if card["id"] in self.handled or card.get("status") != "in_progress" or card.get("assignee"):
                continue
            last = self.last_activity(card)
            if last is None or self.now - last <= limit:
                continue
            reason = f"담당 없음·{self.cfg['orphan_hours']}시간 넘게 갱신·댓글 없음(마지막 {last.isoformat(timespec='minutes')}) → open"
            rc, _, err = self.bd_write("update", card["id"], "--status", "open")
            if rc == 0:
                self.comment(card["id"], f"정리(자동): {reason}")
                self.handled.add(card["id"])
                self.count("reopen")
                self.log("orphan", card["id"], "reopen", reason)
            else:
                self.count("error")
                self.log("orphan", card["id"], "reopen-failed", err.strip()[:160])

    def branch_prs(self, branch):
        """브랜치를 head 로 가진 PR 의 [(번호, 상태)] | None(상한 도달·조회 실패). 실행 안에서는 같은 브랜치를 다시 묻지 않음"""
        key = "head:" + branch
        if key in self.pr_cache:
            return self.pr_cache[key]
        if self.gh_calls >= int(self.cfg["gh_calls_max"]):
            return None  # 다음 회차에 다시 봄. 닫지 않을 뿐 내려놓기는 그대로 함
        self.gh_calls += 1
        rc, out, err = self.run([self.gh_bin, "pr", "list", "--repo", self.cfg["pr_repo"], "--head", branch, "--state", "all",
                                 "--json", "number,state"], int(self.cfg["gh_timeout_sec"]))
        result = None
        if rc == 0:
            try:
                result = [(int(d["number"]), d.get("state")) for d in json.loads(out or "[]")]
            except (json.JSONDecodeError, AttributeError, KeyError, TypeError, ValueError):
                result = None
        if result is None:
            self.count("error")
            self.log("ws_gone", branch, "gh-error", (err or out).strip().replace("\n", " ")[:160] or f"종료 {rc}")
        self.pr_cache[key] = result
        return result

    def put_down(self, rule, card, reason):
        """진행 중 카드를 할 일로 내려놓고 담당을 비움. 작업 공간이 없어 이어서 할 세션이 없는 카드가 진행 중 목록에 남지 않게 함"""
        cid = card["id"]
        rc, _, err = self.bd_write("update", cid, "--status", "open")
        if rc != 0:
            self.count("error")
            self.log(rule, cid, "reopen-failed", err.strip()[:160])
            return
        self.bd_write("update", cid, "--assignee", "")
        self.comment(cid, f"정리(자동): {reason}")
        self.handled.add(cid)
        self.count("reopen")
        self.log(rule, cid, "reopen", reason)

    def rule_ws_gone(self, cards):
        root = os.path.normpath(os.path.expanduser(self.cfg["ws_root"]))
        if not os.path.isdir(root):
            self.log("ws_gone", "-", "root-missing", f"작업 공간 폴더 {root} 없음 — 이번 회차는 건너뜀")
            return
        prefixes, bp = as_list(self.cfg["ws_label_prefix"]), self.cfg["branch_label_prefix"]
        grace = dt.timedelta(hours=float(self.cfg["ws_gone_hours"]))
        for card in cards:
            if card["id"] in self.handled or card.get("issue_type") == "epic":
                continue
            labels = card.get("labels") or []
            paths = [os.path.normpath(os.path.expanduser(lab[len(p):])) for lab in labels for p in prefixes if lab.startswith(p)]
            paths = [p for p in paths if p.startswith(root + os.sep)]
            if not paths or any(os.path.exists(p) for p in paths):
                continue
            last = self.last_activity(card)
            if last is None or self.now - last <= grace:
                continue
            name = os.path.basename(paths[0])
            working = card.get("status") == "in_progress"
            branch = next((lab[len(bp):] for lab in labels if lab.startswith(bp) and lab[len(bp):]), "")
            prs = self.branch_prs(branch) if branch else []
            if prs and all(state == "MERGED" for _, state in prs):
                self.close("ws_gone", card, f"작업 공간 {name} 없음·PR {', '.join(f'#{n}' for n, _ in prs)} 병합")
                if working and card["id"] not in self.handled and not self.dry:
                    # 닫기가 게이트에 막힘: 닫기 후보 라벨은 close 가 붙였고, 진행 중 목록에서는 뺌
                    self.put_down("ws_gone", card, f"작업 공간 {name} 없음(닫기는 게이트에 막혀 닫기 후보로 둠)")
            elif working:
                self.put_down("ws_gone", card, f"작업 공간 {name} 없음 → 할 일로 내려놓음(이어서 하려면 다시 집음)")

    RULES = {"ws_gone": rule_ws_gone, "pr_done": rule_pr_done, "slack_answered": rule_slack_answered, "expired": rule_expired, "stale": rule_stale,
             "duplicate": rule_duplicate, "orphan": rule_orphan}

    def sweep(self):
        cards = self.cards()
        for name in as_list(self.cfg["rules"]):
            fn = self.RULES.get(name)
            if fn is None:
                self.count("error")
                self.log(name, "-", "unknown-rule", "규칙 표 rules 에 모르는 이름 — hubs/board_sweep.tsv 확인")
                continue
            try:
                fn(self, cards)
            except Exception as e:  # 규칙 하나의 실패가 다른 규칙을 막지 않게 함
                self.count("error")
                self.log(name, "-", "rule-error", f"{type(e).__name__}: {e}"[:200])
        summary = {"mode": "dry" if self.dry else "apply", "cards": len(cards), **self.counts,
                   "gh_calls": self.gh_calls, "gh_calls_max": int(self.cfg["gh_calls_max"])}
        # 동작이 없는 회차도 한 줄 남김. 주기 실행이 살아 있는지 기록만 보고 판단
        self.log("run", "-", "summary", " ".join(f"{k}={v}" for k, v in summary.items() if k != "mode"))
        return summary


def main(argv=None):
    ap = argparse.ArgumentParser(prog="board_sweep", description="작업판(bd) 주기 정리기. 규칙은 board_sweep.tsv.",
                                 epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="쓰기 없이 바뀔 동작만 기록(mode=dry)")
    ap.add_argument("--json", action="store_true", help="끝에 요약을 JSON 한 줄로 출력")
    args = ap.parse_args(argv)
    try:
        cfg = load_config()
    except ConfigError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return EXIT_CONFIG
    lock_path = os.path.expanduser(cfg["lock_path"])
    try:
        os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
        lock = open(lock_path, "a")
    except OSError as e:
        print(f"ERROR: 잠금 파일 열기 실패({e.strerror}): {lock_path}", file=sys.stderr)
        return EXIT_CONFIG
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print(f"다른 정리기 실행이 잠금 보유 — 건너뜀: {lock_path}", file=sys.stderr)
        return EXIT_LOCKED
    sweeper = Sweeper(cfg, args.dry_run)
    try:
        summary = sweeper.sweep()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sweeper.log("-", "-", "bd-error", str(e)[:200])
        return EXIT_BD
    if args.json:
        print(json.dumps(summary, ensure_ascii=False))
    else:
        print("요약: " + " · ".join(f"{k} {v}" for k, v in summary.items()))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
