# Architecture Decision Records

각 ADR은 맥락 → 선택지 → 결정 → 결과 순서로 쓴다. 결정을 뒤집을 때는 기존 ADR을 고치지 않고, 새 ADR을 만들고 기존 ADR의 상태를 `Superseded by ADR-XXXX`로 바꾼다.

| # | 제목 | 상태 |
|---|---|---|
| [0001](0001-python-fastmcp.md) | Python + FastMCP로 구현 | Accepted |
| [0002](0002-neo4j.md) | 저장소로 Neo4j 5 Community 사용 | Accepted |
| [0003](0003-temporal-history.md) | 이력: 관계는 validFrom/validTo, 속성은 Revision 노드 | Accepted |
| [0004](0004-closed-relation-vocabulary.md) | 관계 타입을 닫힌 집합으로 제한 | Accepted |
| [0005](0005-entity-identity-and-aliases.md) | 엔티티 식별은 (타입, 정규화 이름) + alias | Accepted |
| [0006](0006-soft-delete-by-default.md) | 기본 삭제는 soft delete, 확인 플래그 필수 | Accepted |
