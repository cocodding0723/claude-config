#!/usr/bin/env python3
"""PR 적대적 리뷰 1회 실행. ci_watch.py 가 새 head 를 처음 볼 때 백그라운드로 띄움.

흐름: gh pr diff → 파일 단위 덩어리 → 덩어리마다 1차 리뷰 → 지적 전체를 2차 검증 → 확인된 것만 남김
     → PR-N-커밋-adv.md + 메인 터미널 전달 + Slack(지적 있으면 warn, 없으면 done). PR 에는 쓰지 않음(허브가 답글과 함께 처리).
모델: ADV_MODEL(기본 Fable 5.2), 실패·한도 초과 시 ADV_FALLBACK(기본 Opus 5.5). 읽기 도구만 허용.
비용 상한: ADV_DAILY_MAX(하루 실행), ADV_MAX_CHUNKS(덩어리 수 상한, 넘으면 건너뜀). 하루 상한 초과는 그날 Slack 1회.
트리거: PR 이 ready 가 될 때 1회(ci_watch, LRM_ADV_SCOPE=bundle 이면 묶음 PR 만) + 수동 요청(이 스크립트 직접 실행).
사용: adv_review.py --pr N --head SHA [--dry-run]   (--dry-run: 전송·상한 기록 없이 결과 파일 경로와 지적만 출력)
설정: ~/.config/lrm-hubs/adv-review.env (KEY=값 줄)
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci_review as review  # noqa: E402

DIR = Path(os.environ.get("LRM_HUB_DIR", Path.home() / ".config/lrm-hubs"))
REPO = os.environ.get("LRM_CI_REPO", "Basakansoft/LoveRevolutionMaker")
GH = os.environ.get("LRM_GH", "gh")
CLAUDE = os.environ.get("LRM_CLAUDE", "claude")
NOTIFY = os.environ.get("LRM_NOTIFY", str(DIR / "slack_notify.sh"))
ORCA = os.environ.get("LRM_ORCA", "orca")
MAIN_ENV = os.environ.get("LRM_MAIN_ENV", str(DIR / "main.env"))
STATE = DIR / "adv-review.state.json"
LOCK = DIR / "adv-review.lock"
OUT = DIR / "ci-review"
DEFAULTS = {"ADV_MODEL": "claude-fable-5-2", "ADV_FALLBACK": "claude-opus-5-5", "ADV_DAILY_MAX": "6",
            "ADV_MAX_CHUNKS": "30", "ADV_CHUNK_LINES": "1500", "ADV_TIMEOUT": "1200"}
FILE_SPLIT = re.compile(r"(?=^diff --git )", re.M)

REVIEW_ASK = """너는 이 PR 을 깨뜨리려는 적대적 리뷰어다. 아래 diff 조각에서 실제로 잘못 동작할 결함만 찾는다.
대상: 정확성 버그, 경계값, 널·예외, 동시성·수명, 데이터 손실, 보안, 테스트가 못 잡는 회귀. 취향·스타일·이름은 제외.
각 지적에 재현 조건(어떤 입력·상태에서 무엇이 틀리나)을 적는다. 확신 없는 추측은 쓰지 않는다.
출력은 JSON 하나만: {"findings":[{"file":"","line":0,"severity":"high|medium|low","title":"40자 이내","scenario":"재현 조건과 잘못된 결과","fix":"고칠 방법 한 줄"}]}
"""
VERIFY_ASK = """아래 지적 목록을 diff 와 대조해 하나씩 검증한다. 코드가 실제로 그 시나리오에서 틀리면 CONFIRMED, 아니면 REJECTED.
다른 곳에서 이미 막고 있거나, 가정이 틀렸거나, 취향 문제면 REJECTED. 출력은 JSON 하나만: {"verdicts":[{"index":0,"verdict":"CONFIRMED|REJECTED","reason":"한 줄"}]}
"""


def config():
    cfg = dict(DEFAULTS)
    try:
        for ln in (DIR / "adv-review.env").read_text(encoding="utf-8").splitlines():
            if "=" in ln and not ln.lstrip().startswith("#"):
                k, v = ln.split("=", 1)
                if k.strip() in DEFAULTS:
                    cfg[k.strip()] = v.strip()
    except OSError:
        pass
    return cfg


def gh(*args):
    p = subprocess.run([GH, *args], capture_output=True, text=True, timeout=180)
    if p.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])} 실패: {p.stderr.strip()[:200]}")
    return p.stdout


def chunks(diff, limit):
    """파일 단위로 묶어 덩어리당 limit 줄 이하. 한 파일이 limit 보다 크면 그 파일 하나가 한 덩어리"""
    out, cur, n = [], [], 0
    for part in filter(None, FILE_SPLIT.split(diff)):
        size = part.count("\n")
        if cur and n + size > limit:
            out.append("".join(cur))
            cur, n = [], 0
        cur.append(part)
        n += size
    if cur:
        out.append("".join(cur))
    return out


def ask(cfg, prompt, body):
    """(JSON dict, 쓴 모델). 기본 모델 실패 시 대체 모델. 둘 다 실패하면 RuntimeError"""
    err = ""
    for model in (cfg["ADV_MODEL"], cfg["ADV_FALLBACK"]):
        try:
            p = subprocess.run([CLAUDE, "-p", "--model", model, "--output-format", "text", "--allowedTools", "Read,Grep,Glob",
                                "--disallowedTools", "Bash,Edit,Write,NotebookEdit"], input=prompt + "\n\n" + body,
                               capture_output=True, text=True, timeout=int(cfg["ADV_TIMEOUT"]))
        except subprocess.TimeoutExpired:
            err = f"{model} 시간 초과"
            continue
        m = re.search(r"\{.*\}", p.stdout, re.S)
        if p.returncode == 0 and m:
            try:
                return json.loads(m.group(0)), model
            except ValueError:
                err = f"{model} JSON 깨짐"
                continue
        err = f"{model} rc={p.returncode} {p.stderr.strip()[:150]}"
    raise RuntimeError(err)


def load_state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def budget(cfg, dry):
    """오늘 실행 가능하면 True. 넘치면 그날 1회 Slack 후 False"""
    st, day = load_state(), time.strftime("%Y%m%d")
    if st.get("day") != day:
        st = {"day": day, "count": 0, "warned": False}
    if st["count"] >= int(cfg["ADV_DAILY_MAX"]):
        if not st["warned"] and not dry:
            slack("warn", f"적대적 리뷰 하루 상한 도달\n\n• 무엇: 오늘 {st['count']}회 실행함\n• 왜: 비용 상한 ADV_DAILY_MAX={cfg['ADV_DAILY_MAX']}\n• 결과: 오늘 남은 PR 은 건너뜀\n\n👉 답: 늘리려면 adv-review.env 수정")
            st["warned"] = True
            STATE.write_text(json.dumps(st))
        return False
    if not dry:
        st["count"] += 1
        STATE.write_text(json.dumps(st))
    return True


def slack(kind, msg):
    subprocess.run([NOTIFY, "--kind", kind, "--tag", "adv-review", msg], capture_output=True)


def write(pr, hub, head, found, model):
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"PR-{pr['number']}-{head[:12]}-adv.md"
    lines = [f"# PR #{pr['number']} 적대적 리뷰 확인 지적 {len(found)}건", "", f"- 제목: {pr.get('title', '')}",
             f"- 브랜치: {pr.get('headRefName', '')}", f"- 담당 허브: {hub}", f"- 커밋: {head}", f"- 리뷰 모델: {model}", "", "## 지적"]
    for i, f in enumerate(found, 1):
        lines += [f"{i}. [{f.get('severity', '?')}] {f.get('title', '')} — {f.get('file', '')}:{f.get('line', '')}",
                  f"   - 재현: {f.get('scenario', '')}", f"   - 고칠 방법: {f.get('fix', '')}", f"   - 검증: {f.get('reason', '')}"]
    lines += ["", "## 처리 규칙", "- 지적마다 수정 커밋 + 일반 push, PR 에 답글(수정 = 커밋 해시, 반박 = 근거). 봇은 PR 에 쓰지 않음", "- 강제 push·dev 병합 금지"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def run(n, head, dry):
    cfg = config()
    pr = json.loads(gh("pr", "view", str(n), "-R", REPO, "--json", "number,title,headRefName,body"))
    diff = gh("pr", "diff", str(n), "-R", REPO)
    parts = chunks(diff, int(cfg["ADV_CHUNK_LINES"]))
    if len(parts) > int(cfg["ADV_MAX_CHUNKS"]):
        if not dry:
            slack("warn", f"적대적 리뷰 건너뜀 PR #{n}\n\n• 무엇: 덩어리 {len(parts)}개\n• 왜: 상한 ADV_MAX_CHUNKS={cfg['ADV_MAX_CHUNKS']}\n• 결과: 이 PR 은 리뷰 안 함\n\n👉 답: 묶음을 나누거나 adv-review.env 상한을 올린 뒤 adv_review.py --pr {n} 수동 실행")
        return None
    if not budget(cfg, dry):
        return None
    ctx = f"PR 제목: {pr.get('title', '')}\nPR 본문:\n{(pr.get('body') or '')[:3000]}\n\ndiff:\n"
    raw, model = [], ""
    for part in parts:
        res, model = ask(cfg, REVIEW_ASK, ctx + part)
        raw += res.get("findings", [])
    found = []
    if raw:
        res, _ = ask(cfg, VERIFY_ASK, "지적:\n" + json.dumps(list(enumerate(raw)), ensure_ascii=False) + "\n\ndiff:\n" + diff)
        for v in res.get("verdicts", []):
            i = v.get("index")
            if v.get("verdict") == "CONFIRMED" and isinstance(i, int) and 0 <= i < len(raw):
                found.append({**raw[i], "reason": v.get("reason", "")})
    hub = review.hub_for(pr.get("headRefName", ""), pr.get("title", ""), review.load_table(DIR / "ci-review-hubs.tsv"))
    path = write(pr, hub, head, found, model)
    sev = {s: sum(1 for f in found if f.get("severity") == s) for s in ("high", "medium", "low")}
    if dry:
        print(json.dumps({"path": str(path), "raw": len(raw), "confirmed": found, "model": model}, ensure_ascii=False, indent=1))
        return path
    if found:
        handle = review.main_handle(MAIN_ENV)
        text = f"[CI→메인] 적대적 리뷰 지적 {len(found)}건(high {sev['high']}·medium {sev['medium']}) PR#{n} · 담당 허브 {hub} · 파일 {path}"
        if handle:
            review.send_main(ORCA, handle, text)
        slack("warn", f"PR #{n} 적대적 리뷰 지적 {len(found)}건\n\n• 무엇: high {sev['high']} · medium {sev['medium']} · low {sev['low']}\n• 왜: 검증을 통과한 지적만 남김\n• 결과: 메인이 담당 허브({hub})에 배정\n\n👉 답: 없음(허브가 처리)\n\n참고: {path}")
    else:
        slack("done", f"PR #{n} 적대적 리뷰 지적 없음\n\n참고: 1차 후보 {len(raw)}건 모두 검증에서 제외")
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--head", default="")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.mkdir(LOCK)  # 동시 1개. 남은 잠금은 2시간 뒤 무시
    except FileExistsError:
        if time.time() - LOCK.stat().st_mtime < 7200:
            print("다른 적대적 리뷰 실행 중 — 건너뜀", file=sys.stderr)
            return 3
    try:
        run(a.pr, a.head or "unknown", a.dry_run)
    except (RuntimeError, ValueError, subprocess.TimeoutExpired) as err:
        print(f"적대적 리뷰 실패: {err}", file=sys.stderr)
        if not a.dry_run:
            slack("warn", f"적대적 리뷰 실패 PR #{a.pr}\n\n• 무엇: {str(err)[:150]}\n• 왜: 모델 호출 또는 gh 실패\n• 결과: 이 커밋은 리뷰 안 됨\n\n👉 답: adv_review.py --pr {a.pr} 로 다시 실행")
        return 1
    finally:
        try:
            LOCK.rmdir()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
