"""문서 보내기: 받는 사람 설정·메시지 초안·전송·보낸 기록. doc_browser.py 의 요청 처리기만 불러 씀.

사용자가 페이지에서 누른 요청만 전송하도록 이 모듈에는 명령줄 진입점·자동 호출 경로를 두지 않음(시험이 import 처를 검사).
받는 사람: $LRM_HUB_DIR/doc-recipients.json {"recipients": [{"name": "이름", "slack_id": "U..."}]}. 소스·저장소에 ID 를 쓰지 않음.
전송: slack_notify.sh --to-user 를 인자 목록으로 호출(셸 해석 없음). 첨부 경로 검사·비밀 파일 차단은 알림 스크립트와 hub_report 가 함.
보낸 기록: $LRM_HUB_DIR/doc-sent.jsonl, 성공했을 때만 한 줄 추가.
"""
import json
import os
import re
import subprocess
import threading
import time

import hub_report

HUB_DIR = os.environ.get("LRM_HUB_DIR") or os.path.expanduser("~/.config/lrm-hubs")
RECIPIENTS = os.path.join(HUB_DIR, "doc-recipients.json")
SENT_LOG = os.path.join(HUB_DIR, "doc-sent.jsonl")
NOTIFY = os.environ.get("LRM_DOC_NOTIFY") or os.path.join(HUB_DIR, "slack_notify.sh")
SEND_TIMEOUT_S = 90
DUP_WINDOW_S = 30            # 같은 문서·사람·글을 이 시간 안에 다시 보내면 거부(더블클릭 방지)
MSG_MAX = 3000
SLACK_ID_RE = re.compile(r"^[UW][A-Z0-9]{6,20}$")
SUMMARY_RE = re.compile(r'data-check="summary"[^>]*>(.*?)</div>', re.S)
SOURCE_TAG = "doc-send"
SUMMARY_LINES = 3

_lock = threading.Lock()
_recent: dict[tuple, float] = {}


class SendError(Exception):
    """사용자에게 그대로 보이는 원인·조치 문구"""


def load_recipients() -> list[dict]:
    """[{name, slack_id}]. 파일 없음·깨짐은 빈 목록. 형식이 틀린 ID 는 ready=False 로 표시"""
    try:
        with open(RECIPIENTS, encoding="utf-8") as f:
            rows = json.load(f).get("recipients", [])
    except (OSError, ValueError, AttributeError):
        return []
    out = []
    for r in rows if isinstance(rows, list) else []:
        if isinstance(r, dict) and isinstance(r.get("name"), str) and r["name"].strip():
            sid = r.get("slack_id") if isinstance(r.get("slack_id"), str) else ""
            out.append({"name": r["name"].strip(), "slack_id": sid, "ready": bool(SLACK_ID_RE.match(sid))})
    return out


def public_recipients() -> list[dict]:
    """화면에 줄 목록. Slack ID 는 내보내지 않음"""
    return [{"name": r["name"], "ready": r["ready"]} for r in load_recipients()]


def draft(path: str, title: str) -> dict:
    """제목 + 문서 3줄 요약에서 뽑은 핵심 3줄. 요약 박스가 없으면 줄을 비워 직접 쓰게 함"""
    lines = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            m = SUMMARY_RE.search(f.read(8 << 20))
        if m:
            text = re.sub(r"<br\s*/?>", "\n", m.group(1), flags=re.I)
            text = re.sub(r"<[^>]+>", "", text)
            lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
            lines = [ln for ln in lines if ln and ln != "3줄 요약"][:SUMMARY_LINES]
    except OSError:
        pass
    body = "\n".join(lines) if lines else "(핵심 3줄을 직접 적어 주세요)"
    return {"subject": title, "message": f"{title}\n\n{body}"}


def sent_by_path() -> dict[str, list[dict]]:
    """문서 경로 → 보낸 기록 [{at, to}] 최신순"""
    out: dict[str, list[dict]] = {}
    try:
        with open(SENT_LOG, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict) and r.get("path"):
                    out.setdefault(r["path"], []).append({"at": r.get("at"), "to": r.get("to")})
    except OSError:
        pass
    for v in out.values():
        v.sort(key=lambda x: x.get("at") or 0, reverse=True)
    return out


def send(path: str, title: str, recipient: str, message: str) -> dict:
    """문서를 받는 사람 DM 에 첨부 전송하고 기록. 실패는 SendError(원인·조치)"""
    message = message.strip()
    if not message:
        raise SendError("메시지가 비어 있음 — 메시지를 적은 뒤 다시 보내세요")
    if len(message) > MSG_MAX:
        raise SendError(f"메시지가 {MSG_MAX}자를 넘음 — 줄여서 다시 보내세요")
    rec = next((r for r in load_recipients() if r["name"] == recipient), None)
    if not rec:
        raise SendError("받는 사람이 설정 파일에 없음 — doc-recipients.json 에 이름과 Slack ID 를 추가하세요")
    if not rec["ready"]:
        raise SendError("받는 사람의 Slack ID 가 비었거나 형식이 틀림 — doc-recipients.json 의 slack_id 를 채우세요")
    if why := hub_report.secret_file_problem(path):
        raise SendError(f"비밀 파일은 보내지 않음 — {why}")
    if not os.path.isfile(NOTIFY):
        raise SendError(f"알림 스크립트 없음: {NOTIFY} — install.sh 로 설치 상태를 확인하세요")
    key = (path, recipient, message)
    with _lock:
        now = time.time()
        for k in [k for k, t in _recent.items() if now - t > DUP_WINDOW_S]:
            del _recent[k]
        if key in _recent:
            raise SendError("방금 같은 글을 보냄 — 잠시 뒤 다시 확인하세요")
        _recent[key] = now
    try:
        p = subprocess.run(["/bin/bash", NOTIFY, "--to-user", rec["slack_id"], "--tag", SOURCE_TAG, "--title", title[:200], "--", message, path],
                           capture_output=True, text=True, timeout=SEND_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as e:
        _recent.pop(key, None)
        raise SendError(f"전송 실패: {type(e).__name__} — 네트워크를 확인하고 다시 보내세요")
    if p.returncode != 0:
        _recent.pop(key, None)
        why = (p.stderr or p.stdout or "").strip().splitlines()
        raise SendError("전송 실패: " + (why[-1][:300] if why else f"종료 코드 {p.returncode}") + " — 보낸 기록은 남기지 않음")
    row = {"at": int(time.time()), "path": path, "title": title[:200], "to": rec["name"], "message": message}
    with _lock, open(SENT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {"at": row["at"], "to": rec["name"]}
