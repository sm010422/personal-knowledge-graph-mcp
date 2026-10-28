"""MCP server: tool definitions and wiring.

Tool descriptions are written for the model that calls them — they say when to
use a tool, when not to, and what to call first. That text is the main lever
for how well an LLM uses this graph.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable
from datetime import date
from importlib.metadata import version
from typing import Annotated, Any, Literal, TypeVar

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AuthProvider
from fastmcp.server.lifespan import lifespan
from neo4j import AsyncDriver
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from pkg_mcp.auth import SharedTokenVerifier
from pkg_mcp.calllog import ToolCallLog, configure_logging
from pkg_mcp.config import Settings
from pkg_mcp.db import bootstrap_schema, create_driver
from pkg_mcp.errors import KnowledgeGraphError
from pkg_mcp.formatting import render_context
from pkg_mcp.observability import init_sentry, report_unexpected
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

logger = logging.getLogger("pkg_mcp")

# How long a tool call waits for the database before giving up with a clear error.
DB_WAIT_SECONDS = 10.0
MAX_BACKOFF_SECONDS = 15.0

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


def create_server(settings: Settings | None = None, auth: AuthProvider | None = None) -> FastMCP:
    """Build the server. `auth` is required for HTTP mode and ignored by stdio."""
    state: dict[str, Any] = {}
    db_ready = asyncio.Event()

    async def connect(driver: AsyncDriver, database: str) -> None:
        """Retry until Neo4j answers, then make sure the schema exists.

        Runs in the background so the process starts even when the database is not
        up yet — in Kubernetes the headless Service does not even resolve until the
        Neo4j pod is Ready. Crashing instead would put the pod in CrashLoopBackOff
        and delay recovery by minutes after the database is back.
        """
        attempt = 0
        while True:
            try:
                await driver.verify_connectivity()
                await bootstrap_schema(driver, database)
            except Exception as exc:
                attempt += 1
                delay = min(2.0**attempt, MAX_BACKOFF_SECONDS)
                logger.warning(
                    "Neo4j not ready (attempt %d): %s; retry in %.0fs", attempt, exc, delay
                )
                await asyncio.sleep(delay)
                continue
            db_ready.set()
            logger.info("Connected to Neo4j after %d retries", attempt)
            return

    @lifespan
    async def graph_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
        cfg = settings or Settings()  # type: ignore[call-arg]
        driver = create_driver(cfg)
        state["driver"] = driver
        task = asyncio.create_task(connect(driver, cfg.neo4j_database))
        try:
            yield {"graph": KnowledgeGraph(driver, cfg.neo4j_database)}
        finally:
            task.cancel()
            state.clear()
            db_ready.clear()
            await driver.close()

    mcp = FastMCP(
        "personal-knowledge-graph",
        version=version("pkg-mcp"),
        instructions=INSTRUCTIONS,
        lifespan=graph_lifespan,
        auth=auth,
    )
    mcp.add_middleware(ToolCallLog())

    # Probes for Kubernetes. Custom routes sit outside the MCP endpoint, so they
    # need no token; they reveal nothing beyond up/down.
    @mcp.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @mcp.custom_route("/ready", methods=["GET"])
    async def ready(request: Request) -> JSONResponse:
        driver = state.get("driver")
        if driver is None or not db_ready.is_set():
            return JSONResponse({"status": "starting"}, status_code=503)
        try:
            await driver.verify_connectivity()
        except Exception:
            return JSONResponse({"status": "neo4j unreachable"}, status_code=503)
        return JSONResponse({"status": "ok"})

    async def graph(ctx: Context) -> KnowledgeGraph:
        try:
            await asyncio.wait_for(db_ready.wait(), timeout=DB_WAIT_SECONDS)
        except TimeoutError:
            raise ToolError(
                "The knowledge graph database is not reachable yet (starting up or down). "
                "Tell the user memory is temporarily unavailable; retry later."
            ) from None
        return ctx.lifespan_context["graph"]

    async def call(op: Awaitable[T]) -> T:
        try:
            return await op
        except KnowledgeGraphError as exc:
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            # A bug or an infrastructure failure, not a bad request. FastMCP still
            # turns it into a tool error for the client.
            report_unexpected(exc)
            raise

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
        return await call(
            (await graph(ctx)).upsert_entity(type, name, properties, aliases, provenance)
        )

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
            (await graph(ctx)).add_relation(
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
            (await graph(ctx)).end_relation(
                from_entity, relation_type, to_entity, valid_to=valid_to
            )
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
        return await call((await graph(ctx)).add_note(content, about_entities, source, provenance))

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
        result = await call(
            (await graph(ctx)).get_context(entity_name, depth, None, include_history)
        )
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
        return await call((await graph(ctx)).search(query, types, include_notes, limit))

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
        return await call((await graph(ctx)).list_entities(type, filters, limit))

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
        return await call((await graph(ctx)).delete_entity(entity_name, confirm, hard))

    return mcp


def main() -> None:
    configure_logging()
    settings = Settings()  # type: ignore[call-arg]
    init_sentry(settings, component="server")
    if settings.mcp_transport == "stdio":
        # The client spawned us; no network listener, so no auth.
        create_server(settings).run(show_banner=False)
        return

    tokens = settings.auth_tokens()
    if not tokens:
        raise SystemExit("MCP_AUTH_TOKEN is required when MCP_TRANSPORT=http.")
    server = create_server(settings, auth=SharedTokenVerifier(tokens))
    server.run(
        transport="http",
        host=settings.mcp_host,
        port=settings.mcp_port,
        path=settings.mcp_path,
        # No per-session server state, so any replica can answer any request and a
        # pod restart does not invalidate clients' sessions.
        stateless_http=True,
        show_banner=False,
    )


if __name__ == "__main__":
    main()
