# 2026-10-27~28 — CI 자동 배포, 암호화 백업, 첫 실사용

Phase 2 배포(10-19) 뒤 8일 만에 이어서 진행했다. 이전 대화 세션은 로컬에 남아 있지 않았다. 그래서 `docs/TODO.md`를 기준으로 다시 시작했다. TODO를 "현재 상태 + 주의사항 + 할 일" 형태로 남겨 둔 덕분에 맥락을 잃지 않았다.

이 문서는 **무엇을 어떤 순서로 했고, 무엇을 확인했고, 어디서 막혔는지**를 남긴다. 최종 구성과 절차는 [deployment.md](../deployment.md), 경로 다이어그램은 [request-flow.md](../request-flow.md)에 있다.

## 0. 출발점

| 항목 | 상태 |
|---|---|
| 클러스터 | `personal-kg` Synced/Healthy, 이미지 `0ed20de` (10-19에 로컬에서 수동 push) |
| CI | test ✅ / build ❌ (`Username and password required`) — `DOCKERHUB_TOKEN` 없음 |
| 백업 | 없음. 클러스터 DB가 유일한 사본 |
| 클라이언트 | **어디에도 등록 안 됨**. 배포 후 8일 동안 실제로 쓰인 적이 없다 |
| 클러스터 DB | 비어 있음 |

## 1. CI 자동 배포 켜기 (TODO 1번)

- Docker Hub에서 Personal access token(Read & Write)을 만들었다. 처음에는 Repositories 화면에서 "새 리포를 만들어야 하나" 헷갈렸다. 리포(`sm010422/personal-knowledge-graph-mcp`)는 이미 있었다. 토큰은 Account settings → Personal access tokens에서 만든다.
- `gh secret set DOCKERHUB_TOKEN`은 사용자가 직접 실행했다. 토큰은 대화에 붙여넣지 않았다.
- `gh workflow run CI` → 세 job 모두 성공(run 40463394862, 1분 41초). 봇이 `f253cab chore(deploy): pin image 4174d06 [skip ci]`를 커밋했고, ArgoCD가 새 파드를 띄웠다.
- 공개 URL 재확인: 토큰 없이 401, 토큰 있으면 `initialize` 200.

남은 것: CI 로그에 "Node.js 20 actions are deprecated" 경고가 있다. 지금은 Node 24로 강제 실행돼서 동작한다. 액션 버전을 올리면 사라진다.

## 2. 백업 저장소 고르기

선택지는 다른 노드의 PVC / 홈서버 Mac 디스크 / 외부 S3 호환 스토리지였다. **외부**를 골랐다. 홈서버는 정전이나 디스크 고장 한 번에 원본과 백업을 같이 잃는다. 추석 정전 사례가 실제로 있었다.

- 처음에는 Cloudflare R2를 추천했다. 하지만 R2는 무료라도 결제 수단을 등록해야 한다.
- 사용자는 **Backblaze B2**를 골랐다. 카드 없이 10GB 무료다.
- 버킷 `sm010422-personal-kg-backup`(Private, Object Lock 끔)과 **그 버킷 하나에만 쓸 수 있는** 앱 키(Read and Write)를 만들었다. 값은 레포 루트 `.env.backup`(git 제외, 권한 600)에 저장했다.
- 이 Mac에서 aws CLI로 쓰기 → 목록 → 삭제를 해 보고 키가 동작하는 것을 확인했다.

## 3. 백업 방식 설계 (ADR-0011)

**덤프 방식.** `neo4j-admin database dump`는 Community에서 DB를 멈춰야만 된다. StatefulSet을 0으로 줄이면 ArgoCD selfHeal이 바로 되돌린다. APOC export는 플러그인을 켜고 설정을 바꿔야 한다. 그래서 **서버 이미지에 Bolt 논리 덤프 명령(`pkg-mcp-backup`)을 직접 넣었다.** 드라이버와 이미지가 이미 있고, Neo4j는 건드리지 않는다.

`server/src/pkg_mcp/backup.py`의 설계 포인트:

| 문제 | 처리 |
|---|---|
| 날짜·시간 속성이 JSON에서 문자열이 되면 복원 후 `validFrom` 비교가 깨진다 | `{"$date": "2026-09-01"}`, `{"$datetime": ...}`처럼 태그를 붙인다. 복원할 때 같은 Neo4j 타입으로 돌린다 |
| 파일이 중간에 잘려도 모를 수 있다 | 마지막 줄 trailer에 노드·관계 개수를 적는다. trailer가 없거나 개수가 다르면 복원을 거부한다 |
| 쓰는 중에 실패하면 반쪽 파일이 남는다 | `.partial`로 쓰고 끝나면 rename한다 |
| 관계의 양 끝 노드가 덤프 시점 차이로 빠질 수 있다 | 노드와 관계를 **한 read 트랜잭션**에서 읽는다 |
| 기존 데이터에 덮어써서 섞일 수 있다 | **빈 DB에만 복원**한다. 노드가 하나라도 있으면 거부한다 |
| 깨진 백업에 중복 키가 있을 수 있다 | 복원 전에 제약(`entity_key`, `note_id`)부터 만든다. 중복이면 바로 실패한다 |
| 노드 ID가 새 DB에서 바뀐다 | 노드를 만들 때 `옛 elementId → 새 elementId` 매핑을 받는다. 관계는 그 매핑으로 연결한다 |
| 라벨·관계 타입이 데이터마다 다르다 | Neo4j 5.26의 동적 라벨 `CREATE (n:$(row.labels))`, `[r:$(row.type)]` 사용. 클러스터 버전이 5.26.32인 것을 먼저 확인했다 |
| 로그에 개인 데이터가 찍힐 수 있다 | 출력은 개수만 한다 (`export ok: N nodes, M relationships`) |

**테스트.** 인코딩 단위 테스트 14개를 추가했다. 통합 테스트(Testcontainers)는 3개다.
- 가상 데이터를 넣는다. alias, Revision, 닫힌 관계(validTo), 노트, soft delete까지 포함한다.
- export → 전체 삭제 → restore 후, 노드·관계 내용이 **타입까지** 같은지 비교한다.
- 복원한 DB에서 `get_context(include_history)`와 한국어 전문 검색이 동작하는지 확인한다.
- 비어 있지 않은 DB에 복원하면 거부하는지, 잘린 파일은 거부하는지 확인한다.

로컬 Colima가 꺼져 있어서 통합 테스트가 처음에 Docker 연결 오류로 실패했다. Colima를 켜고 다시 돌렸다. 전체 85개 통과.

**암호화.** 저장소 쪽 암호화(SSE-B2)만으로는 운영자가 평문을 볼 수 있다. 그래서 **rclone crypt**를 썼다. 업로드 전에 NaCl secretbox로 암호화한다. 이미지 하나(`rclone/rclone:1.75.2`, arm64 있음)가 암호화, 업로드, 정리를 다 한다.
- 파일 이름은 암호화하지 않는다(`filename_encryption=off`). 이름이 타임스탬프뿐이고, 이름과 날짜로 정리해야 하기 때문이다.
- rclone의 **b2 백엔드 + `hard_delete`**를 썼다. B2는 기본적으로 지운 파일을 숨김 버전으로 남겨서 용량을 계속 차지한다. S3 호환 API로 지우면 이 숨김 버전이 남는다.

**CronJob** (`k8s/backup-cronjob.yaml`)
- 매일 04:00 KST(`timeZone: Asia/Seoul`), `concurrencyPolicy: Forbid`, 900초 제한.
- init container(서버 이미지)가 emptyDir에 덤프한다. 본 컨테이너(rclone)가 암호화 → 업로드 → **업로드가 성공한 뒤에만** 30일 지난 파일을 지운다.
- 두 컨테이너 모두 non-root, 읽기 전용 루트 파일시스템, capability 전부 제거.
- 서버 이미지 태그는 kustomize `images`가 Deployment와 CronJob에 똑같이 적용한다. CI 봇 커밋 하나로 둘 다 갱신된다.
- NetworkPolicy에 `app: personal-kg-backup` 파드를 Bolt 허용 대상으로 추가했다.

## 4. 막힌 곳: Secret 생성

Claude가 `kubectl create secret`을 실행하려 하자 auto mode 분류기가 막았다(Secret 저장소 쓰기). 우회하지 않았다. 대신 `scripts/create-backup-secret.sh`를 만들어 사용자가 직접 실행했다.
- 처음 실행하면 `BACKUP_PASSPHRASE`(40자 랜덤)를 만들어 `.env.backup`에 추가한다.
- `rclone obscure`로 변환한 값과 B2 키로 Secret `personal-kg-backup`을 만든다.
- 출력은 키 이름뿐이다.

```
Generated BACKUP_PASSPHRASE in .env.backup — copy it to your password manager.
secret/personal-kg-backup created
Secret keys: B2_APPLICATION_KEY B2_BUCKET B2_KEY_ID BACKUP_PASSWORD_OBSCURED
```

**암호 문구를 잃으면 백업 전체를 풀 수 없다.** 사본은 클러스터 Secret과 이 Mac에만 있다. 둘을 같이 잃는 경우를 대비해서 비밀번호 관리자에 보관해야 한다(TODO에 사용자 할 일로 남김).

## 5. 백업 검증

1. `kubectl create job --from=cronjob/neo4j-backup` → 19초 만에 Complete. `export ok: 0 nodes, 0 relationships`. 이 시점의 클러스터 DB는 비어 있었다.
2. B2를 crypt 없이 직접 보면 `neo4j-20261027T0732Z.jsonl.gz.bin`(228B)이 있고, 첫 바이트가 `RCLONE\0\0`이다. **암호문만 저장된다.**
3. 로컬 rclone(`scripts/backup-rclone-env.sh`)으로 내려받으면서 복호화했다. header와 trailer가 정상이었다.
4. 일회용 `neo4j:5-community` 컨테이너에 `pkg-mcp-backup restore` → `restore ok`. 제약 `entity_key`, `note_id`가 생성된 것도 확인했다. 확인 후 컨테이너와 평문 파일을 지웠다.

**한계**: 빈 DB로 경로만 검증했다. 실제 데이터가 생긴 지금, 데이터가 있는 상태로 리허설을 한 번 더 해야 한다(TODO 2번).

## 6. 원래 목표 점검

여기서 한 번 멈추고 README의 목표와 비교했다.

> Claude가 대화 중에 사용자에 대한 장기 기억을 지식 그래프로 조회하고 기록한다.

인프라(배포, CI, 백업)는 앞서가 있었다. 그런데 **어떤 Claude도 이 서버를 쓰고 있지 않았다.** TODO에 적어 둔 대로 품질은 툴 설명을 다듬는 데서 갈리고, 그건 실제로 써 봐야만 할 수 있다. Phase 3 관측성(메트릭, 대시보드)도 호출이 없으면 볼 게 없다. 그래서 순서를 "백업 마무리 → 클라이언트 연결 → 데이터 적재 → 실사용 관찰 → Phase 3"으로 바꿨다.

사용자의 실제 목적도 확인했다. **지금 회사와 대학원에 지원하고 있고, Claude가 나를 더 잘 알면 자소서나 지원 준비를 더 잘 도와줄 것**이라는 것이다.

## 7. Claude Code 등록 (TODO 3번)

```bash
claude mcp add --scope user --transport http personal-kg "$MCP_URL" --header "Authorization: Bearer $MCP_AUTH_TOKEN"
```

- user scope라서 어느 프로젝트에서 열어도 같은 기억을 쓴다.
- `claude mcp list` → `✔ Connected`, `tools/list` → 툴 8개.
- 로컬 stdio 서버는 등록하지 않았다. 둘 다 등록하면 기억이 두 DB로 갈라진다.

## 8. Claude 데스크톱 앱 연결

사용자가 주로 쓰는 곳은 데스크톱 앱이었다. 앱 설정(`claude_desktop_config.json`)은 stdio 명령만 받고 HTTP 헤더를 넣을 칸이 없다. OAuth를 구현하기 전에 쓸 수 있는 방법으로 **`mcp-remote`**를 썼다. 로컬에서 stdio 서버인 척하고, 토큰을 붙여 원격 HTTP로 중계하는 npm 패키지다.

- Claude가 `npx mcp-remote`로 미리 시험하려 하자 auto mode가 막았다(외부 코드 실행). 토큰을 넘겨받는 서드파티 패키지라서 설치 여부는 사용자가 정하는 게 맞았다. 사용자가 직접 설정 파일을 고쳤다.
- 헤더는 `"Authorization:${AUTH_HEADER}"`로 공백 없이 넣고, 실제 값은 `env.AUTH_HEADER`에 둔다. 인자에 공백이 있으면 앱이 인자를 잘못 나누는 문제가 있어서다.

**막힌 곳: `npm error EACCES`.** 로그(`~/Library/Logs/Claude/mcp-server-personal-kg.log`)를 보면 앱은 `npx`를 정상적으로 실행했다. 그런데 npx가 `~/.npm/_cacache`에 쓰지 못하고 바로 종료했다. `_cacache` 아래 **1,323개 파일이 root 소유**였다. 10월 7일에 `sudo npm ...`을 실행했던 흔적으로 보인다. 사용자가 `sudo chown -R 501:20 ~/.npm`을 실행했고, 다음 실행에서 `initialize` → `tools/list`가 성공했다.

첫 화면에는 연결된 도구가 표시되지 않는다. 그래서 "안 보인다"는 혼동이 한 번 있었다. "personal-kg에서 찾아봐"라고 물어서 툴 호출(`Used Personal-kg`)로 연결을 확인했다. 그때 DB는 비어 있었다.

## 9. 첫 데이터 적재

데스크톱 앱의 Claude가 **자기 메모리에 있던 사용자 정보를 personal-kg로 옮겼다**(10-28 02:11 KST 무렵). 결과는 노드 50개 정도였다. Person(본인), Project(Sofly, OrbitTracer, sentinel-stack, k3s-msa-infrastructure 등), Organization(학교, 이전 직장, 활동, 지원한 대학원), Technology, Decision, Note, Revision이다. 본인 노드에는 관계가 17개 붙었다.

**첫 관찰 (TODO 4번 재료)**
- `STUDIED_AT`, `MEMBER_OF`처럼 오래전부터 이어진 관계의 `validFrom`이 **기록한 날(2026-10-27)**로 들어갔다. 날짜를 안 주면 오늘이 기본값이기 때문이다. 그래서 "2025년 9월 기준"으로 조회하면 이 관계들이 없었던 것으로 나온다.
- 기간을 아는 관계도 `period: "2023.07–2024.02"`를 텍스트 속성으로 저장했다. `validFrom`/`validTo`라는 날짜 필드가 따로 있는데 쓰지 않은 것이다.
- → 툴 설명과 `INSTRUCTIONS`에 "과거 사실은 `valid_from`을 실제 시작일로"를 넣는 게 후보다. 며칠 더 관찰한 뒤 한 번에 고친다.

## 10. 그래프 눈으로 보기

`scripts/graph-browser.sh`를 추가했다. `kubectl port-forward`로 Bolt를 localhost에 열고, Neo4j 비밀번호를 클립보드에 복사한다(출력하지 않음). 그다음 https://browser.neo4j.io 에서 접속한다. DB는 외부에 열지 않는다.

**막힌 곳: `neo4j://`로 접속하면 실패.** `ServiceUnavailable: Could not perform discovery. No routing servers available` 오류가 났다. `neo4j://`는 서버에 라우팅 테이블을 묻는데, 서버는 클러스터 내부의 파드 주소를 알려 준다. 브라우저는 그 주소에 닿을 수 없다. **`bolt://localhost:7687`**(직접 연결)로 바꾸니 됐다. 스크립트 안내와 문서를 고쳤다.

또 하나 헷갈린 점:
- `MATCH (n:Project) RETURN n`은 노드만 가져와서 점만 떠 있었다.
- 사이드바의 관계 목록은 데이터가 쌓이는 도중에 읽어서 `REVISION_OF` 하나만 보였다.
- 서버의 `get_context`로 관계 17개가 있는 것을 확인했다. 경로를 반환하는 쿼리(`MATCH p=(a)-[r]->(b) ... RETURN p`)를 안내했다.

## 11. 현재 상태 (10-28)

| 파드 | 노드 | 메모리 |
|---|---|---|
| `neo4j-0` | k3s-master | 757Mi (요청 768Mi, 상한 1Gi) |
| `personal-kg-mcp` | k3s-worker1 | 105Mi |
| `neo4j-backup-*` | 실행 때만 | Completed |

- 노드 메모리: master 66%, worker1 48%, worker2 45%.
- ArgoCD Synced/Healthy, 이미지 `15dbb5b` 이후 문서 커밋만 있음.
- 테스트 85개(단위 49, 통합 36).
- ADR 1개 추가(0011), 문서 추가: `request-flow.md`, 이 devlog.

## 이번에 배운 것

- **TODO에 "현재 상태 + 주의사항"을 남겨 두면** 세션 기록이 없어도 바로 이어서 할 수 있다.
- **인프라가 쓰임새보다 앞서가기 쉽다.** 배포 후 8일 동안 클라이언트 등록 한 줄을 안 했다. 목표 대비 점검을 중간에 한 번 하는 게 효과가 컸다.
- auto mode가 막은 두 작업(Secret 쓰기, 외부 패키지 실행)은 **사람이 판단해야 하는 지점**이었다. 스크립트로 만들어 넘기는 방식이 잘 맞았다.
- 클라이언트 연결 문제는 대부분 로그 한 파일(`mcp-server-<name>.log`)에 답이 있었다.

## 다음

1. 데이터가 있는 상태로 백업 복구 리허설 다시 하기 (TODO 2번)
2. 며칠 실사용하면서 관찰한 결과로 툴 설명 다듬기 (TODO 4번). 첫 후보는 `valid_from` 사용
3. claude.ai 웹·모바일이 필요해지면 OAuth (TODO 11번)
4. Phase 3: 메트릭, Sentry(에러 추적), 다운·백업 실패 알림. Istio는 클러스터 단위 실험으로 따로 (TODO 참고)
