from datetime import date

from pkg_mcp.formatting import render_context
from pkg_mcp.repository import ContextEdge, ContextGraph
from pkg_mcp.schema import EntityRef, EntityType, NoteRef, RelationRef, RelationType


def _graph() -> ContextGraph:
    center = EntityRef(
        type=EntityType.PROJECT,
        name="orbit",
        aliases=["오르빗"],
        properties={"status": "active"},
        created_at="2026-10-01T00:00:00Z",
        updated_at="2026-10-02T00:00:00Z",
    )
    return ContextGraph(
        center=center,
        edges=[
            ContextEdge(
                RelationRef(
                    from_entity="Person:Alex",
                    type=RelationType.BUILT,
                    to_entity="Project:orbit",
                    properties={"role": "owner"},
                    valid_from=date(2026, 10, 1),
                ),
                touches_center=True,
            ),
            ContextEdge(
                RelationRef(
                    from_entity="Person:Alex",
                    type=RelationType.KNOWS,
                    to_entity="Technology:Python",
                    provenance="inferred",
                ),
                touches_center=False,
            ),
        ],
        notes=[
            NoteRef(
                id="n1",
                content="Chose Neo4j for traversal queries",
                source="conversation",
                about=["Project:orbit", "Technology:Neo4j"],
                created_at="2026-10-03T10:00:00Z",
            )
        ],
    )


def test_render_context_sections() -> None:
    text = render_context(_graph())
    assert text.startswith("# Project: orbit")
    assert "aliases: 오르빗" in text
    assert "- status: active" in text
    assert "- Person:Alex -BUILT {role: owner}-> **this** (since 2026-10-01)" in text
    assert "## 2-hop relations (1)" in text
    assert "[inferred]" in text
    assert "2026-10-03 [conversation] Chose Neo4j" in text
    assert "also about Technology:Neo4j" in text


def test_render_context_without_relations() -> None:
    graph = _graph()
    graph.edges = []
    assert "## Relations (0)\n- none" in render_context(graph)
