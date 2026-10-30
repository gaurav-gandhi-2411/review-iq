#!/usr/bin/env bash
# Main E2 sweep with the small encoder: Track A (3 datasets) and Track B (CLINC, BANKING77, MASSIVE).
# Sequential on purpose: the GPU is shared with other sessions' jobs (spec section 6).
set -u
PY=${PY:-/c/Users/gaura/venvs/engine/Scripts/python.exe}
M=${M:-sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2}
TAG=${TAG:-minilm}
cd "$(dirname "$0")/../.."
run() { name=$1; shift; echo "=== $name $(date +%T)"; PYTHONIOENCODING=utf-8 $PY -u -m engine.experiments.run --model "$M" --out "reports/engine/${TAG}_${name}.json" "$@" 2>&1 | grep -a -E "^(epoch|\{|msp|energy|maha|unknown)|Traceback|Error" | cut -c1-420; }
run banking77_A_s42 --dataset banking77 --epochs 8
run massive_A_en --dataset massive --locales en-US --epochs 6
run massive_A_8loc --dataset massive --locales en-US,hi-IN,ta-IN,bn-BD,de-DE,ja-JP,ar-SA,sw-KE --epochs 3 --batch-size 64
run clinc_B_h30 --dataset clinc --holdout 30 --unknown-head --epochs 6
run banking77_B_h20 --dataset banking77 --holdout 20 --unknown-head --epochs 8
run massive_B_h15 --dataset massive --locales en-US --holdout 15 --unknown-head --epochs 6
for s in 43 44; do
  run clinc_A_s$s --dataset clinc --seed $s --epochs 6
  run banking77_A_s$s --dataset banking77 --seed $s --epochs 8
done
echo "=== done $(date +%T)"
