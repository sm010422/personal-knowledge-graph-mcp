"""Load a small fictional graph for trying the server locally.

All data here is made up. Never put real personal data in this repository.

    cd server && uv run python ../scripts/seed_example.py
"""

import asyncio
from datetime import date

from pkg_mcp.config import Settings
from pkg_mcp.db import bootstrap_schema, create_driver
from pkg_mcp.repository import KnowledgeGraph
from pkg_mcp.schema import EntityType as E
from pkg_mcp.schema import RelationType as R


async def main() -> None:
    settings = Settings()  # type: ignore[call-arg]
    driver = create_driver(settings)
    await bootstrap_schema(driver, settings.neo4j_database)
    kg = KnowledgeGraph(driver, settings.neo4j_database)

    await kg.upsert_entity(E.PERSON, "Jordan Lee", {"isSelf": True, "role": "backend engineer"})
    await kg.upsert_entity(
        E.PROJECT,
        "Harbor Watch",
        {
            "status": "active",
            "summary": "선박 위치 수집과 이상 항로 탐지",
            "startedAt": "2026-03-01",
        },
        aliases=["harbor"],
    )
    await kg.upsert_entity(
        E.PROJECT, "Ticket Relay", {"status": "done", "summary": "공연 예매 대기열"}
    )
    for tech, category in [
        ("Kafka", "messaging"),
        ("PostgreSQL", "database"),
        ("Kubernetes", "infra"),
        ("Spring Boot", "framework"),
    ]:
        await kg.upsert_entity(E.TECHNOLOGY, tech, {"category": category})
    await kg.upsert_entity(E.TECHNOLOGY, "Kubernetes", aliases=["k8s", "쿠버네티스"])
    await kg.upsert_entity(
        E.DECISION,
        "Kafka over RabbitMQ for position stream",
        {
            "rationale": "재처리(replay)가 필요해서 로그 기반 브로커 선택",
            "alternatives": ["RabbitMQ", "Redis Streams"],
            "decidedAt": "2026-03-10",
        },
    )
    await kg.upsert_entity(E.ORGANIZATION, "Example Corp", {"kind": "company"})
    await kg.upsert_entity(
        E.EVENT, "Example Corp interview", {"date": "2026-11-05", "kind": "interview"}
    )

    await kg.add_relation("Jordan Lee", R.BUILT, "Harbor Watch", {"role": "owner"})
    await kg.add_relation("Jordan Lee", R.BUILT, "Ticket Relay", {"role": "backend"})
    for tech in ["Kafka", "PostgreSQL", "Kubernetes"]:
        await kg.add_relation("Harbor Watch", R.USES, tech)
    await kg.add_relation("Ticket Relay", R.USES, "Spring Boot")
    await kg.add_relation("Harbor Watch", R.HAS_DECISION, "Kafka over RabbitMQ for position stream")
    await kg.add_relation(
        "Jordan Lee",
        R.APPLIED_TO,
        "Example Corp",
        {"status": "document"},
        valid_from=date(2026, 10, 1),
    )
    await kg.add_relation(
        "Jordan Lee",
        R.APPLIED_TO,
        "Example Corp",
        {"status": "interview"},
        valid_from=date(2026, 10, 15),
    )
    await kg.add_relation("Example Corp interview", R.CONCERNS, "Example Corp")
    await kg.add_note(
        "면접에서 Harbor Watch의 재처리 설계를 중심으로 설명할 계획",
        ["Example Corp interview", "Harbor Watch"],
    )

    await driver.close()
    print("Seeded fictional example graph.")


if __name__ == "__main__":
    asyncio.run(main())
