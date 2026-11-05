"""The concept map is built by code from stored topics and mastery."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.learning.concept_map import _safe_label, build_concept_map


def topic(id_, name, document_id="d1", first_page=1, prerequisite_ids=()):
    return SimpleNamespace(
        id=id_,
        name=name,
        slug=name.lower().replace(" ", "-"),
        document_id=document_id,
        first_page=first_page,
        prerequisite_ids=list(prerequisite_ids),
    )


def mastery(slug, probability, observations=5):
    return SimpleNamespace(topic_slug=slug, probability=probability, observations=observations)


def test_document_order_and_prerequisites_both_become_edges():
    topics = [
        topic("a", "Convolution", first_page=2),
        topic("b", "Padding", first_page=4, prerequisite_ids=["a"]),
        topic("c", "Pooling", first_page=6),
        topic("d", "Backprop", document_id="d2", first_page=1),
    ]
    result = build_concept_map(topics, [])
    kinds = {(e.source, e.target): e.kind for e in result.edges}
    # The stored prerequisite wins over the document-order edge for the same pair.
    assert kinds[("t0", "t1")] == "prerequisite"
    assert kinds[("t1", "t2")] == "next"
    # Different documents are not chained together.
    assert ("t2", "t3") not in kinds
    assert result.mermaid.startswith("flowchart LR")
    assert 't0["Convolution"]' in result.mermaid
    assert "t0 --> t1" in result.mermaid and "t1 -.-> t2" in result.mermaid


def test_mastery_colours_nodes_and_points_at_where_to_start():
    topics = [
        topic("a", "Convolution", first_page=1),
        topic("b", "Padding", first_page=3),
        topic("c", "Pooling", first_page=5),
    ]
    rows = [
        mastery("convolution", 0.3),  # weak, no predecessor → start here
        mastery("padding", 0.4),  # weak, but convolution is weak first
        mastery("pooling", 0.9, observations=1),  # too few observations → unknown
    ]
    result = build_concept_map(topics, rows)
    labels = {n.slug: n.mastery_label for n in result.nodes}
    assert labels == {"convolution": "weak", "padding": "weak", "pooling": "unknown"}
    assert result.revisit_first == ["convolution"]
    assert "class t0,t1 weak" in result.mermaid
    assert "class t2 unknown" in result.mermaid


def test_topic_names_cannot_inject_mermaid_syntax():
    assert _safe_label('Evil"]; click t0 call alert()') == "Evil click t0 call alert"
    assert _safe_label("x" * 100).endswith("…")
    assert _safe_label("   ") == "Topic"
    result = build_concept_map([topic("a", 'Pad"ding [1]')], [])
    assert '"' not in result.mermaid.split("\n")[1].replace('["', "").replace('"]', "")


def test_empty_course_is_an_empty_map():
    result = build_concept_map([], [])
    assert result.nodes == [] and result.mermaid == ""


def test_the_route_serves_the_indexed_course(client: TestClient, indexed_document):
    course_id = indexed_document["document"]["course_id"]
    response = client.get(f"/api/courses/{course_id}/concept-map")
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["nodes"]) >= 5
    assert all(n["mastery_label"] == "unknown" for n in body["nodes"])
    assert any(e["kind"] == "next" for e in body["edges"])
    assert body["mermaid"].startswith("flowchart LR")
    assert client.get("/api/courses/nope/concept-map").status_code == 404
