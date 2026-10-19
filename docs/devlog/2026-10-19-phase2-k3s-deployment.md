# 2026-10-19 — Phase 2: k3s 배포 과정

Phase 1(로컬, stdio)이 끝난 뒤 같은 날 Phase 2를 진행했다. "지금 k3s에 올라간 게 아니라 로컬 Docker로 도는 것 아니냐"는 질문에서 시작했다. 맞았다. Phase 1은 Neo4j만 로컬 Colima 컨테이너였고, 서버는 Claude가 자식 프로세스로 띄우는 stdio 방식이라 클러스터와 무관했다.

최종 구성과 운영 절차는 [deployment.md](../deployment.md)에 있다. 이 문서는 **어떤 순서로 했고, 무엇을 확인했고, 무엇이 문제였는지**를 남긴다.

## 0. 사전 조사

배포 방식을 기존 앱과 맞추기 위해 먼저 클러스터와 형제 레포를 확인했다.

| 확인한 것 | 결과 | 영향 |
|---|---|---|
| threat-intel-ai-service의 `deploy.yml` | GitHub Actions `ubuntu-24.04-arm` → Docker Hub `:sha`, `:latest` | arm64 러너와 Docker Hub를 그대로 쓴다 |
| 이미지 갱신 방식 | ArgoCD Image Updater가 `:latest` digest를 추적하고 인프라 레포에 write-back | 이 프로젝트는 다르게 한다 ([ADR-0008](../adr/0008-image-delivery-by-manifest-commit.md)) |
| 로컬 Docker Hub 로그인 | `sm010422`로 로그인됨 | 첫 이미지는 로컬에서 push할 수 있다 |
| 레포 Secrets | 이 레포에는 `DOCKERHUB_*` 없음 (형제 레포에만 있음, 값은 읽을 수 없음) | CI build는 사용자가 토큰을 넣기 전까지 실패한다 |
| `cloudflared` | 미설치, 도메인 없음 | 외부 노출 방식을 사용자에게 물었다 |
| **Tailscale Funnel** (`docs/Public-Access-via-Tailscale-Funnel.md`) | k3s-master에서 Traefik 80번 **전체**가 `https://k3s-master.taildcdcee.ts.net`으로 인터넷에 공개 중 | Ingress를 추가하는 순간 공개된다. 토큰 인증이 배포 전 필수 조건이 됐다 |
| 노드 taint / 메모리 | master에 `NoSchedule` taint. 여유 메모리는 master가 가장 큼(약 1.6GiB) | Neo4j는 gitnexus와 같은 방식(affinity + toleration)으로 master에 둔다 |

외부 노출은 Cloudflare 도메인이 없어서 세 가지 선택지(도메인 있음 / Funnel 재사용 / 둘 다 준비)를 제시했다. 사용자는 **Funnel 재사용**을 골랐다.

## 1. HTTP 모드 + 토큰 인증

**설치된 API부터 확인했다.** FastMCP 4.1.1 소스에서 다음을 직접 읽었다.
- `TokenVerifier`, `StaticTokenVerifier`
- `custom_route`
- `run(transport="http", ...)`의 Host 검사 규칙(`_resolve_allowed_hosts_for_run`, `HostOriginMiddleware`)

**발견 1 — 내장 검증기는 운영용이 아니다.** `StaticTokenVerifier`는 docstring에 "Never use this in production"이 적혀 있다. 그래서 `SharedTokenVerifier`를 직접 만들었다. SHA-256 다이제스트 후 `hmac.compare_digest`로 비교하고, 토큰 2개를 허용하고, 32자 미만은 거부한다.

**발견 2 — Host 검사 동작.** 루프백(`127.0.0.1`)으로 바인딩하면 Host 헤더 검사가 자동으로 켜진다. 그러면 Ingress를 거쳐 들어오는 `k3s-master.taildcdcee.ts.net` Host는 421로 막힌다. `0.0.0.0` 바인딩에서는 꺼진다. 통합 테스트도 클러스터와 같은 `0.0.0.0`으로 띄우고, 공개 Host 헤더로 요청해서 421이 아님을 확인하게 했다. 처음에는 테스트를 `127.0.0.1`로 띄웠는데, 이 차이를 알고 나서 바꿨다.

**추가한 것**
- `Settings`에 `MCP_TRANSPORT`, `MCP_HOST/PORT/PATH`, `MCP_AUTH_TOKEN(_NEXT)` 추가
- `/health`(liveness), `/ready`(readiness)
- `main()`: http 모드에서 토큰이 없으면 시작을 거부한다
- `stateless_http=True`: 세션 상태가 없어서 파드가 재시작돼도 클라이언트가 끊기지 않는다

**테스트**: 실제 `python -m pkg_mcp.server` 프로세스를 HTTP로 띄우는 통합 테스트 6개, 검증기 단위 테스트 7개.
- 프로브는 토큰 없이 200이다.
- `/mcp`는 토큰이 없을 때, 틀렸을 때, `Basic` 스킴일 때 모두 401이다.
- 올바른 토큰으로 툴 호출이 된다.
- 공개 Host 헤더로도 요청이 막히지 않는다.

테스트 헬퍼에서 서버가 뜨기 전 `URLError`(connection refused)를 처리하지 않아 처음에 6개가 모두 에러였다. 대기 루프에서 0을 반환하도록 고쳤다.

## 2. 이미지와 CI

- **Dockerfile**: uv 멀티 스테이지, `python:3.12-slim`, uid 10001.
- **로컬 검증**
  - Colima(arm64)에서 빌드했다.
  - compose의 Neo4j 네트워크에 붙여 실행하고, `/ready` 200, 토큰 없이 401, 토큰으로 `initialize` 성공을 확인했다.
  - `id`가 uid 10001이었고, 메모리는 약 96MiB였다.
  - `--read-only --cap-drop ALL --security-opt no-new-privileges`에서도 정상 동작했다. 그래서 k8s에 `readOnlyRootFilesystem: true`를 넣었다.
- **`serverInfo.version` 수정**: `initialize` 응답에 FastMCP 버전(4.1.1)이 찍혀 있었다. 패키지 버전(`importlib.metadata.version("pkg-mcp")`)을 넘기도록 고쳤다.
- **CI 워크플로**: test → build(arm64) → deploy(태그 커밋).

**CI 첫 실행**
1. 첫 실행은 `ruff format --check`에서 실패했다. `config.py` 한 줄이 포맷되지 않은 상태로 커밋됐다. 다음 커밋에서 이미 포맷이 고쳐져 있었고, 두 번째 실행에서 **test job은 통과**했다. GitHub 러너에서 Testcontainers Neo4j 통합 테스트까지 포함한 결과다.
2. build job은 예상대로 `Username and password required`로 실패했다. `DOCKERHUB_USERNAME`은 비밀값이 아니라서 설정했고, `DOCKERHUB_TOKEN`은 사용자가 넣어야 한다.

**첫 이미지는 로컬에서 push했다.** CI와 같은 규칙(전체 커밋 SHA 태그 + `latest`)이다.
- push 출력이 이상해서(`...mcpatest:latest`) Docker Hub API로 확인했더니 `latest` 태그가 없었다. 다시 태그하고 push했다.
- 그 뒤 리포가 public이고 태그 2개가 있는지 API로 확인했다.

## 3. 매니페스트

`k8s/` 아래에 Namespace, Neo4j(headless Service + StatefulSet + PVC), MCP 서버(Service + Deployment), Ingress(`/mcp`), NetworkPolicy, kustomization을 만들었다.

- `kubectl kustomize`와 `kubectl apply --dry-run=client -k k8s`로 검증했다. server dry-run은 namespace가 아직 없어서 실패하는데, 이건 정상이다.
- **Neo4j 메모리 산정**: heap 384m + pagecache 128m + JVM·네이티브 오버헤드. request 768Mi / limit 1Gi. 실측은 629~669MiB다.
- **Secret**: `openssl rand`로 만들어 `kubectl create secret`으로만 넣었다. MCP 토큰은 클라이언트 연결용으로 로컬 `.env.remote`(git-ignored, 권한 600)에만 저장했다.

## 4. ArgoCD 등록과 첫 배포 — CrashLoopBackOff

`k3s-msa-infrastructure/argocd/applications/personal-kg.yaml`을 만들어 `kubectl apply`했다. 10초 간격으로 상태를 관찰한 결과는 이렇다.

```
Synced/Progressing | neo4j-0:Pending            personal-kg-mcp:ContainerCreating
Synced/Progressing | neo4j-0:ContainerCreating  personal-kg-mcp:CrashLoopBackOff
Synced/Progressing | neo4j-0:Running            personal-kg-mcp:CrashLoopBackOff   (×7)
Synced/Healthy     | neo4j-0:Running            personal-kg-mcp:Running            (재시작 4회)
```

결국 Healthy가 됐지만, 그냥 넘기지 않고 원인을 봤다.

```
neo4j.exceptions.ServiceUnavailable: Failed to DNS resolve address neo4j:7687: [Errno -2] Name or service not known
```

**원인 분석**
- Neo4j는 headless Service라 **파드가 Ready가 되기 전까지 DNS 레코드가 아예 없다.**
- 서버는 lifespan 시작에서 연결에 실패하면 프로세스가 종료되는 구조였다.
- 재시작할 때마다 kubelet backoff가 늘어나서, Neo4j가 Ready가 된 뒤에도 한참 기다렸다.

이 클러스터에서 추석 정전 때 threat-intel-ai-service의 Kafka consumer가 겪은 것과 **같은 종류의 문제**다(시작 순서 경쟁 + 재시도 없음).

**수정** ([ADR-0010](../adr/0010-background-db-connect.md))
- 연결과 스키마 부트스트랩을 백그라운드 태스크로 옮기고, 지수 백오프(2→4→8→15초)로 재시도한다.
- `/ready`는 연결 전까지 503을 반환한다.
- 툴 호출은 최대 10초 기다린 뒤, 그래도 안 되면 명확한 에러를 반환한다.
- 테스트를 추가했다. 아무것도 듣지 않는 포트(`bolt://127.0.0.1:1`)로 설정해도 서버가 뜨고, 툴 목록이 나오고, 호출은 "not reachable yet" 에러를 낸다.

**수정 검증 — 일부러 장애를 재현했다**

| 시나리오 | 결과 |
|---|---|
| `neo4j-0` 삭제 | Neo4j 19초 만에 Ready. MCP 파드 재시작 0회 |
| `neo4j-0`과 MCP 파드 **동시** 삭제 (첫 배포와 같은 조건) | MCP 재시작 0회, **39초** 만에 둘 다 Ready (이전: 재시작 4회, 약 150초) |

동시 삭제 때 로그에 백오프 재시도가 그대로 찍혔다.

```
Neo4j not ready (attempt 1) ... retry in 2s
Neo4j not ready (attempt 2) ... retry in 4s
Neo4j not ready (attempt 3) ... retry in 8s
Neo4j not ready (attempt 4) ... retry in 15s
```

Neo4j는 17초에 Ready가 됐는데, MCP는 다음 재시도(15초 대기)까지 기다렸다. 최대 백오프를 낮추면 더 빨라진다(ADR-0010 "남은 개선점").

## 5. 외부 노출 검증

**공개 URL** `https://k3s-master.taildcdcee.ts.net/mcp`로 이 Mac에서 직접 확인했다.

| 요청 | 결과 |
|---|---|
| 토큰 없음 | 401 |
| `Bearer nope` | 401 |
| FastMCP 클라이언트 + 토큰 | 툴 8개, `upsert_entity` → `get_context` → `delete_entity(hard)` 성공. 4번 호출 + 세션 수립 왕복 5.27초 |
| `/health` | MCP 서버에 닿지 않음 (503, Traefik의 다른 규칙). Ingress가 `/mcp`만 열기 때문 |
| `:7687` | 연결 불가 |

테스트용 엔티티는 hard delete로 지워서 클러스터 DB에는 아무것도 남기지 않았다.

**NetworkPolicy가 실제로 적용되는지** 확인했다. k3s는 기본으로 kube-router 기반 NetworkPolicy를 지원한다. busybox 파드로 확인했다.

| 출발지 | `neo4j:7687` |
|---|---|
| `default` namespace 파드 | BLOCKED |
| `personal-kg` namespace, 다른 라벨 파드 | BLOCKED |
| MCP 서버 파드 | REACHABLE |

Cloudflare Tunnel은 `k8s/cloudflare-tunnel/`에 Deployment와 전환 절차만 준비했다. kustomization에는 넣지 않았다. 이미지 태그는 Docker Hub에서 실제로 있는 `2026.10.1`을 확인해서 고정했다.

## 결과 요약

- 클러스터: `personal-kg` namespace, ArgoCD `Synced/Healthy`
  - neo4j-0: k3s-master, 669Mi
  - personal-kg-mcp: worker1, 106Mi
- 노드 메모리: master 67%, worker1 52%, worker2 53%
- 테스트: 68개 (단위 35, 통합 33)
- ADR 4개 추가 (0007~0010)

## 남은 일

1. **`DOCKERHUB_TOKEN` 설정 (사용자).** 그다음 `gh workflow run CI`로 test → build → 태그 커밋 → ArgoCD 반영까지 한 번에 도는지 확인한다.
2. **Claude Code에 원격 서버 등록** (`claude mcp add --transport http ...`).
3. **Phase 3**: Prometheus 메트릭(툴 호출 수/지연/에러), Grafana 대시보드, Neo4j 백업 CronJob. 백업이 생기기 전까지는 클러스터 DB가 유일한 사본이다. 실제 데이터를 많이 쌓기 전에 백업부터 할 것.
4. claude.ai 웹 연결이 필요해지면 OAuth (ADR-0007 후속).
