"""The committed OpenAPI snapshot must match the running application.

`fixtures/openapi.json` is what the frontend's contract test checks the API
client against. If a route is renamed here and the snapshot is not regenerated,
that check would keep passing against a stale document — so this test closes
the loop from the backend side.

Regenerate with:  npm run api:schema
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import Settings
from app.main import create_app

SNAPSHOT = Path(__file__).resolve().parents[3] / "fixtures" / "openapi.json"


def current_schema(tmp_path: Path) -> dict:
    settings = Settings(data_dir=tmp_path / "schema", auth_disabled=True)
    schema = create_app(settings).openapi()
    schema.get("info", {}).pop("version", None)
    return schema


@pytest.fixture
def snapshot() -> dict:
    if not SNAPSHOT.is_file():
        pytest.skip(f"Missing {SNAPSHOT}. Run: npm run api:schema")
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def test_every_route_is_in_the_snapshot(snapshot: dict, tmp_path: Path):
    live = set(current_schema(tmp_path)["paths"])
    recorded = set(snapshot["paths"])

    missing = live - recorded
    assert not missing, (
        f"These routes are not in fixtures/openapi.json: {sorted(missing)}. Run: npm run api:schema"
    )


def test_no_route_was_removed_without_regenerating(snapshot: dict, tmp_path: Path):
    live = set(current_schema(tmp_path)["paths"])
    recorded = set(snapshot["paths"])

    stale = recorded - live
    assert not stale, (
        f"fixtures/openapi.json lists routes that no longer exist: {sorted(stale)}. "
        "Run: npm run api:schema"
    )


def test_methods_match_per_route(snapshot: dict, tmp_path: Path):
    live = current_schema(tmp_path)["paths"]
    for path, operations in snapshot["paths"].items():
        live_methods = set(live.get(path, {}))
        recorded_methods = set(operations)
        assert recorded_methods == live_methods, (
            f"Methods for {path} changed: snapshot {sorted(recorded_methods)} "
            f"vs live {sorted(live_methods)}. Run: npm run api:schema"
        )


def test_answer_response_keeps_its_honesty_fields(tmp_path: Path):
    """These fields are what let the UI avoid overclaiming; they are contract."""
    schemas = current_schema(tmp_path)["components"]["schemas"]
    answer = schemas["AnswerResponse"]["properties"]
    for field in ("grounded", "generative", "abstained", "evidence_sufficient", "provider"):
        assert field in answer, f"AnswerResponse lost its '{field}' field"


def test_question_payload_never_exposes_the_answer_key(tmp_path: Path):
    """A student must not be able to read the answers out of the quiz response."""
    schemas = current_schema(tmp_path)["components"]["schemas"]
    question = schemas["QuestionOut"]["properties"]
    for leaked in ("correct_option", "expected_answer", "rubric", "numeric_answer"):
        assert leaked not in question, f"QuestionOut would leak '{leaked}'"
    assert "provenance" in question
