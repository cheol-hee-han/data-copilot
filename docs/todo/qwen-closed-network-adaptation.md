# TODO — Qwen 3.5 397B 폐쇄망 적응 작업

> **작성일**: 2026-03-26
> **배경**: 폐쇄망 배포 대상 모델이 Qwen 3.5 397B-A17B (MoE, 활성 17B, 컨텍스트 262K)로 확인됨.
> 기존 CLAUDE.md에 "GPT-3.5 Turbo급 7B~70B 소형 모델"로 전제했으나,
> 실제 모델은 GPT-4o급 오픈웨이트 대형 모델이므로 전략을 재수립한다.
> **하위 모델 가능성**: Qwen 3.5 35B-A3B, 27B 등이 사용될 수도 있으므로 대비는 유지한다.

---

## 재평가 결과: 변경하지 않는 것

### 질의 정규화 8슬롯 분할 — 불필요

- 397B는 IFBench 76.5, BFCL 72.9로 복잡 JSON 생성 능력 충분
- 컨텍스트 262K에서 100줄 정규화 프롬프트는 부담 없음
- `llm_call_with_parse_retry` 최대 3회 재시도로 파싱 성공률 확보 가능
- `normalization_enabled` 설정으로 비활성화 가능하므로 하위 모델 대비도 충분
- 분할 전략은 `docs/todo/normalization-prompt.md #9`에 설계만 유지

### SQL Generator 프롬프트 축소 — 불필요

- `reference_sqls`는 유사 SQL의 JOIN/WHERE 구조 참조 역할 → SQL 정확도에 기여
- `dead_ends`는 실패 패턴 반복 방지 → 397B는 이 지시를 이해 가능
- `structural_hints`는 SQLGlot으로 추출한 정확한 구조 힌트 → 유지 가치 높음
- 컨텍스트 262K에서 200줄 + 변수 주입(~3,000 토큰)은 전체의 1~2%

---

## 실제 해야 할 작업 (4개)

---

### 1. 금액 단위 오류 방지 규칙 추가

**우선순위**: 높음 (SQL 정확도 직접 영향)
**작업 범위**: 프롬프트 1곳 수정

#### 문제

Qwen 3.5는 한국어 입력에도 내부적으로 영어로 추론 후 번역하는 특성이 있다 (arXiv:2508.10355).
이로 인해 한국어 금액 표기를 오인하는 검증된 사례가 존재한다.

| 사용자 입력 | 기대 변환 | Qwen 오류 사례 |
|---|---|---|
| "15만원 이상" | 150,000 | 1,500,000 (10배 오류) |
| "1억 이상 대출" | 100,000,000 | 10,000,000 (10분의 1 오류) |
| "5천만원" | 50,000,000 | 5,000 + 만원 파싱 실패 |

금융 도메인에서 금액 단위 오류는 **완전히 다른 데이터**를 조회하게 되므로 치명적이다.

#### 대응

`resources/prompts/reason/sql_generator_system.txt`의 "SQL 작성 규칙" 섹션에 금액 변환 규칙을 명시한다.

```
## 한국어 금액 단위 변환 규칙 (반드시 준수)

사용자 입력에 한국어 금액 단위가 포함되면 아래 규칙으로 숫자 변환 후 SQL에 사용한다.

| 단위 | 승수 | 예시 입력 | 변환 결과 |
|------|------|----------|----------|
| 천 | ×1,000 | 5천 | 5,000 |
| 만 | ×10,000 | 15만 | 150,000 |
| 십만 | ×100,000 | 3십만 | 300,000 |
| 백만 | ×1,000,000 | 2백만 | 2,000,000 |
| 천만 | ×10,000,000 | 5천만 | 50,000,000 |
| 억 | ×100,000,000 | 1억 | 100,000,000 |
| 조 | ×1,000,000,000,000 | 1조 | 1,000,000,000,000 |

복합 표기:
- "1억 5천만" = 100,000,000 + 50,000,000 = 150,000,000
- "3천5백만" = 30,000,000 + 5,000,000 = 35,000,000

주의: "만원", "억원"에서 "원"은 단위 표시일 뿐 승수에 포함하지 않는다.
```

#### 영향 범위

- `resources/prompts/reason/sql_generator_system.txt` — 규칙 추가
- 추가로, Phase1 정규화 프롬프트(`resources/prompts/interpret/query_normalizer_phase1_system.txt`)의
  FILTER 슬롯 해석에도 동일 규칙을 참조할 수 있도록 고려

---

### 2. 환각 억제 강화 — 프롬프트 + 화이트리스트 검증

**우선순위**: 높음 (SQL 정확도 직접 영향)
**작업 범위**: 프롬프트 1곳 수정 + SQL Validator 코드 수정

#### 문제

Qwen 3.5의 Omniscience Index는 -32로, 경쟁 모델 대비 환각률이 높다
(비교: Kimi K2.5 -11, GLM-5 -1).

환각이 SQL 생성에서 발생하면:

| 환각 유형 | 예시 | 결과 |
|---|---|---|
| 존재하지 않는 테이블 사용 | `FROM TB_LOAN_SUMMARY` (실제 없음) | SQL 실행 오류 |
| 존재하지 않는 컬럼 사용 | `SELECT OVERDUE_RATE` (실제 없음) | SQL 실행 오류 |
| 임의 코드값 생성 | `WHERE STATUS_CD = 'ACTIVE'` (실제는 '01') | 잘못된 데이터 조회 |
| 존재하지 않는 조인 키 | `ON A.CUST_ID = B.CUSTOMER_ID` (실제는 B.CUST_ID) | 조인 실패 |

현재 프롬프트에 "CONFIRMED/PROBABLE 상태의 지식 항목만 사용하세요"가 있지만,
**위반 시 탐지/차단 메커니즘이 부족**하다.

#### 대응 (a): 프롬프트 네거티브 지시 강화

`resources/prompts/reason/sql_generator_system.txt`에 환각 방지 규칙을 추가한다.

```
## 환각 방지 규칙 (절대 위반 금지)

1. "사용할 테이블" 섹션에 나열되지 않은 테이블명을 SQL에 사용하지 마라.
2. 해당 테이블의 "컬럼" 목록에 없는 컬럼명을 SQL에 사용하지 마라.
3. "확인된 지식 항목"에 없는 코드값을 SQL에 사용하지 마라.
4. 테이블명·컬럼명·코드값이 확실하지 않으면 추측하지 말고,
   explanation에 "확인 필요: {불확실한 항목}" 을 기재하라.

위반 예시 (절대 금지):
- confirmed_terms에 "TB_LOAN_INFO" 만 있는데 "TB_LOAN_SUMMARY"를 사용 → 금지
- confirmed_terms에 "LOAN_NO, CUST_ID, BAL_AMT"만 있는데 "OVERDUE_RATE"를 사용 → 금지
- confirmed_terms에 코드값 "01=정상, 02=연체"만 있는데 "WHERE STATUS='ACTIVE'" → 금지
```

#### 대응 (b): SQL Validator Layer1에 화이트리스트 검증 추가

생성된 SQL에서 사용된 테이블/컬럼이 `candidate_tables`의 범위 안에 있는지 검증한다.

**검증 로직**:

```python
def _validate_whitelist(
    sql: str,
    candidate_tables: list[CandidateTable],
) -> list[str]:
    """SQL에서 사용된 테이블/컬럼이 candidate_tables 범위 안인지 검증한다.

    Returns:
        위반 사항 목록. 비어있으면 통과.
    """
    # SQLGlot으로 SQL 파싱하여 테이블명/컬럼명 추출
    used_tables = extract_tables_from_sql(sql)
    used_columns = extract_columns_from_sql(sql)

    # 화이트리스트 구성
    allowed_tables = {ct.table_name for ct in candidate_tables}
    allowed_columns = set()
    for ct in candidate_tables:
        allowed_columns.update(ct.relevant_columns)

    violations = []

    # 테이블 검증
    for t in used_tables:
        if t.upper() not in {at.upper() for at in allowed_tables}:
            violations.append(f"확인되지 않은 테이블: {t}")

    # 컬럼 검증 (alias, *, 집계함수 내부는 제외)
    for c in used_columns:
        if c.upper() not in {ac.upper() for ac in allowed_columns}:
            violations.append(f"확인되지 않은 컬럼: {c}")

    return violations
```

**위반 시 처리**:
- `fix_instruction`에 위반 내용을 피드백
- SQL 재생성 트리거 (기존 `sql_max_retry` 루프 활용)

**주의사항**:
- 컬럼 화이트리스트는 **엄격하게 적용하면 안 됨** — alias, 상수, 집계함수 인자를
  SQLGlot 파싱으로 구분해야 한다
- 테이블 화이트리스트는 **엄격 적용 가능** — candidate_tables에 없는 테이블은 확실한 환각

#### 영향 범위

- `resources/prompts/reason/sql_generator_system.txt` — 환각 방지 규칙 추가
- `src/agents/nodes/reason/sql_validator.py` — 화이트리스트 검증 함수 추가
- `tests/auto/unit/test_sql_validator.py` — 화이트리스트 검증 테스트 추가

---

### 3. Thinking 모드 제어

**우선순위**: 중간 (성능/비용 최적화)
**작업 범위**: config 1곳 + client.py 수정 + 호출 사이트 선택적 수정

#### 문제

Qwen 3.5는 기본적으로 thinking 모드가 활성화되어 모든 응답에 `<think>...</think>` 토큰이 포함된다.

```
<think>
사용자가 의도 분류를 요청했다. 이 질의는 데이터 추출에 해당한다.
카테고리는 DATA_EXTRACTION이고 확신도는 HIGH이다.
</think>
{"category": "DATA_EXTRACTION", "confidence": "HIGH", "reason": "..."}
```

**문제점**:
- 13개 LLM 호출 전부에서 thinking이 붙으면 **불필요한 토큰 소비** + **응답 지연 증가**
- 단순 분류 태스크에서 thinking은 **비용 대비 효과 없음**
- thinking 텍스트가 응답 파싱을 방해할 수 있음 (`<think>` 태그 내 JSON이 있으면 regex 오작동)

#### 대응

**(a)** `src/config.py`에 thinking 제어 설정 추가:

```python
# Qwen thinking 모드 제어 (openai_compatible + Qwen 모델 전용)
llm_thinking_enabled: bool = True    # 기본값: 활성화
```

**(b)** `OpenAICompatibleMessages.create()`에서 thinking 파라미터 전달:

vLLM에서 Qwen 3.5 thinking 비활성화 방법 (2가지):

```python
# 방법 1: extra_body 파라미터 (vLLM 지원)
call_kwargs["extra_body"] = {
    "chat_template_kwargs": {"enable_thinking": False}
}

# 방법 2: 시스템 프롬프트 앞에 /no_think 토큰 삽입 (Qwen 네이티브)
if not thinking_enabled:
    system = "/no_think\n" + system
```

**(c)** 응답에서 `<think>` 태그 제거 (thinking이 켜진 경우에도 안전하게 파싱):

```python
import re

def _strip_thinking(text: str) -> str:
    """Qwen thinking 태그를 제거한다."""
    return re.sub(r"<think>[\s\S]*?</think>\s*", "", text).strip()
```

**(d)** 노드별 thinking 권장 설정:

| 노드 | Thinking | 이유 |
|------|----------|------|
| 대화 이력 해석 | Off | 단순 분류 (CONTINUE/NEW/UNSURE) |
| 의도 분류 | Off | 단순 분류 (6-way) |
| 질의 정규화 | **On** | 8슬롯 추출에 추론 필요 |
| 명확화 질문 | Off | 자유 텍스트 생성, 추론 불필요 |
| 가설 계획 | **On** | 탐색 전략 수립에 추론 필요 |
| 도구 결과 해석 | **On** | 메타 데이터 의미 해석, 3측면 추론 |
| SQL 생성 | **On** | **핵심** — thinking 품질이 SQL 정확도에 직결 |
| SQL 검증 Layer2b | **On** | 의미적 검증에 추론 필요 |
| 테이블 비교 판정 | **On** | 유사 테이블 비교에 추론 필요 |
| 재계획 | **On** | 실패 원인 분석에 추론 필요 |
| 데이터 분석 | Off | 결과 요약/정리 태스크 |
| 시각화 판정 | Off | 단순 분류 |
| 응답 포맷팅 | Off | 텍스트 정리 태스크 |

> **참고**: 초기에는 전역 `llm_thinking_enabled` 설정으로 일괄 on/off만 제어한다.
> 노드별 세분화는 폐쇄망 테스트에서 지연/정확도 트레이드오프를 확인한 후 구현한다.

#### 영향 범위

- `src/config.py` — `llm_thinking_enabled` 설정 추가
- `src/utils/llm/client.py` — `OpenAICompatibleMessages.create()`에서 thinking 파라미터 전달,
  응답에서 `<think>` 태그 제거
- 호출 사이트 코드 변경 없음 (client 레이어에서 투명하게 처리)

---

### 4. Structured Output 지원 확장 (보류 — 폐쇄망 테스트 후)

**우선순위**: 낮음 (현행 retry로 397B에서 충분할 것으로 예상)
**작업 범위**: client.py 수정 + 호출 사이트 선택적 수정
**트리거**: 폐쇄망 테스트에서 JSON 파싱 실패율이 높을 때

#### 배경

Qwen 3.5는 vLLM 서빙 시 `response_format` 파라미터로 JSON Schema를 강제할 수 있다.
디코딩 단계에서 스키마 위반 토큰을 차단하므로 **100% 유효 JSON 보장**.

현재 파이프라인은 모든 LLM 호출에서 텍스트 응답 + regex JSON 추출 방식을 사용한다.
`llm_call_with_parse_retry`로 최대 3회 재시도하므로 397B에서는 대부분 성공할 것으로 예상.

#### 필요 시 구현 방향

**(a)** `OpenAICompatibleMessages.create()`에서 `response_format` kwargs 전달 지원:

```python
# 현재 (response_format 미전달)
call_kwargs: dict[str, Any] = {
    "model": model,
    "max_tokens": max_tokens,
    "messages": openai_messages,
}

# 확장 (kwargs에 response_format이 있으면 전달)
if "response_format" in kwargs:
    call_kwargs["response_format"] = kwargs.pop("response_format")
```

**(b)** 호출 사이트에서 선택적 사용:

```python
# 의도 분류 — JSON Schema 강제
response = await client.messages.create(
    model=settings.llm_model,
    max_tokens=200,
    system=prompt,
    messages=[...],
    response_format={
        "type": "json_schema",
        "json_schema": {
            "name": "intent_result",
            "schema": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": [...]},
                    "confidence": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW"]},
                    "reason": {"type": "string"}
                },
                "required": ["category", "confidence", "reason"]
            }
        }
    },
)
```

**(c)** Anthropic provider에서는 `response_format` 무시 (호환성 유지):

```python
# AnthropicMessages.create()
if "response_format" in kwargs:
    kwargs.pop("response_format")  # Anthropic은 이 파라미터 미지원, 조용히 제거
```

#### 적용 우선순위 (필요 시)

| 노드 | Structured Output 효과 | 우선순위 |
|------|----------------------|---------|
| 질의 정규화 (8슬롯) | **최대** — 가장 복잡한 JSON | P1 |
| SQL 생성 | 높음 — JSON {sql, explanation} | P1 |
| 의도 분류 | 중간 — 3필드 JSON | P2 |
| 가설 계획 | 중간 — 배열 JSON | P2 |
| SQL 검증 | 중간 — verdict/checks JSON | P3 |

#### 선행 조건

- 폐쇄망 vLLM 서빙 환경에서 `--guided-decoding-backend` 활성화 확인
- Qwen 3.5 모델의 guided decoding 호환성 테스트
- 하위 모델(35B, 27B)에서의 guided decoding 성능 확인

---

## 작업 순서 요약

| 순서 | 작업 | 난이도 | 시점 |
|------|------|--------|------|
| 1 | 금액 단위 변환 규칙 (프롬프트) | 낮음 | **지금** |
| 2 | 환각 방지 규칙 (프롬프트) | 낮음 | **지금** |
| 3 | 화이트리스트 검증 (SQL Validator) | 중간 | **지금** |
| 4 | Thinking 모드 제어 (config + client) | 중간 | **지금** |
| 5 | Structured Output 확장 | 중간 | 폐쇄망 테스트 후 |
