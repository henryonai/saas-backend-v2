# Sourced by env-from-stack.sh and deploy.sh: the stack's region and outputs.
# The region is AWS_REGION, or the one in samconfig.toml.

region="${AWS_REGION:-$(sed -n 's/^region = "\(.*\)"/\1/p' "$(dirname "${BASH_SOURCE[0]}")/../../samconfig.toml" | head -1)}"

# stack_output <stack> <output key>: the output's value, or exit if the stack has none.
stack_output() {
  local value
  value=$(aws cloudformation describe-stacks --stack-name "$1" --region "$region" \
    --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue | [0]" --output text)
  if [[ -z "$value" || "$value" == "None" ]]; then
    echo "The $1 stack has no $2 output (deploy the backend first: sam deploy)" >&2
    exit 1
  fi
  echo "$value"
}

# stack_output_or_empty <stack> <output key>: the output's value, or nothing if the stack has none
# (for optional parts of the stack, such as the assistant).
stack_output_or_empty() {
  local value
  value=$(aws cloudformation describe-stacks --stack-name "$1" --region "$region" \
    --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue | [0]" --output text)
  [[ "$value" == "None" ]] || echo "$value"
}
