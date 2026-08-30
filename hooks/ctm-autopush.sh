#!/bin/bash
# Mirror the live hooks into the claude-task-manager checkout and push, whenever they differ.
#
# Wired as a Stop hook: every turn that touched one of these files ends with the public repo
# already updated. It is a public repo, so nothing is pushed unless the tests pass and the tracked
# text is free of e-mail addresses and secrets -- a bad commit here is visible to everyone.
set -u
REPO="${CTM_REPO:-$HOME/claude-task-manager}"
HOOKS="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks"
[ -d "$REPO/.git" ] || exit 0

# One console at a time; several sessions can end a turn at the same moment.
exec 9>"${TMPDIR:-/tmp}/ctm-autopush.lock"
flock -n 9 || exit 0

FILES=(pending_tasks.py statusline.sh tasks_tui.py test_pending_tasks.py test_pending_next.py
       test_tasks_tui.py ctm-autopush.sh)
for f in "${FILES[@]}"; do
  [ -f "$HOOKS/$f" ] || continue
  cmp -s "$HOOKS/$f" "$REPO/hooks/$f" || cp "$HOOKS/$f" "$REPO/hooks/$f"
done

cd "$REPO" || exit 0
[ -n "$(git status --porcelain)" ] || exit 0

for t in hooks/test_*.py; do
  NO_COLOR=1 python3 "$t" >/dev/null 2>&1 || { echo "claude-task-manager: $t fails, not pushed"; exit 0; }
done

# Public repo: no address, no token, no key. The GitHub noreply author is the one allowed match.
leak=$(git grep -InIE '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' -- . \
       | grep -v 'users\.noreply\.github\.com' | head -3)
if [ -n "$leak" ]; then
  echo "claude-task-manager: e-mail address in tracked text, not pushed:"; echo "$leak"; exit 0
fi
home=$(git grep -Il -F "$HOME" -- . | head -3)
if [ -n "$home" ]; then
  echo "claude-task-manager: local path in tracked file, not pushed:"; echo "$home"; exit 0
fi
if command -v gitleaks >/dev/null && ! gitleaks detect --no-git --no-banner --redact >/dev/null 2>&1; then
  echo "claude-task-manager: gitleaks found a secret, not pushed"; exit 0
fi

changed=$(git status --porcelain | awk '{print $NF}' | xargs -n1 basename | paste -sd' ')
git add -A
git -c user.name=paunescumihai -c user.email=paunescumihai@users.noreply.github.com \
    commit -q -m "sync from the live hooks: $changed" || exit 0
git push -q origin HEAD 2>/dev/null && echo "claude-task-manager: pushed ($changed)"
