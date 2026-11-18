#!/bin/bash
# Magnm agy runner: hands queued task files to Antigravity CLI (agy), one at a time.
# Portable for Linux and macOS.
# Run manually: bash queue/agy/agy-runner.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${MAGNM_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
Q="$ROOT/queue/agy"
MAX_TASKS=3
TIMEOUT="45m"

# Attended mode: agy keeps normal permission prompts, web-reading runs sandboxed.
FLAGS_WEB=(--sandbox)
FLAGS_LOCAL=()

mkdir -p "$Q/inbox" "$Q/running" "$Q/done" "$Q/failed"
LOCK="$Q/.lock"

if ! mkdir "$LOCK" 2>/dev/null; then
  echo "Runner already active (lock exists at $LOCK)"
  exit 0
fi
trap 'rm -rf "$LOCK"' EXIT

AGY="$(command -v agy || true)"
if [[ -z "$AGY" ]]; then
  echo "$(date -u +%FT%TZ) agy not found on PATH" >> "$Q/runner.log"
  echo "Error: agy not found on PATH" >&2
  exit 1
fi

shopt -s nullglob
tasks=("$Q"/inbox/*.md)
shopt -u nullglob

if [[ ${#tasks[@]} -eq 0 ]]; then
  echo "No tasks pending in $Q/inbox."
  exit 0
fi

n=0
for task in "${tasks[@]}"; do
  (( n >= MAX_TASKS )) && break
  filename="$(basename "$task")"
  name="${filename%.md}"

  mv "$task" "$Q/running/$name.md"

  out=$(grep -m1 '^output:' "$Q/running/$name.md" | sed 's/^output:[[:space:]]*//; s/[[:space:]]*#.*$//' || true)
  ttype=$(grep -m1 '^type:' "$Q/running/$name.md" | sed 's/^type:[[:space:]]*//; s/[[:space:]]*$//' || true)

  if [[ "$ttype" == "render" ]]; then
    AGY_FLAGS=("${FLAGS_LOCAL[@]}")
  else
    AGY_FLAGS=("${FLAGS_WEB[@]}")
  fi

  prompt="You are working for Magnm's marketing team. Your working folder is $ROOT. Read the task file queue/agy/running/$name.md and do exactly what it says, acting as the role it names. Hard rules: stay inside $ROOT; never publish, post, comment, DM, log in anywhere or spend money; never invent facts, numbers, quotes or testimonials; cite a URL for every external fact; never show or describe LazyLayout features or visuals. Write the result to $ROOT/$out (create folders as needed), then append one line to state/activity.log: '$(date -u +%FT%TZ) | agy -> CMO | $name | $out'. Finish with a one-line summary."

  echo "$(date -u +%FT%TZ) start $name" >> "$Q/runner.log"

  rc=0
  ( cd "$ROOT" && "$AGY" "${AGY_FLAGS[@]}" --print-timeout "$TIMEOUT" -p "$prompt" ) 2>&1 | tee "$Q/running/$name.log" || rc=$?

  if [[ $rc -eq 0 && -n "$out" && -s "$ROOT/$out" ]]; then
    mv "$Q/running/$name.md" "$Q/running/$name.log" "$Q/done/"
    echo "$(date -u +%FT%TZ) done $name -> $out" >> "$Q/runner.log"
  else
    mv "$Q/running/$name.md" "$Q/running/$name.log" "$Q/failed/"
    echo "$(date -u +%FT%TZ) FAILED $name rc=$rc (no output at $out)" >> "$Q/runner.log"
  fi
  n=$((n + 1))
done
