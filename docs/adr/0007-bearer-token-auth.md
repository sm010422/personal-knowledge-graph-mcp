# ADR-0007: HTTP 모드 인증은 공유 Bearer 토큰 (OAuth는 후속)

- 상태: Accepted
- 날짜: 2026-10-19

## 맥락

Phase 2부터 서버가 네트워크에 열린다. 게다가 k3s-master의 Tailscale Funnel이 Traefik 80번 포트 전체를 인터넷에 공개하고 있어서, Ingress에 등록하는 순간 누구나 접근 가능한 URL이 된다. 저장된 내용은 한 사람의 장기 기억이라, 인증 없는 노출은 허용할 수 없다.

사용자는 한 명(본인)이다.

## 선택지

1. **공유 Bearer 토큰**: 구현과 운영이 단순하다. Claude Code는 `--header`로 지원한다. claude.ai/Claude Desktop의 커스텀 커넥터는 OAuth 흐름을 기대해서 바로 붙지 않는다.
2. **OAuth 2.1 (FastMCP의 OAuth Proxy + GitHub/Google 등)**: claude.ai 커넥터와 호환된다. 대신 IdP 앱 등록, 콜백 URL, 토큰 저장소가 필요하고, 1인 사용 대비 구성 요소가 많다.
3. **네트워크 수준 보호만 (tailnet 전용, Funnel 미사용)**: 토큰 관리가 필요 없다. 하지만 tailnet 밖(claude.ai 서버)에서 접근할 수 없다.

## 결정

1단계는 **공유 Bearer 토큰**이다. OAuth는 claude.ai 연결이 필요해지는 시점에 별도 ADR로 결정한다.

구현 세부:
- FastMCP 내장 `StaticTokenVerifier`는 쓰지 않는다. 문서에 "테스트·개발용, 운영 금지"라고 명시돼 있다(평문 dict 조회).
- `SharedTokenVerifier`를 직접 구현한다(`server/src/pkg_mcp/auth.py`).
  - 입력 토큰과 저장 토큰을 SHA-256으로 다이제스트한 뒤 `hmac.compare_digest`로 비교한다. 길이 차이도 타이밍으로 새지 않는다.
  - 모든 저장 토큰과 비교하고, 일치해도 조기 종료하지 않는다.
  - 32자 미만 토큰은 시작 단계에서 거부한다.
  - `MCP_AUTH_TOKEN`과 `MCP_AUTH_TOKEN_NEXT` 두 개를 동시에 허용해서, 무중단으로 교체할 수 있다.
- `/health`, `/ready`는 토큰 없이 응답한다. Kubernetes 프로브용이고, 상태 문자열 외에는 아무것도 드러내지 않는다. Ingress는 `/mcp`만 라우팅해서 외부에서는 이 경로들에 닿지 않는다.

## 결과

- 외부에서 토큰 없이 또는 틀린 토큰으로 요청하면 401이다(공개 URL에서 실측).
- 토큰이 새면 전권이 넘어간다. 그래서 교체 절차를 [deployment.md](../deployment.md#토큰-교체)에 두었다.
- claude.ai 웹/모바일에서는 아직 쓸 수 없다. 현재 지원 클라이언트는 Claude Code(원격, 헤더 지정)와 Claude Desktop(로컬 stdio)이다.
