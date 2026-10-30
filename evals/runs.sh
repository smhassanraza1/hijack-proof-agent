#!/bin/bash
# Every run reported in the README and results/, in the order they were made.
#
#   evals/runs.sh checks                     # grader checks: reference solutions, no model needed
#   AGENT_BASE_URLS=... JUDGE_BASE_URL=... evals/runs.sh <group> [<group> ...]
#
# Groups: checks, hillclimb, calibration, final, adaptive, comparison, judge (or all). Each run resumes
# where it stopped, so a group can be restarted after a lost Kaggle session. The URLs come from
# kaggle/serve.ipynb.
#
# The registered evaluation (results/registered, protocol in registration/PROTOCOL.md; one group per Kaggle session, in order):
#   registered_checks (offline), registered_dev (session A), registered_core (B), registered_templates and registered_builtins (C), registered_adaptive (D), registered_spare.
# The final defence (v3, or v5 if round 5 passed validation) is read from results/registered/_state.json
# ("final_defence", written at stage-2 registration).
set -euo pipefail
cd "$(dirname "$0")/.."

checks() {  # the user's and attacker's reference solutions, and an agent that only refuses
  for oracle in user attacker null; do
    .venv/bin/python -m evals.run_eval --variant "_check_$oracle" --oracle "$oracle"
  done
  for attack in tool_knowledge injecagent; do
    .venv/bin/python -m evals.run_eval --variant "_check_$attack" --oracle attacker --split test --attack "$attack"
  done
}

hillclimb() {  # undefended agent twice (noise), then rounds 1-4; train and test
  REPS=2 evals/round.sh baseline
  for round in v1 v2 v3 v4; do evals/round.sh "$round"; done
}

calibration() {  # round 0: a prompt rule on the undefended agent; its rows also feed the judge calibration
  AGENT=baseline SYSTEM_PROMPT=evals/calibration/warn_prompt.md evals/round.sh _calib_warn train
}

final() {  # registered final check: attack templates no round was tuned on; the headline pair runs twice
  for attack in tool_knowledge injecagent; do
    for agent in baseline v3 v4; do
      reps=1; [ "$attack" = tool_knowledge ] && [ "$agent" != v4 ] && reps=2
      REPS=$reps evals/round.sh "$agent-$attack" test "$attack"
    done
  done
}

adaptive() {  # defence-aware attacks: the first template, the train tuning runs, then the chosen template on test
  for agent in baseline v3; do evals/round.sh "$agent-defense_aware" test defense_aware; done
  for attack in tool_knowledge adaptive_judge adaptive_part; do
    AGENT=v3 ATTACKED_ONLY=1 evals/round.sh "_pilot_v3-$attack" train "$attack"
  done
  for agent in baseline v3; do ATTACKED_ONLY=1 evals/round.sh "$agent-adaptive_judge" test adaptive_judge; done
}

comparison() {  # AgentDojo's built-in defences; the classifier loads PyTorch per worker, so 2 workers
  for defense in spotlighting_with_delimiting repeat_user_prompt tool_filter; do
    DEFENSE=$defense evals/round.sh "_def_$defense" test
  done
  DEFENSE=transformers_pi_detector SHARDS=2 evals/round.sh _def_transformers_pi_detector test
}

judge() {  # the judge's repeatability and known-answer checks (results/_judge_checks)
  .venv/bin/python -m evals.judge_checks --base-url "$JUDGE_BASE_URL"
}

final_defence() {
  .venv/bin/python -c "import json; print(json.load(open('results/registered/_state.json'))['final_defence'])"
}

registered_checks() {  # offline: grader and placement checks, fuzzed injections, bootstrap coverage
  .venv/bin/python -m evals.oracle_registered > registration/oracle_checks.md
  .venv/bin/python -m evals.coverage_registered > registration/coverage.out
}

registered_dev() {  # session A: canaries, judge calibration, validation of round 5, attacker tuning on train
  local FLOW=results/registered CASES=evals/cases_registered.jsonl; export FLOW CASES
  for agent in baseline v3 v5; do
    AGENT=$agent LIMIT=2 SKIP_JUDGE=1 evals/round.sh "_canary-$agent" train
  done
  .venv/bin/python -m evals.calibration judge-registered --base-url "$JUDGE_BASE_URL"
  .venv/bin/python -m evals.judge_checks --base-url "$JUDGE_BASE_URL" --strip-notice
  # round 5 changes only get_webpage, a Slack tool, so its validation is the 18 Slack validation cases
  AGENT=v3 SUITES=slack REPS=2 evals/round.sh v3-val val
  AGENT=v5 SUITES=slack REPS=2 evals/round.sh v5-val val
}

registered_tune() {  # session A, after the round-5 decision: attacker tuning on the 40 train pairs against the final defence
  local FLOW=results/registered CASES=evals/cases_registered.jsonl; export FLOW CASES
  local final; final=$(final_defence)
  AGENT=$final ATTACKED_ONLY=1 WHERE=tune SKIP_JUDGE=1 evals/round.sh "_tune-$final-tk" train tool_knowledge
  for prompt in informed plain; do
    AGENT=$final WHERE=tune ADAPTIVE=4 SEED_VARIANT="_tune-$final-tk" ATTACKER_PROMPT="evals/attacker_prompts/$prompt.md" \
      SKIP_JUDGE=1 evals/round.sh "_tune-$final-$prompt" train tool_knowledge
  done
}

registered_core() {  # session B: C1 and S1-S3 data, rep-major so a dead session leaves balanced arms
  local FLOW=results/registered CASES=evals/cases_registered.jsonl; export FLOW CASES
  local final; final=$(final_defence)
  local arms="baseline v3"; [ "$final" = v5 ] && arms="baseline v3 v5"
  for reps in 1 2; do
    for agent in $arms; do AGENT=$agent REPS=$reps evals/round.sh "$agent-ii" test; done
  done
}

registered_templates() {  # session C: tool_knowledge (C1) and the benign drift controls
  local FLOW=results/registered CASES=evals/cases_registered.jsonl; export FLOW CASES
  local final; final=$(final_defence)
  local arms="baseline v3"; [ "$final" = v5 ] && arms="baseline v3 v5"
  for agent in $arms; do AGENT=$agent ATTACKED_ONLY=1 evals/round.sh "$agent-tk" test tool_knowledge; done
  for agent in baseline v3; do AGENT=$agent BENIGN_ONLY=1 evals/round.sh "$agent-drift" test; done
}

registered_builtins() {  # session C: AgentDojo's built-in defences (C2); the classifier loads PyTorch per worker, so 2 workers
  local FLOW=results/registered CASES=evals/cases_registered.jsonl; export FLOW CASES
  for defense in repeat_user_prompt tool_filter; do DEFENSE=$defense evals/round.sh "_def_$defense" test; done
  DEFENSE=transformers_pi_detector SHARDS=2 evals/round.sh _def_transformers_pi_detector test
}

registered_adaptive() {  # session D: the frozen attacker (evals/attacker_prompt.md), undefended then the final defence
  local FLOW=results/registered CASES=evals/cases_registered.jsonl; export FLOW CASES
  local final; final=$(final_defence)
  AGENT=baseline ADAPTIVE=4 SEED_VARIANT=baseline-tk SKIP_JUDGE=1 evals/round.sh baseline-adaptive test tool_knowledge
  AGENT=$final ADAPTIVE=4 SEED_VARIANT="$final-tk" SKIP_JUDGE=1 evals/round.sh "$final-adaptive" test tool_knowledge
}

registered_spare() {  # only after registered_core..registered_adaptive are complete, in this order (fresh-goal set: separate, if registered)
  local FLOW=results/registered CASES=evals/cases_registered.jsonl; export FLOW CASES
  local final; final=$(final_defence)
  [ "$final" = v5 ] && AGENT=v3 ADAPTIVE=4 SEED_VARIANT=v3-tk SKIP_JUDGE=1 evals/round.sh v3-adaptive test tool_knowledge
  DEFENSE=spotlighting_with_delimiting evals/round.sh _def_spotlighting_with_delimiting test
}

[ $# -gt 0 ] || { sed -n 2,16p "$0"; exit 1; }
for group in "$@"; do
  if [ "$group" = all ]; then
    checks; hillclimb; calibration; final; adaptive; comparison; judge
  else
    "$group"
  fi
done
