#!/usr/bin/env python3
"""카드 기록 → 허브 보고 대기열. 첫 관찰은 장부만 작성, dry-run은 쓰기 없음."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hub_report as report
from board_sweep import read_table


class Mirror:
    def __init__(self, policy, dry=False, card=None):
        self.cfg = read_table(policy)
        self.dry, self.card = dry, card
        self.home = Path(os.environ.get("LRM_HUB_DIR") or "~/.config/lrm-hubs").expanduser()
        self.state = self.home / "board-mirror.state.json"
        self.outbox = report.outbox_path()

    def bd(self, *args):
        p = subprocess.run([os.environ.get("BOARD_BD") or "bd", *args],
                           cwd=os.path.expanduser(os.environ.get("BOARD_DIR") or "~/orca/board"),
                           capture_output=True, text=True, timeout=int(self.cfg["timeout_sec"]))
        if p.returncode:
            raise ValueError("bd 조회 실패 — BOARD_DIR·BOARD_BD 확인")
        return json.loads(p.stdout)

    def label(self, card, prefix):
        return next((x[len(prefix):] for x in card.get("labels", []) if x.startswith(prefix)), "")

    def roots(self, cards):
        return [c for c in cards if c.get("status") != "closed" and c.get("issue_type") == "epic"
                and self.cfg["flow_label"] in c.get("labels", [])
                and self.label(c, self.cfg["feature_prefix"]) and self.label(c, self.cfg["thread_prefix"])
                and (not self.card or c["id"] == self.card)]

    def descendants(self, root, cards):
        selected = {root["id"]}
        while True:
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
            text = text.replace(author, "")
        if chars > int(self.cfg["long_chars"]) or re.search(self.cfg["brief_pattern"], text):
            text = text.splitlines()[0] + self.cfg["length_format"].format(chars=chars)
        text = re.sub(self.cfg["ref_pattern"], "", text)
        lines, moved = report.move_refs(text.splitlines())
        return " ".join(lines).strip(), refs + moved

    def enqueue(self, root, card, rows):
        texts, refs = [], [card["id"]]
        for row in rows:
            text, found = self.clean(row)
            if text:
                texts.append(text)
                refs.extend(found)
        if not texts:
            return
        ref = self.cfg["reference"] + " " + " · ".join(dict.fromkeys(refs))
        # 경로는 인라인 코드로 표시. 첨부 없는 로컬 문서로 오인되는 것 방지
        body = "\n".join([self.cfg["title"], "- " + "; ".join(texts), ref])
        body = report.LOCAL_PATH_RE.sub(lambda m: "`" + m[0] + "`", body)
        files = []
        ids = [str(r["id"]) for r in rows]
        if report.has_secret(body):
            raise ValueError("비밀 값 의심 기록 — 카드에서 값을 제거한 뒤 재시도")
        if len(body) > report.MAX_CHARS or report.line_gaps_split(body, "info")[0]:
            digest = hashlib.sha256(json.dumps([card["id"], ids]).encode()).hexdigest()[:20]
            path = self.home / "board-mirror-files" / (digest + ".txt")
            files = [str(path)]
            if not self.dry:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("\n".join(texts + [ref]), encoding="utf-8")
            body = "\n".join([self.cfg["title"], self.cfg["overflow_format"].format(count=len(texts)),
                               self.cfg["reference"] + " " + card["id"]])
        ws = next((self.label(card, p) for p in self.cfg["ws_prefixes"].split(",")
                   if self.label(card, p)), "")
        rep = report.make_report(self.cfg["hub_prefix"] + self.label(root, self.cfg["feature_prefix"]),
                                 report.NO_TASK, "info", body, files, card=card["id"], ws=ws or None)
        rep["mirror_ids"] = ids
        print(json.dumps(rep, ensure_ascii=False))
        if not self.dry:
            report.add(self.outbox, rep, time.time(), False, stage=True)

    def run(self):
        state = json.loads(self.state.read_text()) if self.state.exists() else {}
        cards = self.bd("list", "--all", "--json", "-n", "0")
        queued = report.read_outbox(self.outbox)
        boundary = state.setdefault("_boundary", {})
        for root in self.roots(cards):
            for card in self.descendants(root, cards):
                cid = card["id"]
                rows = sorted(self.bd("comments", cid, "--json"),
                              key=lambda r: (r.get("created_at", ""), str(r["id"])))
                ids = [str(r["id"]) for r in rows]
                if cid not in state:
                    print(json.dumps({"baseline": cid, "records": len(rows)}, ensure_ascii=False))
                else:
                    last = state[cid]
                    if last and last not in ids:
                        raise ValueError("장부의 기록 ID 없음 — 카드 기록 삭제 여부 확인")
                    # bd 시각은 초 단위. 같은 초에 늦게 추가된 UUID의 정렬 위치가 앞설 수 있음
                    stamp = rows[ids.index(last)].get("created_at", "") if last else ""
                    known = set(boundary.get(cid, [last]))
                    fresh = [r for r in rows if r.get("created_at", "") >= stamp and str(r["id"]) not in known]
                    sent = {i for q in queued if q.get("card") == cid for i in q.get("mirror_ids", [])}
                    previous = {q["text"].strip() for q in queued if not q.get("mirror_ids")
                                and (q.get("card") == cid or q.get("hub") == self.cfg["hub_prefix"]
                                     + self.label(root, self.cfg["feature_prefix"]))}
                    self.enqueue(root, card, [r for r in fresh if str(r["id"]) not in sent
                                             and r.get("text", "").strip() not in previous])
                state[cid] = ids[-1] if ids else ""
                boundary[cid] = [str(r["id"]) for r in rows
                                 if r.get("created_at", "") == rows[-1].get("created_at", "")]
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
    try:
        mirror = Mirror(args.policy, args.dry_run, args.card)
        if args.dry_run:
            mirror.run()
        else:
            with report.locked(str(mirror.state)):
                mirror.run()
        return 0
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired, report.ReportError) as e:
        print(f"카드 미러 실패: {e} — 규칙표·작업판·상태 폴더 확인", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
