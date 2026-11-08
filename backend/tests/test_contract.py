"""The shared API contract (contract/openapi.json) must match what the backend actually serves.

The frontend suite validates api.ts requests and its response fixtures against that file, so this
test is what makes a backend change (e.g. renaming a field) show up on the frontend side.
"""

import json

from .export_openapi import CONTRACT_PATH, render_schema


def test_contract_snapshot_matches_the_backend():
    assert CONTRACT_PATH.exists(), "Missing contract/openapi.json; run: uv run python -m tests.export_openapi"
    committed = json.loads(CONTRACT_PATH.read_text())
    current = json.loads(render_schema())
    assert committed == current, (
        "The backend API changed but contract/openapi.json didn't. Regenerate it with "
        "`uv run python -m tests.export_openapi` (from backend/), then fix the frontend types, "
        "fixtures, and calls that tests/contract.test.ts reports."
    )

