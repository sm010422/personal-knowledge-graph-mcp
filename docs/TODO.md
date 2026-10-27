# 할 일 목록

기준일: 2026-10-28 (백업·클라이언트 연결 후). 이전 기준 2026-10-19. 끝난 항목은 체크하고, 관련 커밋이나 문서를 옆에 적는다.

담당 표시: **[나]** = 직접 해야 함(계정·권한·결정 필요) · **[Claude]** = 요청하면 진행 가능

---

## 📍 현재 상태 한눈에

| 항목 | 상태 |
|---|---|
| 클러스터 배포 | namespace `personal-kg`, ArgoCD `personal-kg` Synced/Healthy |
| 공개 엔드포인트 | `https://k3s-master.taildcdcee.ts.net/mcp` (Bearer 토큰 필수) |
| Neo4j | `neo4j-0`, k3s-master 고정, PVC `data-neo4j-0` 2Gi (local-path) |
| MCP 서버 | `personal-kg-mcp`, worker 노드, 1 replica |
| 배포된 이미지 | `k8s/kustomization.yaml`의 `newTag` (커밋 SHA) |
| CI | test ✅ / build ✅ / 태그 커밋 ✅ (2026-10-27 활성화) |
| 백업 | CronJob `neo4j-backup` 매일 04:00 KST → B2 (암호화, 30일 보관) |
| 클라이언트 | Claude Code(user scope, HTTP) · Claude 데스크톱 앱(mcp-remote) — 같은 클러스터 DB |
| 데이터 | 2026-10-28 첫 적재(노드 약 50개). 그래프 보기: `scripts/graph-browser.sh` |
| 토큰 위치 | 클러스터 Secret `personal-kg-secrets` + 이 Mac의 `.env.remote` 두 곳뿐 |
| 기존 C4I 앱 | replicas 0으로 중지 중 (`244a066`), 헬스체크 워크플로 비활성 |
| 로컬 | Colima 실행 중, 로컬 Neo4j `pkg-neo4j`(가상 데이터만) 실행 중 |

---

## ⚠️ 알아둬야 할 점

모르고 지나가면 데이터 유실, 보안 사고, 장애로 이어질 수 있는 것들이다.

### 데이터
- **백업은 하루 한 번이다.** 매일 04:00 KST에 B2로 백업한다. 마지막 백업 이후에 쓴 내용은 그 사이 장애가 나면 잃는다. 백업이 실패해도 알림은 아직 없다(`kubectl get jobs -n personal-kg`로 확인) → 할 일 7-1.
- **`BACKUP_PASSPHRASE`를 잃으면 백업 전체를 풀 수 없다.** 클러스터 Secret과 이 Mac의 `.env.backup`에만 있다. 비밀번호 관리자에 따로 보관한다.
- **로컬 DB와 클러스터 DB는 서로 다른 DB다.** 로컬 stdio 서버(docker compose Neo4j)에 쓴 내용은 클러스터에 없다. 반대도 마찬가지다. 실사용은 원격 하나로 통일하는 게 좋다.
- **실제 개인 데이터는 레포에 절대 넣지 않는다.** 레포가 public이다. 시드 스크립트, 테스트, 덤프, 스크린샷 모두 해당된다. `.gitignore`가 일부 막지만, 마지막 방어선은 사람이다.
- **soft delete는 데이터가 남는다.** "완전히 잊기"가 필요하면 `delete_entity(confirm=true, hard=true)`를 써야 한다.

### 보안
- **Funnel은 Traefik 전체를 인터넷에 공개한다.** 이 클러스터에 Ingress를 하나 추가하면 그 경로도 즉시 공개된다. 인증 없는 서비스에 Ingress를 붙이지 말 것.
- **MCP 토큰이 새면 기억 전체를 읽고 쓰고 지울 수 있다.** 채팅, 스크린샷, 커밋에 붙여넣지 말 것. 샜다고 의심되면 바로 교체한다(`docs/deployment.md` "토큰 교체").
- **`/health`·`/ready`는 토큰 없이 응답한다.** 지금은 Ingress가 `/mcp`만 열어서 외부에 안 보인다. Ingress 경로를 `/`로 넓히면 노출된다. 노출돼도 상태 문자열뿐이지만, 의도한 설계는 아니다.

### 운영
- **다운 알림이 꺼져 있다.** C4I 앱을 내리면서 `ai-health-check` 워크플로를 비활성화했다. 그래서 **클러스터나 personal-kg가 죽어도 ntfy 알림이 오지 않는다.** Phase 3 모니터링 전까지는 직접 확인해야 한다.
- **k3s-master가 단일 장애점이다.** Neo4j, Funnel 공개 URL, control-plane이 모두 master에 있다. master가 내려가면 서비스 전체가 멈춘다. 홈서버 Mac 전원이 끊긴 추석 사례(`k3s-msa-infrastructure/docs/Chuseok-Power-Outage-Recovery.md`)가 그대로 적용된다.
- **ArgoCD Application `personal-kg`를 지우지 말 것.** `prune: true`라서 PVC까지 같이 삭제될 수 있다. 내리고 싶으면 replicas 0으로 바꿔서 커밋한다.
- **Neo4j 비밀번호는 Secret만 바꾸면 안 된다.** `NEO4J_AUTH`는 데이터 디렉터리가 비어 있을 때 한 번만 적용된다. 먼저 Neo4j 안에서 `ALTER USER`를 실행한다(`docs/deployment.md`).
- **수동으로 바꾼 리소스는 되돌아간다.** `selfHeal: true`라서 `kubectl edit`나 `kubectl scale`로 바꾼 값은 ArgoCD가 git 상태로 복원한다. 변경은 항상 git으로 한다.
- **C4I 앱을 그대로 복구하면 master 메모리가 모자란다.** gitnexus와 Neo4j가 둘 다 master에 고정돼 있다 → 할 일 10번.
- **Funnel 설정은 GitOps 밖에 있다.** 노드 CLI로 켠 상태다. master VM을 다시 만들면 `sudo tailscale funnel 80`을 다시 실행해야 한다.

### CI · 레포
- **CI가 봇 커밋을 만든다(토큰 등록 후부터).** 배포할 때마다 `chore(deploy): pin image ...` 커밋이 `main`에 생긴다. 로컬에서 push하기 전에 항상 `git pull --rebase`를 한다.
- **`k8s/`만 바꾸면 CI가 돌지 않는다.** 이건 의도된 동작이다(경로 필터). ArgoCD가 바로 sync한다.
- **롤백은 커밋 하나다.** `k8s/kustomization.yaml`의 `newTag`를 이전 SHA로 되돌려 push한다.

### 클라이언트
- **claude.ai 웹·모바일과 Claude Desktop 커넥터로는 아직 원격 연결이 안 된다.** Bearer 토큰이라 원격은 Claude Code에서만 된다. Desktop은 로컬 stdio로 붙인다 → 할 일 11번.
- **서버가 막 재시작된 직후 첫 툴 호출은 최대 10초 기다린 뒤 실패할 수 있다.** "not reachable yet" 에러다. DB 연결이 끝나면 다시 정상 동작한다.

### 로컬 환경
- **Colima 설정을 한 줄 고쳤다.** `~/.colima/_lima/colima/colima.yaml`의 `disk`를 60 → 100으로 바꿨다. 실제 디스크 크기에 맞춘 것이다. 백업은 `colima.yaml.bak-20261019`.
- **Testcontainers는 Colima 소켓을 자동으로 찾는다.** `tests/integration/conftest.py`가 docker context에서 읽는다. Docker Desktop이나 OrbStack으로 바꿔도 그대로 동작한다.

---

## 🔴 지금 바로 (차단 요소 / 데이터 안전)

### 1. Docker Hub 토큰 등록 → CI 자동 배포 활성화 **[나]**
지금은 CI의 build 단계가 `Username and password required`로 실패한다. 그래서 이미지를 로컬에서 수동으로 push하고 있다.

1. https://hub.docker.com → Account settings → Personal access tokens → **Read & Write** 토큰을 만든다.
2. 등록 후 재실행한다.
   ```bash
   gh secret set DOCKERHUB_TOKEN -R sm010422/personal-knowledge-graph-mcp   # 프롬프트에 토큰 붙여넣기
   gh workflow run CI -R sm010422/personal-knowledge-graph-mcp
   ```
- [x] 토큰 등록 (2026-10-27)
- [x] **[Claude]** 파이프라인 전체 확인 (run 40463394862, 봇 커밋 `f253cab`, 롤아웃 확인): test → build → `chore(deploy): pin image ...` 봇 커밋 → ArgoCD가 새 이미지로 롤아웃

**완료 기준**: `gh run list`에서 세 job이 모두 success이고, `kubectl get deploy -n personal-kg personal-kg-mcp -o jsonpath='{..image}'`가 최신 커밋 SHA다.

### 2. Neo4j 백업 **[Claude]**
매일 04:00 KST, 암호화해서 Backblaze B2로 백업한다. 30일 보관(ADR-0011, `docs/deployment.md` "백업과 복구").
- [x] 백업 방식 결정: Bolt 논리 덤프(`pkg-mcp-backup`, 무중단) → rclone crypt → B2 (ADR-0011)
- [x] CronJob 매니페스트 (`k8s/backup-cronjob.yaml`), NetworkPolicy 허용
- [x] 저장 위치: Backblaze B2 무료 버킷 `sm010422-personal-kg-backup` (2026-10-27)
- [x] Secret `personal-kg-backup` 생성 (`scripts/create-backup-secret.sh`)
- [x] 수동 백업 1회 성공, B2에는 암호문만 있음 확인, 로컬 일회용 Neo4j에 복원 성공 (2026-10-27, 당시 클러스터 DB는 0 nodes)
- [ ] **[나]** `.env.backup`의 `BACKUP_PASSPHRASE`를 비밀번호 관리자에 보관
- [ ] 실제 데이터를 넣은 뒤(5번) 복구 리허설 한 번 더: 개수와 `get_context` 결과 비교

**완료 기준**: 실제 데이터로 복원 리허설을 한 번 성공한다. 지금은 빈 DB로 경로만 검증된 상태다.

---

## 🟠 이번 주 (실제로 쓰기 시작)

### 3. Claude Code에 원격 서버 등록 **[Claude]** (확인만 [나])
```bash
cd /Users/parksangmin/coding/portfolio/personal-knowledge-graph-mcp
source .env.remote
claude mcp add --transport http personal-kg "$MCP_URL" --header "Authorization: Bearer $MCP_AUTH_TOKEN"
```
- [x] user scope로 등록, `claude mcp list` ✔ Connected, `tools/list` 8개 확인 (2026-10-27)
- [x] 로컬 stdio 서버는 등록하지 않음. 원격 하나만 사용

### 4. 실사용하면서 툴 description 다듬기 **[나 + Claude]**
코드보다 이게 품질을 좌우한다. 며칠 실제 대화에 써 보고 관찰한다.
- [ ] 저장해야 할 사실을 LLM이 놓치는 경우
- [ ] 쓸데없는 걸 저장하는 경우
- [ ] 관계 타입을 잘못 고르는 경우, 특히 `RELATED_TO` 남용
- [ ] 중복 엔티티 생성(검색 없이 바로 upsert)
- [ ] 관찰 결과를 `docs/devlog/`에 기록하고 description과 `INSTRUCTIONS` 수정

### 5. 초기 데이터 적재 **[나]**
- [ ] 나(Person, `isSelf: true`), 진행한 프로젝트들, 주요 기술 스택, 지원 중인 회사와 일정
- [ ] **주의**: 실제 데이터는 클러스터 DB에만 둔다. 레포, 시드 스크립트, 덤프 파일에는 절대 넣지 않는다(`docs/security.md`)
- [ ] 2번(백업)이 끝난 뒤에 하는 걸 권장

---

## 🟡 Phase 3 — 관측성 & 운영

### 6. Prometheus 메트릭 **[Claude]**
- [ ] `/metrics` 엔드포인트: 툴별 호출 수, 지연(histogram), 에러 수(도메인 에러 / 정책 위반 / DB 미연결 구분), 관계 타입별 생성 수(`RELATED_TO` 남용 관찰용, ADR-0004)
- [ ] 기존 클러스터 Prometheus(`k3s-msa-infrastructure/apps/monitoring`) scrape 설정에 추가
- [ ] Ingress는 `/mcp`만 열려 있으므로 `/metrics`는 외부에 노출되지 않는다. 그 상태가 유지되는지 확인

### 7. Grafana 대시보드 **[Claude]**
- [ ] 툴 호출량과 지연, 에러율, Neo4j 파드 메모리, 노드 메모리
- [ ] 대시보드 JSON을 ConfigMap으로 관리(기존 monitoring 앱 방식)

### 7-1. 다운 알림 복구 **[Claude]**
- [ ] `ai-health-check` 워크플로가 꺼져 있어서 지금은 장애 알림이 전혀 없다
- [ ] 선택지: (a) personal-kg 엔드포인트만 확인하는 새 워크플로(공개 URL에 토큰 없이 요청해서 401이면 정상) (b) Grafana alert → ntfy
- [ ] C4I 앱 복구 여부(10번)와 함께 결정

### 7-2. Sentry 에러 추적 **[나: 계정 / Claude: 구현]**
- [ ] Sentry SaaS 무료 플랜(Developer). self-hosted는 메모리가 수 GB 필요해서 이 클러스터에 못 올린다
- [ ] `sentry-sdk` 초기화는 `SENTRY_DSN`이 있을 때만. DSN은 Secret에 둔다
- [ ] **개인 데이터 차단이 핵심**: `send_default_pii=False`, `include_local_variables=False`, 요청 본문 미첨부, `before_send`에서 툴 인자·예외 메시지 속 값 제거. 툴 이름과 에러 타입만 남긴다
- [ ] 도메인 에러(엔티티 없음, 관계 규칙 위반)는 정상 흐름이라 보내지 않는다. 예상 못 한 예외만
- [ ] 백업 CronJob 실패도 Sentry Crons(체크인)로 감시 → 7-1 백업 실패 알림과 같이 해결

### 8. 로깅 정리 **[Claude]**
- [ ] 현재 `logger.info("Connected to Neo4j ...")`가 기본 로깅 설정에서 안 보인다(ADR-0010 남은 점)
- [ ] 구조화(JSON) 로그. 툴 이름, 소요 시간, 결과 상태를 남기되 **툴 인자 내용(개인 데이터)은 로그에 남기지 않는다**

### 9. 시작 백오프 상한 조정 **[Claude]**
- [ ] 최대 15초 → 5초. 동시 재시작 시 정상화가 39초에서 약 25초로 줄어들 것으로 예상된다. 재현 테스트로 확인

---

## 🔵 결정이 필요한 것

### 10. 보류 중인 C4I 앱 처리 **[나]**
gitnexus(약 1.27GB)와 Neo4j가 둘 다 k3s-master에 고정돼 있어서, `git revert 244a066`으로 그대로 복구하면 master 메모리가 부족하다.
- [ ] 선택: (a) 계속 중지 (b) gitnexus만 빼고 복구 (c) master VM 메모리 증설 (d) personal-kg를 내리고 복구
- 참고: `k3s-msa-infrastructure/docs/Personal-KG-MCP-Deployment.md` "주의 1"

### 11. claude.ai 웹·모바일 연결 (OAuth) **[나: 결정 / Claude: 구현]**
현재는 Bearer 토큰이라 Claude Code에서만 원격으로 쓸 수 있다.
- [ ] 필요한지 결정
- [ ] 필요하면 IdP 선택(GitHub / Google), FastMCP OAuth 구성, ADR-0011(또는 다음 번호) 작성

### 12. Cloudflare Tunnel 전환 **[나]**
- [ ] 도메인 구매 여부 결정. 사면 `k8s/cloudflare-tunnel/README.md` 절차대로 전환
- 장점: k3s-master 단일 장애점 제거, GitOps 관리, WAF·Access 정책

---

## ⚪ 정리 · 위생

### 13. 보안 위생 **[나]**
- [ ] `server1` VM SSH 비밀번호 변경 (대화에 평문으로 공유됐음)
- [ ] MCP 토큰 첫 교체 연습 (`docs/deployment.md` "토큰 교체"). 절차가 실제로 무중단인지 확인

### 14. 로컬 개발 환경 정리 **[나]**
- [ ] 로컬 Neo4j 컨테이너(`pkg-neo4j`)는 가상 시드 데이터만 있다. 안 쓰면 `docker compose down` (볼륨까지 지우려면 `-v`)
- [ ] Colima는 계속 켜져 있다. 안 쓰면 `colima stop`
- [ ] 로컬 이미지 정리: `docker image prune` / `personal-knowledge-graph-mcp:local`
- [ ] `~/.colima/_lima/colima/colima.yaml.bak-20261019`: Colima가 정상 기동하는 걸 확인했으니 지워도 된다

### 15. k3s-msa-infrastructure 레포 **[나 + Claude]**
- [ ] `graphify-out/` 변경분이 커밋되지 않은 채 남아 있다. 커밋할지 되돌릴지 결정
- [ ] GitNexus 인덱스가 오래됐다는 경고 → `gitnexus analyze --index-only` (선택)

---

## 🧪 실험 (이 프로젝트 밖, 클러스터 단위)

### 16. Istio 서비스 메시 **[나 + Claude]**
personal-kg에 꼭 필요한 기능은 아니다. 서비스가 2개(MCP 서버, Neo4j)이고 Neo4j는 HTTP가 아니라 Bolt(TCP)라서 Istio의 L7 기능(재시도, 트래픽 분할, HTTP 지표)은 MCP 서버 쪽에만 의미가 있다. 배우는 목적으로는 좋다: mTLS, Kiali로 요청 흐름 시각화, 카나리 배포.
- [ ] 메모리 확인이 먼저다. master 66%. **sidecar 모드는 파드마다 프록시가 붙어 부담이 크다 → ambient 모드(노드당 ztunnel)** 권장
- [ ] k3s-msa-infrastructure 레포에서 클러스터 단위로 설치·관리(ArgoCD). Traefik은 그대로 입구로 두고 메시 안쪽만 Istio
- [ ] 이 프로젝트는 namespace에 ambient 라벨만 붙이는 사용자가 된다. 적용 전후 지연과 메모리를 재서 devlog로 남긴다
- [ ] NetworkPolicy와 Istio AuthorizationPolicy가 겹치는 부분을 정리한다

## 🟣 Phase 4 (나중)
- [ ] 대화 텍스트에서 LLM으로 엔티티·관계 자동 추출 → `provenance: inferred`, `confidence`로 적재
- [ ] 시간축 질의 툴: "2026년 9월 기준 지원 현황"처럼 `validFrom/validTo`를 쓰는 질의(ADR-0003 구조 활용)
- [ ] 이름 변경(rename) 툴 (ADR-0005의 남은 점)

---

## 권장 순서

```
1 Docker Hub 토큰 ─▶ 2 백업 ─▶ 3 Claude Code 등록 ─▶ 5 초기 데이터 ─▶ 4 실사용 관찰
                                                                         │
                     6·7·8·9 Phase 3 (관측성) ◀───────────────────────────┘
10·11·12 결정은 언제든 · 13 비밀번호 변경은 가능한 빨리
```
