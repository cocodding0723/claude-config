#!/usr/bin/env python3
"""카드 기록 → 허브 보고 대기열. 기능 첫 관찰은 기준선, dry-run은 쓰기 없음."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hub_report as report
from board_sweep import read_table


class BudgetExpired(Exception):
    pass


class Mirror:
    def __init__(self, policy, dry=False, card=None):
        self.cfg = read_table(policy)
        self.dry, self.card = dry, card
        self.home = Path(os.environ.get("LRM_HUB_DIR") or "~/.config/lrm-hubs").expanduser()
        self.state = self.home / "board-mirror.state.json"
        self.outbox = report.outbox_path()
        self.deadline = time.monotonic() + float(self.cfg["budget_sec"])

    def remaining(self):
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise BudgetExpired("전체 시간 예산 소진")
        return left

    def log(self, cid, reason):
        line = f"{dt.datetime.now(dt.timezone.utc).isoformat()} {cid} {reason}"
        if self.dry:
            print(line)
            return
        self.home.mkdir(parents=True, exist_ok=True)
        data = (line.replace("\n", " ") + "\n").encode("utf-8")
        limit = int(self.cfg.get("log_bytes", 1024 * 1024))
        with (self.home / "board-mirror.log").open("a+b") as f:
            if f.tell() + len(data) > limit:
                f.seek(max(0, f.tell() - limit // 2))
                tail = f.read().partition(b"\n")[2]
                f.truncate(0)
                f.write(tail)
            f.write(data[-limit:])

    def bd(self, *args):
        p = subprocess.run([os.environ.get("BOARD_BD") or "bd", *args],
                           cwd=os.path.expanduser(os.environ.get("BOARD_DIR") or "~/orca/board"),
                           capture_output=True, text=True, timeout=min(float(self.cfg["timeout_sec"]), self.remaining()))
        if p.returncode:
            raise ValueError("bd 조회 실패 — BOARD_DIR·BOARD_BD 확인")
        return json.loads(p.stdout)

    def label(self, card, prefix):
        return next((x[len(prefix):] for x in card.get("labels", []) if x.startswith(prefix)), "")

    def hub(self, root):
        return self.cfg["hub_prefix"] + self.label(root, self.cfg["feature_prefix"]).lower()

    def active(self, card):
        if card.get("status") != "closed":
            return True
        try:
            closed = dt.datetime.fromisoformat(card["closed_at"].replace("Z", "+00:00"))
            age = dt.datetime.now(dt.timezone.utc) - closed.astimezone(dt.timezone.utc)
            return age.total_seconds() <= float(self.cfg["closed_hours"]) * 3600
        except (KeyError, ValueError, TypeError):
            return False

    def roots(self, cards):
        return [c for c in cards if self.active(c) and c.get("issue_type") == "epic"
                and self.cfg["flow_label"] in c.get("labels", [])
                and self.label(c, self.cfg["feature_prefix"]) and self.label(c, self.cfg["thread_prefix"])
                and (not self.card or c["id"] == self.card)]

    def descendants(self, root, cards):
        selected = {root["id"]}
        while True:
            self.remaining()
            added = {c["id"] for c in cards if c.get("parent") in selected or any(
                d.get("type") == "parent-child" and d.get("depends_on_id") in selected
                for d in c.get("dependencies", []))}
            if added <= selected:
                return [c for c in cards if c["id"] in selected]
            selected |= added

    def clean(self, row):
        text = row.get("text", "").strip()
        if not text or re.search(self.cfg["skip_pattern"], text):
            return "", []
        refs = re.findall(self.cfg["ref_pattern"], text)
        chars = len(text)
        author = row.get("author", "")
        if author:
            refs.append(author)
            text = re.sub(r"^" + re.escape(author) + r"(?:\s*[:：]\s*|\s+)", "", text, count=1)
        if chars > int(self.cfg["long_chars"]) or re.search(self.cfg["brief_pattern"], text):
            text = text.splitlines()[0] + self.cfg["length_format"].format(chars=chars)
        text = re.sub(self.cfg["ref_pattern"], "", text)
        lines, moved = report.move_refs(text.splitlines())
        body = []
        for line in lines:
            if line.lstrip().startswith(self.cfg["reference"]):
                refs.append(line.lstrip()[len(self.cfg["reference"]):].strip())
            else:
                body.append(line)
        return " ".join(body).strip(), refs + moved

    def filter_records(self, root, cid, rows, queued):
        previous = {q["text"].strip() for q in queued if not q.get("mirror_ids")
                    and (q.get("card") == cid or q.get("hub") == self.hub(root))}
        return [r for r in rows if self.clean(r)[0] and r.get("text", "").strip() not in previous]

    def enqueue(self, root, card, rows, validate=False):
        texts, refs = [], [card["id"]]
        for row in rows:
            text, found = self.clean(row)
            if text:
                texts.append(text)
                refs.extend(found)
        if not texts:
            return
        ref = self.cfg["reference"] + " " + " · ".join(dict.fromkeys(refs))
        body = "\n".join([self.cfg["title"], "- " + "; ".join(texts), ref])
        # 기존 인라인 코드를 보존. 경로를 다시 감싸면 첨부 검사에서 코드 경계가 깨짐
        body = "".join(part if i % 2 else report.LOCAL_PATH_RE.sub(lambda m: "`" + m[0] + "`", part)
                       for i, part in enumerate(re.split(r"(`[^`\n]*`)", body)))
        files = []
        ids = [str(r["id"]) for r in rows]
        if report.has_secret(body):
            raise ValueError("비밀 값 의심 기록 — 카드에서 값을 제거한 뒤 재시도")
        if len(body) > report.MAX_CHARS or report.line_gaps_split(body, "info")[0]:
            digest = hashlib.sha256(json.dumps([card["id"], ids]).encode()).hexdigest()[:20]
            path = self.home / "board-mirror-files" / (digest + ".txt")
            files = [str(path)]
            if not self.dry and not validate:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("\n".join(texts + [ref]), encoding="utf-8")
            body = "\n".join([self.cfg["title"], self.cfg["overflow_format"].format(count=len(texts)),
                               self.cfg["reference"] + " " + card["id"]])
        rep = report.make_report(self.hub(root), report.NO_TASK, "info", body, files,
                                 card=root["id"])
        rep.update(mirror_ids=ids, mirror_card=card["id"])
        if not validate:
            print(json.dumps(rep, ensure_ascii=False))
            if not self.dry:
                report.add(self.outbox, rep, time.time(), False, stage=True)

    def failure(self, cid, rid, error, previous, attempts):
        count = previous.get(rid, 0) + 1
        attempts[rid] = count
        skip = count >= int(self.cfg["failure_limit"])
        reason = str(error) if not report.has_secret(str(error)) else "비밀 값 의심"
        self.log(cid, f"기록 {rid} {'건너뜀' if skip else '실패'} {count}회: {reason[:200]}")
        return skip

    def process(self, root, card, state, queued, baseline, baseline_before=None):
        cid = card["id"]
        rows = self.bd("comments", cid, "--json")[-int(self.cfg["seen_limit"]):]
        ids = [str(r["id"]) for r in rows]
        seen = state.setdefault("_seen", {})
        failures = state.setdefault("_failures", {})
        known = seen.get(cid)
        if known is None:
            old = state.get(cid)
            known = ids[:ids.index(old) + 1] if old in ids else []
        if baseline:
            def old(row):
                try:
                    return dt.datetime.fromisoformat(row["created_at"].replace("Z", "+00:00")).timestamp() <= baseline_before
                except (KeyError, ValueError):
                    return True
            known = [str(r["id"]) for r in rows if old(r)]
            state.setdefault("_baseline_ids", {})[cid] = known[-int(self.cfg["seen_limit"]):]
            print(json.dumps({"baseline": cid, "records": len(known)}, ensure_ascii=False))
        if not baseline or len(known) < len(rows):
            sent = {i for q in queued if q.get("mirror_card", q.get("card")) == cid for i in q.get("mirror_ids", [])}
            fresh = [r for r in self.filter_records(root, cid, rows, queued) if str(r["id"]) not in set(known) | sent]
            accepted, blocked, attempts = [], False, {}
            prior = failures.get(cid, {})
            for row in fresh:
                self.remaining()
                try:
                    self.enqueue(root, card, [row], validate=True)
                    accepted.append(row)
                except BudgetExpired:
                    raise
                except (ValueError, report.ReportError) as e:
                    skipped = self.failure(cid, str(row["id"]), e, prior, attempts)
                    if skipped:
                        history = state.setdefault("_skipped_ids", {}).setdefault(cid, [])
                        history[:] = list(dict.fromkeys([*history, str(row["id"])]))[-int(self.cfg["seen_limit"]):]
                    blocked |= not skipped
            failures[cid] = attempts
            if blocked:
                return
            try:
                self.enqueue(root, card, accepted)
            except BudgetExpired:
                raise
            except Exception as e:
                reason = str(e) if not report.has_secret(str(e)) else "비밀 값 의심"
                self.log(cid, f"대기열 기록 실패 — 다음 회차 재시도: {reason[:200]}")
                return
        seen[cid] = ids
        state[cid] = ids[-1] if ids else ""
        failures.pop(cid, None)
        return True

    def cleanup(self):
        if self.dry:
            return
        cutoff = time.time() - float(self.cfg["retention_days"]) * 86400
        for path in (self.home / "board-mirror-files").glob("*.txt"):
            self.remaining()
            if path.stat().st_mtime < cutoff:
                path.unlink()

    def run(self):
        state = json.loads(self.state.read_text()) if self.state.exists() else {}
        if "_baseline_ids" not in state:
            state["_baseline_ids"] = {}
            state["_baseline_tracking_at"] = int(time.time())
        cards = self.bd("list", "--all", "--json", "-n", "0")
        queued = report.read_outbox(self.outbox)
        features = state.setdefault("_features", [])
        bootstrap = state.setdefault("_bootstrap", {})
        bootstrap_at = state.setdefault("_bootstrap_at", {})
        try:
            jobs = []
            for root in self.roots(cards):
                descendants = self.descendants(root, cards)
                rid = root["id"]
                if rid not in features and rid not in state and rid not in bootstrap:
                    bootstrap[rid] = [c["id"] for c in descendants]
                if rid in bootstrap:
                    bootstrap_at.setdefault(rid, time.time())
                jobs.extend((root, card) for card in descendants)
            keys = [[root["id"], card["id"]] for root, card in jobs]
            offset = keys.index(state["_resume"]) if state.get("_resume") in keys else 0
            jobs = jobs[offset:] + jobs[:offset]
            for index, (root, card) in enumerate(jobs):
                rid, cid = root["id"], card["id"]
                state["_resume"] = [rid, cid]
                try:
                    self.remaining()
                    pending = bootstrap.get(rid, [])
                    completed = self.process(root, card, state, queued, cid in pending and cid not in state,
                                             bootstrap_at.get(rid))
                    if completed and cid in pending:
                        pending.remove(cid)
                    if rid in bootstrap and not pending:
                        bootstrap.pop(rid)
                        bootstrap_at.pop(rid, None)
                        if rid not in features:
                            features.append(rid)
                except BudgetExpired:
                    raise
                except Exception as e:
                    reason = str(e) if not report.has_secret(str(e)) else "비밀 값 의심"
                    self.log(cid, f"카드 조회·기록 실패: {type(e).__name__} {reason[:200]}")
                following = jobs[(index + 1) % len(jobs)]
                state["_resume"] = [following[0]["id"], following[1]["id"]]
            state.pop("_resume", None)
            self.cleanup()
        except BudgetExpired:
            self.log("-", "전체 시간 예산 소진 — 다음 회차 재개")
        finally:
            state.pop("_boundary", None)
            if not self.dry:
                temp = self.state.with_suffix(".tmp")
                temp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
                temp.replace(self.state)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--once", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--card", help="미리볼 기능 카드")
    p.add_argument("--budget-seconds", type=float, help="폴러에 남은 실행 시간")
    p.add_argument("--policy", default=str(Path(__file__).resolve().parents[1] / "board_mirror.tsv"))
    args = p.parse_args()
    mirror = None
    def expired(*_):
        raise BudgetExpired("전체 시간 예산 소진")
    try:
        mirror = Mirror(args.policy, args.dry_run, args.card)
        if args.budget_seconds is not None:
            mirror.deadline = min(mirror.deadline, time.monotonic() + max(0, args.budget_seconds))
        signal.signal(signal.SIGALRM, expired)
        # flock 대기도 실행 예산에 포함. 루프 검사만으로는 잠금 대기를 끊지 못함
        signal.setitimer(signal.ITIMER_REAL, mirror.remaining())
        if args.dry_run:
            mirror.run()
        else:
            with report.locked(str(mirror.state)):
                mirror.run()
        return 0
    except Exception as e:
        if mirror:
            reason = str(e) if not report.has_secret(str(e)) else "비밀 값 의심"
            mirror.log("-", f"미러 실행 실패: {type(e).__name__} {reason[:200]}")
        print(f"카드 미러 실패: {type(e).__name__} — 규칙표·작업판·상태 폴더 확인", file=sys.stderr)
        return 1
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


if __name__ == "__main__":
    sys.exit(main())
