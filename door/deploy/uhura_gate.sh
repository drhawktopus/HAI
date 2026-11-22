#!/bin/bash
# Uhura's test gate, from Softserve: send the MIRROR's tree (what ship_uhura.sh would ship) to a throwaway folder on
# Sundae, run EVERY test_*.py in the test image (deploy/Dockerfile.test) on an internal network (no route out), with
# fastmcp's store on a tmpfs (production sets FASTMCP_HOME=/state; the uhura user has no home), print the verdict, clean up.
# Exit 0 only when pytest exits 0 AND collected at least MIN_TESTS (a suite that cannot import prints a short count;
# empty is not a pass). Usage: bash deploy/uhura_gate.sh
MIN_TESTS=95
cd "$(dirname "$0")/.." || exit 2       # the door's folder: the gate tests what this checkout holds
eval "$(python deploy/uhura_settings.py --sh UHURA_SSH_HOST)"
: "${UHURA_SSH_HOST:?set UHURA_SSH_HOST in deploy/uhura.env: the machine the tests run on, with Docker}"
tar --exclude=__pycache__ --exclude=state --exclude=published --exclude=liaison-state \
    --exclude='*.log' -cf - Dockerfile docker-compose.yml deploy liaison uhura.py transcript_store.py test_*.py 2>/dev/null |
ssh "$UHURA_SSH_HOST" 'set -o pipefail
  d=$(mktemp -d /tmp/uhura-gate.XXXXXX) && tar -xf - -C "$d" || exit 2
  chmod -R a+rwX "$d"     # a throwaway copy: the tests write state beside themselves, as uid 10001
  docker build --quiet -t uhura-test:latest -f "$d/deploy/Dockerfile.test" "$d/deploy" >/dev/null || { echo "test image build failed"; rm -rf "$d"; exit 2; }
  # an INTERNAL network: a real interface with no route out. --network none leaves only lo, which the Uhura watchdog
  # rightly calls "no network interface", so its test cannot run there. (No apostrophes in here: this is one quote.)
  docker network inspect uhura-gate-net >/dev/null 2>&1 || docker network create --internal uhura-gate-net >/dev/null
  docker run --rm --network uhura-gate-net -v "$d":/src --tmpfs /tmp:exec -e HOME=/tmp -e FASTMCP_HOME=/tmp/fastmcp -e PYTHONPATH=/src:/src/deploy -w /src uhura-test:latest \
    python -m pytest -q -p no:cacheprovider --tb=short > "$d.out" 2>&1
  rc=$?
  rm -rf "$d"
  grep -E "^(FAILED|ERROR) " "$d.out" | cut -c1-160
  tail -1 "$d.out"
  n=$(tail -1 "$d.out" | grep -oE "[0-9]+ passed" | grep -oE "[0-9]+")
  mv -f "$d.out" /tmp/uhura-gate.last.log    # the whole run, for the traceback behind a RED
  if [ "$rc" != "0" ]; then echo "RED rc=$rc"; exit 1; fi
  if [ "${n:-0}" -lt '"$MIN_TESTS"' ]; then echo "RED: only ${n:-0} passed, expected at least '"$MIN_TESTS"'"; exit 1; fi
  echo "GREEN $n passed"'
