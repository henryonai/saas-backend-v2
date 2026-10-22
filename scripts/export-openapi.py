"""
Writes each module's OpenAPI document to docs/openapi/<module>.json, for the
frontend (API clients, docs). Generated from the routes and Pydantic models,
so it never drifts from the code:

    uv run python scripts/export-openapi.py
"""
import importlib
import json
from pathlib import Path

# Modules with an HTTP API (the assistant streams from AgentCore instead: see its schemas.py).
MODULES = sorted(p.name for p in Path("modules").iterdir() if (p / "src" / p.name / "handler.py").is_file())
OUTPUT = Path("docs/openapi")

OUTPUT.mkdir(parents=True, exist_ok=True)
for module in MODULES:
    app = importlib.import_module(f"{module}.handler").app
    schema = json.loads(app.get_openapi_json_schema())
    (OUTPUT / f"{module}.json").write_text(json.dumps(schema, indent=2) + "\n")
    print(f"{OUTPUT / f'{module}.json'}: {len(schema['paths'])} paths")
