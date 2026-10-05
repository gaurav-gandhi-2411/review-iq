#!/usr/bin/env bash
# Restore ONE backup artifact into the throwaway `pgrestore` container and verify it (S17 H2).
# Called by .github/workflows/db-restore-test.yml as `bash scripts/restore_one.sh NAME N`; its own
# process so that `set -e` really aborts (errexit is ignored inside an `if`/`&&` list in-line).
# Needs: GH_TOKEN, REPO, DB_BACKUP_ENCRYPTION_KEY in the environment; docker, gh, gpg, uv.
# Never prints the key. Exit 0 only if decrypt, gunzip, restore (zero psql ERROR lines) and
# scripts/verify_restore.py all pass.
set -euo pipefail
NAME="$1"
N="$2"
WORK="work_${N}"
DB="restore_${N}"
mkdir -p "${WORK}"

ID=$(gh api "repos/${REPO}/actions/artifacts?per_page=100&name=${NAME}" \
  --jq '[.artifacts[] | select(.expired | not)] | .[0].id')
if [ -z "${ID}" ] || [ "${ID}" = "null" ]; then
  echo "artifact ${NAME} not found or expired"
  exit 1
fi
gh api "repos/${REPO}/actions/artifacts/${ID}/zip" > "${WORK}/a.zip"
unzip -q -o "${WORK}/a.zip" -d "${WORK}"
FILE=$(find "${WORK}" -type f \( -name '*.sql.gz.gpg' -o -name '*.sql.gz' \) | head -1)
case "${FILE}" in
  *.gpg)
    gpg --batch --yes --decrypt --passphrase-fd 0 --output "${WORK}/dump.sql.gz" "${FILE}" \
      <<< "${DB_BACKUP_ENCRYPTION_KEY}"
    ;;
  *) cp "${FILE}" "${WORK}/dump.sql.gz" ;;
esac
gunzip -t "${WORK}/dump.sql.gz"
echo "decrypted + gzip OK ($(du -h "${WORK}/dump.sql.gz" | cut -f1))"

docker exec pgrestore psql -U postgres -X -q -c "CREATE DATABASE ${DB}"
# Roles the dump references (anon, authenticated, service_role, review_iq_*, ...). Without them
# every GRANT / CREATE POLICY ... TO <role> fails and the restored database has the data but no
# RLS (ops/runbooks/db-restore.md, Step 4 note).
for ROLE in $(uv run python scripts/verify_restore.py roles "${WORK}/dump.sql.gz"); do
  docker exec pgrestore psql -U postgres -X -q -c \
    "DO \$\$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='${ROLE}') THEN CREATE ROLE \"${ROLE}\" NOLOGIN; END IF; END \$\$;"
done

gunzip -c "${WORK}/dump.sql.gz" \
  | docker exec -i pgrestore psql -U postgres -d "${DB}" -X -v ON_ERROR_STOP=0 \
    > "${WORK}/restore.out" 2> "${WORK}/restore.err" || true
NERR=$(grep -c 'ERROR:' "${WORK}/restore.err" || true)
echo "psql ERROR lines during restore: ${NERR}"
grep 'ERROR:' "${WORK}/restore.err" | sort | uniq -c | sort -rn | head -20 || true

uv run python scripts/verify_restore.py verify "${WORK}/dump.sql.gz" \
  "postgresql://postgres:restore_test@127.0.0.1:5432/${DB}"
[ "${NERR}" = "0" ]
