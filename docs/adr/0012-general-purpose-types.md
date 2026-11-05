# ADR-0012: 커리어 밖 용도를 위한 범용 타입 4개와 관계 7개 추가

- 상태: Accepted
- 날짜: 2026-11-05

## 맥락

처음 스키마는 개발자 커리어 기록용이었다. 엔티티 타입은 Person, Project, Technology, Decision, Event, Organization이고, 관계는 BUILT, USES, APPLIED_TO, WORKED_AT 같은 것들이다.

외부 리뷰에서 "엔진은 범용인데 스키마는 한 사람 전용"이라는 지적을 받았다. 공부("고유값 분해를 알아야 대각화를 이해한다"), 독서, 연구(논문·주장·근거), 생활(사람·약속·장소) 기록을 넣으려 하면 맞는 타입이 없다. 결국 전부 `RELATED_TO`로 들어가고, 관계의 의미로 묻는다는 그래프의 장점이 사라진다.

## 선택지

1. **범용 타입 몇 개를 추가한다.** `Concept`, `Resource`, `Place`, `Goal`과 그 관계들을 넣는다. 닫힌 어휘는 유지한다([ADR-0004](0004-closed-relation-vocabulary.md)).
2. **스키마 팩.** 타입과 관계를 설정 파일로 정의하고, 커리어·학습·연구 프리셋을 둔다. 서버가 시작할 때 읽어 툴 스키마를 만든다.
3. **그대로 둔다.** 개인 도구이니 커리어 용도만 지원한다.

## 결정

1번.

- 비용이 반나절이다. 2번은 enum이 툴 JSON 스키마, 관계 규칙 검증, 테스트, 문서 전체에 박혀 있어서 2~3일 걸린다.
- 닫힌 어휘의 장점(LLM이 같은 뜻을 여러 이름으로 흩뜨리지 않음)이 그대로 남는다. 2번은 그 책임을 각 사용자의 설정 파일로 넘긴다.
- 2번은 "다른 사람이 실제로 다른 도메인에 쓰고 싶어 한다"는 수요가 확인된 뒤에 해도 늦지 않다. 그때 이 ADR을 Superseded로 바꾼다.

추가한 타입:

| 타입 | 용도 | 자주 쓰는 속성 |
|---|---|---|
| `Concept` | 공부하는 개념·이론 | `field`, `summary` |
| `Resource` | 책·논문·강의·글 | `kind`, `url`, `author`, `status` |
| `Place` | 장소 | `kind`, `city` |
| `Goal` | 목표 | `status`, `targetDate`, `summary` |

추가한 관계와 바꾼 관계:

| 관계 | From → To | 쓰임 |
|---|---|---|
| `PREREQUISITE_OF` | Concept / Technology → Concept / Technology | 학습 순서 |
| `COVERS` | Resource → Concept / Technology | 자료가 다루는 개념 |
| `READ` | Person → Resource | 읽음·들음, `status` |
| `CITES` | Decision / Project / Resource → Resource | 근거 자료 |
| `PURSUES` | Person → Goal | 목표 |
| `SUPPORTS` | Project / Resource / Concept / Technology / Event / Decision → Goal | 목표에 기여하는 것 |
| `LOCATED_AT` | Event / Organization / Person → Place | 장소 |
| `KNOWS` (확장) | Person → Technology / **Concept** | 아는 것 |

## 결과

- 기존 데이터와 기존 관계 규칙은 바뀌지 않는다. 추가만 했다.
- 툴 description의 예시도 범용으로 바꿨다. 실제 회사 이름 예시는 지웠고, 커리어 예시 옆에 공부·독서 예시를 함께 넣었다. 모델은 description의 예시를 보고 무엇을 기록할지 정하기 때문이다.
- 사용자 본인은 `Person {isSelf: true}`로 정해 instructions에 적었다. 서버 코드에는 특정 사용자 이름이 없다.
- 관계가 20개가 되면서 `add_relation` description이 길어졌다. 모델이 고르기 어려워지는지 `RELATED_TO` 비율로 관찰한다(첫 점검 때 6/132).
