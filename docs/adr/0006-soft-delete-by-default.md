# ADR-0006: 기본 삭제는 soft delete, 확인 플래그 필수

- 상태: Accepted
- 날짜: 2026-10-19

## 맥락

삭제를 호출하는 것도 LLM이다. 오해한 지시 한 번으로 연결된 관계와 노트가 함께 사라지면 복구할 방법이 없다. 반면 사용자가 "이건 잊어줘"라고 하면 실제로 지워져야 한다.

## 결정

- `delete_entity(confirm=false)`(기본값)는 아무것도 바꾸지 않고, 영향 범위(관계 수, 노트 수)와 함께 `needs_confirmation`을 돌려준다. 툴 description에 "사용자에게 확인받고 다시 호출하라"고 적는다.
- `confirm=true`는 soft delete다(`deletedAt` 설정). 검색과 컨텍스트에서 숨겨지고, 이웃의 컨텍스트에서도 사라진다. 같은 이름으로 upsert하면 관계와 함께 복구된다(`restored`).
- `hard=true`는 사용자가 명시적으로 원할 때만 쓴다. 엔티티, `Revision`, 그 엔티티에만 연결된 노트를 물리적으로 지운다.
- 사실이 "끝난" 경우(퇴사, 프로젝트 종료)는 삭제가 아니라 `end_relation`이나 `status` 속성으로 처리하도록 description에서 안내한다.

## 결과

- 실수로 지운 데이터는 upsert 한 번으로 돌아온다.
- soft delete된 데이터는 DB에 남는다. "완전히 잊기"를 원하면 hard delete가 필요하다는 점을 [security.md](../security.md)에 적었다.
