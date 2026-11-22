#!/usr/bin/env bash
# Fetch upstream Niko1221/Strata updates, rebase local branch, and rebuild RTX 4080 binary
set -euo pipefail

REPO_DIR="${STRATA_SRC_DIR:-/home/shinsuke/StrataTurbo/src/Strata}"
BUILD_SCRIPT="/home/shinsuke/StrataTurbo/scripts/build_rtx4080.sh"

cd "$REPO_DIR"
git fetch origin
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
echo "Rebasing $CURRENT_BRANCH onto origin/main..."
git rebase origin/main
bash "$BUILD_SCRIPT"

echo "Update & rebuild complete. Restart strata-llm.service to apply:"
echo "  systemctl --user restart strata-llm.service"
