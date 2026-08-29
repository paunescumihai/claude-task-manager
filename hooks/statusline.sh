#!/bin/bash
# Claude Code statusline. Rendered on every turn, so everything here is live:
# host - user | cwd | model | email | rate limits | context | todo count | caveman | bypass
# The model name comes from the session payload on stdin, not from settings.json, so it shows the
# model actually answering (including /model overrides and subagent models), not the configured default.

stdin_json=$(cat)
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"

# Keep the last payload around; it is the only way to see what Claude Code actually sends.
printf '%s' "$stdin_json" > /tmp/claude-statusline-last.json 2>/dev/null

# --- one parse pass over the payload ---
IFS='|' read -r pct5h pct7d pctctx json_cwd json_proj json_sid model_name model_id cc_ver cost_usd lines_add lines_del <<< "$(python3 -c "
import json,sys
def out(*a): print(*a, sep='|')
try:
  d=json.load(sys.stdin)
  rl=d.get('rate_limits') or {}
  fh=(rl.get('five_hour') or {}).get('used_percentage')
  sd=(rl.get('seven_day') or {}).get('used_percentage')
  ctx=d.get('context') or {}
  used, maxt = ctx.get('tokens_used'), ctx.get('tokens_max')
  ws=d.get('workspace') or {}
  m=d.get('model') or {}
  if isinstance(m,str): m={'display_name':m,'id':m}
  c=d.get('cost') or {}
  usd=c.get('total_cost_usd')
  out(round(fh) if fh is not None else '',
      round(sd) if sd is not None else '',
      round(used/maxt*100) if used and maxt else '',
      ws.get('current_dir') or d.get('cwd') or '',
      ws.get('project_dir') or ws.get('current_dir') or d.get('cwd') or '',
      d.get('session_id') or '',
      m.get('display_name') or '',
      m.get('id') or '',
      d.get('version') or '',
      ('%.2f'%usd) if isinstance(usd,(int,float)) and usd>0 else '',
      c.get('total_lines_added') or '',
      c.get('total_lines_removed') or '')
except Exception:
  out(*(['']*12))
" <<< "$stdin_json" 2>/dev/null)"

# --- colors (plain escapes so this works in any terminal Claude Code renders into) ---
E=$'\033'
R="${E}[0m"; B="${E}[1m"; D="${E}[2m"
RED="${E}[31m"; GRN="${E}[32m"; YEL="${E}[33m"; BLU="${E}[34m"; MAG="${E}[35m"; CYN="${E}[36m"
SEP="${D}│${R}"

# green under 60%, yellow under 85%, red above — same scale for rate limits and context
heat() {
  local v="$1"
  if   [ "$v" -ge 85 ] 2>/dev/null; then printf '%s' "$RED"
  elif [ "$v" -ge 60 ] 2>/dev/null; then printf '%s' "$YEL"
  else printf '%s' "$GRN"; fi
}

# --- model: exact id, since "Opus" alone does not say which Opus ---
if [ -z "$model_name" ] && [ -z "$model_id" ]; then
  model_id="${CLAUDE_CODE_SUBAGENT_MODEL:-}"
  [ -z "$model_id" ] && model_id=$(python3 -c "
import json
try: print(json.load(open('$CLAUDE_DIR/settings.json')).get('model','sonnet'))
except Exception: print('sonnet')
" 2>/dev/null)
fi
model_part="${B}${MAG}${model_name:-$model_id}${R}"
[ -n "$model_id" ] && [ "$model_id" != "$model_name" ] && model_part="${model_part} ${D}${model_id}${R}"

# --- email (cached 1h; `claude auth status` is too slow to run per render) ---
EMAIL_CACHE="/tmp/claude-oauth-email"
if [ ! -f "$EMAIL_CACHE" ] || [ "$(find "$EMAIL_CACHE" -mmin +60 2>/dev/null)" ]; then
  claude auth status 2>/dev/null | python3 -c "
import json,sys
try: print(json.load(sys.stdin).get('email',''))
except Exception: pass
" > "$EMAIL_CACHE" 2>/dev/null
fi
email=$(cat "$EMAIL_CACHE" 2>/dev/null)

# --- rate limits + context ---
rate_part=""
[ -n "$pct5h" ] && rate_part="${D}5h${R} $(heat "$pct5h")${pct5h}%${R}"
[ -n "$pct7d" ] && rate_part="${rate_part:+$rate_part }${D}7d${R} $(heat "$pct7d")${pct7d}%${R}"
ctx_part=""
[ -n "$pctctx" ] && ctx_part="${D}ctx${R} $(heat "$pctctx")${pctctx}%${R}"

# --- session cost and churn, when Claude Code reports them ---
cost_part=""
[ -n "$cost_usd" ] && cost_part="${D}\$${R}${GRN}${cost_usd}${R}"
churn_part=""
if [ -n "$lines_add" ] || [ -n "$lines_del" ]; then
  [ -n "$lines_add" ] && [ "$lines_add" != "0" ] && churn_part="${GRN}+${lines_add}${R}"
  [ -n "$lines_del" ] && [ "$lines_del" != "0" ] && churn_part="${churn_part:+$churn_part }${RED}-${lines_del}${R}"
fi

# --- caveman flag ---
caveman=""
if [ -f "$CLAUDE_DIR/.caveman-active" ]; then
  cvm=$(cat "$CLAUDE_DIR/.caveman-active" 2>/dev/null)
  [ -n "$cvm" ] && [ "$cvm" != "off" ] && caveman="${MAG}CAVEMAN${R} ${D}${cvm}${R}"
fi

# --- bypass permissions: loud when on, quiet when off ---
bypass=""
bp=$(python3 -c "
import json
try:
  d=json.load(open('$CLAUDE_DIR/settings.json'))
  print('on' if d.get('bypassPermissionsModeEnabled') else 'off')
except Exception: print('')
" 2>/dev/null)
[ "$bp" = "on" ]  && bypass="${B}${RED}BYPASS${R}"
[ "$bp" = "off" ] && bypass="${D}bypass off${R}"

# --- pending task queue (persisted per project, shared by every console on it) ---
# The count rides on the main line; the list itself gets its own rows underneath, so the whole
# backlog is on screen permanently instead of living in the conversation.
# The session id matters too: queues under ~ are per session, since ~ is a catch-all, not a project.
qenv=(CLAUDE_PROJECT_DIR="${json_proj:-$PWD}" CLAUDE_CODE_SESSION_ID="${json_sid:-$CLAUDE_CODE_SESSION_ID}")
todo=$(env "${qenv[@]}" python3 "$CLAUDE_DIR/hooks/pending_tasks.py" line 2>/dev/null)
board=$(env "${qenv[@]}" python3 "$CLAUDE_DIR/hooks/pending_tasks.py" board 2>/dev/null)
[ -n "$todo" ] && todo="${YEL}${todo}${R}"

# --- path: the session's actual working directory (Claude reports it on stdin;
# CLAUDE_PROJECT_DIR/PWD stay at the project root and miss `cd` into subdirs) ---
cwd="${json_cwd:-${CLAUDE_PROJECT_DIR:-$PWD}}"
cwd="${cwd/#$HOME/~}"
host=$(hostname -s)
user=$(whoami)

parts=("${B}${CYN}${host}${R}${D}·${R}${CYN}${user}${R}" "${BLU}${cwd}${R}" "$model_part")
[ -n "$email" ]      && parts+=("${D}${email}${R}")
[ -n "$rate_part" ]  && parts+=("$rate_part")
[ -n "$ctx_part" ]   && parts+=("$ctx_part")
[ -n "$cost_part" ]  && parts+=("$cost_part")
[ -n "$churn_part" ] && parts+=("$churn_part")
[ -n "$todo" ]       && parts+=("$todo")
[ -n "$caveman" ]    && parts+=("$caveman")
[ -n "$bypass" ]     && parts+=("$bypass")
[ -n "$cc_ver" ]     && parts+=("${D}v${cc_ver}${R}")

line1=""
for p in "${parts[@]}"; do
  [ -z "$line1" ] && line1="$p" || line1="$line1 $SEP $p"
done
printf '%s' "$line1"
[ -n "$board" ] && printf '\n%s' "$board"

# The board line is optional; without this the final test is the script's exit code, so an empty
# queue made statusline.sh exit 1 and Claude Code hid the whole statusline.
exit 0
