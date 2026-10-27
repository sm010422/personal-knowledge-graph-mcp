# 요청 흐름

Claude가 툴 하나를 부를 때 요청이 어디를 거쳐 Neo4j까지 가는지, 각 구간에서 무엇을 하고 무엇이 실패할 수 있는지 정리한다. 백업, 배포, 운영자 조회 경로도 함께 그린다.

기준: 2026-10-28. 구성 요소의 설정 근거는 [deployment.md](deployment.md), 서버 내부 계층은 [architecture.md](architecture.md)에 있다.

## 1. 전체 경로

```mermaid
flowchart LR
    subgraph Mac["이 Mac"]
        CC["Claude Code<br/>(HTTP 직접)"]
        CD["Claude 데스크톱 앱"]
        MR["mcp-remote<br/>(npx, stdio ↔ HTTP)"]
        NB["Neo4j Browser<br/>browser.neo4j.io"]
        PF["kubectl port-forward<br/>localhost:7687"]
    end
    CA["claude.ai 웹·모바일<br/>(OAuth 필요, 미연결)"]

    subgraph Edge["k3s-master · Tailscale"]
        F["Tailscale Funnel<br/>https://k3s-master.taildcdcee.ts.net<br/>TLS 종료 → :80"]
    end

    subgraph K["k3s · namespace personal-kg"]
        T["Traefik Ingress<br/>경로 /mcp 만"]
        M["personal-kg-mcp<br/>worker · :8000"]
        N[("neo4j-0<br/>master · :7687")]
        PV[("PVC data-neo4j-0<br/>local-path 2Gi")]
        B["CronJob neo4j-backup<br/>매일 04:00 KST"]
        NP{{"NetworkPolicy<br/>Bolt ← mcp, backup 만"}}
    end
    B2[("Backblaze B2<br/>암호문만")]

    CC -- "HTTPS + Bearer" --> F
    CD -- stdio --> MR -- "HTTPS + Bearer" --> F
    CA -. "향후 OAuth" .-> F
    F --> T --> M -- Bolt --> N --- PV
    B -- "Bolt (읽기)" --> N
    B -- "rclone crypt" --> B2
    NB -- "bolt:// WebSocket" --> PF -- "API 서버 터널" --> N
    NP -.- N
```

- 인터넷에 열린 입구는 **Funnel 하나**다. Funnel은 Traefik의 80번을 통째로 공개하므로, 실제로 열리는 경로는 Ingress가 정한다. 지금은 `/mcp` 하나다.
- Neo4j(7687)는 인터넷에도, 클러스터 안의 다른 파드에도 열려 있지 않다. 운영자는 `kubectl port-forward`로만 들어간다. 이 터널은 Kubernetes API 서버를 거치므로 kubeconfig 권한이 있어야 한다.
- 데스크톱 앱은 설정에 HTTP 헤더를 넣을 수 없다. 그래서 `mcp-remote`가 로컬에서 stdio 서버인 척하고, 원격으로는 토큰을 붙인 HTTP로 중계한다.

## 2. 툴 호출 한 번: `add_relation`

```mermaid
sequenceDiagram
    autonumber
    participant C as Claude (Code / 앱)
    participant F as Tailscale Funnel
    participant T as Traefik
    participant A as SharedTokenVerifier
    participant S as FastMCP 툴 (server.py)
    participant R as KnowledgeGraph (repository.py)
    participant N as Neo4j

    C->>F: POST /mcp (TLS, Authorization: Bearer …)
    F->>T: HTTP :80 (TLS는 여기서 끝남)
    T->>A: Ingress 규칙 /mcp → Service personal-kg-mcp:8000
    alt 토큰 없음/불일치
        A-->>C: 401
    end
    A->>S: tools/call add_relation (stateless 세션)
    S->>S: Pydantic 검증 (enum 오타, 필수 인자)
    S->>R: add_relation(...)
    R->>R: validate_properties + policy (민감정보 차단)
    R->>N: 쓰기 트랜잭션 (Bolt)
    Note over R,N: 엔티티 해석(이름·alias) → 관계 규칙 확인 →<br/>현재 관계 비교 → unchanged / created / superseded
    N-->>R: 커밋
    R-->>S: RelationResult
    S-->>C: 구조화된 JSON
```

## 3. 구간별 역할과 실패 모습

| 구간 | 하는 일 | 실패하면 클라이언트가 보는 것 | 확인 |
|---|---|---|---|
| Funnel | 공개 TLS 인증서, TLS 종료 | 연결 자체 실패 | 노드에서 `sudo tailscale funnel status` |
| Traefik | 경로 `/mcp` → Service 라우팅 | 404 (경로 불일치), 502/503 (파드 없음) | `kubectl get ingress -n personal-kg` |
| 토큰 검증 | SHA-256 다이제스트 상수 시간 비교, 토큰 2개(교체용) | 401 | `.env.remote`와 Secret 비교 |
| FastMCP 검증 | 인자 타입·enum | 툴 에러 (validation error) | 에러 메시지에 인자 이름이 나온다 |
| 정책 | 민감정보 키·값 차단 | 툴 에러 (저장 거부) | `docs/security.md` |
| 저장소 | 엔티티 해석, 관계 규칙, 이력 | 툴 에러 ("upsert_entity 먼저", "타입을 붙여라") | 메시지대로 다시 호출 |
| DB 연결 | 백그라운드 연결, 최대 10초 대기 | "not reachable yet" | `kubectl logs deploy/personal-kg-mcp -n personal-kg` |
| Neo4j | 저장, 제약(`entity_key` 유일) | 툴 에러 | `kubectl logs neo4j-0 -n personal-kg` |

## 4. 백업 경로

```mermaid
sequenceDiagram
    autonumber
    participant CJ as CronJob (04:00 KST)
    participant E as init: export (서버 이미지)
    participant N as Neo4j
    participant V as emptyDir /backup
    participant U as upload (rclone)
    participant B2 as Backblaze B2

    CJ->>E: Job 생성
    E->>N: 읽기 트랜잭션 하나로 노드·관계 전체 조회
    E->>V: neo4j-<UTC ts>.jsonl.gz (header·trailer 포함)
    E-->>CJ: "export ok: N nodes, M relationships"
    CJ->>U: init 성공 후 시작
    U->>U: rclone crypt 암호화 (NaCl secretbox)
    U->>B2: neo4j/<ts>.jsonl.gz.bin 업로드
    U->>B2: 업로드 성공 시에만 30일 지난 파일 삭제 (hard delete)
```

복구는 반대 방향이다. 로컬 rclone이 내려받으면서 복호화하고, `pkg-mcp-backup restore`가 빈 Neo4j에 넣는다. 절차는 [deployment.md "백업과 복구"](deployment.md#백업과-복구)에 있다.

## 5. 배포 경로

```mermaid
flowchart LR
    D["git push main"] --> CI["GitHub Actions<br/>lint + test"]
    CI --> BU["arm64 이미지 빌드"]
    BU --> DH[("Docker Hub<br/>:sha, :latest")]
    BU --> PIN["봇 커밋<br/>k8s/kustomization.yaml newTag"]
    PIN --> AR["ArgoCD<br/>(약 3분 주기 폴링)"]
    AR --> RO["Deployment 롤아웃<br/>CronJob 이미지도 같은 태그"]
    DH -. pull .-> RO
```

- `k8s/`만 바꾼 커밋은 CI를 건너뛰고 ArgoCD가 바로 반영한다.
- 롤백은 `newTag`를 이전 SHA로 되돌리는 커밋 하나다.
- 걸리는 시간: CI 약 1분 40초(2026-10-27 실측) + ArgoCD 폴링 대기 최대 약 3분.
