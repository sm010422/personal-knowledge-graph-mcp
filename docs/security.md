# 보안 · 개인정보 정책

이 서버는 한 사람에 대한 장기 기억을 저장한다. 그래서 "무엇을 저장하지 않는가"가 기능만큼 중요하다.

## 저장하지 않는 정보

| 분류 | 예 |
|---|---|
| 건강 | 진단명, 병력, 복용 약 |
| 재정 | 연봉, 소득, 계좌번호, 카드번호 |
| 인증 정보 | 비밀번호, API 토큰, 시크릿 |
| 식별번호 | 주민등록번호, 여권번호 |

### 코드로 강제하는 부분 (`server/src/pkg_mcp/policy.py`)

- **속성 키 차단**: `password`, `token`, `salary`, `health`, `주민`, `계좌`, `진단` 등을 포함하는 키는 거부한다.
- **값 패턴 차단**: 주민등록번호 형태(`######-#######`)와 카드번호 형태(`####-####-####-####`)는 속성 값, 이름, alias, 노트 본문 어디에 있어도 거부한다.
- 거부되면 `PolicyViolation` 에러가 LLM에게 그대로 전달되고, 아무것도 기록되지 않는다.

이 검사는 **최선의 노력(best-effort)** 이다. "어제 병원에서 들은 얘기" 같은 자연어는 패턴으로 잡을 수 없다. 그래서 MCP 서버 `instructions`와 툴 description에도 같은 정책을 적어, LLM이 애초에 저장하지 않도록 한다.

## 리포지토리

- 실제 개인 데이터, DB 덤프, 시드 데이터는 커밋하지 않는다. `.gitignore`가 `data/`, `dumps/`, `backups/`, `*.dump`, `seed/private/`, `neo4j/`를 막는다.
- `scripts/seed_example.py`와 테스트 데이터는 모두 **가상 인물**(Jordan Lee, Alex, Example Corp 등)이다.
- 시크릿은 `.env`(git 제외)나 k8s Secret에만 둔다. 커밋되는 것은 `.env.example`뿐이다.

## 네트워크 노출

- **Phase 1 (stdio)**: 서버는 포트를 열지 않는다. Neo4j도 `127.0.0.1`에만 바인딩한다(`docker-compose.yml`).
- **Phase 2 (HTTP)**: 인증 없는 엔드포인트는 노출하지 않는다. Bearer 토큰 → OAuth 순으로 검토하고, 외부 노출은 Cloudflare Tunnel로만 한다.

## 삭제 요청

- `delete_entity(confirm=false)`는 영향 범위(관계 수, 노트 수)만 미리 보여준다.
- 기본 삭제는 soft delete(`deletedAt`)라서 복구할 수 있다.
- 사용자가 "지워달라"고 명시하면 `hard=true`로 엔티티, 속성 이력(`Revision`), **그 엔티티에만 연결된** 노트까지 물리적으로 지운다. 다른 엔티티에도 연결된 노트는 남는다.
