# 2026-10-19 — 프로젝트 시작과 Phase 1 구현 과정

이 프로젝트를 어떻게 시작했고 Phase 1을 어떤 순서로 만들었는지, 중간에 겪은 문제까지 기록한다.

## 1. 시작 전: 기존 클러스터 정리

이 서버는 Phase 2부터 기존 홈 k3s 클러스터([k3s-msa-infrastructure](https://github.com/sm010422/k3s-msa-infrastructure))에 배포한다. 그런데 클러스터 메모리 사용률이 master 83%, worker 74~75%라서 Neo4j가 들어갈 자리가 없었다.

처음에는 "클러스터를 내리고 새로 구축해야 하나"를 고민했다. 하지만 새 프로젝트도 같은 클러스터와 그 위의 ArgoCD, Traefik, Prometheus/Grafana를 그대로 쓸 계획이다. 그래서 클러스터는 두고, **이전 프로젝트의 앱 워크로드만 replicas 0으로 내렸다**.

- ArgoCD `selfHeal` 때문에 `kubectl scale`로는 바로 되돌아간다. 그래서 git 매니페스트를 바꾸는 GitOps 방식으로 내렸다.
- PVC, Secret, Service, ArgoCD Application은 그대로 두었다. `git revert` 한 번으로 복구할 수 있다.
- 결과: master 83% → 46%, worker1 75% → 39%, worker2 74% → 57%. 약 1.9GB를 확보했다.
- 매시간 다운 알림을 보내는 헬스체크 워크플로는 비활성화했다.
- 전체 절차와 복구 방법은 그쪽 레포의 `docs/Pausing-Legacy-Apps-for-New-Project.md`에 있다.

Phase 1은 로컬(docker-compose)만 쓰므로 클러스터는 Phase 2에서 다시 다룬다.

## 2. 기술 선택

- **언어**: Python 3.12 + FastMCP. 근거는 [ADR-0001](../adr/0001-python-fastmcp.md)에 있다. 요약하면 MCP 생태계가 Python/TS 중심이고, 기존 포트폴리오가 Java/Spring 위주라 균형이 맞는다.
- **DB**: Neo4j 5 Community([ADR-0002](../adr/0002-neo4j.md)).
- **도구**: uv(패키지·Python 버전 관리), ruff, pytest + pytest-asyncio, testcontainers.

설치 시점의 실제 버전: fastmcp 4.1.1, mcp 2.3.0, neo4j 드라이버 6.4.1, pydantic 2.14, testcontainers 4.15. FastMCP가 4.x라서, 코드를 쓰기 전에 설치된 패키지 소스에서 `FastMCP.__init__`, `@tool`, lifespan, `Context.lifespan_context`, `Client` API를 직접 확인했다.

## 3. 계획 → 확인 → 구현

코드를 쓰기 전에 레포 구조, 스키마 개선안, 툴 시그니처를 먼저 정리해서 확인받았다. 초안 스키마에서 바꾼 점:

| 초안 | 변경 | 이유 |
|---|---|---|
| Skill과 Technology를 따로 둠 | `Technology {category}` 하나로 통합하고, 숙련도는 `KNOWS {level}` 관계에 둠 | 같은 개념이 두 노드 타입으로 중복됨 |
| 이름이 식별자 | `(타입, 정규화 이름)` + `aliases` | "k8s"와 "쿠버네티스" 문제, 동명 다른 타입 문제 ([ADR-0005](../adr/0005-entity-identity-and-aliases.md)) |
| 관계 타입이 예시 수준 | 닫힌 enum 12개 + 방향 규칙 | LLM의 어휘 표류 방지 ([ADR-0004](../adr/0004-closed-relation-vocabulary.md)) |
| validFrom/validTo "검토" | 관계는 유효기간 버전 관리, 속성은 Revision 노드 | [ADR-0003](../adr/0003-temporal-history.md) |
| — | `provenance`(user/inferred), `confidence` | 사용자가 말한 것과 LLM이 추론한 것을 구분 |
| `delete_entity` 확인 플래그 | soft delete 기본 + 미리보기 | [ADR-0006](../adr/0006-soft-delete-by-default.md) |
| — | `end_relation` 툴 추가 | "퇴사했다", "그 기술은 이제 안 쓴다"를 삭제가 아닌 종료로 표현하기 위해 |

구현 순서는 `schema` / `policy` / `errors` → `config` / `db` → `repository` → `formatting` → `server`였다. 테스트는 단위 테스트와 통합 테스트(Testcontainers)로 나눴다.

## 4. 리포지토리

- `sm010422/personal-knowledge-graph-mcp`. private으로 만들었다가 public으로 바꿨다.
- public이어도 되는 이유: 코드와 가상 데이터만 커밋하고, 실제 개인 데이터와 시크릿은 `.gitignore`와 정책으로 막는다([security.md](../security.md)). 포트폴리오로 보여주려면 오히려 공개가 필요하다.

## 5. 겪은 문제

### 5.1 Colima가 시작되지 않음 — 디스크 크기 설정 불일치

```
level=info msg="Resize instance colima's disk from 100GiB to 60GiB"
level=fatal msg="disk shrinking is not supported"
```

실제 VM 디스크(`~/.colima/_lima/colima/diffdisk`)는 100GiB인데, Colima가 실제로 읽는 인스턴스 설정(`~/.colima/_lima/colima/colima.yaml`)에는 `disk: 60`이 남아 있었다. 그래서 축소를 시도하다 실패했다. `colima start --disk 100`은 프로필 설정(`~/.colima/default/colima.yaml`)만 바꿔서 효과가 없었다.

**해결**: 인스턴스 설정을 백업(`colima.yaml.bak-20261019`)하고 `disk: 100`으로 실제 크기에 맞췄다. 디스크 데이터는 건드리지 않는다.

### 5.2 Testcontainers가 Colima 소켓을 못 찾음 (예방)

testcontainers-python은 `DOCKER_HOST`만 보고 docker context는 보지 않는다. Colima는 소켓을 `~/.colima/default/docker.sock`에 두고 context로만 알린다. 그래서 `tests/integration/conftest.py`에서 `DOCKER_HOST`가 비어 있으면 활성 context의 endpoint를 읽어 설정한다. Ryuk 컨테이너는 VM 내부 경로를 마운트해야 하므로 `TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock`도 함께 지정한다. Colima와 OrbStack 모두에 맞는 방식이다.

### 5.3 stdio 모드에서 Neo4j 경고가 쿼리마다 출력됨

실제 stdio로 서버를 띄워 스모크 테스트를 하자, 쿼리마다 다음 경고가 stderr에 찍혔다.

```
Received notification from DBMS server: ... The property `deletedAt` does not exist.
```

`deletedAt`은 soft delete된 노드에만 생기는 선택적 속성이라, 아직 아무것도 삭제되지 않은 DB에서는 Neo4j가 "없는 속성"이라고 경고한다. 기능 문제는 아니지만, stdio 모드에서는 이 출력이 MCP 클라이언트(Claude Desktop) 로그를 덮는다.

**해결**: 드라이버 생성 시 `notifications_min_severity="OFF"`로 지정했다. 함께 FastMCP 시작 배너도 `show_banner=False`로 껐다.

## 6. 결과

- 툴 8개: `upsert_entity`, `add_relation`, `end_relation`, `add_note`, `get_context`, `search`, `list_entities`, `delete_entity`
- 테스트 54개 통과(단위 28, 통합 26). 통합 테스트는 실제 `neo4j:5-community` 컨테이너에서 돌고, 약 15~20초 걸린다.
  - 생성·갱신·이력, alias 해석, 동명 다른 타입, alias 충돌
  - 관계 생성·멱등성·상태 변경 이력, 방향 규칙, `end_relation`
  - depth 1/2 컨텍스트, 노트, 한글·부분 단어·접두어 검색, Lucene 특수문자
  - soft delete·복구·hard delete(전용 노트만 삭제)
  - 민감정보 차단
  - MCP 프로토콜 왕복(in-memory 클라이언트)
- stdio 스모크 테스트: 가상 데이터 시드 후 실제 `uv run pkg-mcp` 프로세스에 붙어 확인했다.
  - `get_context(depth=2)`에서 5개 직접 관계와 2개 2-hop 관계가 나왔다.
  - "쿠버네티스" 검색이 Kubernetes를 찾았다.
  - 지원 상태가 서류 → 면접으로 바뀐 이력이 보였다.

## 7. 다음 단계

- Claude Desktop / Claude Code에 붙여 실제 대화에서 툴 description이 잘 작동하는지 관찰하고 문구를 다듬는다.
- Phase 2: HTTP 모드, Bearer 토큰 인증, k3s 매니페스트(Neo4j StatefulSet + PVC), Cloudflare Tunnel.
