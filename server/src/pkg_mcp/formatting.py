"""Render a ContextGraph as compact Markdown an LLM can read in one pass."""

from __future__ import annotations

from datetime import UTC, datetime, tzinfo

from pkg_mcp.repository import ContextGraph
from pkg_mcp.schema import NoteRef, RelationRef

# Long notes (a verbatim cover letter can be 5,000 chars) are cut to this in
# get_context so one lookup does not cost thousands of tokens; get_note has the rest.
NOTE_PREVIEW_CHARS = 300


def local_time(timestamp: str | None, tz: tzinfo, fmt: str = "%Y-%m-%d %H:%M %Z") -> str:
    """Show a stored UTC timestamp in the user's time zone."""
    if not timestamp:
        return "?"
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        return timestamp
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(tz).strftime(fmt)


def _fmt_note(note: NoteRef, center_label: str, tz: tzinfo, full: bool) -> str:
    others = [a for a in note.about if a != center_label]
    also = f" (also about {', '.join(others)})" if others else ""
    body = note.content
    if not full and len(body) > NOTE_PREVIEW_CHARS:
        body = (
            f"{body[:NOTE_PREVIEW_CHARS].rstrip()}… [{len(note.content)} chars, "
            "get_note for the full text]"
        )
    day = local_time(note.created_at, tz, "%Y-%m-%d")
    return f"- {day} [{note.source}] {body}{also} (note {note.id})"


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


def render_context(graph: ContextGraph, tz: tzinfo = UTC, full_notes: bool = False) -> str:
    c = graph.center
    center_label = f"{c.type.value}:{c.name}"
    lines = [f"# {c.type.value}: {c.name}"]
    if c.aliases:
        lines.append(f"aliases: {', '.join(c.aliases)}")
    for key, value in sorted(c.properties.items()):
        lines.append(f"- {key}: {_fmt_value(value)}")
    if c.provenance == "inferred":
        lines.append("- (inferred by an assistant, not stated by the user)")
    lines.append(
        f"_created {local_time(c.created_at, tz)}, updated {local_time(c.updated_at, tz)}_"
    )

    direct = [e.relation for e in graph.edges if e.touches_center]
    indirect = [e.relation for e in graph.edges if not e.touches_center]
    lines += ["", f"## Relations ({len(direct)})"]
    lines += [_fmt_relation(r, center_label) for r in direct] or ["- none"]
    if indirect:
        lines += ["", f"## 2-hop relations ({len(indirect)})"]
        lines += [_fmt_relation(r, center_label) for r in indirect]
    if graph.truncated:
        lines.append("- … more relations omitted; ask about a neighbor directly.")

    total = max(graph.note_total, len(graph.notes))
    if graph.notes:
        shown = f"{len(graph.notes)} of {total}" if total > len(graph.notes) else f"{total}"
        lines += ["", f"## Notes ({shown}, newest first)"]
        lines += [_fmt_note(n, center_label, tz, full_notes) for n in graph.notes]
        if total > len(graph.notes):
            lines.append(f"- … {total - len(graph.notes)} older note(s); raise note_limit.")
    elif total:
        lines += ["", f"## Notes ({total}, not loaded; raise note_limit to see them)"]

    if graph.revisions:
        lines += ["", "## Property history"]
        for rev in graph.revisions:
            changes = "; ".join(
                f"{k}: {_fmt_value(v['before'])} -> {_fmt_value(v['after'])}"
                for k, v in rev["changes"].items()
            )
            lines.append(f"- {local_time(rev['changed_at'], tz, '%Y-%m-%d')} {changes}")

    return "\n".join(lines)
