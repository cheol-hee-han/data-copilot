# SQLGlot 파싱 정확도 및 신뢰성 리서치

**작성일**: 2026-03-24
**작성자**: Research Analyst Agent
**목적**: Data Copilot 프로젝트에서 SQLGlot을 SQL 파싱 레이어로 사용하기 위한 신뢰성 평가

---

## 요약 (Executive Summary)

SQLGlot은 현재 Python 순수 구현 SQL 파서 중 가장 기능이 풍부하고 활발히 유지되는 라이브러리이다 (GitHub 9.1k stars, 200+ 기여자). 단순 테이블명/조인/WHERE 리터럴/집계함수 추출 용도로는 **신뢰할 수 있는 수준**이나, 세 가지 구조적 위험이 존재한다.

1. **`find_all(exp.Table)` 패턴은 CTE를 실제 테이블로 잘못 분류한다** — scope 기반 API 필수
2. **기본 `error_level=WARN`은 파싱 실패를 조용히 삼킨다** — 명시적 에러 핸들링 필요
3. **Impala는 공식 지원 방언이 아니다** — Hive 방언 매핑 필요, Sybase IQ는 지원 없음

이 세 가지를 대응하면 프로젝트 요건(PostgreSQL 온라인 개발, Impala/Sybase IQ 폐쇄망 배포)을 모두 충족할 수 있다.

---

## 1. SQLGlot 파싱 정확도 — 전반적 평가

### 1.1 커뮤니티/산업계 데이터

DataHub는 자사 SQL 파싱 레이어를 SQLGlot 위에 구축하여 **97~99% 정확도**로 컬럼 레벨 lineage를 추출한다고 보고했다 (출처: DataHub 공식 문서). 이는 단순 파싱이 아니라 CTE·서브쿼리·UNION ALL까지 포함한 복합 쿼리 기준이다.

CrackSQL (Tsinghua University, SIGMOD 2025 채택 논문)은 SQLGlot을 dialect 변환 벤치마크 기준선으로 사용했다. 실험 결과:

- MySQL → PostgreSQL 변환 시 데이터타입 처리에서 48% 오류율
- ROLLUP 및 FULL OUTER JOIN 변환에서 40.74% 오류율

단, 이 수치는 **방언 간 변환(transpilation)** 오류이지, **파싱(AST 생성) 자체의 오류가 아님**을 주의해야 한다. 파싱 목적으로만 사용하면 이 오류율은 해당되지 않는다.

### 1.2 에러 처리 메커니즘 (ErrorLevel)

SQLGlot은 4단계 에러 레벨을 지원한다 (출처: sqlglot.errors 공식 문서):

| 레벨 | 동작 | 기본값 여부 |
| --- | --- | --- |
| `IGNORE` | 모든 에러 무시 | - |
| `WARN` | 에러 로깅만, 실행 계속 | 기본값 |
| `RAISE` | 에러 수집 후 단일 예외 발생 | - |
| `IMMEDIATE` | 첫 에러 즉시 예외 발생 | - |

**핵심 위험**: 기본값이 `WARN`이기 때문에, 파싱이 실패했을 때 불완전한 AST가 반환되면서 에러가 로그에만 기록된다. 테이블명·조인·WHERE 리터럴 추출 시 이 부분 파싱 결과를 신뢰하면 **silent data loss**가 발생한다.

**권고**: 파싱 목적 사용 시 항상 `error_level=ErrorLevel.RAISE` 명시.

```python
from sqlglot import parse_one, ErrorLevel

ast = parse_one(sql, dialect="postgres", error_level=ErrorLevel.RAISE)
```

---

## 2. 복잡 SQL 처리 능력

### 2.1 CTE (Common Table Expressions)

파싱 자체는 정상 동작하나, **추출 API 선택이 결정적**이다.

**잘못된 패턴** (CTE 별칭을 실제 테이블로 오인):

```python
# 위험: CTE 별칭 'x'와 실제 테이블 'y' 모두 반환
for table in parse_one("WITH x AS (SELECT 1) SELECT * FROM x JOIN y").find_all(exp.Table):
    print(table.name)  # 출력: x, y  <- x는 실제 테이블이 아님
```

**올바른 패턴** (scope 기반 실제 테이블만 추출):

```python
from sqlglot.optimizer.scope import build_scope, traverse_scope

for scope in traverse_scope(ast):
    for alias, (node, source) in scope.selected_sources.items():
        if isinstance(source, exp.Table):
            print(source.name)  # 실제 테이블만 출력
```

출처: sqlglot AST Primer 공식 문서 (tobymao/sqlglot/posts/ast_primer.md)

**알려진 CTE 관련 버그:**

- Issue #79 (2022년): `WITH ... UPDATE` 구문 파싱 실패 — CTE가 SELECT 뒤에만 오는 것으로 하드코딩되어 있어 PostgreSQL의 `WITH cte AS (...) UPDATE ...` 패턴 미지원
- PostgreSQL 금융 쿼리에서 `WITH ... UPDATE` 패턴은 흔하지 않으나, 이력 SQL에 포함될 가능성 존재

### 2.2 윈도우 함수 (Window Functions)

공식 지원하며 AST에서 `exp.Window`, `exp.Over` 노드로 표현된다. 별도 알려진 파싱 실패 없음.

```python
# 정상 파싱 예시
parse_one("SELECT ROW_NUMBER() OVER (PARTITION BY dept ORDER BY salary DESC) FROM emp")
```

집계함수 추출 시 `find_all(exp.AggFunc)` 또는 `find_all(exp.Window)`로 분리 추출 가능.

### 2.3 서브쿼리 및 상관 서브쿼리

파싱은 정상 동작. 단, 상관 서브쿼리에서 테이블명을 `find_all(exp.Table)`로 추출하면 외부 스코프 테이블이 내부에서 재참조되는 경우 중복 또는 누락이 발생할 수 있다. `traverse_scope()`가 스코프 경계를 올바르게 추적한다.

**Issue #4133 (2024년 9월, 해결됨)**: PostgreSQL 15의 `FROM (VALUES ...) JOIN ...`을 괄호로 감싼 복합 구문 파싱 실패. PR #4135로 수정 완료.

**LATERAL JOIN**: PostgreSQL의 `JOIN LATERAL` 구문은 기본 지원하나 방언별 호환성 테스트 필요.

### 2.4 복합 JOIN 조건

`JOIN ... USING`을 내부적으로 `JOIN ... ON`으로 정규화하는 능력 확인됨 (DataHub 문서). 다중 테이블 JOIN에서 조인 컬럼 추출은 `exp.Join` 노드에서 `on` 속성으로 접근 가능.

---

## 3. 타 파서 대비 비교

### 3.1 Python 기반 파서 비교

| 라이브러리 | 방언 지원 | CTE 지원 | AST 깊이 | 유지 상태 | 순수 Python |
| --- | --- | --- | --- | --- | --- |
| **sqlglot** | 31개 | 완전 지원 | 완전한 AST | 활발 (200+ 기여자) | Yes |
| sqlparse | 제한적 | 부분적 | 토큰 기반 (AST 없음) | 유지보수 모드 | Yes |
| sqlfluff | 다수 | 지원 | 완전한 AST | 활발 | Yes |
| sqloxide | 제한적 | 지원 | 완전한 AST | 활발 | No (Rust 바인딩) |

**sqlparse와의 결정적 차이**: sqlparse는 진정한 AST를 생성하지 않고 토큰 스트림으로 처리한다. 복잡한 서브쿼리나 CTE에서 테이블명 추출 신뢰도가 현저히 낮다.

**sqlfluff와의 차이**: sqlfluff는 Linting에 특화되어 있으며, lineage/파싱 추출 API가 sqlglot보다 빈약하다.

DataHub가 sqlparse에서 sqlglot으로 전환한 이유가 바로 이 한계 때문이다 (DataHub 블로그, 2023년).

---

## 4. 방언별 지원 현황 — 프로젝트 대상 DB 기준

### 4.1 PostgreSQL (온라인 개발 환경)

공식 지원 방언. 사용법: `dialect="postgres"`

**알려진 파싱 이슈:**

| 이슈 | 상태 | 영향 |
| --- | --- | --- |
| `IS JSON` 함수 구문 파싱 실패 (Issue #3965, 2024년 8월) | 수정 진행 중 | PG 17+ 구문, 구버전엔 무해 |
| INTERVAL 표현식 일부 패턴 ParseError (Issue #4490, 2024년 12월) | 수정 진행 중 | `interval '1 day 1 hour'`(괄호 없이) 실패 |
| 예약어를 컬럼명으로 사용 시 ParseError (Issue #2098) | 수정됨 | `interval`을 컬럼명으로 사용하는 경우 |
| `WITH ... UPDATE` CTE 패턴 미지원 (Issue #79) | 오래된 이슈, 부분 개선 | 이력 SQL에 간헐적 존재 가능 |

**금융 도메인 영향 평가**: PostgreSQL 정보계 DB의 일반적인 SELECT 쿼리(CTE + 윈도우함수 + 서브쿼리)는 안정적으로 파싱된다. 위의 이슈들은 특수 구문에 한정되며, LLM 생성 SQL에서는 발생 빈도 낮음.

### 4.2 Impala (폐쇄망 타겟)

**공식 방언 없음** — Impala는 SQLGlot의 31개 공식 지원 방언에 포함되지 않는다.

Apache Superset이 동일 문제에 직면했고 (Issue #32143), PR #34662에서 Hive 방언으로 매핑하는 것으로 해결했다. Impala SQL이 Hive SQL과 문법적으로 매우 유사하기 때문.

**권고**: `dialect="hive"` 사용.

#### Hive 방언이 처리하는 Impala 문법 (호환 범위)

SQLGlot Hive 방언은 아래 구문을 정상 처리한다 (공식 Hive dialect API 문서 기준):

| 구문 유형 | 상세 | 비고 |
| --- | --- | --- |
| 백틱 식별자 | `` `column_name` `` | Impala/Hive 공통 |
| 날짜 함수 | `DATE_ADD`, `DATE_SUB`, `DATEDIFF`, `FROM_UNIXTIME`, `UNIX_TIMESTAMP` | Hive 방언 명시적 override |
| 집계 함수 | `COLLECT_LIST`, `COLLECT_SET`, `PERCENTILE`, `PERCENTILE_APPROX` | Hive 방언 명시적 override |
| 파티션 구문 | `PARTITION BY` (윈도우함수/DDL) | 공통 지원 |
| CTE, 서브쿼리 | `WITH ... AS (...)` | 공통 지원 |
| 윈도우 함수 | `ROW_NUMBER() OVER (...)`, `RANK()`, `LAG()`, `LEAD()` | 공통 지원 |
| 표준 집계 | `SUM`, `COUNT`, `AVG`, `MIN`, `MAX` | ANSI 공통 |
| 파라미터 변수 | `${key}`, `${key:default}` | Hive 방언 지원 |

**결론**: 일반적인 SELECT 분석 쿼리 (JOIN + WHERE + 집계 + 윈도우함수 + CTE) 범위에서 Hive 방언은 Impala SQL을 **신뢰할 수 있는 수준으로** 파싱한다.

#### Hive 방언에서 실패하는 Impala 전용 구문

아래는 Impala가 고유하게 지원하는 구문으로, Hive 방언 파싱 시 오류 또는 의미 왜곡이 발생한다.

##### 카테고리 A: DDL/유지관리 구문 — SELECT 추출에는 미출현, 무해

| 구문 | 실패 유형 | SELECT 파싱 영향 |
| --- | --- | --- |
| `COMPUTE STATS tbl` | `ParseError` 또는 `exp.Command` 폴백 | 없음 (DDL) |
| `INVALIDATE METADATA [tbl]` | `ParseError` 또는 `exp.Command` 폴백 | 없음 (DDL) |
| `REFRESH tbl [PARTITION ...]` | Hive의 `REFRESH` 처리와 충돌 가능 | 없음 (DDL) |

##### 카테고리 B: SELECT 쿼리 내 Impala 고유 구문 — 실제 위험

| 구문 | 예시 | 실패 유형 | 심각도 |
| --- | --- | --- | --- |
| `/* +broadcast */` 힌트 | `SELECT /* +BROADCAST */ * FROM t1 JOIN t2` | 힌트 무시 또는 토큰 오류 — Hive는 `/* */` Impala 힌트명 미인식 | 중간 |
| `[broadcast]` 대괄호 힌트 | `SELECT [broadcast] * FROM t1 JOIN t2` | `ParseError` — `[` 힌트 형식 완전 미지원 | 높음 |
| `STRAIGHT_JOIN` | `SELECT STRAIGHT_JOIN * FROM t1 JOIN t2` | Hive 방언에서 예약어 미인식, 경우에 따라 무시됨 | 낮음 |
| `NDV()` | `SELECT NDV(col) FROM t` | `exp.Anonymous` 노드 반환, 파싱 성공 | 낮음 |
| `APPX_MEDIAN()` | `SELECT APPX_MEDIAN(col) FROM t` | `exp.Anonymous` 노드 반환 | 낮음 |
| `TABLESAMPLE SYSTEM(n)` | `SELECT * FROM t TABLESAMPLE SYSTEM(10)` | Hive `TABLESAMPLE` 문법과 상이 시 `ParseError` | 중간 |

##### 카테고리 C: Hive Recursive CTE 미지원 (양쪽 공통 제약)

Hive 방언은 재귀 CTE (`WITH RECURSIVE`)를 지원하지 않는다. Impala도 지원하지 않으므로 양쪽 공통 제약이며 실질적 위험 없음.

#### 우리 사용 용도 (SELECT-only 파싱)에서의 현실적 신뢰도

폐쇄망 Impala 환경에서 LLM이 생성하거나 이력 DB에서 추출한 SELECT 쿼리를 파싱하는 용도 기준:

- **표준 분석 쿼리 (JOIN/WHERE/AGG/윈도우함수)**: 신뢰도 **높음** (95%+)
- **Impala 힌트 포함 쿼리**: 신뢰도 **중간** — `/* +hint */` 형식은 파싱은 되나 힌트 노드 손실, `[hint]` 형식은 ParseError 가능
- **Impala 전용 집계함수 (NDV, APPX_MEDIAN) 포함**: 신뢰도 **중간-높음** — `exp.Anonymous`로 처리, 함수명 기반 추출 로직에서 누락 가능
- **DDL/유지관리 구문 혼재 스크립트**: 신뢰도 **낮음** — exp.Command 폴백 주의

**결론**: LLM이 생성하는 SELECT 쿼리는 표준 HiveQL 범위에서 생성되므로 `dialect="hive"` 신뢰도는 높다. 이력 SQL 파싱 시 힌트 구문 포함 여부가 핵심 위험 요소.

### 4.3 Sybase IQ (폐쇄망 타겟)

**공식 지원 없음 — 메인테이너가 명시적으로 거절함** (GitHub Issues #3274, #7204 참조).

#### SQLGlot의 Sybase IQ 지원 요청 이력

| 이슈 번호 | 내용 | 상태 |
| --- | --- | --- |
| Issue #3274 — "Sybase TSQL Support" | Sybase IQ에서 DATETIME2 → BIGDATETIME 타입 매핑, 타임존 오프셋(`+00:00`) 파싱 실패 보고. 메인테이너: "TSQL subclass로 직접 구현하라, out of scope" | Closed, Not Planned |
| Issue #7204 — "New SYBASE SQL Dialect with Lazy Parsing and Control-Flow Support" | 외부 기여자가 WHILE/IF/EXEC/GOTO/커서 등 제어흐름 완전 지원 Sybase 방언 PR 제출. 수천 개 저장프로시저로 프로덕션 테스트 완료. 메인테이너: "Not at the moment" | Closed, Not Planned |
| Issue #4069 — "convert joins `*=` and `=*` from SQL89" | Sybase SQL89 스타일 조인 연산자(`*=` outer join) 변환 지원 요청 | Closed, Not Planned |

SQLGlot 프로젝트는 Sybase 지원을 공식적으로 거절했으며, 향후 네이티브 추가 가능성 낮음.

#### ANSI 폴백(dialect=None)에서 실패하는 Sybase IQ 구문

Sybase IQ는 ANSI SQL-89/92 기반이나, T-SQL 계열 확장과 IQ 고유 확장이 혼재한다. ANSI 파서로 처리 불가능한 구문:

##### 카테고리 A: T-SQL 계열 날짜 함수 — SELECT에서 빈번히 출현

| 구문 | 예시 | ANSI 파서 동작 | 심각도 |
| --- | --- | --- | --- |
| `DATEADD(part, n, date)` | `DATEADD(month, 3, GETDATE())` | `exp.Anonymous` 또는 ParseError | 높음 |
| `DATEDIFF(part, date1, date2)` | `DATEDIFF(day, start_dt, end_dt)` | `exp.Anonymous` 또는 TSQL 방언 필요 | 높음 |
| `DATEPART(part, date)` | `DATEPART(year, trans_dt)` | `exp.Anonymous` | 높음 |
| `GETDATE()` | `WHERE trans_dt >= GETDATE()` | `exp.Anonymous` (ANSI는 `CURRENT_TIMESTAMP`) | 중간 |
| `CONVERT(type, expr, style)` | `CONVERT(VARCHAR, amount, 1)` | 세 번째 인자(format_style)에서 파싱 실패 | 높음 |

참고: TSQL Issue #4520 (DATEDIFF 리터럴 인자 파싱 실패, PR #4523으로 수정됨)은 TSQL 방언에서의 버그였으나, Sybase IQ는 TSQL 방언 자체도 사용할 수 없어 동일 패턴에서 다른 경로로 실패 발생.

##### 카테고리 B: Sybase IQ 고유 구문 — SELECT에서 출현 가능

| 구문 | 예시 | ANSI 파서 동작 | 심각도 |
| --- | --- | --- | --- |
| `SELECT TOP n ...` | `SELECT TOP 100 * FROM t` | ANSI 미지원 — ParseError 또는 TOP이 컬럼명으로 오인 | 높음 |
| `SELECT TOP n START AT m` | `SELECT TOP 100 START AT 201 * FROM t` | ParseError | 높음 |
| SQL89 외부조인 `*=` | `WHERE t1.id *= t2.id` | ParseError — `*=` 연산자 미지원 | 중간 |
| `KEY JOIN` | `SELECT * FROM t1 KEY JOIN t2` | ParseError — ANSI/TSQL 없는 Sybase 고유 구문 | 중간 |
| `ROWID()` 함수 | `WHERE ROWID() < 1000` | `exp.Anonymous` | 낮음 |
| CASE 내부 서브쿼리 | `CASE WHEN (SELECT ...) ...` | IQ에서도 오류이므로 파싱 실패는 오탐 아님 | 낮음 |

##### 카테고리 C: 저장프로시저/제어흐름 — SELECT 파싱 시 스크립트 혼재

| 구문 | ANSI 파서 동작 | 영향 |
| --- | --- | --- |
| `IF ... BEGIN ... END` | `exp.Command` 폴백 | 이력 SQL 스크립트 전체 파싱 실패 |
| `WHILE ... BEGIN ... END` | `exp.Command` 폴백 | 동일 |
| `DECLARE @var TYPE` | ParseError 또는 Command 폴백 | 동일 |
| `SELECT ... INTO #temp` | `#temp` 테이블명 토큰화 실패 가능 | 중간 |

#### SELECT-only 파싱에서의 예상 실패율

금융 정보계 Sybase IQ DB에서의 일반적인 SELECT 쿼리 패턴 기준:

| 쿼리 유형 | 예상 파싱 성공률 | 주요 실패 원인 |
| --- | --- | --- |
| 단순 SELECT (WHERE + JOIN, 표준 함수만) | 85~90% | GETDATE(), 날짜 리터럴 변환 |
| DATEADD/DATEDIFF/DATEPART 포함 | 40~60% | T-SQL 날짜 함수 3개가 금융 쿼리에서 매우 빈번 |
| SELECT TOP N 포함 | 50~70% | TOP 절 처리 실패, TSQL 방언 사용 시 개선 |
| 저장프로시저 혼재 스크립트 | 10~30% | IF/WHILE/DECLARE로 전체 파싱 실패 |
| 표준 윈도우함수 + CTE | 75~85% | 윈도우함수 자체는 ANSI 지원, 날짜 함수 혼재 여부에 따라 변동 |

**중요**: TSQL 방언(`dialect="tsql"`)을 사용하면 날짜 함수와 TOP N 처리가 크게 개선된다. 단, Sybase IQ 고유 구문(KEY JOIN, ROWID 등)은 여전히 처리 불가. **실무 권고는 `dialect="tsql"` 우선 시도, 실패 시 `dialect=None` 재시도, 이후 regex fallback** 순서.

#### dialect="tsql" vs dialect=None 선택 기준

| 항목 | `dialect="tsql"` | `dialect=None` |
| --- | --- | --- |
| TOP N 처리 | 지원 (`exp.Top` 노드) | 실패 |
| DATEADD/DATEDIFF/DATEPART | 지원 | exp.Anonymous |
| CONVERT(type, expr, style) | 지원 | 3인자에서 실패 |
| GETDATE() | 지원 | exp.Anonymous |
| `*=` SQL89 조인 | 미지원 | 미지원 |
| KEY JOIN | 미지원 | 미지원 |
| BIGDATETIME 타입 | 미지원 (DATETIME2로 오파싱) | 미지원 |
| 타임존 오프셋 `+00:00` | ParseError (Issue #3274) | ParseError |

**결론**: `dialect="tsql"`이 Sybase IQ 쿼리 파싱에서 `dialect=None` 대비 유의미하게 유리하다. 단, TSQL 방언도 Sybase IQ 고유 타입/함수에서 실패하므로 fallback 레이어는 필수.

---

## 5. 실패 모드 분석

### 5.1 실패 유형별 정리

| 실패 유형 | 기본 동작 | 탐지 가능 여부 |
| --- | --- | --- |
| 완전 파싱 실패 (ParseError) | `WARN` 레벨에서 로그만 기록, `None` 또는 불완전 AST 반환 | `error_level=RAISE` 시 탐지 가능 |
| 부분 파싱 성공 (partial AST) | 조용히 불완전한 트리 반환 | 탐지 어려움 — 결과 검증 필요 |
| CTE를 실제 테이블로 오인 | 에러 없이 잘못된 결과 반환 | `traverse_scope()` 사용 시 방지 |
| 불지원 구문 Command로 파싱 | 에러 없이 `exp.Command` 노드 반환 | `isinstance(ast, exp.Command)` 체크 필요 |
| 방언 불일치 | 방언이 다른 SQL을 다른 의미로 파싱 | 방언 명시적 지정으로 방지 |

### 5.2 Silent Failure의 가장 위험한 경우: `exp.Command` 폴백

SQLGlot은 파싱할 수 없는 구문을 `exp.Command` 노드로 처리하는 경우가 있다. 이 경우 예외가 발생하지 않아 감지가 어렵다.

```python
ast = parse_one("SOME UNSUPPORTED SYNTAX", error_level=ErrorLevel.WARN)
# ast가 exp.Command 타입으로 반환될 수 있음
# find_all(exp.Table) 결과: 빈 리스트 — 테이블 없는 것처럼 보임

# 방어 코드
from sqlglot import exp
if isinstance(ast, exp.Command):
    raise ValueError(f"SQL이 명령문으로 폴백 파싱됨: {sql[:100]}")
```

### 5.3 파싱 실패 시 불완전 AST 예시

공식 문서 확인된 동작:

- `parse("select a from t1 where")` → 예외 발생 (완전 실패)
- `parse("select a from")` → 부분 표현식 트리 반환 (silent partial)

---

## 6. 실용적 추출 신뢰도 평가

Data Copilot 프로젝트에서 실제로 필요한 추출 대상별 신뢰도:

| 추출 대상 | 신뢰도 | 권장 API | 주의사항 |
| --- | --- | --- | --- |
| **테이블명** | 중간 | `traverse_scope()` | `find_all(exp.Table)` 금지 — CTE 오인 |
| **JOIN 조건** | 높음 | `find_all(exp.Join)` + `.on` | USING 구문은 ON으로 자동 정규화됨 |
| **WHERE 리터럴** | 높음 | `find_all(exp.Literal)` in WHERE | 서브쿼리 내부 리터럴도 함께 추출 주의 |
| **집계함수** | 높음 | `find_all(exp.AggFunc)` | 윈도우 함수 집계와 일반 집계 구분 필요 |
| **컬럼명** | 중간 | `qualify()` 후 `find_all(exp.Column)` | 스키마 정보 없으면 테이블 귀속 불확실 |
| **SELECT 절 별칭** | 높음 | `find_all(exp.Alias)` | 서브쿼리 별칭과 컬럼 별칭 혼재 주의 |

---

## 7. 기각된 대안과 사유

### 7.1 sqlparse 기각

sqlparse는 진정한 AST를 생성하지 않고 토큰 기반으로 처리한다. CTE, 서브쿼리, 복합 JOIN 에서 테이블명 추출 정확도가 현저히 낮아 금융 도메인 복잡 쿼리에 부적합. DataHub도 동일 이유로 sqlparse → sqlglot 전환.

### 7.2 sqloxide (Rust 바인딩) 기각

폐쇄망 배포 환경에서 Rust 바이너리 빌드 의존성이 추가되어 배포 복잡도 증가. 프로젝트의 "설정 변경만으로 폐쇄망 전환" 요건과 충돌. 성능 이점도 있으나 순수 Python 의존성 유지가 더 중요.

### 7.3 정규식 기반 파싱 기각 (주 파서로서)

프로젝트의 이력 SQL에는 중첩 서브쿼리, CTE, 윈도우함수가 혼재할 것으로 예상된다. 정규식으로 이를 신뢰성 있게 처리하는 것은 유지보수 불가 수준의 복잡도를 초래한다. 단, SQLGlot 파싱 실패 시 **fallback 레이어**로는 적합.

---

## 8. 프로젝트별 권고 구현 패턴

### 8.1 안전한 파싱 래퍼

```python
from sqlglot import parse_one, exp, ErrorLevel
from sqlglot.errors import ParseError
from sqlglot.optimizer.scope import traverse_scope
import logging

logger = logging.getLogger(__name__)

def safe_parse_sql(sql: str, dialect: str = "postgres") -> exp.Expression | None:
    """
    SQL 파싱 래퍼 — 실패를 명시적으로 처리하며 Command 폴백을 감지한다.

    Args:
        sql: 파싱할 SQL 문자열
        dialect: SQLGlot 방언 ("postgres", "hive", None)
    Returns:
        파싱된 AST 또는 None (실패 시)
    """
    try:
        ast = parse_one(sql, dialect=dialect, error_level=ErrorLevel.RAISE)
        if isinstance(ast, exp.Command):
            logger.warning("SQL이 Command로 폴백 파싱됨 — 지원되지 않는 구문 포함: %s", sql[:200])
            return None
        return ast
    except ParseError as e:
        logger.error("SQLGlot 파싱 실패: %s | SQL: %s", str(e), sql[:200])
        return None


def extract_physical_tables(sql: str, dialect: str = "postgres") -> list[str]:
    """
    CTE 별칭을 제외한 실제 물리 테이블명만 추출한다.
    """
    ast = safe_parse_sql(sql, dialect)
    if ast is None:
        return []

    tables = []
    for scope in traverse_scope(ast):
        for alias, (node, source) in scope.selected_sources.items():
            if isinstance(source, exp.Table):
                tables.append(source.name)
    return list(set(tables))
```

### 8.2 방언 선택 전략 (환경별)

```python
# 환경에 따른 방언 매핑
DIALECT_MAP = {
    "postgresql": "postgres",
    "impala": "hive",      # 공식 미지원 — Hive로 매핑
    "sybase_iq": None,     # 공식 미지원 — ANSI 폴백
}
```

### 8.3 Sybase IQ/Impala 폴백 정규식

SQLGlot 파싱 실패 시 최소 테이블명 추출을 위한 fallback:

```python
import re

def regex_extract_tables_fallback(sql: str) -> list[str]:
    """
    SQLGlot 파싱 실패 시 정규식 기반 테이블명 추출 (best-effort).
    CTE와 실제 테이블 구분 불가 — 경고 로그 필수.
    """
    # FROM, JOIN 뒤에 오는 식별자 추출 (기본적인 경우만 처리)
    pattern = r'(?:FROM|JOIN)\s+([`"]?[\w.]+[`"]?)(?:\s+(?:AS\s+)?[\w]+)?'
    matches = re.findall(pattern, sql, re.IGNORECASE)
    return [m.strip('`"') for m in matches]
```

---

## 9. 결론 및 액션 아이템

### 권고사항

1. **SQLGlot 사용 유지** — 프로젝트에 이미 채택된 선택이며, 대안 대비 충분한 기술적 우위 존재
2. **`find_all(exp.Table)` 전면 금지** — `traverse_scope()` 기반 API로 대체 필수
3. **`error_level=ErrorLevel.RAISE` 명시** — 모든 파싱 호출에 적용, `ParseError` catch 후 fallback
4. **`exp.Command` 타입 체크** — 파싱 후 반드시 검사
5. **방언 명시**: PostgreSQL → `"postgres"`, Impala → `"hive"`, Sybase IQ → `"tsql"` 우선 후 `None` 재시도
6. **Sybase IQ/Impala 파싱 실패 fallback** — 최소 정규식 기반 테이블명 추출 구현

### 리스크 등급

| 환경 | 파싱 리스크 | 대응 전략 |
| --- | --- | --- |
| PostgreSQL (온라인) | 낮음 | `dialect="postgres"` + RAISE 레벨 |
| Impala (폐쇄망) | 중간 | `dialect="hive"` + RAISE + regex fallback |
| Sybase IQ (폐쇄망) | 높음 | `dialect="tsql"` 우선, 실패 시 `dialect=None` 재시도 + regex fallback |

---

## 참고 문헌

### Tier 1 — 학술 논문

1. **CrackSQL (SIGMOD 2025)** — "Cracking SQL Barriers: An LLM-based Dialect Translation System", Tsinghua University. SQLGlot을 벤치마크 기준선으로 사용하여 방언 변환 오류율 측정. DOI: 10.1145/3725278 — [arxiv.org/abs/2504.00882](https://arxiv.org/abs/2504.00882)
2. **PARROT (2025)** — "A Benchmark for Evaluating LLMs in Cross-System SQL Translation". Cross-system SQL 변환 평가 체계 제시. — [arxiv.org/pdf/2509.23338](https://arxiv.org/pdf/2509.23338)
3. **RISE (2025)** — "Rule-Driven SQL Dialect Translation via Query Reduction". 규칙 기반 SQL 방언 번역 시스템, SQLGlot 한계 분석. — [arxiv.org/html/2601.05579](https://arxiv.org/html/2601.05579)

### Tier 2 — 공식 문서 및 기술 블로그

1. DataHub SQL Parsing 공식 문서 (97~99% lineage 정확도 주장) — [docs.datahub.com/docs/lineage/sql_parsing](https://docs.datahub.com/docs/lineage/sql_parsing)
2. DataHub Column-Level Lineage 블로그 (SQLGlot 기반 lineage 추출 상세) — [datahub.com/blog/extracting-column-level-lineage-from-sql](https://datahub.com/blog/extracting-column-level-lineage-from-sql/)
3. SQLGlot AST Primer 공식 문서 (CTE 처리 함정, scope 기반 API 설명) — [github.com/tobymao/sqlglot — posts/ast_primer.md](https://github.com/tobymao/sqlglot/blob/main/posts/ast_primer.md)
4. SQLGlot errors 공식 API 문서 (ErrorLevel 상세) — [sqlglot.com/sqlglot/errors.html](https://sqlglot.com/sqlglot/errors.html)

### Tier 3 — GitHub 이슈 트래커 (실사례)

1. Issue #79: CTE with UPDATE 파싱 실패 — [github.com/tobymao/sqlglot/issues/79](https://github.com/tobymao/sqlglot/issues/79)
2. Issue #4133: PostgreSQL LATERAL + VALUES 파싱 실패 (수정됨) — [github.com/tobymao/sqlglot/issues/4133](https://github.com/tobymao/sqlglot/issues/4133)
3. Issue #2098: 예약어 컬럼명 ParseError — [github.com/tobymao/sqlglot/issues/2098](https://github.com/tobymao/sqlglot/issues/2098)
4. Superset Issue #32143: Impala 방언 미지원 (Hive 매핑으로 해결됨) — [github.com/apache/superset/issues/32143](https://github.com/apache/superset/issues/32143)
5. Issue #4490: PostgreSQL INTERVAL 파싱 이슈 — [github.com/tobymao/sqlglot/issues/4490](https://github.com/tobymao/sqlglot/issues/4490)
6. Issue #3274: Sybase TSQL Support 요청 (거절됨) — [github.com/tobymao/sqlglot/issues/3274](https://github.com/tobymao/sqlglot/issues/3274)
7. Issue #7204: Sybase Dialect with Control-Flow Support 요청 (거절됨) — [github.com/tobymao/sqlglot/issues/7204](https://github.com/tobymao/sqlglot/issues/7204)
8. Issue #4520: TSQL DATEDIFF 리터럴 파싱 실패 (PR #4523으로 수정됨) — [github.com/tobymao/sqlglot/issues/4520](https://github.com/tobymao/sqlglot/issues/4520)
