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

- 실제 개인 데이터, DB 덤프, 시드 데이터는 커밋하지 않는다. `.gitignore`가 `data/`, `dumps/`, `backups/`, `*.dump`, `*.jsonl.gz`, `seed/private/`, `neo4j/`를 막는다.
- 클러스터 백업은 업로드 전에 rclone crypt로 암호화한다. 저장소(B2)에는 암호문만 있다(ADR-0011). 복구하려고 내려받은 평문 덤프는 작업이 끝나면 바로 지운다.
- `scripts/seed_example.py`와 테스트 데이터는 모두 **가상 인물**(Jordan Lee, Alex, Example Corp 등)이다.
- 시크릿은 `.env`(git 제외)나 k8s Secret에만 둔다. 커밋되는 것은 `.env.example`뿐이다.

## 네트워크 노출

- **Phase 1 (stdio)**: 서버는 포트를 열지 않는다. Neo4j도 `127.0.0.1`에만 바인딩한다(`docker-compose.yml`).
- **Phase 2 (HTTP, k3s)**: 공개 URL `https://k3s-master.taildcdcee.ts.net/mcp`. 노출 면과 방어선은 이렇다.

| 계층 | 방어 |
|---|---|
| 전송 | Tailscale Funnel이 TLS를 종료한다(Let's Encrypt 자동 인증서) |
| 라우팅 | Ingress는 `/mcp`만 연다. `/health`·`/ready`와 Neo4j는 외부에서 닿지 않는다 |
| 인증 | 공유 Bearer 토큰, 상수 시간 비교, 32자 이상 강제. 토큰 없음·틀림은 401 ([ADR-0007](adr/0007-bearer-token-auth.md)) |
| 클러스터 내부 | NetworkPolicy: Neo4j 7687은 MCP 서버 파드에서만 접근할 수 있다. 다른 파드는 차단된다(실측) |
| 컨테이너 | non-root(uid 10001), 읽기 전용 루트 FS, capability 전부 제거, seccomp RuntimeDefault |
| 시크릿 | `kubectl create secret`으로만 생성한다. git에는 `secret.example.yaml` 템플릿만 있다. 클라이언트용 토큰 사본은 로컬 `.env.remote`(git-ignored, 600) |

**주의**: Funnel은 Traefik 80번 **전체**를 공개한다. 이 클러스터에 Ingress를 새로 추가하면 그 경로도 바로 인터넷에 열린다. 인증 없는 서비스에 Ingress를 붙이지 말 것.

## 삭제 요청

- `delete_entity(confirm=false)`는 영향 범위(관계 수, 노트 수)만 미리 보여준다.
- 기본 삭제는 soft delete(`deletedAt`)라서 복구할 수 있다.
- 사용자가 "지워달라"고 명시하면 `hard=true`로 엔티티, 속성 이력(`Revision`), **그 엔티티에만 연결된** 노트까지 물리적으로 지운다. 다른 엔티티에도 연결된 노트는 남는다.
