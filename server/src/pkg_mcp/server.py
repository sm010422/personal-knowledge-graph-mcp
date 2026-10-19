"""MCP server: tool definitions and wiring.

Tool descriptions are written for the model that calls them — they say when to
use a tool, when not to, and what to call first. That text is the main lever
for how well an LLM uses this graph.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable
from datetime import date
from typing import Annotated, Any, Literal, TypeVar

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.lifespan import lifespan
from pydantic import Field

from pkg_mcp.config import Settings
from pkg_mcp.db import bootstrap_schema, create_driver
from pkg_mcp.errors import KnowledgeGraphError
from pkg_mcp.formatting import render_context
from pkg_mcp.repository import KnowledgeGraph
from pkg_mcp.schema import (
    DeleteResult,
    EntityRef,
    EntityType,
    NoteRef,
    Provenance,
    RelationResult,
    RelationType,
    SearchHit,
    UpsertResult,
)

T = TypeVar("T")

INSTRUCTIONS = """\
Long-term memory about the user, stored as a knowledge graph.

- Before answering questions about the user's projects, skills, decisions, career
  or schedule, call search or get_context instead of guessing.
- When the user states a durable fact about themselves, record it: search first to
  reuse an existing entity, then upsert_entity / add_relation / add_note.
- Never store health, financial, credential or ID-number information.
- Set provenance="inferred" when you are recording something the user implied
  rather than said.
"""

EntityName = Annotated[
    str,
    Field(
        description="Entity name or alias. Prefix with the type to disambiguate, "
        "e.g. 'Project:k3s-msa-infrastructure'."
    ),
]
ProvenanceArg = Annotated[
    Provenance,
    Field(description="'user' if the user said it, 'inferred' if you concluded it."),
]


def create_server(settings: Settings | None = None) -> FastMCP:
    @lifespan
    async def graph_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
        cfg = settings or Settings()  # type: ignore[call-arg]
        driver = create_driver(cfg)
        try:
            await driver.verify_connectivity()
            await bootstrap_schema(driver, cfg.neo4j_database)
            yield {"graph": KnowledgeGraph(driver, cfg.neo4j_database)}
        finally:
            await driver.close()

    mcp = FastMCP("personal-knowledge-graph", instructions=INSTRUCTIONS, lifespan=graph_lifespan)

    def graph(ctx: Context) -> KnowledgeGraph:
        return ctx.lifespan_context["graph"]

    async def call(op: Awaitable[T]) -> T:
        try:
            return await op
        except KnowledgeGraphError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(annotations={"idempotentHint": True})
    async def upsert_entity(
        ctx: Context,
        type: Annotated[EntityType, Field(description="Kind of thing being recorded.")],
        name: Annotated[str, Field(description="Canonical name, e.g. 'Neo4j', '코오롱베니트'.")],
        properties: Annotated[
            dict[str, Any],
            Field(
                description="Attributes to set. Keys are camelCase identifiers. Values are "
                "strings, numbers, booleans or flat lists. Set a value to null to remove it. "
                "Common keys: Project{status, summary, startedAt, repoUrl}, "
                "Technology{category}, Decision{rationale, alternatives, decidedAt}, "
                "Event{date, kind}, Organization{kind}."
            ),
        ] = {},
        aliases: Annotated[
            list[str],
            Field(description="Other spellings that should resolve to this entity."),
        ] = [],
        provenance: ProvenanceArg = "user",
    ) -> UpsertResult:
        """Create an entity, or update it if one with this name or alias already exists.

        Use when the user mentions a durable fact: a project, a technology they use,
        a decision and its reasoning, a company they applied to, an upcoming event.
        Call search first if the name might already exist under another spelling.
        Changed values are kept in history; nothing is silently overwritten.
        Upserting a soft-deleted name restores it. Do not use for one-off chat details.
        """
        return await call(graph(ctx).upsert_entity(type, name, properties, aliases, provenance))

    @mcp.tool
    async def add_relation(
        ctx: Context,
        from_entity: EntityName,
        relation_type: Annotated[
            RelationType,
            Field(
                description="BUILT Person->Project, USES Project->Technology, "
                "KNOWS Person->Technology, HAS_DECISION Project->Decision, "
                "APPLIED_TO/WORKED_AT/STUDIED_AT/MEMBER_OF Person->Organization, "
                "ATTENDED Person->Event, CONCERNS Event->Project|Organization|Decision, "
                "COLLABORATED_WITH Person->Person, RELATED_TO any->any (last resort)."
            ),
        ],
        to_entity: EntityName,
        properties: Annotated[
            dict[str, Any],
            Field(description="Edge attributes, e.g. {role: 'owner'} or {status: 'interview'}."),
        ] = {},
        valid_from: Annotated[
            date | None, Field(description="When this became true (YYYY-MM-DD). Default today.")
        ] = None,
        provenance: ProvenanceArg = "user",
        confidence: Annotated[
            float | None, Field(ge=0, le=1, description="Only for inferred relations.")
        ] = None,
    ) -> RelationResult:
        """Link two existing entities.

        Both entities must exist (upsert_entity first). If the same relation already
        holds with different properties — e.g. an application moving from
        status 'document' to 'interview' — the old one is closed and a new one opened,
        so the history is preserved. Same properties again is a no-op.
        """
        return await call(
            graph(ctx).add_relation(
                from_entity,
                relation_type,
                to_entity,
                properties,
                valid_from=valid_from,
                provenance=provenance,
                confidence=confidence,
            )
        )

    @mcp.tool
    async def end_relation(
        ctx: Context,
        from_entity: EntityName,
        relation_type: RelationType,
        to_entity: EntityName,
        valid_to: Annotated[
            date | None, Field(description="When it stopped being true. Default today.")
        ] = None,
    ) -> RelationResult:
        """Mark a relation as no longer true (left a company, stopped using a technology).

        The relation stays in history and is hidden from get_context by default.
        Prefer this over delete_entity when something merely ended.
        """
        return await call(
            graph(ctx).end_relation(from_entity, relation_type, to_entity, valid_to=valid_to)
        )

    @mcp.tool
    async def add_note(
        ctx: Context,
        content: Annotated[str, Field(description="One self-contained observation.")],
        about_entities: Annotated[
            list[str],
            Field(description="Names of existing entities this note is about (at least one)."),
        ],
        source: Annotated[
            str, Field(description="Where this came from, e.g. 'claude-code session'.")
        ] = "conversation",
        provenance: ProvenanceArg = "user",
    ) -> NoteRef:
        """Record a free-text observation that does not fit a property or relation.

        Good for reasons, preferences, lessons learned, or context around a decision.
        If the fact has a clear shape (a status, a date, a link between two things),
        use upsert_entity or add_relation instead so it can be queried.
        """
        return await call(graph(ctx).add_note(content, about_entities, source, provenance))

    @mcp.tool(annotations={"readOnlyHint": True})
    async def get_context(
        ctx: Context,
        entity_name: EntityName,
        depth: Annotated[
            Literal[1, 2],
            Field(description="1 = direct neighbors. 2 = neighbors of neighbors too."),
        ] = 1,
        include_history: Annotated[
            bool, Field(description="Also show ended relations and past property values.")
        ] = False,
    ) -> str:
        """Everything known about one entity, as Markdown: properties, relations, notes.

        Use before answering a question about a specific project, company, technology
        or decision. Use depth=2 for broader questions like "what is my stack across
        projects". If you do not know the exact name, call search first.
        """
        result = await call(graph(ctx).get_context(entity_name, depth, None, include_history))
        return render_context(result)

    @mcp.tool(annotations={"readOnlyHint": True})
    async def search(
        ctx: Context,
        query: Annotated[str, Field(description="Words to look for (Korean or English).")],
        types: Annotated[
            list[EntityType] | None, Field(description="Restrict to these entity types.")
        ] = None,
        include_notes: bool = True,
        limit: Annotated[int, Field(ge=1, le=50)] = 10,
    ) -> list[SearchHit]:
        """Find entities and notes by name, alias, summary or note text.

        Use when you don't know the exact entity name, or to check whether something
        already exists before creating it. Exact name matches rank first.
        """
        return await call(graph(ctx).search(query, types, include_notes, limit))

    @mcp.tool(annotations={"readOnlyHint": True})
    async def list_entities(
        ctx: Context,
        type: Annotated[EntityType | None, Field(description="Entity type, or null for all.")],
        filters: Annotated[
            dict[str, Any],
            Field(description="Exact property matches, e.g. {status: 'active'}."),
        ] = {},
        limit: Annotated[int, Field(ge=1, le=200)] = 50,
    ) -> list[EntityRef]:
        """List entities of a type, most recently updated first.

        Use for enumerations: "what projects am I running", "which companies did I
        apply to". For a single entity's details use get_context instead.
        """
        return await call(graph(ctx).list_entities(type, filters, limit))

    @mcp.tool(annotations={"destructiveHint": True})
    async def delete_entity(
        ctx: Context,
        entity_name: EntityName,
        confirm: Annotated[
            bool,
            Field(
                description="Must be true to actually delete. With false you get a "
                "preview of what would be affected — show it to the user first."
            ),
        ] = False,
        hard: Annotated[
            bool,
            Field(
                description="false = hide but keep (restorable). true = erase permanently, "
                "only when the user explicitly asks for data to be removed."
            ),
        ] = False,
    ) -> DeleteResult:
        """Remove an entity. Only when the user asks to forget something or it was a mistake.

        If something just ended (left a job, project finished), use end_relation or set
        a status property instead. Always call with confirm=false first and confirm
        with the user.
        """
        return await call(graph(ctx).delete_entity(entity_name, confirm, hard))

    return mcp


def main() -> None:
    create_server().run(show_banner=False)  # stdio by default


if __name__ == "__main__":
    main()
