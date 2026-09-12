#!/usr/bin/env bash
# Flip-flop for reviewiq-prod-260813's Cloud Scheduler pipeline (ingest-tick,
# digest-daily, detector-sweep). All three are PAUSED by default -- run `on`
# only when the live pipeline needs to actually run, then `off` again to
# avoid standing invocation cost.
set -euo pipefail

PROJECT="reviewiq-prod-260813"
LOCATION="asia-south1"
JOBS=(review-iq-ingest-tick review-iq-digest-daily review-iq-detector-sweep)

usage() {
  echo "Usage: $0 {on|off|status}"
  echo "  on     - resume the ingest/digest/detector pipeline"
  echo "  off    - pause the pipeline (zero scheduled invocation cost)"
  echo "  status - show current state of each job"
  exit 1
}

[[ $# -eq 1 ]] || usage

case "$1" in
  on)
    for job in "${JOBS[@]}"; do
      gcloud scheduler jobs resume "$job" --project="$PROJECT" --location="$LOCATION"
    done
    ;;
  off)
    for job in "${JOBS[@]}"; do
      gcloud scheduler jobs pause "$job" --project="$PROJECT" --location="$LOCATION"
    done
    ;;
  status)
    gcloud scheduler jobs list --project="$PROJECT" --location="$LOCATION" \
      --format="table(name,schedule,state)"
    ;;
  *)
    usage
    ;;
esac
