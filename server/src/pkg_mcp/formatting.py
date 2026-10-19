"""Render a ContextGraph as compact Markdown an LLM can read in one pass."""

from __future__ import annotations

from pkg_mcp.repository import ContextGraph
from pkg_mcp.schema import RelationRef


def _fmt_value(value: object) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def _fmt_relation(rel: RelationRef, center_label: str) -> str:
    def side(label: str) -> str:
        return "**this**" if label == center_label else label

    props = ""
    if rel.properties:
        props = " {" + ", ".join(f"{k}: {_fmt_value(v)}" for k, v in rel.properties.items()) + "}"
    period = ""
    if rel.valid_to is not None:
        period = f" ({rel.valid_from or '?'} ~ {rel.valid_to}, ended)"
    elif rel.valid_from is not None:
        period = f" (since {rel.valid_from})"
    inferred = " [inferred]" if rel.provenance == "inferred" else ""
    return (
        f"- {side(rel.from_entity)} -{rel.type.value}{props}-> {side(rel.to_entity)}"
        f"{period}{inferred}"
    )


def render_context(graph: ContextGraph) -> str:
    c = graph.center
    center_label = f"{c.type.value}:{c.name}"
    lines = [f"# {c.type.value}: {c.name}"]
    if c.aliases:
        lines.append(f"aliases: {', '.join(c.aliases)}")
    for key, value in sorted(c.properties.items()):
        lines.append(f"- {key}: {_fmt_value(value)}")
    if c.provenance == "inferred":
        lines.append("- (inferred by an assistant, not stated by the user)")
    lines.append(f"_created {c.created_at}, updated {c.updated_at}_")

    direct = [e.relation for e in graph.edges if e.touches_center]
    indirect = [e.relation for e in graph.edges if not e.touches_center]
    lines += ["", f"## Relations ({len(direct)})"]
    lines += [_fmt_relation(r, center_label) for r in direct] or ["- none"]
    if indirect:
        lines += ["", f"## 2-hop relations ({len(indirect)})"]
        lines += [_fmt_relation(r, center_label) for r in indirect]
    if graph.truncated:
        lines.append("- … more relations omitted; ask about a neighbor directly.")

    if graph.notes:
        lines += ["", f"## Notes ({len(graph.notes)}, newest first)"]
        for note in graph.notes:
            day = (note.created_at or "")[:10]
            others = [a for a in note.about if a != center_label]
            also = f" (also about {', '.join(others)})" if others else ""
            lines.append(f"- {day} [{note.source}] {note.content}{also}")

    if graph.revisions:
        lines += ["", "## Property history"]
        for rev in graph.revisions:
            changes = "; ".join(
                f"{k}: {_fmt_value(v['before'])} -> {_fmt_value(v['after'])}"
                for k, v in rev["changes"].items()
            )
            lines.append(f"- {rev['changed_at'][:10]} {changes}")

    return "\n".join(lines)
