#!/usr/bin/env bash
# Fleet registration — called automatically by copier after scaffolding when you
# answered "yes" to the register-in-fleet question. Opens a PR on the platform's
# agent-registry adding this agent to members.yaml (the fleet inventory the
# migration bot reads). NEVER modifies the registry directly — a human merges.
#
# Requires: gh CLI authenticated with access to the registry. If you don't have
# access (external contributors), this prints the entry to send to the platform
# admin instead — scaffolding never fails because of registration.
set -u

REPO_PATH="${1:?usage: register-in-fleet.sh <owner/name> <agent-name>}"
NAME="${2:?usage: register-in-fleet.sh <owner/name> <agent-name>}"
REGISTRY="${FLEET_REGISTRY:-turingplanet/agent-registry}"

manual() {
  echo "→ Could not open the registration PR automatically ($1)."
  echo "  Ask the platform admin to add this to ${REGISTRY} members.yaml:"
  printf '    - name: %s\n      repo: %s\n' "$NAME" "$REPO_PATH"
  exit 0
}

[ -n "${CI:-}" ] && exit 0   # never run on CI (the migration bot re-runs copier tasks)
command -v gh >/dev/null 2>&1 || manual "gh CLI not installed"
gh auth status >/dev/null 2>&1 || manual "gh not authenticated"

# If only a name was given (no "owner/"), use the gh-authenticated username as owner.
case "$REPO_PATH" in
  */*) : ;;
  *)
    owner=$(gh api user -q .login 2>/dev/null) || manual "no owner in '${REPO_PATH}' and couldn't read your gh username"
    REPO_PATH="${owner}/${REPO_PATH}"
    echo "→ Registering as ${REPO_PATH} (owner from your gh login)."
    ;;
esac

# Idempotent: already registered (or registration PR content already present)?
current=$(gh api "repos/${REGISTRY}/contents/members.yaml" -q .content 2>/dev/null | base64 -d 2>/dev/null) \
  || manual "no read access to ${REGISTRY}"
if printf '%s' "$current" | grep -q "repo: ${REPO_PATH}\$"; then
  echo "→ ${REPO_PATH} is already in the fleet inventory — nothing to do."
  exit 0
fi

branch="register/${NAME}"
main_sha=$(gh api "repos/${REGISTRY}/git/ref/heads/main" -q .object.sha 2>/dev/null) || manual "cannot read main"
gh api -X POST "repos/${REGISTRY}/git/refs" -f ref="refs/heads/${branch}" -f sha="$main_sha" >/dev/null 2>&1 \
  || true   # branch may already exist from a previous attempt — reuse it

file_json=$(gh api "repos/${REGISTRY}/contents/members.yaml?ref=${branch}" 2>/dev/null) || manual "cannot read members.yaml"
file_sha=$(printf '%s' "$file_json" | jq -r .sha)
branch_content=$(printf '%s' "$file_json" | jq -r .content | base64 -d)
# Idempotent against the PR branch too (a re-run must not append a duplicate).
if printf '%s' "$branch_content" | grep -q "repo: ${REPO_PATH}\$"; then
  echo "→ ${REPO_PATH} already pending in the registration PR — nothing to do."
  exit 0
fi
new_content=$(printf '%s' "$branch_content"
              printf '\n  - name: %s\n    repo: %s\n' "$NAME" "$REPO_PATH")
gh api -X PUT "repos/${REGISTRY}/contents/members.yaml" \
  -f message="fleet: register ${NAME} (${REPO_PATH})" \
  -f branch="$branch" -f sha="$file_sha" \
  -f content="$(printf '%s\n' "$new_content" | base64 | tr -d '\n')" >/dev/null 2>&1 \
  || manual "no write access to ${REGISTRY}"

gh pr create --repo "$REGISTRY" --head "$branch" --base main \
  --title "fleet: register ${NAME}" \
  --body "Auto-opened by the agent-template scaffold. Adds \`${REPO_PATH}\` to the fleet inventory so the migration bot keeps it in sync. Merging is the admin's call." \
  >/dev/null 2>&1 || true   # PR may already exist for this branch — that's fine

echo "→ Fleet registration PR opened/updated on ${REGISTRY} (branch: ${branch})."
exit 0
