"""Export the core schema as JSON Schema files.

Usage: `uv run python scripts/export_jsonschema.py [--check]`

With --check, exits non-zero if the committed files are out of date.
"""

import json
import sys
from pathlib import Path

from schema import SCHEMA_VERSION
from schema.models import ALL_MODELS

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://github.com/kdniem/SCLiveSim/schema"


def output_dir() -> Path:
    major, minor, _ = SCHEMA_VERSION.split(".")
    return ROOT / "schema" / "jsonschema" / f"v{major}.{minor}"


def render() -> dict[str, str]:
    files: dict[str, str] = {}
    for name, model in ALL_MODELS.items():
        doc = model.model_json_schema(mode="serialization")
        doc = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"{BASE_URL}/v{SCHEMA_VERSION}/{name}.json",
            "x-schema-version": SCHEMA_VERSION,
            **doc,
        }
        files[f"{name}.json"] = json.dumps(doc, indent=2, sort_keys=False) + "\n"
    return files


def main() -> int:
    check = "--check" in sys.argv[1:]
    out = output_dir()
    stale = []
    for filename, content in render().items():
        path = out / filename
        if check:
            if not path.exists() or path.read_text() != content:
                stale.append(filename)
        else:
            out.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
    if stale:
        print(f"JSON Schema out of date: {', '.join(stale)}. Run scripts/export_jsonschema.py")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
