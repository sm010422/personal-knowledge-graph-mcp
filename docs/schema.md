# 그래프 스키마

## 노드

모든 엔티티는 공통 라벨 `:Entity`와 타입 라벨을 함께 가진다(예: `(:Entity:Project)`). 유니크 제약·전문 검색 인덱스를 `:Entity` 하나에 걸기 위해서다.

| 타입 | 용도 | 자주 쓰는 속성 |
|---|---|---|
| `Person` | 나 자신, 협업자 | `isSelf`, `role` |
| `Project` | 프로젝트 | `status`, `summary`, `startedAt`, `repoUrl` |
| `Technology` | 기술·스킬 (초안의 Skill과 통합) | `category` |
| `Decision` | 설계/커리어 결정 | `rationale`, `alternatives`, `decidedAt` |
| `Event` | 면접·지원·마감·릴리즈 | `date`, `kind` |
| `Organization` | 회사·학교·동아리 | `kind` |

### 공통(내부) 속성

| 속성 | 설명 |
|---|---|
| `key` | `"<Type>:<normName>"`. 유니크 제약. 같은 이름이라도 타입이 다르면 다른 엔티티 |
| `name` | 표시용 정식 이름 |
| `normName` | NFKC + casefold + 공백 정리. 식별 비교에 사용 |
| `aliases` / `aliasKeys` / `aliasText` | 다른 표기(원문 / 정규화 / 전문검색용 연결 문자열) |
| `provenance` | `user`(사용자가 말함) / `inferred`(LLM이 추론) |
| `createdAt`, `updatedAt` | Neo4j `datetime` |
| `deletedAt` | soft delete 시각. 있으면 검색·컨텍스트에서 숨김 |

### 보조 노드

- `(:Note {id, content, source, provenance, createdAt})-[:ABOUT]->(:Entity)` — 자유 텍스트 관찰. 최소 1개 엔티티에 연결해야 한다.
- `(:Revision {changedAt, changes, provenance})-[:REVISION_OF]->(:Entity)` — 속성 변경 이력. `changes`는 `{"status": {"before": "planning", "after": "active"}}` 형태의 JSON 문자열이다(Neo4j는 map을 속성으로 저장할 수 없다).

## 관계

관계 타입은 닫힌 집합이다([ADR-0004](adr/0004-closed-relation-vocabulary.md)).

| 관계 | From → To | 예시 속성 |
|---|---|---|
| `BUILT` | Person → Project | `role` |
| `USES` | Project → Technology | |
| `KNOWS` | Person → Technology | `level` |
| `HAS_DECISION` | Project → Decision | |
| `APPLIED_TO` | Person → Organization | `status`, `position` |
| `WORKED_AT` | Person → Organization | `title` |
| `STUDIED_AT` | Person → Organization | `major` |
| `MEMBER_OF` | Person → Organization | |
| `ATTENDED` | Person → Event | |
| `CONCERNS` | Event → Project / Organization / Decision | |
| `COLLABORATED_WITH` | Person → Person | |
| `RELATED_TO` | 아무거나 → 아무거나 | 다른 타입이 맞지 않을 때만 |

### 관계 공통 속성

| 속성 | 설명 |
|---|---|
| `validFrom` | 사실이 성립한 날짜(`date`). 기본값 오늘 |
| `validTo` | 사실이 끝난 날짜. `null`이면 현재 유효 |
| `provenance`, `confidence` | 출처와 (추론일 때) 확신도 0~1 |
| `createdAt` | 기록 시각 |

## 이력 규칙 ([ADR-0003](adr/0003-temporal-history.md))

- **관계**: 같은 (from, type, to)에 대해 현재 유효한 관계는 최대 1개다. 속성이 바뀌면 기존 관계에 `validTo`를 찍고 새 관계를 만든다.
  ```
  (Alex)-[:APPLIED_TO {status: "document",  validFrom: 2026-10-01, validTo: 2026-10-15}]->(Acme)
  (Alex)-[:APPLIED_TO {status: "interview", validFrom: 2026-10-15, validTo: null}]->(Acme)
  ```
- **노드 속성**: 현재 값은 노드에 덮어쓰고, 바뀐 값의 before/after를 `Revision`으로 남긴다.
- `get_context`는 기본적으로 현재 사실만 보여주고, `include_history=true`일 때 끝난 관계와 속성 이력을 함께 보여준다.

## 인덱스

| 이름 | 종류 | 대상 |
|---|---|---|
| `entity_key` | 유니크 제약 | `Entity.key` |
| `note_id` | 유니크 제약 | `Note.id` |
| `entity_type`, `entity_norm_name` | range | `Entity.type`, `Entity.normName` |
| `entity_fulltext` | fulltext (cjk) | `name, aliasText, summary, description, rationale` |
| `note_fulltext` | fulltext (cjk) | `Note.content` |
