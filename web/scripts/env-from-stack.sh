#!/usr/bin/env bash
# Writes web/.env.local from a deployed stack's outputs, so `pnpm dev` talks to it:
#
#   ./scripts/env-from-stack.sh            # the dev stack (saas-dev)
#   ./scripts/env-from-stack.sh saas-prod
set -euo pipefail

stack="${1:-saas-dev}"
cd "$(dirname "$0")/.."
source scripts/stack.sh

api_url=$(stack_output "$stack" ApiUrl)
user_pool_id=$(stack_output "$stack" UserPoolId)
client_id=$(stack_output "$stack" UserPoolClientId)

cat > .env.local <<ENV
# Written by scripts/env-from-stack.sh from the $stack stack ($region).
VITE_API_URL=$api_url
VITE_USER_POOL_ID=$user_pool_id
VITE_USER_POOL_CLIENT_ID=$client_id
ENV
echo "Wrote web/.env.local from $stack ($region)"
