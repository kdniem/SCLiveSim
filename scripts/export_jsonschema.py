"""Export the core schema as JSON Schema files.

Usage: `uv run python scripts/export_jsonschema.py [--check]`

With --check, exits non-zero if the committed files are out of date.
"""

import json
import sys
from pathlib import Path

from engine.simulation.spec import SPEC_VERSION, ScenarioSpec
from schema import SCHEMA_VERSION
from schema.models import ALL_MODELS

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://github.com/kdniem/SCLiveSim/schema"


def output_dir() -> Path:
    major, minor, _ = SCHEMA_VERSION.split(".")
    return ROOT / "schema" / "jsonschema" / f"v{major}.{minor}"


def render() -> dict[Path, str]:
    """Return generated files by path: core schema and the scenario format."""
    files: dict[Path, str] = {}
    for name, model in ALL_MODELS.items():
        doc = model.model_json_schema(mode="serialization")
        doc = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"{BASE_URL}/v{SCHEMA_VERSION}/{name}.json",
            "x-schema-version": SCHEMA_VERSION,
            **doc,
        }
        files[output_dir() / f"{name}.json"] = json.dumps(doc, indent=2) + "\n"
    scenario = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"{BASE_URL.rsplit('/', 1)[0]}/scenarios/scenario.schema.json",
        "x-spec-version": SPEC_VERSION,
        **ScenarioSpec.model_json_schema(mode="validation"),
    }
    files[ROOT / "scenarios" / "scenario.schema.json"] = json.dumps(scenario, indent=2) + "\n"
    return files


def main() -> int:
    check = "--check" in sys.argv[1:]
    stale = []
    for path, content in render().items():
        if check:
            if not path.exists() or path.read_text() != content:
                stale.append(str(path.relative_to(ROOT)))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
    if stale:
        print(f"JSON Schema out of date: {', '.join(stale)}. Run scripts/export_jsonschema.py")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
