#!/bin/bash
# Git push helper that uses PAT from file and never loses keys
set -e
PAT=$(cat /home/user/.github_pat 2>/dev/null || cat .github_pat 2>/dev/null || echo "")
if [ -z "$PAT" ]; then
  echo "❌ No PAT found in /home/user/.github_pat or .github_pat"
  exit 1
fi
# Configure
git config user.email "karoon177@users.noreply.github.com"
git config user.name "karoon177"
# Push with PAT
echo "Pushing with PAT..."
git push https://${PAT}@github.com/abi18636/lundry.git main
echo "✅ Pushed"
