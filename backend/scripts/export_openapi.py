"""Export the FastAPI OpenAPI contract for frontend type generation."""

import json
import sys
from pathlib import Path

from carrier_pool.main import app


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: export_openapi.py OUTPUT_PATH")
    output = Path(sys.argv[1])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
