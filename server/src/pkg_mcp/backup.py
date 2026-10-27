"""Logical backup: dump the whole graph to gzipped JSON Lines and load it back.

Neo4j Community can only `neo4j-admin database dump` a stopped database, and ArgoCD
self-heal would undo scaling the StatefulSet down. So the backup reads the graph over
Bolt instead, without downtime. See docs/adr/0011-logical-backup-to-b2.md.

File layout, one JSON object per line:
  {"format": "pkg-mcp-backup", "version": 1, "createdAt": "..."}   header
  {"n": "<id>", "l": ["Entity", "Project"], "p": {...}}             node
  {"r": ["<start id>", "<end id>"], "t": "USES", "p": {...}}        relationship
  {"end": {"nodes": N, "relationships": M}}                         trailer

The trailer is written last, so a truncated file is detected on restore. Temporal
property values are tagged ({"$date": "2026-10-01"}) so they come back as the same
Neo4j types instead of strings.

Usage (environment as for the server: NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD):
  pkg-mcp-backup export /backup/neo4j-20261027T0300Z.jsonl.gz
  pkg-mcp-backup restore /backup/neo4j-20261027T0300Z.jsonl.gz   # target must be empty
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from neo4j import Driver, GraphDatabase, ManagedTransaction
from neo4j.time import Date, DateTime, Duration, Time

from pkg_mcp.config import Settings
from pkg_mcp.db import SCHEMA_STATEMENTS

FORMAT = "pkg-mcp-backup"
VERSION = 1
BATCH_SIZE = 500

# Checked in order: DateTime is not a subclass of Date in the driver, but keep the
# most specific types first anyway.
_TEMPORAL_TAGS: tuple[tuple[str, type], ...] = (
    ("$datetime", DateTime),
    ("$date", Date),
    ("$time", Time),
)


class BackupError(Exception):
    pass


# ---- value encoding -------------------------------------------------------------


def encode_value(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, list):
        return [encode_value(v) for v in value]
    for tag, cls in _TEMPORAL_TAGS:
        if isinstance(value, cls):
            return {tag: value.iso_format()}
    if isinstance(value, Duration):
        return {"$duration": value.iso_format()}
    raise BackupError(f"Unsupported property type: {type(value).__name__}")


def decode_value(value: Any) -> Any:
    if isinstance(value, list):
        return [decode_value(v) for v in value]
    if isinstance(value, dict):
        ((tag, raw),) = value.items()
        if tag == "$datetime":
            return DateTime.from_iso_format(raw)
        if tag == "$date":
            return Date.from_iso_format(raw)
        if tag == "$time":
            return Time.from_iso_format(raw)
        if tag == "$duration":
            return Duration.from_iso_format(raw)
        raise BackupError(f"Unknown value tag: {tag}")
    return value


def encode_props(props: dict[str, Any]) -> dict[str, Any]:
    return {k: encode_value(v) for k, v in props.items()}


def decode_props(props: dict[str, Any]) -> dict[str, Any]:
    return {k: decode_value(v) for k, v in props.items()}


# ---- export -----------------------------------------------------------------------


def _dump(tx: ManagedTransaction, out: Any) -> dict[str, int]:
    nodes = 0
    for record in tx.run("MATCH (n) RETURN elementId(n) AS id, labels(n) AS l, properties(n) AS p"):
        line = {"n": record["id"], "l": sorted(record["l"]), "p": encode_props(record["p"])}
        out.write(json.dumps(line, ensure_ascii=False) + "\n")
        nodes += 1
    rels = 0
    for record in tx.run(
        "MATCH (a)-[r]->(b) "
        "RETURN elementId(a) AS s, elementId(b) AS e, type(r) AS t, properties(r) AS p"
    ):
        line = {"r": [record["s"], record["e"]], "t": record["t"], "p": encode_props(record["p"])}
        out.write(json.dumps(line, ensure_ascii=False) + "\n")
        rels += 1
    return {"nodes": nodes, "relationships": rels}


def export_graph(driver: Driver, database: str, path: Path) -> dict[str, int]:
    """Write the whole graph to `path`. Nodes and relationships are read in one
    transaction so every relationship's endpoints are in the same file."""
    tmp = path.with_name(path.name + ".partial")
    with gzip.open(tmp, "wt", encoding="utf-8") as out:
        header = {"format": FORMAT, "version": VERSION, "createdAt": datetime.now(UTC).isoformat()}
        out.write(json.dumps(header) + "\n")
        with driver.session(database=database) as session:
            counts = session.execute_read(_dump, out)
        out.write(json.dumps({"end": counts}) + "\n")
    tmp.rename(path)
    return counts


# ---- restore ----------------------------------------------------------------------


def read_backup(path: Path) -> tuple[list[dict], list[dict], dict[str, int]]:
    nodes: list[dict] = []
    rels: list[dict] = []
    trailer: dict[str, int] | None = None
    with gzip.open(path, "rt", encoding="utf-8") as f:
        lines: Iterator[str] = iter(f)
        header = json.loads(next(lines, "{}"))
        if header.get("format") != FORMAT or header.get("version") != VERSION:
            raise BackupError(f"Not a {FORMAT} v{VERSION} file: {path}")
        for raw in lines:
            line = json.loads(raw)
            if trailer is not None:
                raise BackupError("Data after the trailer line")
            if "n" in line:
                nodes.append(line)
            elif "r" in line:
                rels.append(line)
            elif "end" in line:
                trailer = line["end"]
            else:
                raise BackupError(f"Unrecognised line: {raw[:80]}")
    if trailer is None:
        raise BackupError("Missing trailer: the backup file is truncated")
    if trailer != {"nodes": len(nodes), "relationships": len(rels)}:
        raise BackupError(f"Trailer {trailer} does not match the file contents")
    return nodes, rels, trailer


def _batches(items: list[dict]) -> Iterator[list[dict]]:
    for i in range(0, len(items), BATCH_SIZE):
        yield items[i : i + BATCH_SIZE]


def restore_graph(driver: Driver, database: str, path: Path) -> dict[str, int]:
    """Load a backup into an empty database. Refuses to merge into existing data."""
    nodes, rels, expected = read_backup(path)

    existing = driver.execute_query("MATCH (n) RETURN count(n) AS c", database_=database).records[
        0
    ]["c"]
    if existing:
        raise BackupError(
            f"Target database is not empty ({existing} nodes). Restore only into an empty database."
        )

    # Constraints first, so a broken backup with duplicate keys fails loudly.
    for statement in SCHEMA_STATEMENTS:
        driver.execute_query(statement, database_=database)

    id_map: dict[str, str] = {}
    for batch in _batches(nodes):
        rows = [{"id": n["n"], "labels": n["l"], "props": decode_props(n["p"])} for n in batch]
        result = driver.execute_query(
            "UNWIND $rows AS row "
            "CREATE (n:$(row.labels)) SET n = row.props "
            "RETURN row.id AS old, elementId(n) AS new",
            rows=rows,
            database_=database,
        )
        id_map.update({r["old"]: r["new"] for r in result.records})

    for batch in _batches(rels):
        rows = [
            {
                "s": id_map[r["r"][0]],
                "e": id_map[r["r"][1]],
                "type": r["t"],
                "props": decode_props(r["p"]),
            }
            for r in batch
        ]
        driver.execute_query(
            "UNWIND $rows AS row "
            "MATCH (a) WHERE elementId(a) = row.s "
            "MATCH (b) WHERE elementId(b) = row.e "
            "CREATE (a)-[r:$(row.type)]->(b) SET r = row.props",
            rows=rows,
            database_=database,
        )

    driver.execute_query("CALL db.awaitIndexes(60)", database_=database)
    actual = count_graph(driver, database)
    if actual != expected:
        raise BackupError(f"Restored {actual}, expected {expected}")
    return actual


def count_graph(driver: Driver, database: str) -> dict[str, int]:
    record = driver.execute_query(
        "CALL () { MATCH (n) RETURN count(n) AS nodes } "
        "CALL () { MATCH ()-[r]->() RETURN count(r) AS relationships } "
        "RETURN nodes, relationships",
        database_=database,
    ).records[0]
    return {"nodes": record["nodes"], "relationships": record["relationships"]}


# ---- CLI --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="pkg-mcp-backup", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("export", "dump the graph to a .jsonl.gz file"),
        ("restore", "load a .jsonl.gz file into an empty database"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("path", type=Path)
    args = parser.parse_args(argv)

    settings = Settings()  # type: ignore[call-arg]
    driver = GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
        notifications_min_severity="OFF",
    )
    try:
        driver.verify_connectivity()
        if args.command == "export":
            counts = export_graph(driver, settings.neo4j_database, args.path)
        else:
            counts = restore_graph(driver, settings.neo4j_database, args.path)
    except BackupError as e:
        print(f"{args.command} failed: {e}", file=sys.stderr)
        raise SystemExit(1) from e
    finally:
        driver.close()
    # Counts only: never print property values, they are personal data.
    print(f"{args.command} ok: {counts['nodes']} nodes, {counts['relationships']} relationships")


if __name__ == "__main__":
    main()
