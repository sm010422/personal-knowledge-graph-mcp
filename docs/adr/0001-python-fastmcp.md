# ADR-0001: Python + FastMCP로 구현

- 상태: Accepted
- 날짜: 2026-10-19

## 맥락

MCP 서버 구현 언어로 Java 21 + Spring AI MCP Server와 Python 3.12 + FastMCP가 후보였다. 이 프로젝트는 백엔드/인프라/AX 직무 포트폴리오도 겸한다.

## 선택지

| | Java + Spring AI MCP | Python + FastMCP |
|---|---|---|
| MCP 생태계 | Spring AI 쪽 지원은 비교적 최근 | 공식 SDK가 Python/TS 중심, FastMCP가 사실상 표준이라 예제·이슈 해결 사례가 가장 많다 |
| Phase 4 (LLM 기반 엔티티 추출) | 가능하지만 라이브러리가 적음 | Anthropic SDK, 평가 도구 등 LLM 생태계가 Python 중심 |
| Neo4j 드라이버 | 공식, 성숙 | 공식, async 지원 |
| 포트폴리오 | 기존 target-tracking-service가 이미 Spring Boot | Java 일변도인 스택에 Python 백엔드 사례 추가 |
| 테스트 | Testcontainers 원조 | testcontainers-python으로 동일한 방식 가능 |

## 결정

Python 3.12 + FastMCP(4.x)를 쓴다. 패키지 관리는 uv, 린트·포맷은 ruff를 쓴다.

## 결과

- MCP 툴 시그니처를 Python 타입 힌트와 Pydantic `Field(description=...)`로 선언하면, 그게 그대로 LLM이 보는 JSON Schema가 된다. 툴 설명을 코드 옆에서 관리할 수 있다.
- Neo4j async 드라이버와 FastMCP lifespan으로 드라이버 수명을 서버 수명에 묶었다.
- Java/Spring 경험은 기존 프로젝트로 보여주고, 이 프로젝트는 Python 백엔드와 AI 툴링 사례로 쓴다.
