#!/usr/bin/env bash
# From a dev box: install a local branch on the staging server, straight over ssh (nothing goes to GitHub).
#   deploy/stage-push.sh [branch] [ssh host]     defaults: the current branch, minutes-stage
# The branch lands as origin/stage-test in the server's clone, which update.sh then installs (with its rollback).
set -euo pipefail
B=${1:-$(git rev-parse --abbrev-ref HEAD)} H=${2:-minutes-stage}
O=$(ssh "$H" stat -c %U /opt/club-minutes)  # root, or 'minutes' on an install from before root owned the code
git push -q -f --receive-pack="sudo -u $O git-receive-pack" "ssh://$H/opt/club-minutes" "$B:refs/remotes/origin/stage-test"
ssh "$H" BRANCH=stage-test /opt/club-minutes/deploy/update.sh
