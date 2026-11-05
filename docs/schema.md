# 그래프 스키마

## 노드

모든 엔티티는 공통 라벨 `:Entity`와 타입 라벨을 함께 가진다(예: `(:Entity:Project)`). 유니크 제약·전문 검색 인덱스를 `:Entity` 하나에 걸기 위해서다.

| 타입 | 용도 | 자주 쓰는 속성 |
|---|---|---|
| `Person` | 사용자 본인(`isSelf: true`, 하나만), 다른 사람 | `isSelf`, `role` |
| `Project` | 프로젝트 | `status`, `summary`, `startedAt`, `repoUrl`, `latestRelease` |
| `Technology` | 도구·언어·프레임워크 | `category` |
| `Decision` | 결정과 그 이유 | `rationale`, `alternatives`, `decidedAt` |
| `Event` | 날짜가 있는 일: 마감·약속·시험·면접 | `date`(`YYYY-MM-DD`로 시작, 검증함), `kind` |
| `Organization` | 회사·학교·동아리 | `kind`, `techStack` |
| `Concept` | 공부하는 개념·이론 (예: 고유값 분해) | `field`, `summary` |
| `Resource` | 책·논문·강의·글 | `kind`, `url`, `author`, `status` |
| `Place` | 장소 | `kind`, `city` |
| `Goal` | 목표 | `status`, `targetDate`, `summary` |

`Concept`·`Resource`·`Place`·`Goal`은 커리어 밖의 용도(공부, 독서, 연구, 생활)를 위해 2026-11-05에 추가했다([ADR-0012](adr/0012-general-purpose-types.md)).

작성 규칙 (툴 description에도 들어 있다):

- **`summary`에는 잘 안 바뀌는 설명만 쓴다.** 버전, 개수, 상태는 전용 속성(`latestRelease`, `status`)에 둔다. 두 곳에 같은 숫자가 있으면 한쪽만 고쳐지고 어긋난다.
- **마감·면접·과제·발표는 `Event`로 만들고 `CONCERNS`로 회사·프로젝트에 잇는다.** 관계 속성 안의 날짜 문자열(`deadline: 2026-11-15`)은 `upcoming_events`가 찾지 못한다.
- **`Technology`는 사용자와 사용자의 프로젝트가 쓰는 것만 만든다(`KNOWS`, `USES`).** 회사 기술 스택은 Organization의 `techStack` 목록 속성으로 둔다. 회사 스택을 Technology로 만들면 "내 스택" 질문에 섞여 들어온다.
- 같은 대상이 두 엔티티로 생기면 `merge_entities`로 합친다. 관계·노트가 옮겨 가고, 없어지는 쪽 이름은 alias가 되며, 합친 기록은 남는 쪽 Revision에 `mergedFrom`으로 남는다.

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
  - **원문 보존**: 사용자가 다른 곳에 원본이 없는 완성된 문서(제출한 자기소개서, 이력서 등)를 주거나 "그대로 남겨 달라"고 하면, 전문을 고치지 않고 노트 하나에 넣는다. `source`는 `original: <제목>`이다. 가장 구체적인 엔티티(예: 그 자소서를 낸 회사)에 붙이고, 본인 Person 노드에는 붙이지 않는다. 본인 노드에 붙이면 본인에 대한 `get_context`마다 전문이 딸려 나온다. 문서 속 사실(지원 회사·직무, 프로젝트, 기술)은 별도로 엔티티·관계로 뽑는다.
  - **원본이 따로 있는 문서**(레포 파일, 노션 페이지, URL)는 복사하지 않는다. 사실은 엔티티·관계로 뽑고, 짧은 요약 노트의 `source`에 경로나 URL을 적는다(예: `docs/devlog/2026-11-02-...md`). 원문이 필요하면 그 경로를 직접 읽는다. 복사본은 원본이 바뀌면 낡고, 노트는 수정할 수 없어서 구버전과 신버전이 함께 남기 때문이다. KG는 원문 저장소가 아니라 "무엇이 어디에 있는지" 아는 색인이다.
  - 저장 시 앞뒤 공백만 잘린다. 본문의 줄바꿈·들여쓰기는 그대로다.
  - 읽기: `get_context`는 최신 10개(`note_limit`, 최대 20)를 보여 주고, 300자가 넘는 노트는 미리보기와 id만 준다. 전문은 `get_note(id)`로 받는다. `search`는 200자 미리보기다. 긴 원문 하나가 그 엔티티를 조회할 때마다 수천 토큰씩 딸려 오는 것을 막기 위해서다.
  - 노트는 수정하지 않는다. 고칠 때는 새 노트를 넣고 이전 것을 `delete_note`로 지운다(미리보기 → 확인 → 삭제).
- `(:Revision {changedAt, changes, provenance})-[:REVISION_OF]->(:Entity)` — 속성 변경 이력. `changes`는 `{"status": {"before": "planning", "after": "active"}}` 형태의 JSON 문자열이다(Neo4j는 map을 속성으로 저장할 수 없다).

## 관계

관계 타입은 닫힌 집합이다([ADR-0004](adr/0004-closed-relation-vocabulary.md)).

| 관계 | From → To | 예시 속성 |
|---|---|---|
| `BUILT` | Person → Project | `role` |
| `USES` | Project → Technology | |
| `KNOWS` | Person → Technology / Concept | `level` |
| `HAS_DECISION` | Project → Decision | |
| `APPLIED_TO` | Person → Organization | `status`, `position` |
| `WORKED_AT` | Person → Organization | `title` |
| `STUDIED_AT` | Person → Organization | `major` |
| `MEMBER_OF` | Person → Organization | |
| `ATTENDED` | Person → Event | |
| `CONCERNS` | Event → Project / Organization / Decision | |
| `COLLABORATED_WITH` | Person → Person | |
| `PART_OF` | Project → Organization | `kind` (동아리·부트캠프·캡스톤 등) |
| `PREREQUISITE_OF` | Concept / Technology → Concept / Technology | A를 알아야 B를 이해할 수 있음 |
| `COVERS` | Resource → Concept / Technology | `chapter` |
| `READ` | Person → Resource | `status` (reading, finished) |
| `CITES` | Decision / Project / Resource → Resource | 근거로 삼은 자료 |
| `PURSUES` | Person → Goal | |
| `SUPPORTS` | Project / Resource / Concept / Technology / Event / Decision → Goal | 목표에 기여 |
| `LOCATED_AT` | Event / Organization / Person → Place | |
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
