# 2026-11-02 — 공개 정비, 첫 운영 그래프 점검, `PART_OF` 승격

같은 날 오전에 [검색 AND/OR/NOT 버그](2026-11-02-lucene-operator-search-bug.md)를 고친 뒤 이어서 진행했다. 외부에서 연락이 몇 건 오기 시작해서 **남이 보고 쓸 수 있는 레포로 정비**하고, 일주일 동안 쌓인 **운영 그래프를 처음으로 점검**했다.

이 문서는 **무엇을 근거로 무엇을 바꿨고, 무엇이 남았는지**를 남긴다.

## 0. 출발점: 관심의 정체

레포 관련 메일 3통과 GitHub 이슈 1건이 왔다. 내용을 보면 사용자 문의는 없었다.

| 출처 | 성격 |
|---|---|
| GitHub 이슈 #1 | 실제 버그 제보 → 오전에 수정 |
| 개인 메일 3통 | 각자 자기 프로젝트·마켓플레이스 홍보, 피드백 요청 |

GitHub 트래픽(14일)도 같은 그림이었다.

| 지표 | 값 |
|---|---|
| 페이지 조회 | 6회 / 고유 3명 |
| clone | 278회 / **고유 120명** |
| 스타 / 포크 | 1 / 0 |

페이지를 보지 않고 clone만 하는 쪽은 대부분 봇·에이전트 스캐너다. 이 분야(MCP 메모리, 지식 그래프)가 자동 탐색 대상이라는 뜻이지만, 사람 사용자는 아직 없다. 그래서 "사람이 왔을 때 막히는 곳"부터 없앴다.

## 1. 공개 레포 정비

| 문제 | 조치 | 커밋 |
|---|---|---|
| README가 한국어뿐이다 | 영문 `README.md`를 기본으로, 한국어는 `README.ko.md`(`git mv`로 이력 유지). 상단에 언어 전환 링크 | `2137b70` |
| **LICENSE가 없다** → 법적으로 남이 쓸 수 없는 코드 | MIT. `pyproject.toml`에도 `license = "MIT"` (uv_build로 wheel 빌드 확인) | `5bffc18` |
| 비슷한 도구와 무엇이 다른지 안 보인다 | README에 공식 `server-memory`, Graphiti, mem0 비교표. "이럴 땐 다른 걸 써라"도 한 줄 넣었다 | `5bffc18` |
| GitHub 검색에 안 걸린다 | topics 11개 (`mcp`, `mcp-server`, `knowledge-graph`, `neo4j`, `llm-memory` 등) | — (레포 설정) |

비교표는 공개된 설계 수준에서 확실한 것만 적었다. 다른 프로젝트는 빠르게 바뀌므로 주기적으로 대조가 필요하다.

### 비밀값 유출 점검

공개 정비 전에 로컬 `.env`, `.env.remote`, `.env.backup`의 값이 git에 들어간 적이 있는지 확인했다.

- 세 파일 모두 `.gitignore`로 제외되고, 히스토리에 추가된 env 파일은 `.env.example`뿐이다.
- 각 값을 `git log --all -p -S<값>`으로 전체 히스토리에서 찾았다(개수만 출력). 토큰, Neo4j 비밀번호, B2 키, 백업 암호 모두 **0건**.
- 레포에 있는 것은 공개를 의도한 엔드포인트 URL과 B2 버킷 이름뿐이다. 둘 다 키 없이는 쓸모가 없다.

## 2. 운영 그래프 첫 점검

10-27~31에 쌓인 실제 데이터를 읽기 전용 Cypher로 집계했다. 개인 데이터라서 아래에는 개수만 적는다.

| 항목 | 값 |
|---|---|
| 엔티티 | 89 (Technology 51, Decision 18, Project 11, Organization 8, Person 1) |
| 관계 | 132 (`USES` 82) |
| 노트 / Revision | 43 / 6 |
| 이름이 겹치는 중복 엔티티 | 0 |
| provenance | user 81, inferred 8 |

잘 되는 것: 중복 엔티티가 없다. alias 기반 식별([ADR-0005](../adr/0005-entity-identity-and-aliases.md))이 실사용에서 동작한다.

발견한 문제:

1. **`RELATED_TO` 6개 중 5개가 Project → Organization.** 스키마에 "프로젝트가 어떤 조직 활동의 일부"를 나타내는 관계가 없어서 LLM이 탈출구로 빠졌다. LLM은 그 의미를 엣지 속성 `kind`에 적어 두었다(4개가 동아리·부트캠프·캡스톤). → 3절
2. **시간축 기능이 쓰이지 않았다.** 끝난 관계(`validTo`) 0개, Event 0개, 지원 기록 2개. 지금은 "프로젝트·기술 목록"에 가깝다. 툴 description을 다듬는 TODO 4번의 관찰 대상이다.
3. **세션 종료 자동 동기화 훅이 중복 확인을 반쪽만 했다.** 로그에 "`get_context`는 권한이 없어 `search`로만 확인했다"는 기록이 있었다. → 4절

## 3. `PART_OF` 승격 (ADR-0004 갱신)

[ADR-0004](../adr/0004-closed-relation-vocabulary.md)는 관계 어휘를 닫힌 집합으로 두고, "`RELATED_TO`에 자주 쓰이는 패턴이 보이면 새 타입으로 승격한다"고 정해 두었다. 그 기준을 처음 적용했다.

- **이름**: `FOR`도 검토했다. 하지만 "Orbit FOR 코딩 동아리"는 "동아리를 위해 만든 것"으로 읽혀 실제 의미(동아리 활동으로 한 것)와 다르다. → `PART_OF` (Project → Organization)
- **범위 밖**: "지원하려는 회사를 겨냥해 만든 프로젝트"는 소속이 아니다. `RELATED_TO`로 둔다.
- **변경**: `schema.py` enum과 방향 규칙, `add_relation` 툴 description, `docs/schema.md`, ADR-0004 갱신 절, 단위 테스트.
- **배포 확인**: CI 성공(`fdef63a`) → ArgoCD Synced/Healthy → 공개 엔드포인트 `tools/list`의 `add_relation` enum에 `PART_OF`가 있음.

ADR 예시에 처음에는 실제 그래프의 이름을 적었다가, 커밋 전에 가상 이름으로 바꿨다. 운영 데이터를 보고 쓰는 문서일수록 이 실수가 쉽다.

### 기존 엣지 이전 (대기 중)

이미 있는 `RELATED_TO` 4개를 `PART_OF`로 바꿔야 새 스키마와 맞는다. MCP 툴로는 하지 않는다. `end_relation`을 쓰면 "그 관계가 11-02에 끝났다"는 **사실이 아닌 이력**이 남기 때문이다. 대신 Cypher 한 트랜잭션으로 속성(`validFrom`, `provenance`, `kind`)을 그대로 복사한 `PART_OF`를 만들고 원래 엣지를 지운다.

운영 DB에 직접 쓰는 작업이라 Claude Code의 자동 승인에서 막혔다. 사람이 직접 실행하도록 남겼다(TODO 4-1). 실행 전에 그날 백업이 성공했는지 본다(`kubectl -n personal-kg get jobs`).

```bash
kubectl -n personal-kg exec neo4j-0 -- sh -c 'cypher-shell -u neo4j -p "${NEO4J_AUTH#neo4j/}" "
MATCH (a:Entity {type:\"Project\"})-[r:RELATED_TO]->(b:Entity {type:\"Organization\"})
WHERE r.kind IN [\"팀 프로젝트 소속\",\"캡스톤디자인\"] AND r.validTo IS NULL
CREATE (a)-[n:PART_OF]->(b) SET n = properties(r)
DELETE r
RETURN count(n) AS migrated"'
```

`migrated`가 4이면 된다. 문장 하나라서 한 트랜잭션으로 실행되고, 중간에 실패하면 아무것도 바뀌지 않는다.

## 4. 세션 종료 자동 동기화 훅 (`kg-sync`)

레포 밖(`~/.claude/hooks/kg-sync.sh`)에 있는 개인 설정이다. Claude Code 세션이 끝나면 headless Claude가 대화를 읽고, 오래 유지될 사실만 `provenance="inferred"`로 그래프에 넣는다.

| | 전 | 후 |
|---|---|---|
| 허용 툴 | `search` + 쓰기 3개 | `search`, **`get_context`**, **`list_entities`** + 쓰기 3개 |
| 중복 확인 | 엔티티 이름만(`search`) | 관계·노트를 더하기 전에 `get_context`로 기존 내용 확인 |
| 삭제·관계 종료 | 불가 | 불가 (그대로) |

추가한 툴은 둘 다 읽기 전용이라 위험 범위는 늘지 않는다. 이 수정은 Claude Code가 자기 설정을 바꾸는 일이라 자동 승인에서 막혔고, 사용자가 직접 고쳤다. Claude는 문법 검사와 반영 여부만 확인했다.

## 5. 남은 것

- [ ] 기존 `RELATED_TO` 4개 → `PART_OF` 이전 (TODO 4-1)
- [ ] 다음 몇 세션 뒤 `~/.claude/kg-sync.log`에서 `get_context`를 실제로 쓰는지, 노트 중복이 줄었는지 확인
- [ ] 남이 5분 안에 써 볼 수 있는 설치 경로(compose 일체형, PyPI/`uvx`, MCP Registry)와 데모

## 6. 배운 점

- **관심 신호를 숫자로 확인한다.** 메일이 와서 "관심이 생겼다"고 느꼈지만, 트래픽과 메일 내용을 보면 대부분 자동 탐색과 홍보였다. 대신 그 숫자가 "사람이 오면 바로 막히는 곳(라이선스, 설치)"을 알려 줬다.
- **닫힌 어휘의 탈출구는 관찰 장치다.** `RELATED_TO`를 남겨 두었기 때문에, LLM이 어떤 관계를 필요로 했는지가 엣지 속성에 기록으로 남았다. 어휘를 넓히는 근거가 추측이 아니라 실제 데이터가 됐다.
- **이력이 있는 모델에서는 "고치기"와 "끝내기"가 다르다.** 잘못 분류한 관계를 `end_relation`으로 닫으면 사실이 아닌 이력이 생긴다. 이력 모델을 만들 때 "정정" 경로도 같이 설계해야 한다. 지금은 DB 직접 수정뿐이라 다음 개선 후보다.
