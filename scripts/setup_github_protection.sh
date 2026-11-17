#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Apply the repository protection recommended in GOVERNANCE.md, using the GitHub CLI.
#
# NOT run by CI. The lead maintainer runs it once, by hand, after reading it:
#   gh auth login                    # an account with admin rights on the repo
#   scripts/setup_github_protection.sh            # dry run: prints what would be sent
#   scripts/setup_github_protection.sh --apply    # actually applies it
#
# Idempotent: existing rulesets with the same name are updated instead of duplicated.
set -euo pipefail

REPO="${REPO:-Blue-Studio-Lab/Revelado}"
APPLY=0
[[ "${1:-}" == "--apply" ]] && APPLY=1

command -v gh >/dev/null || { echo "gh (GitHub CLI) is required" >&2; exit 1; }
command -v jq >/dev/null || { echo "jq is required" >&2; exit 1; }

run() {
  if [[ $APPLY -eq 1 ]]; then "$@"; else printf 'DRY RUN:'; printf ' %q' "$@"; printf '\n'; fi
}

# Upsert a ruleset by name. $1 = name, $2 = JSON body.
upsert_ruleset() {
  local name="$1" body="$2" id
  id="$(gh api "repos/$REPO/rulesets" --jq ".[] | select(.name == \"$name\") | .id" 2>/dev/null || true)"
  if [[ -n "$id" ]]; then
    echo "Updating ruleset '$name' ($id)"
    run gh api --method PUT "repos/$REPO/rulesets/$id" --input - <<<"$body"
  else
    echo "Creating ruleset '$name'"
    run gh api --method POST "repos/$REPO/rulesets" --input - <<<"$body"
  fi
}

# Status-check contexts must match job names in .github/workflows/*.yml.
MAIN_RULESET="$(jq -n '{
  name: "main-protection",
  target: "branch",
  enforcement: "active",
  bypass_actors: [],
  conditions: { ref_name: { include: ["~DEFAULT_BRANCH"], exclude: [] } },
  rules: [
    { type: "deletion" },
    { type: "non_fast_forward" },
    { type: "required_linear_history" },
    { type: "required_signatures" },
    { type: "pull_request", parameters: {
        required_approving_review_count: 1,
        dismiss_stale_reviews_on_push: true,
        require_code_owner_review: true,
        require_last_push_approval: true,
        required_review_thread_resolution: true,
        allowed_merge_methods: ["squash", "rebase"] } },
    { type: "required_status_checks", parameters: {
        strict_required_status_checks_policy: true,
        required_status_checks: [
          { context: "ci-ok" },
          { context: "gitleaks" },
          { context: "dco" },
          { context: "dependency-review" },
          { context: "Analyze (python)" },
          { context: "Analyze (javascript-typescript)" } ] } }
  ] }')"

TAG_RULESET="$(jq -n '{
  name: "release-tags",
  target: "tag",
  enforcement: "active",
  bypass_actors: [ { actor_type: "RepositoryRole", actor_id: 5, bypass_mode: "always" } ],
  conditions: { ref_name: { include: ["refs/tags/v*"], exclude: [] } },
  rules: [ { type: "deletion" }, { type: "update" }, { type: "non_fast_forward" }, { type: "required_signatures" } ]
}')"
# actor_id 5 = the built-in "Admin" repository role: only admins (maintainers) may create v* tags.

upsert_ruleset "main-protection" "$MAIN_RULESET"
upsert_ruleset "release-tags" "$TAG_RULESET"

echo "Repository security features"
run gh api --method PUT "repos/$REPO/private-vulnerability-reporting"
run gh api --method PUT "repos/$REPO/vulnerability-alerts"
run gh api --method PUT "repos/$REPO/automated-security-fixes"
run gh api --method PATCH "repos/$REPO" --input - <<<'{
  "security_and_analysis": {
    "secret_scanning": {"status": "enabled"},
    "secret_scanning_push_protection": {"status": "enabled"}
  },
  "delete_branch_on_merge": true,
  "allow_merge_commit": false,
  "has_discussions": true
}'

echo "Actions: read-only default token, no PR approval by Actions"
run gh api --method PUT "repos/$REPO/actions/permissions/workflow" --input - <<<'{
  "default_workflow_permissions": "read",
  "can_approve_pull_request_reviews": false
}'

echo "Protected 'release' environment (reviewer = the authenticated maintainer, v* tags only)"
ME_ID="$(gh api user --jq .id)"
ENV_BODY="$(jq -n --argjson id "$ME_ID" '{
  wait_timer: 5,
  prevent_self_review: false,
  reviewers: [ { type: "User", id: $id } ],
  deployment_branch_policy: { protected_branches: false, custom_branch_policies: true } }')"
run gh api --method PUT "repos/$REPO/environments/release" --input - <<<"$ENV_BODY"
run gh api --method POST "repos/$REPO/environments/release/deployment-branch-policies" \
  -f name='v*' -f type='tag' || true

cat <<'EOF'

Manual steps that the API does not cover well (Settings in the web UI):
  * Settings → Actions → General → "Require approval for all outside collaborators".
  * Organisation → Settings → Authentication security → require two-factor authentication.
  * Settings → Code security → CodeQL "Advanced" (this repo ships its own codeql.yml).
EOF
[[ $APPLY -eq 1 ]] || echo "Nothing was changed (dry run). Re-run with --apply."
