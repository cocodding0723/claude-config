"""문서 모아보기 시험 공통: 임시 허브 폴더·가짜 문서. 실제 ~/.config 는 읽지도 쓰지도 않음."""
import json
import os
import sys
import tempfile
import warnings

warnings.simplefilter("ignore", ResourceWarning)  # 시험 코드의 단순 open 경고 억제
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "hubs", "scripts"))
CHECKER = os.path.join(ROOT, "skills", "doc-kit", "scripts", "check.py")

SVG3 = '<svg viewBox="0 0 10 10"><rect x="1" y="1" width="3" height="3"/><rect x="5" y="5" width="3" height="3"/><text x="1" y="9">a</text></svg>'
SVG1 = '<svg viewBox="0 0 10 10"><rect x="1" y="1" width="3" height="3"/></svg>'


def page(title, body=""):
    return f"<!doctype html><html><meta charset=utf-8><title>{title}</title><h1>{title}</h1>{body}</html>"


class Env:
    """허브 폴더·문서 폴더를 임시로 만들고 환경 변수를 맞춤. close() 로 되돌림"""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cc-docbr-")
        self.hub = os.path.join(self.tmp.name, "hub")
        self.docs = os.path.join(self.tmp.name, "Documents", "90_정리기록")
        self.ws = os.path.join(self.tmp.name, "orca", "workspaces", "repo", "coord-vfx-hub", "docs", "out")
        for d in (self.hub, self.docs, self.ws):
            os.makedirs(d)
        self._old = dict(os.environ)
        os.environ.update({
            "LRM_HUB_DIR": self.hub, "LRM_DOC_CHECK": CHECKER,
            "LRM_DOC_ROOTS": os.pathsep.join([self.docs + "/**/*.html", self.ws + "/**/*.html"]),
        })
        for m in [m for m in sys.modules if m in ("doc_index", "doc_send", "doc_browser", "doc_decide", "doc_confirm", "doc_approve", "confirm_items", "todo_items", "panel_collect", "answer_store", "answer_route", "card_actions", "main_terminal", "hub_dups", "ci_review", "todo_facts", "progress_collect")]:
            del sys.modules[m]

    def write(self, folder, name, html):
        p = os.path.join(folder, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(html)
        return p

    def jsonl(self, name, rows):
        with open(os.path.join(self.hub, name), "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def close(self):
        os.environ.clear()
        os.environ.update(self._old)
        self.tmp.cleanup()
