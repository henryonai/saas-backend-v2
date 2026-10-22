#!/usr/bin/env bash
# Writes web/.env.local from a deployed stack's outputs, so the app talks to it:
#
#   ./scripts/env-from-stack.sh            # the dev stack (saas-dev)
#   ./scripts/env-from-stack.sh saas-prod
#
# The region is AWS_REGION, or the one in samconfig.toml.
set -euo pipefail

stack="${1:-saas-dev}"
cd "$(dirname "$0")/.."
region="${AWS_REGION:-$(sed -n 's/^region = "\(.*\)"/\1/p' ../samconfig.toml | head -1)}"

output() {
  local value
  value=$(aws cloudformation describe-stacks --stack-name "$stack" --region "$region" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue | [0]" --output text)
  if [[ -z "$value" || "$value" == "None" ]]; then
    echo "The $stack stack has no $1 output" >&2
    exit 1
  fi
  echo "$value"
}

api_url=$(output ApiUrl)
user_pool_id=$(output UserPoolId)
client_id=$(output UserPoolClientId)

cat > .env.local <<ENV
# Written by scripts/env-from-stack.sh from the $stack stack ($region).
VITE_API_URL=$api_url
VITE_USER_POOL_ID=$user_pool_id
VITE_USER_POOL_CLIENT_ID=$client_id
ENV
echo "Wrote web/.env.local from $stack ($region)"
