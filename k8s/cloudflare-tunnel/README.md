# Cloudflare Tunnel (준비만 됨, 비활성)

현재 외부 노출은 Tailscale Funnel(`https://k3s-master.taildcdcee.ts.net/mcp`)로 한다. 이 디렉터리는 Cloudflare에 도메인이 생겼을 때 전환하기 위한 준비물이다. 왜 지금은 Funnel인지는 [ADR-0009](../../docs/adr/0009-expose-via-tailscale-funnel.md)에 있다.

## 전환 절차

1. **터널 생성**: Cloudflare 대시보드 → Zero Trust → Networks → Tunnels → *Create a tunnel* (Cloudflared). 이름은 `personal-kg`. 발급된 **토큰**을 복사한다.
2. **Public hostname 추가** (같은 화면):
   - Hostname: `mcp.<도메인>`
   - Service: `http://personal-kg-mcp.personal-kg.svc.cluster.local:8000`
   - Path는 비워 둔다. 서버는 `/mcp`(토큰 필요), `/health`·`/ready`(상태만 반환)에만 응답하고 나머지는 404다. 프로브 경로까지 숨기려면 Path에 `mcp`를 지정한다.
3. **토큰 Secret 생성** (git에 넣지 않는다):
   ```bash
   kubectl create secret generic cloudflared-token -n personal-kg --from-literal=token='<터널 토큰>'
   ```
4. **활성화**: `k8s/kustomization.yaml`의 `resources`에 `cloudflare-tunnel/cloudflared.yaml`을 추가하고 push한다. ArgoCD가 배포한다.
5. **확인**:
   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' -X POST https://mcp.<도메인>/mcp   # 401 이어야 함
   ```
6. **Funnel 경로 정리(선택)**: 더 이상 Funnel로 노출하지 않으려면 `k8s/ingress.yaml`을 `resources`에서 빼고, Claude 클라이언트의 URL을 새 도메인으로 바꾼다.

## Funnel 대비 차이

| | Tailscale Funnel (현재) | Cloudflare Tunnel |
|---|---|---|
| 도메인 | `*.ts.net` (무료, 자동 TLS) | 내 도메인 필요 |
| 경로 | k3s-master tailscaled → Traefik → Service | 클러스터 안 cloudflared → Service (Traefik 우회) |
| 단일 장애점 | k3s-master 노드 | cloudflared 파드 (아무 노드로 재스케줄 가능) |
| GitOps 관리 | 아님 (노드에서 CLI로 켬) | 됨 (Deployment) |
| 추가 보호 | 없음 | WAF, Access 정책(이메일 인증 등)을 앞단에 둘 수 있음 |
