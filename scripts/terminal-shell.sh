#!/usr/bin/env bash
set -u

APP_URL="${DEV_TERMINAL_API_URL:-http://127.0.0.1:8000}"
QUEUE_TOKEN="${TERMINAL_QUEUE_TOKEN:-}"
PROJECTS_DIR="${PROJECTS_DIR:-$HOME/Projects}"

# Execute only locally defined tools. Never evaluate text received over HTTP.
_dev_terminal_run_tool() {
  local tool_id="$1" project_b64="${2:-}" project="" projects_root="" candidate="" resolved="" resolved_parent=""
  case "$tool_id" in
    pwd)
      printf '\n\033[1;36m[dev-terminal tool]\033[0m Print working directory\n'
      command pwd
      ;;
    list-files|git-status|git-log|git-diff|pytest|ruff|git-fetch|git-pull)
      [[ -n "$project_b64" ]] || { printf '[dev-terminal] Missing project; refusing tool.\n' >&2; return 1; }
      project="$(printf '%s' "$project_b64" | base64 --decode 2>/dev/null)" || return 1
      [[ -n "$project" && "$project" != "." && "$project" != ".." && "$project" != */* && "$project" != *\\* && "$project" != *$'\n'* ]] || {
        printf '[dev-terminal] Invalid project name; refusing tool.\n' >&2; return 1;
      }
      projects_root="$(realpath -e -- "$PROJECTS_DIR" 2>/dev/null)" || { printf '[dev-terminal] Projects directory unavailable.\n' >&2; return 1; }
      candidate="$projects_root/$project"
      [[ ! -L "$candidate" && -d "$candidate" && -d "$candidate/.git" ]] || {
        printf '[dev-terminal] Project must be a direct, non-symlink Git directory.\n' >&2; return 1;
      }
      resolved="$(realpath -e -- "$candidate" 2>/dev/null)" || return 1
      resolved_parent="$(dirname -- "$resolved")"
      [[ "$resolved_parent" == "$projects_root" ]] || { printf '[dev-terminal] Project escaped the projects directory.\n' >&2; return 1; }
      cd -- "$resolved" || return 1
      printf '\n\033[1;36m[dev-terminal tool]\033[0m %s in %s\n' "$tool_id" "$resolved"
      case "$tool_id" in
        list-files) command ls -la ;;
        git-status) command git status --short --branch ;;
        git-log) command git --no-pager log -10 --oneline --decorate ;;
        git-diff) command git --no-pager diff --stat ;;
        pytest) command pytest -q ;;
        ruff) command ruff check . ;;
        git-fetch) command git fetch --all --prune ;;
        git-pull) command git pull --ff-only ;;
      esac
      ;;
    *)
      printf '[dev-terminal] Unknown tool ID %q; refusing execution.\n' "$tool_id" >&2
      return 1
      ;;
  esac
}

_dev_terminal_poll_queue() {
  local payload encoded tool_id project_b64
  [[ -n "$QUEUE_TOKEN" ]] || return 0
  payload="$(curl --silent --show-error --max-time 1 -H "X-Terminal-Queue-Token: ${QUEUE_TOKEN}" "${APP_URL}/api/terminal/next-command" 2>/dev/null)" || return 0
  # Encode the project name so whitespace/newlines cannot alter field parsing.
  encoded="$(printf '%s' "$payload" | python3 -c 'import base64,json,sys; d=json.load(sys.stdin); t=d.get("tool"); print((t.get("tool_id", "") if isinstance(t,dict) else "") + "\t" + (base64.b64encode((t.get("project") or "").encode()).decode() if isinstance(t,dict) else ""))' 2>/dev/null)" || return 0
  IFS=$'\t' read -r tool_id project_b64 <<< "$encoded"
  [[ -n "${tool_id:-}" ]] || return 0
  _dev_terminal_run_tool "$tool_id" "${project_b64:-}"
  printf '\n\033[2m[dev-terminal tool finished]\033[0m\n'
}

export QUEUE_TOKEN APP_URL PROJECTS_DIR
export -f _dev_terminal_run_tool _dev_terminal_poll_queue
export PROMPT_COMMAND="_dev_terminal_poll_queue${PROMPT_COMMAND:+; $PROMPT_COMMAND}"
exec bash --noprofile --norc -i
