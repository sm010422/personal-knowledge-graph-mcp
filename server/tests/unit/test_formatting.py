from datetime import date
from zoneinfo import ZoneInfo

from pkg_mcp.formatting import NOTE_PREVIEW_CHARS, local_time, render_context
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


def test_long_note_is_previewed_with_its_id() -> None:
    graph = _graph()
    graph.notes[0].content = "가" * (NOTE_PREVIEW_CHARS + 50)
    graph.note_total = 4
    text = render_context(graph)
    assert "가" * NOTE_PREVIEW_CHARS + "… [350 chars, get_note for the full text]" in text
    assert "(note n1)" in text
    assert "## Notes (1 of 4, newest first)" in text
    assert "가" * (NOTE_PREVIEW_CHARS + 50) in render_context(graph, full_notes=True)


def test_dates_follow_display_timezone() -> None:
    # 01:30 KST on the 4th is still the 3rd in UTC.
    seoul = ZoneInfo("Asia/Seoul")
    assert local_time("2026-11-03T16:30:00.000000000+00:00", seoul) == "2026-11-04 01:30 KST"
    graph = _graph()
    graph.notes[0].created_at = "2026-11-03T16:30:00Z"
    assert "- 2026-11-04 [conversation]" in render_context(graph, seoul)
    assert "- 2026-11-03 [conversation]" in render_context(graph)
    assert local_time(None, seoul) == "?"
