#!/usr/bin/env bash
set -euo pipefail
# Usage: GH_TOKEN=... ./scripts/deploy_github.sh
# Creates/updates public or private repo under the authenticated user.

REPO_NAME="${REPO_NAME:-zenith-trader-bot}"
VISIBILITY="${VISIBILITY:-private}"   # private recommended (trading bot)
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -z "${GH_TOKEN:-}" ]]; then
  echo "Set GH_TOKEN"; exit 2
fi

AUTH_USER=$(curl -sS -H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github+json" https://api.github.com/user | python3 -c "import sys,json; print(json.load(sys.stdin)['login'])")
echo "GitHub user: $AUTH_USER"

# create repo if missing
CODE=$(curl -sS -o /tmp/gh_repo.json -w "%{http_code}" -H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github+json" "https://api.github.com/repos/$AUTH_USER/$REPO_NAME")
if [[ "$CODE" == "404" ]]; then
  echo "Creating repo $REPO_NAME ($VISIBILITY)"
  curl -sS -X POST -H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github+json" \
    https://api.github.com/user/repos \
    -d "{\"name\":\"$REPO_NAME\",\"private\":$([[ $VISIBILITY == private ]] && echo true || echo false),\"description\":\"Zenith v001 REBIRTH trader bot — Deribit testnet\",\"auto_init\":false}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('html_url') or d)"
else
  echo "Repo exists ($CODE)"
fi

if [[ ! -d .git ]]; then
  git init
  git checkout -b main
fi
git config user.email "bot@local"
git config user.name "zenith-lab"

# ensure secrets not staged
grep -q '^\.env$' .gitignore || echo '.env' >> .gitignore

git add -A
git status
git commit -m "zenith trader bot: Deribit testnet + zenith-v001 REBIRTH apex" || true

REMOTE="https://x-access-token:${GH_TOKEN}@github.com/${AUTH_USER}/${REPO_NAME}.git"
if git remote | grep -q origin; then
  git remote set-url origin "$REMOTE"
else
  git remote add origin "$REMOTE"
fi
git push -u origin main
echo "PUSHED https://github.com/${AUTH_USER}/${REPO_NAME}"
