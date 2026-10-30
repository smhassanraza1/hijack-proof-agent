#!/bin/bash
# One eval round with a fixed configuration: agent pass (parallel workers), judge pass,
# and summary.
#
#   AGENT_BASE_URLS=https://a.trycloudflare.com/v1,https://b.../v1 JUDGE_BASE_URL=https://j.../v1 \
#     evals/round.sh <variant> [split=all] [attack=important_instructions]
#
# AGENT=<baseline|v1|v2|v3|v4> picks the agent code (default: the variant name's prefix, e.g. v4-injecagent);
# DEFENSE=<name> runs one of AgentDojo's built-in defences (evals/defenses.py) instead;
# REPS=<n> runs every case n times; ATTACKED_ONLY=1 skips benign cases; SHARDS=<n> sets the worker count;
# SYSTEM_PROMPT=<file> swaps the system prompt (judge calibration runs only).
# Registered evaluation: FLOW=results/registered CASES=evals/cases_registered.jsonl (split may be val); SUITES=<a,b> keeps those suites;
# BENIGN_ONLY=1 skips attacked cases; WHERE=<field> keeps cases whose field is true (WHERE=tune: attacker tuning);
# ADAPTIVE=<K> SEED_VARIANT=<variant> runs the best-of-K adaptive attacker (evals/adaptive.py) with the
# judge server as the attacker (ATTACKER_PROMPT=<file> picks its prompt, default evals/attacker_prompt.md); SKIP_JUDGE=1 skips the judge pass. Outside results/, the judge cuts the
# defence's notice from the reply (evals/judge.py --strip-notice) and the development summary is not printed.
#
# With the URLs set (printed by kaggle/serve.ipynb), the models run on Kaggle's T4s under
# Ollama, two workers per agent server. Without them, both run here on MLX servers, one at a time. Every variant in one
# comparison must use the same backend, models, server settings and worker count, recorded in
# <variant>/env.json: batched serving changes trajectories slightly, so these must not vary.
set -euo pipefail
cd "$(dirname "$0")/.."

VARIANT=$1
SPLIT=${2:-all}
ATTACK=${3:-important_instructions}
FLOW=${FLOW:-results}
CASES=${CASES:-evals/cases.jsonl}
mkdir -p logs "$FLOW/$VARIANT"

if [ -n "${AGENT_BASE_URLS:-}" ]; then
  : "${JUDGE_BASE_URL:?set JUDGE_BASE_URL too (printed by kaggle/serve.ipynb)}"
  BACKEND=kaggle-ollama
  AGENT_MODEL=qwen3.5:4b
  JUDGE_MODEL=gemma4:12b-it-qat
  CONTEXT=32768
  IFS=, read -r -a AGENT_URLS <<< "$AGENT_BASE_URLS"
  SHARDS=${SHARDS:-$((2 * ${#AGENT_URLS[@]}))}
  SERVER_SETTINGS="ollama (kaggle/serve.ipynb): ${#AGENT_URLS[@]} agent servers + judge on 2xT4, OLLAMA_CONTEXT_LENGTH=$CONTEXT, OLLAMA_NUM_PARALLEL=1"
else
  BACKEND=local-mlx
  AGENT_MODEL=mlx-community/Qwen3.5-4B-MLX-4bit
  JUDGE_MODEL=mlx-community/gemma-4-12B-it-qat-4bit
  CONTEXT=262144
  SHARDS=${SHARDS:-4}
  AGENT_FLAGS=(--port 8000 --max-tokens 1024 --decode-concurrency 4 --prompt-concurrency 4 --prompt-cache-size 8
               --chat-template-args '{"enable_thinking": false}')
  JUDGE_FLAGS=(--port 8001 --max-tokens 300 --prefill-step-size 256 --prompt-cache-size 1
               --chat-template-args '{"enable_thinking": false}')
  SERVER_SETTINGS="mlx_lm.server agent: ${AGENT_FLAGS[*]} | judge: ${JUDGE_FLAGS[*]}"
  AGENT_URLS=(http://localhost:8000/v1)
  JUDGE_BASE_URL=http://localhost:8001/v1
fi

serve_mlx() {  # serve_mlx <model> <port> <log> <flags...>
  local model=$1 port=$2 log=$3; shift 3
  pkill -f "mlx_lm.server" || true; sleep 2
  .venv/bin/mlx_lm.server --model "$model" "$@" > "$log" 2>&1 &
  SERVER_PID=$!
  for _ in $(seq 1 120); do curl -sf "localhost:$port/v1/models" > /dev/null && return; sleep 2; done
  echo "server for $model did not start" >&2; exit 1
}

python3 - "$FLOW/$VARIANT/env.json" "$BACKEND" "$AGENT_MODEL" "$JUDGE_MODEL" "$CONTEXT" "$SHARDS" "$ATTACK" "$SERVER_SETTINGS" "${DEFENSE:-}" "${SYSTEM_PROMPT:-}" "${AGENT:-}" "${REPS:-1}" "${ATTACKED_ONLY:-}" "$FLOW" "$CASES" "$SPLIT" "${SUITES:-}" "${ADAPTIVE:-}" "${SEED_VARIANT:-}" "${SKIP_JUDGE:-}" "${ATTACKER_PROMPT:-evals/attacker_prompt.md}" "${BENIGN_ONLY:-}" "${WHERE:-}" <<'EOF'
import hashlib, json, subprocess, sys
(path, backend, agent_model, judge, context, shards, attack, settings, defense, system_prompt, agent, reps, attacked_only,
 flow, cases, split, suites, adaptive, seed_variant, skip_judge, attacker_prompt, benign_only, where) = sys.argv[1:]
sha = lambda f: hashlib.sha256(open(f, "rb").read()).hexdigest()
git = lambda *a: subprocess.run(["git", *a], capture_output=True, text=True).stdout.strip()
env = {"backend": backend, "agent_model": agent_model, "judge_model": judge, "context_tokens": int(context),
       "shards": int(shards), "attack": attack, "defense": defense or None, "agent": agent or None,
       "reps": int(reps), "attacked_only": bool(attacked_only),
       "flow": flow, "cases": cases, "cases_sha256": sha(cases), "split": split, "suites": suites or None,
       "adaptive": int(adaptive) if adaptive else None, "seed_variant": seed_variant or None,
       "attacker_prompt": attacker_prompt if adaptive else None,
       "attacker_prompt_sha256": sha(attacker_prompt) if adaptive else None,
       "skip_judge": bool(skip_judge), "benign_only": bool(benign_only), "where": where or None,
       "system_prompt_override": system_prompt or None, "server_settings": settings,
       "git_commit": git("rev-parse", "HEAD"),
       "git_dirty": bool(git("status", "--porcelain", "--", ".", ":(exclude)results", ":(exclude)logs")),
       "lock_file": "uv.lock", "lock_sha": hashlib.sha256(open("uv.lock", "rb").read()).hexdigest()[:16]}
json.dump(env, open(path, "w"), indent=2)
EOF

if command -v caffeinate > /dev/null; then caffeinate -i -w $$ & fi  # keep a Mac awake while running

[ "$BACKEND" = local-mlx ] && serve_mlx "$AGENT_MODEL" 8000 logs/server-agent.log "${AGENT_FLAGS[@]}"
for attempt in 1 2; do
  pids=()
  for k in $(seq 0 $((SHARDS - 1))); do
    .venv/bin/python -m evals.run_eval --flow "$FLOW" --cases "$CASES" --variant "$VARIANT" --split "$SPLIT" --attack "$ATTACK" \
      --base-url "${AGENT_URLS[$((k % ${#AGENT_URLS[@]}))]}" --model "$AGENT_MODEL" --context-tokens "$CONTEXT" \
      --shard "$k/$SHARDS" ${AGENT:+--agent "$AGENT"} ${REPS:+--reps "$REPS"} ${ATTACKED_ONLY:+--attacked-only} ${LIMIT:+--limit "$LIMIT"} ${DEFENSE:+--defense "$DEFENSE"} ${SYSTEM_PROMPT:+--system-prompt "$SYSTEM_PROMPT"} \
      ${SUITES:+--suites "$SUITES"} ${BENIGN_ONLY:+--benign-only} ${WHERE:+--where "$WHERE"} ${ADAPTIVE:+--adaptive "$ADAPTIVE" --seed-variant "$SEED_VARIANT" --attacker-url "$JUDGE_BASE_URL" --attacker-model "$JUDGE_MODEL"} ${ATTACKER_PROMPT:+--attacker-prompt "$ATTACKER_PROMPT"} \
      > "logs/$VARIANT-shard$k.log" 2>&1 &
    pids+=($!)
  done
  wait "${pids[@]}" || true
  grep -h "ERROR\|harness changed" logs/"$VARIANT"-shard*.log | tail -5 || true
done
[ "$BACKEND" = local-mlx ] && kill "$SERVER_PID" 2>/dev/null || true

if [ -z "${SKIP_JUDGE:-}" ]; then
  [ "$BACKEND" = local-mlx ] && serve_mlx "$JUDGE_MODEL" 8001 logs/server-judge.log "${JUDGE_FLAGS[@]}"
  STRIP=""; [ "$FLOW" != results ] && STRIP=--strip-notice
  .venv/bin/python -m evals.judge --flow "$FLOW" --variant "$VARIANT" --model "$JUDGE_MODEL" --base-url "$JUDGE_BASE_URL" \
    ${STRIP:+"$STRIP"} > "logs/$VARIANT-judge.log" 2>&1
  tail -1 "logs/$VARIANT-judge.log"
  [ "$BACKEND" = local-mlx ] && kill "$SERVER_PID" 2>/dev/null || true
fi

if [ "$FLOW" = results ]; then
  for split in train test; do .venv/bin/python -m evals.summarize --split "$split" 2>/dev/null; done
fi
echo "round $VARIANT done"
