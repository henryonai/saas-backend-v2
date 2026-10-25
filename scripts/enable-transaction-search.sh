#!/usr/bin/env bash
# Turns on CloudWatch Transaction Search, once per account and region: X-Ray
# then keeps spans in the CloudWatch Logs group aws/spans. The assistant's
# traces need it: AgentCore Runtime sends them to X-Ray's OTLP endpoint, which
# refuses them until then ("The OTLP API is supported with CloudWatch Logs as
# a Trace Segment Destination"). Not in template.yaml: it is one setting for
# the whole account, and an account can hold several stacks.
#
#   ./scripts/enable-transaction-search.sh <region> [days to keep spans, default 7]
#
# Spans carry no messages: ADOT logs those to the runtime's log group instead
# (template.yaml, AWS_GENAI_INSTRUMENTATION).
set -euo pipefail

region=${1:?usage: $0 <region> [days to keep spans]}
days=${2:-7}
account=$(aws sts get-caller-identity --query Account --output text)

# X-Ray may write spans (and Application Signals' data) to CloudWatch Logs.
aws logs put-resource-policy --region "$region" --policy-name TransactionSearchXRayAccess --policy-document "$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "TransactionSearchXRayAccess",
    "Effect": "Allow",
    "Principal": {"Service": "xray.amazonaws.com"},
    "Action": "logs:PutLogEvents",
    "Resource": [
      "arn:aws:logs:$region:$account:log-group:aws/spans:*",
      "arn:aws:logs:$region:$account:log-group:/aws/application-signals/data:*"
    ],
    "Condition": {
      "ArnLike": {"aws:SourceArn": "arn:aws:xray:$region:$account:*"},
      "StringEquals": {"aws:SourceAccount": "$account"}
    }
  }]
}
EOF
)" > /dev/null

aws xray update-trace-segment-destination --region "$region" --destination CloudWatchLogs > /dev/null

# Every span is kept in aws/spans; 1% are also indexed as trace summaries (free).
aws xray update-indexing-rule --region "$region" --name Default \
  --rule '{"Probabilistic": {"DesiredSamplingPercentage": 1}}' > /dev/null

# aws/spans would otherwise keep spans forever.
aws logs create-log-group --region "$region" --log-group-name aws/spans 2> /dev/null || true
aws logs put-retention-policy --region "$region" --log-group-name aws/spans --retention-in-days "$days"

aws xray get-trace-segment-destination --region "$region" --output text
