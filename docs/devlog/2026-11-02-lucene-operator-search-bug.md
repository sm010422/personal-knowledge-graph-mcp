# 2026-11-02 — 검색어에 AND / OR / NOT이 있으면 `search`가 통째로 실패하던 버그

외부 사용자가 [이슈 #1](https://github.com/sm010422/personal-knowledge-graph-mcp/issues/1)로 제보한 버그를 재현하고 고쳤다. 이 문서는 **원인, 재현 방법, 고친 방식과 그 이유, 검증**을 남긴다.

## 0. 증상

`search("cats AND dogs")`가 결과를 돌려주지 않고 예외로 끝났다.

```
neo4j.exceptions.ClientError: Failed to invoke procedure `db.index.fulltext.queryNodes`:
Caused by: org.apache.lucene.queryparser.classic.ParseException:
Encountered " <AND> "AND "" at line 1, column 17.
```

`NOT sure`, `this OR that`, 그냥 `AND` 한 단어도 같은 이유로 실패한다.

## 1. 원인

`search`는 사용자 입력을 그대로 Lucene에 넘기지 않는다. `build_fulltext_query`(`server/src/pkg_mcp/repository.py`)가 Lucene 쿼리 문자열로 바꾼다.

1. 공백으로 단어를 나눈다.
2. 단어마다 Lucene 특수문자(`+ - ! ( ) { } [ ] ^ " ~ * ? : \ / && ||`)를 백슬래시로 escape한다.
3. 라틴 문자 단어에는 접두어 검색 `kube*`를 하나 더 붙인다.
4. 전부 ` OR `로 잇는다.

2번은 **기호**만 막았다. Lucene classic QueryParser는 대문자 `AND`, `OR`, `NOT`을 **단어 형태의 연산자**로 읽는데, 이 단어들은 escape 대상이 아니었다.

```
입력:   cats AND dogs
결과:   cats OR cats* OR AND OR and* OR dogs OR dogs*
                         ^^^^^^^^
                         연산자 두 개가 연달아 → ParseException
```

접두어 절(`and*`)은 이미 소문자라서 문제가 없었다. 원래 단어 절만 대문자로 남아 있었다.

### 왜 실제로 터질 수 있나

이 서버의 `search`를 부르는 쪽은 사람이 아니라 LLM이다. "Kubernetes와 ArgoCD 관련 기록 찾아줘" 같은 요청을 LLM이 `Kubernetes AND ArgoCD`로 바꿔 넘기는 것은 아주 흔한 패턴이다. 그러면 검색 결과가 없는 게 아니라 **툴 호출 자체가 에러**가 된다. 그러면 Claude는 "기억이 없다"고 잘못 판단하거나 재시도에 턴을 쓴다.

## 2. 먼저 재현했다

제보 내용을 그대로 믿지 않고 실패하는 테스트부터 만들었다. 기존 통합 테스트 `test_search_survives_lucene_syntax`(Testcontainers로 실제 Neo4j 5 Community를 띄운다)에 케이스를 추가했다.

```python
for query in ("cats AND dogs", "NOT sure", "this OR that", "AND"):
    assert isinstance(await graph.search(query), list)
```

수정 전에 돌려서 위의 `ParseException`으로 **실패하는 것을 확인**했다. 단위 테스트만으로는 "Lucene이 실제로 이 문자열을 거부한다"는 사실을 확인할 수 없어서 실제 DB에서 재현했다.

## 3. 수정

```python
# Lucene reads these as operators only in upper case; the analyzer lowercases anyway.
_LUCENE_OPERATORS = {"AND", "OR", "NOT"}

...
        escaped = _LUCENE_SPECIAL.sub(r"\\\1", term)
        if escaped in _LUCENE_OPERATORS:
            escaped = escaped.lower()
        clauses.append(escaped)
```

결과: `cats AND dogs` → `cats OR cats* OR and OR and* OR dogs OR dogs*`

### 고려한 방법

| 방법 | 판단 |
|---|---|
| **소문자로 바꾸기** (채택) | Lucene은 대문자일 때만 연산자로 읽는다. 인덱스의 `cjk` analyzer가 색인과 질의를 모두 소문자로 바꾸므로 **검색 결과는 대문자일 때와 같다**. 한 줄이고 다른 경로에 영향이 없다 |
| 따옴표로 감싸기 (`"AND"`) | 이슈에서 제안한 방법이다. 동작은 하지만 phrase query가 되어 의미가 달라 보이고, 결과는 소문자 방식과 같다 |
| 백슬래시 escape (`\AND`) | Lucene classic parser는 단어 연산자를 백슬래시로 escape하는 방식을 보장하지 않는다 |
| 사용자에게 불리언 문법 허용 | 입력이 LLM이 만든 자연어라서 연산자를 의도한 것인지 알 수 없다. 이 서버는 "모든 단어 OR + 점수 정렬" 정책을 유지한다 |

이번에 함께 확인한 점: 기호 연산자(`&&`, `||`, `!`)는 이미 `_LUCENE_SPECIAL`에서 escape되고 있었다. 단어 연산자만 빠져 있었다.

## 4. 검증

- 단위 테스트: `build_fulltext_query("cats AND dogs")`가 위 문자열과 정확히 같은지 확인한다.
- 통합 테스트: 네 가지 입력이 실제 Neo4j에서 예외 없이 결과 리스트를 돌려준다.
- 결과: 단위 54개, 검색 통합 테스트 6개 모두 통과. `ruff check` 통과.

## 5. 배운 점

- **escape는 기호만의 문제가 아니다.** 쿼리 언어에는 키워드도 문법이다. SQL의 예약어, Cypher의 키워드처럼 Lucene에도 단어 연산자가 있다. 사용자 입력을 쿼리 문자열로 조립할 때는 "어떤 토큰이 문법으로 해석되는가"를 기호와 단어 둘 다 기준으로 봐야 한다.
- **LLM이 호출하는 툴은 입력이 더 넓다.** 사람이 쓰는 검색창보다 LLM이 `AND`, `OR`를 넣을 가능성이 높다. MCP 툴은 "어떤 입력이 와도 에러 대신 결과(빈 결과 포함)를 돌려준다"를 기본 계약으로 두는 게 맞다.
- **외부 제보는 재현부터.** 제보에 있던 원인 분석은 정확했다. 그래도 실패하는 테스트를 먼저 만들고 고친 덕분에, 이 테스트가 앞으로 같은 회귀를 막는다.
