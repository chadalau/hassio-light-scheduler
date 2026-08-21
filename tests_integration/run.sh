#!/usr/bin/env sh
# Run the Light Scheduler integration tests against a real Home Assistant.
# Any argument is passed straight to pytest:
#   ./tests_integration/run.sh -k ownership -v
set -eu

repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

# Git Bash needs both halves of this handled separately. Paths given to Docker
# have to stay Windows-shaped, because docker.exe reads them; paths meant for
# inside the container must not be rewritten at all. cygpath fixes the first,
# MSYS_NO_PATHCONV the second. Neither exists elsewhere, where $repo is already
# the right thing and no rewriting happens.
if command -v cygpath >/dev/null 2>&1; then
    host_repo=$(cygpath -m -- "$repo")
    MSYS_NO_PATHCONV=1
    MSYS2_ARG_CONV_EXCL='*'
    export MSYS_NO_PATHCONV MSYS2_ARG_CONV_EXCL
else
    host_repo=$repo
fi

docker build -f "$host_repo/tests_integration/Dockerfile" -t light-scheduler-tests "$host_repo"

# Read-only mount: a test must never be able to write into the working tree.
exec docker run --rm -v "$host_repo:/workspace:ro" -w /workspace light-scheduler-tests \
    pytest tests_integration -q -o asyncio_mode=auto -p no:cacheprovider "$@"
