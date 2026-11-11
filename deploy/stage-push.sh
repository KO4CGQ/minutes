#!/usr/bin/env bash
# From a dev box: install a local branch on the staging server, straight over ssh (nothing goes to GitHub).
#   deploy/stage-push.sh [branch] [ssh host]     defaults: the current branch, minutes-stage
# The branch lands as origin/stage-test in the server's clone, which update.sh then installs (with its rollback).
set -euo pipefail
B=${1:-$(git rev-parse --abbrev-ref HEAD)} H=${2:-minutes-stage}
git push -q -f --receive-pack="sudo -u minutes git-receive-pack" "ssh://$H/opt/w8lbz-minutes" "$B:refs/remotes/origin/stage-test"
ssh "$H" BRANCH=stage-test /opt/w8lbz-minutes/deploy/update.sh
