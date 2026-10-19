#!/bin/bash
# One-call state digest for the daily CMO run (keeps reads cheap).
cd "$(dirname "$0")/.." || exit 1
today=$(TZ=Asia/Kolkata date +%F)
echo "=== DIGEST $today (IST $(TZ=Asia/Kolkata date +%H:%M)) ==="
echo "--- board"; sed -n '1,25p' state/board.md
echo "--- plan"; sed -n '1,30p' plan/plan.md
echo "--- last log"; last=$(ls -t logs/*.md 2>/dev/null | head -1); [ -n "$last" ] && { echo "$last"; sed -n '1,40p' "$last"; } || echo "(none)"
echo "--- agy done since last log"
for f in queue/agy/done/*.md; do [ -e "$f" ] || continue; if [ -z "$last" ] || [ "$f" -nt "$last" ]; then echo "$f -> $(grep -m1 '^output:' "$f" | sed 's/^output:[[:space:]]*//; s/[[:space:]]*#.*$//')"; fi; done
echo "--- agy failed"; ls queue/agy/failed/*.md 2>/dev/null || echo "(none)"
echo "--- agy inbox (pending)"; ls queue/agy/inbox/*.md 2>/dev/null || echo "(none)"
echo "--- runner"; tail -3 queue/agy/runner.log 2>/dev/null || echo "(runner never ran - agy not installed?)"
echo "--- awaiting yes"; grep -l "AWAITING YES" outputs/posts/*/READY.md 2>/dev/null || echo "(none)"
echo "--- palette log"; tail -3 state/palette-log.md 2>/dev/null
echo "--- activity (last 5)"; tail -5 state/activity.log
