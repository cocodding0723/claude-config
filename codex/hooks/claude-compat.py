#!/usr/bin/env python3
"""Codex 훅 입력을 Claude 공통 훅 형식으로 변환."""
import copy
import json
from pathlib import Path
import re
import subprocess
import sys

SCRIPTS = {
    'PreToolUse': 'git-guard.sh',
    'PostToolUse': 'post-edit.sh',
    'UserPromptSubmit': 'prompt-context.sh',
    'Stop': 'scoped-test.sh',
    'SessionStart': 'session-start.sh',
    'SessionEnd': 'session-end.sh',
}
SOURCE = Path.home() / '.claude/hooks'


def invoke(event, payload):
    result = subprocess.run(
        ['bash', str(SOURCE / SCRIPTS[event])],
        input=json.dumps(payload), text=True, capture_output=True,
        timeout=145 if event == 'Stop' else (2 if event == 'SessionEnd' else 18),
    )
    if result.returncode:
        raise RuntimeError(f'{SCRIPTS[event]}: exit {result.returncode}; {result.stderr.strip()}')
    return json.loads(result.stdout) if result.stdout.strip() else {}


def main():
    payload = json.load(sys.stdin)
    event = payload.get('hook_event_name')
    if event not in SCRIPTS:
        return
    sid = payload.get('session_id', '')
    # Claude와 세션 상태 파일이 겹치지 않도록 별도 접두사 사용.
    payload['session_id'] = 'codex-' + re.sub(r'[^a-zA-Z0-9_-]', '_', sid) if sid else ''
    inputs = payload.get('tool_input') or {}
    if isinstance(inputs, str):
        inputs = {'command': inputs}
    inputs.setdefault('command', inputs.get('cmd', ''))
    payload['tool_input'] = inputs
    if event == 'PostToolUse':
        paths = [inputs['file_path']] if inputs.get('file_path') else re.findall(
            r'^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)$',
            inputs.get('command', ''), re.MULTILINE,
        )
        output = {}
        for path in dict.fromkeys(paths):
            item = copy.deepcopy(payload)
            item['tool_input']['file_path'] = str((Path(payload.get('cwd', '.')) / path).resolve())
            output = invoke(event, item) or output
    elif event == 'Stop':
        # 읽기·설정 작업만 한 세션에서 기존 dirty 코드의 테스트 실행 방지.
        touched = SOURCE / 'state/sessions' / (payload['session_id'] + '.files')
        if not touched.is_file():
            return
        output = invoke(event, payload)
    else:
        output = invoke(event, payload)
    specific = output.get('hookSpecificOutput', {})
    if event == 'PreToolUse' and specific.get('permissionDecision') == 'ask':
        # Codex는 ask를 오류로 처리한 뒤 명령을 계속하므로 deny로 변환.
        specific['permissionDecision'] = 'deny'
        specific['permissionDecisionReason'] += ' Codex 훅은 ask 미지원. 우회하지 말고 사용자 직접 실행을 요청.'
    if output:
        print(json.dumps(output, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f'Claude 호환 훅 실패: {error}', file=sys.stderr)
        sys.exit(2)
