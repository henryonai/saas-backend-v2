"""
Writes each module's OpenAPI document to docs/openapi/<module>.json, for the
frontend (API clients, docs). Generated from the routes and Pydantic models,
so it never drifts from the code:

    uv run python scripts/export-openapi.py
"""
import importlib
import json
from pathlib import Path

# Modules with an HTTP API. The assistant's chat streams from AgentCore over AG-UI (its schemas.py);
# only its conversations routes (/assistant/conversations) are in its document.
MODULES = sorted(p.name for p in Path("modules").iterdir() if (p / "src" / p.name / "handler.py").is_file())
OUTPUT = Path("docs/openapi")

OUTPUT.mkdir(parents=True, exist_ok=True)
for stale in OUTPUT.glob("*.json"):   # a removed module leaves no document behind
    stale.unlink()
for module in MODULES:
    app = importlib.import_module(f"{module}.handler").app
    schema = json.loads(app.get_openapi_json_schema())
    (OUTPUT / f"{module}.json").write_text(json.dumps(schema, indent=2) + "\n")
    print(f"{OUTPUT / f'{module}.json'}: {len(schema['paths'])} paths")
