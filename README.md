# Personal Knowledge Graph MCP Server

Claude(claude.ai / Claude Desktop / Claude Code)가 사용자에 대한 장기 기억을 **지식 그래프**로 조회·기록할 수 있게 하는 MCP 서버.
프로젝트, 기술 스택, 의사결정, 경력, 일정을 노드와 관계로 저장하고, LLM이 대화 중 필요한 맥락을 쿼리하거나 새 사실을 적재한다.

```
Claude ──(MCP: stdio | Streamable HTTP + 인증)──> MCP Server ──(Bolt)──> Neo4j
```

- 언어: Python 3.12 + FastMCP
- DB: Neo4j 5 Community
- 배포: 홈 k3s 클러스터([k3s-msa-infrastructure](https://github.com/sm010422/k3s-msa-infrastructure)), 외부 노출은 Cloudflare Tunnel

## 로드맵

1. **Phase 1** — 로컬: docker-compose + stdio 모드 + 핵심 툴 + 통합 테스트 ← 진행 중
2. Phase 2 — HTTP 모드 + 토큰 인증 + k3s 배포 + Cloudflare Tunnel
3. Phase 3 — Prometheus 메트릭, Grafana 대시보드, 백업 CronJob
4. Phase 4 — 대화 텍스트에서 엔티티/관계 자동 추출, 시간축 질의
