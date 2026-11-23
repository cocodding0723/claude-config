#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd -P)
python3 - "$ROOT" <<'PY'
from pathlib import Path
import re
import subprocess
import sys
import tempfile

root = Path(sys.argv[1])
role_path = root / "hubs/roles/role-feature.txt"
common_path = root / "skills/orca-hub-create/references/common-rules.txt.tmpl"
role, common = (p.read_text(encoding="utf-8") for p in (role_path, common_path))


def require(condition, label):
    if not condition:
        raise SystemExit("FAIL  " + label)
    print("PASS  " + label)


require(len(role) + len(common) <= 7000,
        f"두 역할 합계 {len(role) + len(common)}자 / 상한 7000자")
for text, phrases in (
    (common, (
        "① 계획 컨펌 1회 ② 최종 결과 검수 ③ dev/main 병합·라이브·강제 push",
        "decisions.tsv", "구현자와 다른 모델 검수", "board-claim <카드> <이름>",
        "board-merge <카드> <작업자>", "git merge·bd close 직접 금지",
        "board-merge-check", "AI 흔적 금지", "Co-Authored-By·Generated with",
        "source.slack_ts", "7일 만료", "메인만 작성", "완료·보류 전환은 건별 승인",
        "사용자 말을 그대로", "직접 acquire/release 금지", "하위 공간 10개 이하",
    )),
    (role, (
        "직접 지시(이 창에 사용자가 친 글)는 계획·검수 모델 지적·티켓 기준보다 우선",
        "board-said {{EPIC}}", "--sync", "--mark <번호>", "확인 못 함",
        "large는 사용자 이해 확인", "--user-ok-typed", "--user-ok", "--shipped",
        "--ask-review", "--review-note", "검수 카드(1=통과, 2=고칠 점 있음)는 완료 검사가 올린다",
        "검수 통과만으로 종료하지 않는다", "board-skip {{EPIC}}", "준비(이해·계획)만",
        "--no-capture", "기존 작업자·공간", "--card {{EPIC}} --ws", "--top",
        "main_inbox.py read --since-offset --box feat-{{SLUG}}",
    )),
):
    for phrase in phrases:
        require(phrase in text, "핵심 규칙: " + phrase)

require("카드 기록만 남기면 전송기가 옮김, 결정 카드·문서 첨부만" in role,
        "기능 카드 기록 자동 전송·결정/문서 전용 보고")
require("전송기가 옮김" not in common
        and "진행 보고는 묻지 않고 HTML 로 보내기만" in common
        and "도식·캡처·결정은 Slack 봇(hub_report.py)으로 직접" in common,
        "일반 허브의 HTML·Slack 직접 보고 유지")
require("허브 작업 공간에는 문서·설정만 있고 게임 코드가 없다" in common,
        "공통 sparse 작업 공간 규칙 유지")
require("계획 카드를 다시 올리면 기존 승인이 무효" in role,
        "계획 카드 재발행 승인 무효 경고")
sys.path.insert(0, str(root / "hubs/scripts"))
sys.dont_write_bytecode = True
import decide_card

example = re.search(r"--text (\$'[^']*')", role)[1]
body = subprocess.check_output(["bash", "-c", "printf '%s' " + example], text=True)
require(not decide_card.problem(body, "1=승인 => 계획대로 구현,2=수정 요청 => 계획 수정"),
        "계획 승인 예시의 배경·용어·선택지 검사 통과")
require("board-stage" not in common and "0 접수" not in common,
        "기능 전용 단계는 역할 파일에 한정")
require(set(re.findall(r"\{\{(\w+)\}\}", role)) ==
        {"NAME", "TICKET", "BRANCH", "BASE", "EPIC", "SLUG"}, "기능 자리표시자 보존")
require(set(re.findall(r"__(\w+)__", common)) ==
        {"PERSON_SHORT", "OWNER_NAME", "DM_CHANNEL"}, "공통 자리표시자 보존")


def function(path, name):
    text = path.read_text(encoding="utf-8")
    return re.search(r"^" + name + r"\(\).*?^\}", text, re.M | re.S)[0]


render = function(root / "hubs/scripts/feature_open.sh", "render_role")
for ticket in ("TASK-1", ""):
    rendered = subprocess.check_output([
        "bash", "-c", render + '\nNAME=시험; TICKET=$2; BRANCH=feat/test; '
        'BASE_REF=dev; EPIC=lrm-test; SLUG=test; P_KEY=test; CFG_REPO=test; '
        'CFG_ROLE_TMPL=$1; render_role', "roles-test", str(role_path), ticket,
    ], text=True)
    require("{{" not in rendered and "기능 통합 브랜치: feat/test (기준 dev)" in rendered,
            "실제 기능 렌더: " + (ticket or "티켓 없음"))

subst = function(root / "install.sh", "subst_file")
with tempfile.TemporaryDirectory() as temp:
    temp = Path(temp)
    values = temp / "values.txt"
    values.write_text("PERSON_SHORT=시험\nOWNER_NAME=사용자\nDM_CHANNEL=DM_TEST\n",
                      encoding="utf-8")
    output = temp / "common.txt"
    subprocess.run(["bash", "-c", subst + '\nVALUES=$2; subst_file "$1" "$3" values',
                    "roles-test", str(common_path), str(values), str(output)], check=True)
    rendered = output.read_text(encoding="utf-8")
    require("__" not in rendered and "사용자 DM(DM_TEST)" in rendered,
            "실제 공통 렌더: 개인 문서 발송 자리표시자 치환")
    require(rendered.endswith("\n"), "공통 렌더 끝 개행 보존")
PY
