#!/bin/zsh -l
# Magnm agy runner: hands queued task files to Antigravity CLI (agy), one at a time.
# Run by hand when you're at the Mac: zsh ~/Magnm-Marketing/queue/agy/agy-runner.sh
set -u
ROOT="${MAGNM_ROOT:-$HOME/Magnm-Marketing}"
Q="$ROOT/queue/agy"
MAX_TASKS=3
TIMEOUT="45m"
# Attended mode (2026-10-18): the CEO starts this runner by hand; agy keeps its normal
# permission prompts, so the CEO approves risky steps. Web-reading tasks also run sandboxed.
FLAGS_WEB=(--sandbox)
FLAGS_LOCAL=()

mkdir -p "$Q"/{inbox,running,done,failed}
LOCK="$Q/.lock"
if ! mkdir "$LOCK" 2>/dev/null; then echo "runner already active"; exit 0; fi
trap 'rmdir "$LOCK"' EXIT

AGY="$(command -v agy || true)"
if [[ -z "$AGY" ]]; then echo "$(date -u +%FT%TZ) agy not found on PATH" >> "$Q/runner.log"; exit 1; fi

n=0
for task in "$Q"/inbox/*.md(N); do
  (( n >= MAX_TASKS )) && break
  name="${task:t:r}"
  mv "$task" "$Q/running/$name.md"
  out=$(grep -m1 '^output:' "$Q/running/$name.md" | sed 's/^output:[[:space:]]*//; s/[[:space:]]*#.*$//')
  ttype=$(grep -m1 '^type:' "$Q/running/$name.md" | sed 's/^type:[[:space:]]*//; s/[[:space:]]*$//')
  if [[ "$ttype" == "render" ]]; then AGY_FLAGS=("${FLAGS_LOCAL[@]}"); else AGY_FLAGS=("${FLAGS_WEB[@]}"); fi
  prompt="You are working for Magnm's marketing team. Your working folder is $ROOT. Read the task file queue/agy/running/$name.md and do exactly what it says, acting as the role it names. Hard rules: stay inside $ROOT; never publish, post, comment, DM, log in anywhere or spend money; never invent facts, numbers, quotes or testimonials; cite a URL for every external fact; never show or describe LazyLayout features or visuals. Write the result to $ROOT/$out (create folders as needed), then append one line to state/activity.log: '<UTC timestamp> | agy -> CMO | $name | $out'. Finish with a one-line summary."
  echo "$(date -u +%FT%TZ) start $name" >> "$Q/runner.log"
  ( cd "$ROOT" && "$AGY" "${AGY_FLAGS[@]}" --print-timeout "$TIMEOUT" -p "$prompt" ) 2>&1 | tee "$Q/running/$name.log"
  rc=${pipestatus[1]}
  if [[ $rc -eq 0 && -n "$out" && -s "$ROOT/$out" ]]; then
    mv "$Q/running/$name.md" "$Q/running/$name.log" "$Q/done/"
    echo "$(date -u +%FT%TZ) done $name -> $out" >> "$Q/runner.log"
  else
    mv "$Q/running/$name.md" "$Q/running/$name.log" "$Q/failed/"
    echo "$(date -u +%FT%TZ) FAILED $name rc=$rc (no output at $out)" >> "$Q/runner.log"
  fi
  n=$((n+1))
done
