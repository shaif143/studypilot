"""The course concept map: topics, how they lead into each other, and where the
student stands on each.

Built by code from what the course already stores — no model call — so the
map is the same every time and never shows a concept the material does not
contain. Edges come from two sources:

* ``prerequisite`` — a topic's stored ``prerequisite_ids``.
* ``next`` — the order topics appear in a document: slide 3's topic leads to
  slide 5's. Lecturers teach in an order for a reason, and without these
  edges a course with no stated prerequisites would be a map of islands.

Each node carries the student's mastery label for that topic (``weak``,
``developing``, ``strong``, or ``unknown`` while there are too few
observations), so the map answers the question the slide reader cannot:
"which earlier concept should I go back to?" — a weak topic whose
predecessor is also weak points at the predecessor.

The output is a Mermaid ``flowchart`` plus the structured nodes and edges it
was drawn from. The frontend renders the Mermaid text in strict mode; nothing
the student uploaded reaches the diagram except topic names, and those are
quoted and stripped of the characters Mermaid treats as syntax.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import asdict, dataclass, field

from app.learning.mastery import _label
from app.storage.models import MasteryEstimate, Topic

#: Mermaid reads these inside a label even when quoted.
_LABEL_UNSAFE = re.compile(r'["`#;<>{}\[\]()|\\]')
_MAX_LABEL = 48


@dataclass
class MapNode:
    id: str
    topic_id: str
    slug: str
    name: str
    document_id: str | None
    first_page: int
    mastery_label: str = "unknown"
    mastery_probability: float | None = None
    observations: int = 0


@dataclass
class MapEdge:
    source: str
    target: str
    #: "prerequisite" from stored prerequisites, "next" from document order.
    kind: str


@dataclass
class ConceptMap:
    nodes: list[MapNode] = field(default_factory=list)
    edges: list[MapEdge] = field(default_factory=list)
    mermaid: str = ""
    #: Topics worth revisiting first: weak, and every predecessor is not weak.
    revisit_first: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "nodes": [asdict(n) for n in self.nodes],
            "edges": [asdict(e) for e in self.edges],
            "mermaid": self.mermaid,
            "revisit_first": self.revisit_first,
        }


def build_concept_map(topics: list[Topic], mastery: list[MasteryEstimate]) -> ConceptMap:
    if not topics:
        return ConceptMap()
    by_slug = {m.topic_slug: m for m in mastery}
    nodes: list[MapNode] = []
    node_id_for_topic: dict[str, str] = {}
    for index, topic in enumerate(topics):
        node_id = f"t{index}"
        node_id_for_topic[topic.id] = node_id
        estimate = by_slug.get(topic.slug)
        nodes.append(
            MapNode(
                id=node_id,
                topic_id=topic.id,
                slug=topic.slug,
                name=topic.name,
                document_id=topic.document_id,
                first_page=topic.first_page,
                mastery_label=_label(estimate.probability, estimate.observations)
                if estimate
                else "unknown",
                mastery_probability=round(estimate.probability, 3) if estimate else None,
                observations=estimate.observations if estimate else 0,
            )
        )

    edges: list[MapEdge] = []
    seen: set[tuple[str, str]] = set()

    def add(source: str, target: str, kind: str) -> None:
        if source == target or (source, target) in seen:
            return
        seen.add((source, target))
        edges.append(MapEdge(source=source, target=target, kind=kind))

    for topic in topics:
        for prerequisite_id in topic.prerequisite_ids or []:
            if prerequisite_id in node_id_for_topic:
                add(node_id_for_topic[prerequisite_id], node_id_for_topic[topic.id], "prerequisite")

    # Document order: consecutive topics within one document.
    by_document: dict[str | None, list[Topic]] = {}
    for topic in topics:
        by_document.setdefault(topic.document_id, []).append(topic)
    for document_topics in by_document.values():
        ordered = sorted(document_topics, key=lambda t: (t.first_page, t.name))
        for earlier, later in itertools.pairwise(ordered):
            add(node_id_for_topic[earlier.id], node_id_for_topic[later.id], "next")

    concept_map = ConceptMap(nodes=nodes, edges=edges)
    concept_map.revisit_first = _revisit_first(concept_map)
    concept_map.mermaid = _render(concept_map)
    return concept_map


def _revisit_first(concept_map: ConceptMap) -> list[str]:
    """Weak topics with no weak predecessor: the right place to start."""
    label = {n.id: n.mastery_label for n in concept_map.nodes}
    predecessors: dict[str, list[str]] = {n.id: [] for n in concept_map.nodes}
    for edge in concept_map.edges:
        predecessors[edge.target].append(edge.source)
    return [
        n.slug
        for n in concept_map.nodes
        if n.mastery_label == "weak" and not any(label[p] == "weak" for p in predecessors[n.id])
    ]


def _safe_label(name: str) -> str:
    text = _LABEL_UNSAFE.sub(" ", name).strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) > _MAX_LABEL:
        text = text[: _MAX_LABEL - 1].rstrip() + "…"
    return text or "Topic"


def _render(concept_map: ConceptMap) -> str:
    lines = ["flowchart LR"]
    for node in concept_map.nodes:
        lines.append(f'  {node.id}["{_safe_label(node.name)}"]')
    for edge in concept_map.edges:
        arrow = "-->" if edge.kind == "prerequisite" else "-.->"
        lines.append(f"  {edge.source} {arrow} {edge.target}")
    lines.append("  classDef weak fill:#fde2e1,stroke:#c0392b,color:#7a1f16")
    lines.append("  classDef developing fill:#fff3cd,stroke:#b7791f,color:#6b4a00")
    lines.append("  classDef strong fill:#dcf5e3,stroke:#2e7d4f,color:#1b4d31")
    lines.append("  classDef unknown fill:#eef0f3,stroke:#8a94a6,color:#3b4252")
    for label in ("weak", "developing", "strong", "unknown"):
        ids = [n.id for n in concept_map.nodes if n.mastery_label == label]
        if ids:
            lines.append(f"  class {','.join(ids)} {label}")
    return "\n".join(lines)
