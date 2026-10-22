#!/usr/bin/env bash
# `sam build` installs dependencies with pip from requirements.txt files.
# This regenerates them from uv.lock, so Lambda gets exactly the versions the
# tests ran with. Run it after changing any dependency, before `sam build`.
set -euo pipefail
cd "$(dirname "$0")/.."

export_requirements() {  # <package> <output file> [extra uv export options]
  local package=$1 output=$2
  shift 2
  {
    echo "# Generated from uv.lock by scripts/export-requirements.sh. Do not edit: change pyproject.toml instead."
    uv export --locked --package "$package" --no-emit-workspace --no-dev --no-hashes --no-header "$@"
  } > "$output"
}

export_requirements saas-shared layers/shared/layer/requirements.txt

for module_dir in modules/*/; do
  module=$(basename "$module_dir")
  # The shared layer already provides saas-shared and everything it depends on.
  export_requirements "saas-$module" "${module_dir}src/requirements.txt" --prune saas-shared
done
