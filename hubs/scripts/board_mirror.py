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
        with (self.home / "board-mirror.log").open("a", encoding="utf-8") as f:
            f.write(line.replace("\n", " ") + "\n")

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
        return " ".join(lines).strip(), refs + moved

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
        ws = next((self.label(card, p) for p in self.cfg["ws_prefixes"].split(",")
                   if os.path.isabs(self.label(card, p)) and not re.search(r"[\n,]", self.label(card, p))), "")
        rep = report.make_report(self.hub(root), report.NO_TASK, "info", body, files,
                                 card=root["id"], ws=ws or None)
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

    def process(self, root, card, state, queued, baseline):
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
            print(json.dumps({"baseline": cid, "records": len(rows)}, ensure_ascii=False))
        else:
            sent = {i for q in queued if q.get("mirror_card", q.get("card")) == cid for i in q.get("mirror_ids", [])}
            previous = {q["text"].strip() for q in queued if not q.get("mirror_ids")
                        and (q.get("card") == cid or q.get("hub") == self.hub(root))}
            fresh = [r for r in rows if str(r["id"]) not in set(known) | sent
                     and r.get("text", "").strip() not in previous]
            accepted, blocked, attempts = [], False, {}
            prior = failures.get(cid, {})
            for row in fresh:
                self.remaining()
                try:
                    self.enqueue(root, card, [row], validate=True)
                    accepted.append(row)
                except BudgetExpired:
                    raise
                except Exception as e:
                    blocked |= not self.failure(cid, str(row["id"]), e, prior, attempts)
            failures[cid] = attempts
            if blocked:
                return
            try:
                self.enqueue(root, card, accepted)
            except BudgetExpired:
                raise
            except Exception as e:
                skipped = []
                for row in accepted:
                    skipped.append(self.failure(cid, str(row["id"]), e, prior, attempts))
                if not all(skipped):
                    return
        seen[cid] = ids
        state[cid] = ids[-1] if ids else ""
        failures.pop(cid, None)

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
        cards = self.bd("list", "--all", "--json", "-n", "0")
        queued = report.read_outbox(self.outbox)
        features = state.setdefault("_features", [])
        try:
            for root in self.roots(cards):
                baseline = root["id"] not in state and root["id"] not in features
                if root["id"] not in features:
                    features.append(root["id"])
                for card in self.descendants(root, cards):
                    try:
                        self.remaining()
                        self.process(root, card, state, queued, baseline)
                    except BudgetExpired:
                        raise
                    except Exception as e:
                        reason = str(e) if not report.has_secret(str(e)) else "비밀 값 의심"
                        self.log(card["id"], f"카드 조회·기록 실패: {type(e).__name__} {reason[:200]}")
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
    p.add_argument("--policy", default=str(Path(__file__).resolve().parents[1] / "board_mirror.tsv"))
    args = p.parse_args()
    mirror = None
    def expired(*_):
        raise BudgetExpired("전체 시간 예산 소진")
    try:
        mirror = Mirror(args.policy, args.dry_run, args.card)
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
