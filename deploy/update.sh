#!/bin/sh
# Step 1: update the repository checkout to the revision being deployed.
# Usage: ./deploy/update.sh <git-ref>
set -e

REF="${1:?usage: update.sh <git-ref>}"

git fetch --all --prune
git checkout "$REF"
git pull --ff-only origin "$REF" || true

echo ">>> Repository updated to $REF ($(git rev-parse --short HEAD))."
