"""Regenerate the shared API contract, contract/openapi.json, from the backend's routes and models.

Run from backend/ after changing an API route or schema:

    uv run python -m tests.export_openapi

The backend test test_contract.py fails while the file is out of date, and the frontend test
tests/contract.test.ts checks api.ts and its fixtures against it.
"""

import json
from pathlib import Path

import dotenv

# Importing the app would otherwise load backend/.env; the schema doesn't depend on it
dotenv.load_dotenv = lambda *args, **kwargs: False  # noqa: E731

CONTRACT_PATH = Path(__file__).resolve().parents[2] / "contract" / "openapi.json"


def render_schema() -> str:
    from app.main import app

    return json.dumps(app.openapi(), indent=2) + "\n"


def main() -> None:
    CONTRACT_PATH.parent.mkdir(exist_ok=True)
    CONTRACT_PATH.write_text(render_schema())
    print(f"Wrote {CONTRACT_PATH}")


if __name__ == "__main__":
    main()
