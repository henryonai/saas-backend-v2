#!/usr/bin/env bash
# `sam build` installs dependencies with pip from requirements.txt files.
# This regenerates them from uv.lock, so Lambda gets exactly the versions the
# tests ran with. Run it after changing any dependency, before `sam build`.
#
#   ./scripts/export-requirements.sh           # write the files
#   ./scripts/export-requirements.sh --check   # fail if they are out of date (CI)
set -euo pipefail
cd "$(dirname "$0")/.."

check=false
[[ "${1:-}" == "--check" ]] && check=true
stale=()

export_requirements() {  # <package> <output file> [extra uv export options]
  local package=$1 output=$2
  shift 2
  local content
  content=$(
    echo "# Generated from uv.lock by scripts/export-requirements.sh. Do not edit: change pyproject.toml instead."
    uv export --locked --package "$package" --no-emit-workspace --no-dev --no-hashes --no-header "$@"
  )
  if $check; then
    [[ "$content" == "$(cat "$output" 2>/dev/null)" ]] || stale+=("$output")
  else
    echo "$content" > "$output"
  fi
}

export_requirements saas-shared layers/shared/layer/requirements.txt

for module_dir in modules/*/; do
  module=$(basename "$module_dir")
  # Runs on AgentCore Runtime, not Lambda: scripts/package-assistant.py bundles it from uv.lock.
  [[ "$module" == assistant ]] && continue
  # The shared layer already provides saas-shared and everything it depends on.
  export_requirements "saas-$module" "${module_dir}src/requirements.txt" --prune saas-shared
done

if $check && ((${#stale[@]})); then
  echo "Out of date (run ./scripts/export-requirements.sh): ${stale[*]}" >&2
  exit 1
fi
