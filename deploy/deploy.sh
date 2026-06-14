#!/bin/sh
# Step 3: deploy the new version by rebuilding and restarting the production stack.
set -e

. ./deploy/_common.sh

log "Deploying the new version..."
podman-compose up -d --build

log "Deployment started."
