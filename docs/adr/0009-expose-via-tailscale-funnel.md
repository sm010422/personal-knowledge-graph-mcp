# ADR-0009: 외부 노출은 기존 Tailscale Funnel 재사용 (Cloudflare Tunnel은 준비만)

- 상태: Accepted
- 날짜: 2026-10-19

## 맥락

원래 계획은 Cloudflare Tunnel이었다. 하지만 Cloudflare Tunnel로 고정 URL을 만들려면 Cloudflare에 등록된 도메인이 필요한데, 현재 없다.

한편 클러스터에는 이미 Tailscale Funnel이 켜져 있다. k3s-master 노드의 tailscaled가 `https://k3s-master.taildcdcee.ts.net` → `localhost:80`(Traefik)으로 매핑하고 있다. TLS 인증서는 Tailscale이 자동으로 발급한다.

## 선택지

1. **Cloudflare Tunnel + 도메인 구매**: GitOps로 관리할 수 있고, WAF와 Access 정책을 앞단에 둘 수 있다. 도메인 비용과 초기 설정이 필요하다.
2. **Cloudflare Quick Tunnel (`trycloudflare.com`)**: 도메인 없이 된다. 하지만 URL이 재시작할 때마다 바뀌어서, 클라이언트 설정이 계속 깨진다.
3. **기존 Tailscale Funnel 재사용**: Ingress에 `/mcp` 경로만 추가하면 된다. 추가 구성 요소도 비용도 없다.

## 결정

3번이다. 사용자가 이 선택지를 골랐다. Cloudflare Tunnel 매니페스트(`k8s/cloudflare-tunnel/`)와 전환 절차는 미리 준비해 두되, kustomization에는 넣지 않는다.

## 결과

- 공개 URL: `https://k3s-master.taildcdcee.ts.net/mcp`
- **Funnel 노출 면이 Traefik 전체라는 점**이 이 결정의 핵심 위험이다. 앞으로 이 클러스터에 Ingress를 추가하면 자동으로 인터넷에 공개된다. 그래서 MCP 서버는 자체 인증(ADR-0007)을 필수로 하고, Ingress는 `/mcp` 경로만 연다.
- Funnel 설정은 노드 CLI 상태라 GitOps 밖에 있다. 노드를 다시 만들면 `sudo tailscale funnel 80`을 다시 실행해야 한다(k3s-msa-infrastructure의 `docs/Public-Access-via-Tailscale-Funnel.md`).
- k3s-master가 단일 장애점이다. 이 노드가 내려가면 공개 URL도 같이 내려간다.
