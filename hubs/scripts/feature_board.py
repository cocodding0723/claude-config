"""기능 스레드 현황판. 작업판 읽기와 메시지 생성 분리."""
import argparse
from datetime import datetime, timezone
import hashlib
import html
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


class Format:
    title = 36
    line = 100
    timeout = 5
    ended = ("closed", "tombstone")
    echoes = ("Slack 결정 답:", "Slack 답:", "Slack 스레드", "Slack 보고:",
              "[Slack", "[slack", "되울림:", "[board-mirror]")
    blocked = re.compile(r"^(?:\[block\]|🔴|막힘\s*[:：]|차단\s*[:：]|blocked\s*[:：])", re.I)
    ids = re.compile(r"\b(?:lrm-[\w.-]+|D-[A-Za-z0-9_-]+|(?=[a-f0-9]*\d)(?=[a-f0-9]*[a-f])[a-f0-9]{7,40})\b", re.I)
    pr = re.compile(r"https://github\.com/[^\s<>]+/pull/\d+|\bPR\s*#\d+", re.I)
    docs = re.compile(r"https?://[^\s<>\"']+\.html|(?<![\w:/])(?:/|docs/)[^\s<>\"']+\.html")


def stamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return datetime.min.replace(tzinfo=timezone.utc)


def plain(value, limit=Format.line):
    text = Format.docs.sub("", Format.pr.sub("", str(value or "")))
    text = Format.ids.sub("", text)
    text = " ".join(text.replace(" / ", " — ").split()).strip(" :：-·")
    text = text if len(text) <= limit else text[:limit - 1] + "…"
    return html.escape(text, quote=False)


def field(description, name):
    match = re.search(r"^[ \t>*-]*" + re.escape(name) + r"\**\s*[:：]\s*(.+)$", description or "", re.M)
    return match.group(1) if match else ""


def visible_comments(comments):
    return sorted((c for c in comments if c.get("text", "").strip()
                   and not c["text"].lstrip().startswith(Format.echoes)),
                  key=lambda c: (stamp(c.get("created_at")), str(c.get("id", ""))))


def render(epic, children, comments, stages):
    """같은 스냅샷에서 같은 Slack 본문 생성. stages는 (키, 이름) 순서 목록."""
    children = sorted(children, key=lambda c: str(c.get("id", "")))
    records = visible_comments(comments)
    latest = records[-1] if records else {}
    waiting = [c for c in children if c.get("status") not in Format.ended
               and "kind:decision" in (c.get("labels") or [])]
    blocked = any(c.get("status") == "blocked" for c in [epic, *children])
    # 이전 막힘 기록이 해결 뒤에도 남는 오판 방지. 카드별 최신 기록만 판정.
    active = {c.get("id") for c in [epic, *children] if c.get("status") not in Format.ended}
    last_by_card = {c.get("issue_id", epic.get("id")): c for c in records}
    blocked = blocked or any(Format.blocked.match(c["text"].lstrip()) for cid, c in last_by_card.items() if cid in active)
    state = "🔴 막힘" if blocked else "🟡 사용자 답 기다림" if waiting else "🟢 진행"
    name = field(epic.get("description"), "쉬운 이름") or epic.get("title") or "기능 이름 없음"
    title_limit = max(1, Format.title - len(state))
    lines = [f"{state} {plain(name, title_limit)}", "",
             "- 무엇: " + (plain(field(epic.get("description"), "목표")) or "목표 미기재"),
             "- 왜: 카드 상태와 사용자 할 일을 한곳에서 확인"]
    labels = epic.get("labels") or []
    selected = [key for key, _ in stages if "stage:" + key in labels]
    current = selected[-1] if selected else None
    bar = " → ".join(f"{'▶ ' if key == current else ''}{i} {name}" for i, (key, name) in enumerate(stages))
    lines.append("- 단계: " + bar + (" (미지정)" if current is None else ""))
    done = sum(c.get("status") == "closed" for c in children)
    lines.append(f"- 결과: 하위 카드 {done}/{len(children)} 완료")
    lines.extend(f"- {'✅' if c.get('status') == 'closed' else '⏳'} {plain(c.get('title'), Format.title)}" for c in children)
    lines.append("- 지금: " + (plain(latest.get("text", "").strip().splitlines()[0]) if latest else "기록 없음"))
    lines.append("👉 사용자 할 일: " + ("없음" if not waiting else plain(waiting[0].get("title"))))
    lines.extend("- 답 기다림: " + plain(c.get("title")) for c in waiting[1:])
    dates = [stamp(c.get(k)) for c in [epic, *children] for k in ("updated_at", "created_at", "closed_at")]
    dates.extend(stamp(c.get("created_at")) for c in records)
    updated = max(dates)
    lines.append("- 최근 갱신: " + (updated.strftime("%Y-%m-%d %H:%M:%S UTC") if updated.year > 1 else "시각 미기재"))
    refs = [str(epic.get("id", ""))]
    texts = [c.get("text", "") for c in records]
    for pattern in (Format.pr, Format.docs):
        found = sorted({m.group(0) for text in texts for m in pattern.finditer(text)})
        refs.extend(found)
    refs.extend(Format.ids.findall("\n".join([str(name), *[c.get("title", "") for c in children], *texts])))
    lines.extend(["", "참고: " + html.escape(" · ".join(dict.fromkeys(refs)), quote=False)])
    return "\n".join(lines)


def summary(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class BdReader:
    def __init__(self, board=None, bd=None, timeout=Format.timeout):
        self.board = Path(board or os.environ.get("BOARD_DIR") or "~/orca/board").expanduser()
        self.bd = bd or os.environ.get("BOARD_BD") or shutil.which("bd") or "/opt/homebrew/bin/bd"
        self.timeout = timeout

    def query(self, *args):
        try:
            result = subprocess.run([self.bd, "-C", str(self.board), *args, "--json"],
                                    capture_output=True, text=True, timeout=self.timeout,
                                    stdin=subprocess.DEVNULL, check=True)
            rows = json.loads(result.stdout)
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ValueError("JSON 목록 아님")
            return rows
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            raise RuntimeError(f"작업판 {args[0]} 조회 실패({type(exc).__name__}) — bd·작업판 경로 확인") from exc

    def stages(self):
        # 작업판이 소유한 단계 이름 재사용. 복사본의 단계 변경 누락 방지.
        sys.path.insert(0, str(self.board / "lib"))
        try:
            flow = importlib.import_module("board_flow")
            return tuple((s.key, s.name) for s in flow.STAGES)
        finally:
            sys.path.pop(0)

    def text(self, epic_id):
        shown = self.query("show", epic_id)
        if not shown or shown[0].get("id") != epic_id:
            raise RuntimeError("기능 카드 없음 — 기능 카드 번호 확인")
        children = self.query("list", "--parent", epic_id, "--all", "--limit", "0")
        comments = []
        for card in [shown[0], *children]:
            comments.extend(dict(c, issue_id=card["id"]) for c in self.query("comments", card["id"]))
        return render(shown[0], children, comments, self.stages())


def main():
    parser = argparse.ArgumentParser(description="기능 현황판 읽기 전용 출력")
    parser.add_argument("epic")
    parser.add_argument("--board")
    parser.add_argument("--bd")
    args = parser.parse_args()
    try:
        print(BdReader(args.board, args.bd).text(args.epic))
    except (RuntimeError, ImportError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
