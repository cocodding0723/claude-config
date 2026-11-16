#!/usr/bin/env bash
# AI 흔적 규칙 공용(ai-trace-guard 의 push 검사·git commit-msg 훅의 줄 삭제가 source). 값은 이 파일 한 곳에만 둠.
# 검사 기준과 삭제 기준이 어긋나면 삭제한 줄을 검사가 다시 잡거나 그 반대가 되므로 같은 출처에서 읽음.
AI_TRACE_NAMES='claude|anthropic|noreply@anthropic\.com|openai|codex'                       # 공동 작성자 줄에서 AI 로 보는 이름(대소문자 무시)
AI_TRACE_COAUTHOR="co-authored-by:[[:space:]]*.*(${AI_TRACE_NAMES})"
AI_TRACE_GENERATED='generated with[[:space:]]*(\[?claude|claude code)|claude\.com/claude-code|🤖'
AI_TRACE_RE="${AI_TRACE_COAUTHOR}|${AI_TRACE_GENERATED}"                                     # push 검사: 메시지 어디서든
AI_STRIP_RE="^[[:space:]]*(co-authored-by:.*(${AI_TRACE_NAMES})|🤖[[:space:]]*generated with|generated with[[:space:]]*\[?claude)"   # 커밋 때 줄 삭제: 줄 시작 기준
