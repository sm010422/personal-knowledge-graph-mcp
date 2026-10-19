from neo4j import AsyncDriver, AsyncGraphDatabase

from pkg_mcp.config import Settings

# Idempotent: safe to run on every server start.
SCHEMA_STATEMENTS: tuple[str, ...] = (
    "CREATE CONSTRAINT entity_key IF NOT EXISTS FOR (n:Entity) REQUIRE n.key IS UNIQUE",
    "CREATE CONSTRAINT note_id IF NOT EXISTS FOR (n:Note) REQUIRE n.id IS UNIQUE",
    "CREATE INDEX entity_type IF NOT EXISTS FOR (n:Entity) ON (n.type)",
    "CREATE INDEX entity_norm_name IF NOT EXISTS FOR (n:Entity) ON (n.normName)",
    # 'cjk' tokenizes Korean into bigrams, so partial Korean words still match.
    """
    CREATE FULLTEXT INDEX entity_fulltext IF NOT EXISTS
    FOR (n:Entity) ON EACH [n.name, n.aliasText, n.summary, n.description, n.rationale]
    OPTIONS {indexConfig: {`fulltext.analyzer`: 'cjk'}}
    """,
    """
    CREATE FULLTEXT INDEX note_fulltext IF NOT EXISTS
    FOR (n:Note) ON EACH [n.content]
    OPTIONS {indexConfig: {`fulltext.analyzer`: 'cjk'}}
    """,
)


def create_driver(settings: Settings) -> AsyncDriver:
    return AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
        # Optional properties such as deletedAt make the server warn "property does not
        # exist" on every query. In stdio mode those land in the MCP client's log.
        notifications_min_severity="OFF",
    )


async def bootstrap_schema(driver: AsyncDriver, database: str) -> None:
    for statement in SCHEMA_STATEMENTS:
        await driver.execute_query(statement, database_=database)
    await driver.execute_query("CALL db.awaitIndexes(60)", database_=database)
