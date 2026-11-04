#!/usr/bin/env bash
# Deploy a git ref to kepler, restart the services, and check they came up.
#
#   deploy/deploy.sh [REF]        # default REF: main
#
# kepler's ~/ocrcade is a git checkout on branch `deploy` with
# receive.denyCurrentBranch=updateInstead, so a push rewrites the working tree in
# place. Git refuses the push if a tracked file was edited on the Pi, which keeps hand
# edits from being silently overwritten. Event data (.env, teams.json, flags/, ...) is
# gitignored and never touched. Rolling back is deploying the previous commit, which
# this script prints at the end.
#
# The target is the git remote named by OCRCADE_REMOTE (default `kepler`), whose URL
# must be ssh://USER@HOST/ABS/PATH. One-time setup: deploy/README.md.
set -euo pipefail

ref="${1:-main}"
remote="${OCRCADE_REMOTE:-kepler}"
units=(ocrcade-exec ocrcade-vision)
health_url="http://127.0.0.1:5000/health"

url="$(git remote get-url "$remote")" || {
  echo "no git remote '$remote' (see deploy/README.md)" >&2
  exit 1
}
if [[ ! "$url" =~ ^ssh://([^/]+)(/.+)$ ]]; then
  echo "remote '$remote' URL must be ssh://USER@HOST/ABS/PATH, got: $url" >&2
  exit 1
fi
host="${BASH_REMATCH[1]}"
path="${BASH_REMATCH[2]}"

commit="$(git rev-parse --verify --quiet "${ref}^{commit}")" || {
  echo "unknown ref: $ref" >&2
  exit 1
}
prev="$(ssh "$host" git -C "$path" rev-parse HEAD)"

echo "deploying $(git log --oneline -1 "$commit") to $host:$path"
# --force so deploying an older commit (a rollback) is allowed.
git push --force "$remote" "$commit:refs/heads/deploy"

# Remote steps run under bash -s with the values passed as arguments, so nothing here
# is re-parsed by the remote shell.
ssh "$host" bash -s -- "$path" "$health_url" "${units[@]}" <<'REMOTE'
set -euo pipefail
path="$1"; health_url="$2"; shift 2
units=("$@")

reload=0
for u in "${units[@]}"; do
  if ! cmp -s "$path/deploy/$u.service" "/etc/systemd/system/$u.service"; then
    echo "installing changed unit $u.service"
    sudo install -m 0644 "$path/deploy/$u.service" "/etc/systemd/system/$u.service"
    reload=1
  fi
done
if (( reload )); then sudo systemctl daemon-reload; fi

sudo systemctl restart "${units[@]}"

for _ in $(seq 1 20); do
  if curl -fsS --max-time 2 "$health_url" >/dev/null 2>&1; then break; fi
  sleep 1
done
curl -fsS --max-time 2 "$health_url" >/dev/null || {
  echo "health check failed: $health_url" >&2
  exit 1
}
for u in "${units[@]}"; do
  systemctl is-active --quiet "$u" || {
    echo "$u is not active; see: journalctl -u $u -n 50" >&2
    exit 1
  }
done
echo "services up, /health ok"
REMOTE

echo "deployed ${commit:0:7} (was ${prev:0:7})"
echo "roll back with: deploy/deploy.sh ${prev:0:7}"
