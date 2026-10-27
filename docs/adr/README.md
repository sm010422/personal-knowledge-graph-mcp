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
| [0007](0007-bearer-token-auth.md) | HTTP 모드 인증은 공유 Bearer 토큰 (OAuth는 후속) | Accepted |
| [0008](0008-image-delivery-by-manifest-commit.md) | 이미지 배포는 CI가 매니페스트의 태그를 커밋 | Accepted |
| [0009](0009-expose-via-tailscale-funnel.md) | 외부 노출은 Tailscale Funnel 재사용, Cloudflare Tunnel은 준비만 | Accepted |
| [0010](0010-background-db-connect.md) | DB 연결은 시작을 막지 않고 백그라운드에서 재시도 | Accepted |
| [0011](0011-logical-backup-to-b2.md) | 백업은 Bolt 논리 덤프 → rclone crypt → Backblaze B2 | Accepted |
