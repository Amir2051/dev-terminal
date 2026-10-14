#!/usr/bin/env bash
set -u
APP_URL="${DEV_TERMINAL_API_URL:-http://127.0.0.1:8000}"

_dev_terminal_poll_queue() {
  local payload command
  payload="$(curl --silent --show-error --max-time 1 "${APP_URL}/api/terminal/next-command" 2>/dev/null)" || return 0
  command="$(printf '%s' "$payload" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("command") or "")' 2>/dev/null)" || return 0
  if [[ -n "$command" ]]; then
    printf '\n\033[1;36m[dev-terminal tool]\033[0m %s\n' "$command"
    eval "$command"
    printf '\n\033[2m[dev-terminal tool finished]\033[0m\n'
  fi
}
export PROMPT_COMMAND="_dev_terminal_poll_queue${PROMPT_COMMAND:+; $PROMPT_COMMAND}"
exec bash --noprofile --norc -i
