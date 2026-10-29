#!/usr/bin/env bash
# Builds the app for a stack and publishes it on that stack's CloudFront (the
# WebUrl output). Deploy the backend first (sam deploy): it creates the bucket.
#
#   ./scripts/deploy.sh            # the dev stack (saas-dev)
#   ./scripts/deploy.sh saas-prod
set -euo pipefail

stack="${1:-saas-dev}"
cd "$(dirname "$0")/.."
source scripts/stack.sh

bucket=$(stack_output "$stack" WebBucketName)
distribution=$(stack_output "$stack" WebDistributionId)
web_url=$(stack_output "$stack" WebUrl)

# The stack's settings for this build only: they win over .env.local, which stays as it is.
# The app calls the API on its own address, /api, which CloudFront sends to the HTTP API.
echo "Building for $stack"
VITE_API_URL=/api \
VITE_USER_POOL_ID=$(stack_output "$stack" UserPoolId) \
VITE_USER_POOL_CLIENT_ID=$(stack_output "$stack" UserPoolClientId) \
VITE_ASSISTANT_URL=$(stack_output_or_empty "$stack" AssistantUrl) \
  pnpm build

# Hashed files never change, so browsers keep them for a year. They go up first and
# are never deleted, so a page that is still open can load the chunks it expects.
echo "Uploading to s3://$bucket"
aws s3 sync dist/assets "s3://$bucket/assets" --region "$region" --only-show-errors \
  --cache-control "public, max-age=31536000, immutable"
# Everything else (index.html) is checked on every visit, so a deploy shows at once.
aws s3 sync dist "s3://$bucket" --region "$region" --only-show-errors --delete \
  --exclude "assets/*" --cache-control "no-cache"

# One path, so it stays within CloudFront's free 1,000 a month.
aws cloudfront create-invalidation --distribution-id "$distribution" --paths "/*" \
  --query "Invalidation.Id" --output text > /dev/null
echo "Published: $web_url"
