#!/usr/bin/env bash
# Copies the hooks into ~/.claude and wires them into settings.json. Existing hooks are kept:
# the script adds its own entries and leaves everything else in the file untouched.
set -euo pipefail

CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SETTINGS="$CLAUDE_DIR/settings.json"

mkdir -p "$CLAUDE_DIR/hooks" "$CLAUDE_DIR/pending"
cp "$SRC/hooks/pending_tasks.py" "$SRC/hooks/test_pending_tasks.py" "$SRC/hooks/statusline.sh" "$CLAUDE_DIR/hooks/"
chmod +x "$CLAUDE_DIR/hooks/pending_tasks.py" "$CLAUDE_DIR/hooks/statusline.sh"

[ -f "$SETTINGS" ] || echo '{}' > "$SETTINGS"
cp "$SETTINGS" "$SETTINGS.bak-$(date +%Y%m%d%H%M%S)"

CLAUDE_DIR="$CLAUDE_DIR" SETTINGS="$SETTINGS" python3 - <<'PY'
import json, os

settings, claude_dir = os.environ["SETTINGS"], os.environ["CLAUDE_DIR"]
hook = "python3 \"%s/hooks/pending_tasks.py\"" % claude_dir
d = json.load(open(settings))

d.setdefault("statusLine", {"type": "command", "command": "bash %s/hooks/statusline.sh" % claude_dir})

hooks = d.setdefault("hooks", {})
for event, mode in (("UserPromptSubmit", "submit"), ("Stop", "stop")):
    cmd = "%s %s" % (hook, mode)
    groups = hooks.setdefault(event, [])
    if any(h.get("command") == cmd for g in groups for h in g.get("hooks", [])):
        continue
    groups.append({"hooks": [{"type": "command", "command": cmd, "timeout": 5}]})

json.dump(d, open(settings, "w"), indent=2)
print("wired into", settings)
PY

python3 "$CLAUDE_DIR/hooks/test_pending_tasks.py"
echo "Done. Restart Claude Code to pick up the hooks."
