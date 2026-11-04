#!/bin/sh
# Fail-closed storage guard for the Dubs compose stack.
#
# Every media service depends on this one-shot check completing successfully.
# It refuses to start the stack unless the operator has placed the sentinel
# file on the intended persistent storage. This catches the common failure of
# a missing/typoed mount (e.g. Docker silently creating an empty directory on
# the root filesystem) — it cannot detect every possible mount mistake, so
# verify mounts after setup as well.
#
# To arm the storage (once, after verifying the mount):
#   install -m 0644 /dev/null "$MEDIA_ROOT/.dubs-storage-ok"

f="/storage/.dubs-storage-ok"

if [ -f "$f" ]; then
    echo "storage-sentinel: OK ($f present)"
    exit 0
fi

cat >&2 <<'EOF'
storage-sentinel: REFUSING TO START.

The sentinel file .dubs-storage-ok was not found on the mounted MEDIA_ROOT.
This guard exists so the media containers can never bind a wrong or
non-persistent path. If you have verified that MEDIA_ROOT in compose/.env
points at the real, persistent media storage, arm it with:

    install -m 0644 /dev/null "<MEDIA_ROOT>/.dubs-storage-ok"

Then re-run: docker compose up -d
EOF
exit 1
