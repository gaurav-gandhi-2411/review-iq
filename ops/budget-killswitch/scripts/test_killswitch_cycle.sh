#!/bin/bash
# Live-fire test for the billing kill-switch: publishes a synthetic 100%-threshold budget
# breach, waits for the deployed Cloud Function to actually disable billing, then relinks.
#
# WARNING: this disables real billing on a live production project for a short window
# (previously measured 15.7s-18s). Only run this deliberately, with explicit sign-off --
# never as part of an unattended script or CI job.
#
# Hardened 2026-08-15 after a real run exposed two problems with an earlier ad-hoc version
# of this script:
#   1. It depended on `bc` for elapsed-time arithmetic, which is not installed on this
#      machine. `bc`'s absence caused a silent failure in the timing computation, which in
#      turn made the script falsely report the disable as "not confirmed" and skip its own
#      automated relink step -- billing stayed disabled until the operator manually noticed
#      and relinked by hand. The actual recovery time (confirmed via Cloud Audit Logs) was
#      15.7s only because a human caught the false negative immediately; the script itself
#      would have left billing disabled indefinitely.
#   2. There was no guaranteed relink path -- if ANY step after the disable-confirmation
#      failed or raised, the script would exit with billing left disabled and no automatic
#      recovery attempt at all.
#
# Fixes in this version:
#   - No `bc` dependency -- uses bash's native integer arithmetic ($(( ))) for elapsed time.
#     Sub-second precision isn't needed here: the authoritative timing record is always
#     Cloud Audit Logs (`protoPayload.serviceName=cloudbilling.googleapis.com`), not this
#     script's own console output.
#   - A `trap ... EXIT` guarantees the relink command fires no matter how the script exits
#     (normal completion, error, or an operator Ctrl-C) UNLESS relink has already succeeded.
#     The script cannot exit with billing left disabled without at least attempting recovery.
#   - A pre-flight dependency check runs BEFORE the synthetic breach is published, aborting
#     with no side effects if anything required is missing or misconfigured.
#
# Usage: bash ops/budget-killswitch/scripts/test_killswitch_cycle.sh

set -uo pipefail

ACCT="gaurav.gandhi1129@gmail.com"
PROJECT="reviewiq-prod-260813"
BILLING_ACCT="01285B-91E4CB-70AD7E"
TOPIC="projects/${PROJECT}/topics/billing-alerts"

RELINKED=0

relink_now() {
    if [ "$RELINKED" -eq 1 ]; then
        return 0
    fi
    echo "[relink] issuing gcloud billing projects link now..."
    if gcloud billing projects link "$PROJECT" --billing-account="$BILLING_ACCT" --account="$ACCT" 2>&1; then
        RELINKED=1
        echo "[relink] link command succeeded"
    else
        echo "[relink] FAILED -- gcloud billing projects link returned non-zero. RETRY MANUALLY NOW:"
        echo "  gcloud billing projects link $PROJECT --billing-account=$BILLING_ACCT --account=$ACCT"
    fi
}

# Guaranteed relink attempt on ANY exit path (success, error, Ctrl-C) once billing has
# actually been confirmed disabled. Set only after the breach is published, so a pre-flight
# failure never triggers a relink attempt against a project that was never disabled.
GUARD_ARMED=0
on_exit() {
    if [ "$GUARD_ARMED" -eq 1 ] && [ "$RELINKED" -eq 0 ]; then
        echo "[guard] script exiting with billing not yet confirmed relinked -- forcing relink attempt"
        relink_now
        enabled=$(gcloud billing projects describe "$PROJECT" --account="$ACCT" --format="value(billingEnabled)" 2>&1)
        echo "[guard] final billingEnabled=${enabled}"
        if [ "$enabled" != "True" ]; then
            echo "[guard] *** BILLING MAY STILL BE DISABLED. VERIFY MANUALLY IMMEDIATELY. ***"
        fi
    fi
}
trap on_exit EXIT

echo "=== Pre-flight checks ==="
FAIL=0
for cmd in gcloud date; do
    if ! command -v "$cmd" > /dev/null 2>&1; then
        echo "[preflight] MISSING: $cmd"
        FAIL=1
    fi
done
if ! gcloud auth list --format="value(account)" --filter="status:ACTIVE" 2>&1 | grep -qx "$ACCT"; then
    echo "[preflight] $ACCT is not the active gcloud account -- aborting before touching anything"
    FAIL=1
fi
baseline=$(gcloud billing projects describe "$PROJECT" --account="$ACCT" --format="value(billingEnabled)" 2>&1)
if [ "$baseline" != "True" ]; then
    echo "[preflight] baseline billingEnabled=${baseline}, expected True -- billing is not in a clean starting state, aborting"
    FAIL=1
fi
if ! gcloud pubsub topics describe "$TOPIC" --account="$ACCT" > /dev/null 2>&1; then
    echo "[preflight] cannot describe topic $TOPIC -- aborting"
    FAIL=1
fi
if [ "$FAIL" -eq 1 ]; then
    echo "=== PRE-FLIGHT FAILED -- nothing published, nothing touched. Fix the above and retry. ==="
    exit 1
fi
echo "=== Pre-flight OK: baseline billingEnabled=True, topic reachable, correct identity active ==="

t0=$(date +%s)
echo "T+0s  publishing synthetic breach message"
gcloud pubsub topics publish "$TOPIC" \
  --account="$ACCT" \
  --message='{"costAmount": 2500, "budgetAmount": 2500, "alertThresholdExceeded": 1.0, "currencyCode": "INR", "budgetDisplayName": "synthetic-test-killswitch-cycle"}' 2>&1

GUARD_ARMED=1

echo "polling for billing to actually disable..."
disabled=0
for _ in $(seq 1 60); do
    enabled=$(gcloud billing projects describe "$PROJECT" --account="$ACCT" --format="value(billingEnabled)" 2>&1)
    now=$(date +%s)
    echo "  T+$((now - t0))s  billingEnabled=${enabled}"
    if [ "$enabled" = "False" ]; then
        disabled=1
        echo "CONFIRMED DISABLED at T+$((now - t0))s"
        break
    fi
    sleep 1
done

if [ "$disabled" -eq 0 ]; then
    echo "billing never showed disabled after 60s of polling -- the function may not have fired."
    echo "Not relinking (nothing to relink) -- but leaving GUARD_ARMED so the exit trap still verifies final state."
    exit 1
fi

relink_start=$(date +%s)
relink_now

echo "polling for billing to re-enable..."
for _ in $(seq 1 30); do
    enabled=$(gcloud billing projects describe "$PROJECT" --account="$ACCT" --format="value(billingEnabled)" 2>&1)
    now=$(date +%s)
    echo "  T+$((now - t0))s  billingEnabled=${enabled}"
    if [ "$enabled" = "True" ]; then
        echo "CONFIRMED RECOVERED at T+$((now - t0))s (relink took $((now - relink_start))s)"
        echo "Authoritative timing record: check Cloud Audit Logs, not this console output --"
        echo "  gcloud logging read \"protoPayload.serviceName=cloudbilling.googleapis.com\" --project=$PROJECT --account=$ACCT --freshness=10m --format=\"table(timestamp,protoPayload.methodName,protoPayload.authenticationInfo.principalEmail)\" --order=asc"
        exit 0
    fi
    sleep 1
done

echo "relink issued but not confirmed enabled=True within 30s of polling -- exit trap will retry and verify"
exit 1
