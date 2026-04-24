# Qwen3.5-397B-A17B 프롬프트 엔지니어링 스킬 재설계 전략

**상태**: 초안 (검토 중)
**작성일**: 2026-04-10
**대체 대상**: `.claude/skills/prompt-engineer/SKILL.md`, `docs/agent-guides/prompt-rules.md`
**적용 대상**: `resources/prompts/` 하위 전체 시스템 프롬프트
**승인 시점**: 본 문서 내용이 확정되면 기존 두 파일을 이 내용으로 대체

---

## 0. 문서 목적

폐쇄망 NL-to-SQL 에이전트(LangGraph 기반, 은행 도메인)의 프롬프트 가이드를 타겟 모델 **Qwen3.5-397B-A17B** 특성에 맞게 재설계한다.

기존 SKILL.md는 Claude 기반 개발 과정에서 축적된 범용 원칙이 주류였고, 폐쇄망 타겟 모델(Solar Pro 2 → Qwen3.5 397B / GPT OSS 120B)에 대한 최적화가 부족했다. 본 전략은 Qwen3.5의 공식 모델카드, Qwen3 기술보고서, vLLM 서빙 이슈, 그리고 두 명의 프롬프트 엔지니어링 전문가 독립 자문(공격적 최적화 vs 보수 방어)을 합의하여 도출한 최종 규칙이다.

---

## 1. 자료 출처 구분 — Qwen 버전 명확화

"Qwen3.0"이라는 공식 버전은 **존재하지 않는다**. Qwen 시리즈는 Qwen1.5 → Qwen2 → Qwen2.5 → **Qwen3** → **Qwen3.5** 순서이며, Qwen3은 시리즈 이름이다. 본 전략에서 인용하는 자료는 출처에 따라 세 단계로 분리해서 적용한다.

### 1.1 Qwen3.5-397B-A17B 전용 자료 (그대로 적용)

| 항목 | 출처 |
|---|---|
| 아키텍처: Gated DeltaNet + Sparse MoE, 512 experts, 10+1 active | HF 모델카드 |
| Native context 262K, YaRN 최대 1M | HF 모델카드 |
| IFBench 76.5 | HF 모델카드 |
| LongBench v2 63.2 | HF 모델카드 |
| 권장 샘플링 (thinking: temp=0.6 top_p=0.95 top_k=20, no_think: temp=0.7 top_p=0.8 top_k=20 presence_penalty=1.5) | HF 모델카드 |

### 1.2 Qwen3 시리즈 공통 자료 (그대로 적용)

| 항목 | 출처 |
|---|---|
| ChatML 포맷 (`<|im_start|>`/`<|im_end|>`), 기본 system 프롬프트 없음 | HF 블로그, Qwen 공식 문서 |
| thinking 모드 + `/no_think` 스위치, `enable_thinking` chat_template_kwargs | HF 블로그, Qwen3 기술보고서 arXiv:2505.09388 |
| 다중턴에서 이전 `<think>` 블록 자동 제거 | Qwen3 chat template |
| vLLM `enable_thinking=False` + `guided_json` 조합 JSON 깨짐 (v0.9.1 미만) | vLLM Issue #18819, SGLang #6675 |
| JSON 출력 시 `description` 필드 오인 버그 | vLLM Issue #23404 |

### 1.3 Qwen3 시리즈 벤치 수치 (참고만, 3.5에 직접 적용 금지)

| 항목 | 출처 | 주의 |
|---|---|---|
| IFEval strict 83.2 | Qwen3-235B-A22B, arXiv:2505.09388 | 3.5에는 직접 인용 불가 |
| BFCL-V4 72.9 | Qwen3-235B-A22B | 3.5 tool use 참고만 |
| Text2SQL 커뮤니티 관측 ("thinking이 단순 SQL에 불리") | Qwen3-30B 기반, ghost.oxen.ai | 3.5에서는 경향 참고만, 본 프로젝트는 sql_generator에 thinking ON 적용 |

### 1.4 LLM 범용 자료 (타 모델에도 적용되는 일반 법칙)

- Liu et al. "Lost in the Middle" TACL 2024 — 긴 context에서 중반 정보 무시
- arXiv:2505.23646 "Are Reasoning Models More Prone to Hallucination?" — think-answer mismatch 현상
- arXiv:2512.22250 "Hallucination Detection for LLM-based Text-to-SQL" — schema-linking 환각과 logical-synthesis 환각 분리
- Few-shot recency bias, 7+ template trap — 다수 커뮤니티 관측

### 1.5 인용 원칙

본 문서 내 규칙은 각 조항마다 위 §1.1~1.4 중 어느 근거에서 나왔는지를 꼬리표 `(R-3.5)`, `(R-Q3)`, `(R-Q3-ref)`, `(R-gen)` 형식으로 명시한다. 해석:

- `(R-3.5)`: Qwen3.5-397B-A17B 전용, 절대 적용
- `(R-Q3)`: Qwen3 시리즈 공통, 3.5에 그대로 유효
- `(R-Q3-ref)`: Qwen3 시리즈 벤치/관측, 참고만
- `(R-gen)`: LLM 범용 법칙

---

## 2. 인프라 계층과 프롬프트 계층의 책임 분리 (중요)

### 2.1 원칙

**프롬프트 파일(`resources/prompts/**/*.txt`)은 thinking 모드와 샘플링 파라미터를 일절 언급하지 않는다.** 이는 노드 코드에서 LLM 클라이언트를 호출할 때 설정하는 인프라 계층의 책임이다.

### 2.2 thinking 파라미터 제어 지점

`src/utils/llm/client.py:50` `_resolve_thinking_params` 함수가 이미 Qwen 계열에 대해 `extra_body.chat_template_kwargs.enable_thinking` 을 자동 주입한다. 노드 코드는 `mode` 인자(`"on" | "off" | "auto"`)만 지정하면 된다. (R-Q3)

### 2.3 노드별 thinking 정책 (확정)

**무엇을 정하는가**: 각 노드마다 Qwen3.5 thinking 모드를 ON/OFF 중 어느 쪽으로 호출할지 고정한다.

**왜 노드별로 다른가**: thinking ON은 다단계 추론·가설 탐색에는 유리하지만 JSON 출력 안정성·응답 latency·think-answer mismatch 위험이 따라온다. 노드의 주 임무가 "추론"이면 ON이, "결정성·규칙 검증·분류·구조화 출력"이면 OFF가 유리하다. 전 노드 ON 통일은 결정성 노드에서 latency·JSON 깨짐을 유발하므로 기각됐다(§19.2).

**어떻게 적용하는가**: 아래 표의 값을 그대로 `_resolve_thinking_params(model, mode)` 호출 시 mode 인자로 전달한다. 프롬프트 본문에는 어떤 형태로도 thinking 지시를 쓰지 않는다(§2.1, §15). thinking **ON** 노드 4개는 반드시 `[HALLUCINATION_GUARD]`와 `reasoning_summary` 필드를 동반한다(§5.2, §6.2).

| 노드 | thinking | 이유 |
|---|---|---|
| `context_interpreter` | **ON** | 다중 도구 실행 결과(테이블 메타 + 과거 SQL + 업무 매뉴얼)를 교차 참조하여 지식 통합하는 다단계 추론. 열린 추론 공간 필요 (R-gen, R-Q3) |
| `recovery_agent` | **ON** | 실패 원인 진단 + 재계획 수립. 실패 패턴별 분기 추론 필요 (R-gen) |
| `sql_generator` | **ON** | 복잡 SQL 생성 시 JOIN 구조·집계 방식 추론 필요. 단, think-answer mismatch 방어 장치(§6.2) 필수 적용 (R-gen, R-Q3-ref) |
| `analyzer` | **ON** | 데이터 기반 인사이트 도출은 "숫자 패턴 관찰 → 가설 → 검증 → 해석" 다단계 판단. 열린 추론이 유리. JSON 안정성은 `reasoning_summary` + 3중 방어로 커버 (R-gen) |
| `intent_classifier` | OFF | 단순 분류, 결정성 우선 |
| `query_normalizer phase1` | OFF | 슬롯 채움, JSON 안정성 우선 |
| `query_normalizer phase2` | OFF | 교차검증 R1~R12 체크리스트, 결정성 |
| `sql_validator` | OFF | **규칙 기반 검증 + 다른 노드의 교차 검증자 역할이므로 결정성 절대 우선.** thinking ON으로 돌리면 자기 자신이 think-answer mismatch에 노출되어 검증자로서의 신뢰성을 잃는다 |
| `analyzer_viz_judgment` | OFF | 이산 선택 |
| `analyzer_viz_svg` | OFF | 구조화 출력, 토큰 절약 |

### 2.4 샘플링 파라미터 권장치 (인프라 설정)

프롬프트가 아닌 LLM 클라이언트 호출 인자로 설정. (R-3.5)

| 모드 | temperature | top_p | top_k | presence_penalty |
|---|---|---|---|---|
| thinking ON | 0.6 | 0.95 | 20 | - |
| thinking OFF (일반) | 0.7 | 0.8 | 20 | 1.5 |
| thinking OFF (결정성 강화) | 0.2~0.3 | 0.8 | 20 | 1.5 |

`sql_validator`, `query_normalizer phase2` 등은 결정성 강화 프로파일 권장. 본 전략 문서는 원칙만 기재하고, 실제 수치 튜닝은 골든셋 평가 결과에 따라 `src/utils/llm/client.py` 또는 노드 코드에서 조정한다.

### 2.5 vLLM 버전 조건부 운영 (필수 확인)

폐쇄망 배포 전 vLLM 버전을 반드시 확인한다. (R-Q3)

- **vLLM ≥ 0.9.1**: `guided_json` + `enable_thinking` 자유 조합 가능
- **vLLM < 0.9.1**: `enable_thinking=False` + `guided_json` 조합은 **사용 금지** (JSON 깨짐). 이 경우 두 가지 fallback 중 선택:
  1. `guided_json` 없이 프롬프트 레이어 방어(§5)에만 의존 + 코드 레이어 fence/trailing 제거 재시도(§5.3)
  2. `/no_think` 토큰을 user 메시지 말미에 삽입하는 방식으로 전환

폐쇄망 환경은 라이브러리 업그레이드가 제한적이므로, **기본 방어선은 프롬프트 + 코드 레이어**로 설정하고 `guided_json`은 버전 확인 후 보강 수단으로 취급한다.

---

## 3. 프롬프트 유형 분류

생성·수정 전 반드시 유형을 먼저 식별한다. 유형에 따라 골격 순서와 Few-shot 개수가 달라진다.

### 3.1 단순형

단일 지시, 단순 출력. 예: `intent_classifier`, `analyzer_viz_judgment`, `analyzer_viz_svg`.

골격: `[ROLE] → [RULES] → [EXAMPLES] → [OUTPUT_CONTRACT] → [TASK]`

### 3.2 에이전트 계획 수립형

도구 실행 계획 수립, 다음 행동 결정. 예: `recovery_agent`, (광의로) `sql_generator`.

골격: `[ROLE] → [CONTEXT] → [RULES] → [TOOLS] → [EXAMPLES] → [OUTPUT_CONTRACT] → [TASK]`

### 3.3 에이전트 분석 판정형

도구 결과 해석, 지식 갱신, 판정. 예: `context_interpreter`, `query_normalizer phase1/phase2`, `sql_validator`, `analyzer`.

골격: `[ROLE] → [CONTEXT] → [RULES] → [EXAMPLES] → [OUTPUT_CONTRACT] → [TASK]`

**공통 규칙**: `[TASK]`는 반드시 마지막. 중간에 배치하면 모델이 예시·규칙을 참고하지 않고 조기 응답한다. (R-gen)

---

## 4. 프롬프트 구조 표기 규칙

### 4.1 지시/제어 영역 — `[TAG]` 형식

```
[ROLE]
[CONTEXT]
[RULES]
[TOOLS]
[EXAMPLES]
[OUTPUT_CONTRACT]
[HALLUCINATION_GUARD]
[TASK]
```

- 태그는 영어 대문자 스네이크 케이스
- 모든 태그가 필수는 아님. 유형에 따라 필요한 태그만 사용
- 블록이 필요한 경우: `[TAG]...[/TAG]`
- 지시 태그와 식별자 태그를 혼용하지 않는다

### 4.2 문서/지식 영역 — `##` 형식

- 대주제 `##`, 소주제 `###`, 최대 3단계 (`####`)까지
- 초과 시 새 `##` 섹션으로 분리

### 4.3 규칙 리스트 분량 상한 (MoE 라우팅 대응)

**무엇을 제한하는가**: 하나의 `[RULES]` 블록·카테고리에 넣을 수 있는 규칙 항목 개수를 상한선으로 고정한다.

**왜 제한하는가**: Qwen3.5는 MoE(512 experts, 10+1 active) 구조라서, 한 카테고리 안에 규칙이 6개 이상 나열되면 항목마다 활성화되는 expert 조합이 달라지면서 중간 규칙이 라우팅 분기 과정에서 "잊히는" 현상이 관찰된다. 규칙을 5개 이하 카테고리로 쪼개면 각 카테고리가 상대적으로 일관된 expert 집합으로 처리되어 중간 드롭 확률이 떨어진다. (R-3.5, R-gen)

**어떻게 적용하는가**: 프롬프트를 작성할 때 아래 숫자를 경직된 상한으로 취급하고, 초과되면 반드시 카테고리 분리 또는 별도 섹션 분할로 대응한다. "5개 규칙을 6개로 늘리는" 절충은 허용하지 않는다.

- **카테고리당 최대 5 규칙**, 초과 시 카테고리 분리
- **한 `[RULES]` 블록 내 총 20 규칙 이내**
- 초과 시 별도 섹션(`[HARD_CONSTRAINTS]`, `[HALLUCINATION_GUARD]` 등)으로 분할

### 4.4 항목 나열 — `-` 하이픈

- 순서가 중요한 절차에만 숫자 사용
- 항목 설명 한 줄이면 콜론으로 이어쓰기
- 두 줄 이상이면 `###` 승격
- 중첩은 1단계까지만 허용

### 4.5 조건-결과 매핑 — `→` 형식

```
- 테이블 메타에서 컬럼 존재 확인 → CANDIDATE (confidence 0.3~0.5)
- 샘플 데이터에서 실제 값 확인 → CONFIRMED (confidence 0.8~0.95)
```

산문으로 섞어 쓰지 않는다. 모델이 조건과 결과를 직접 파싱한다.

### 4.6 판단 이중 구조

하나의 판단이 "해야 할 때"와 "하지 않아도 될 때"를 모두 포함할 때는 명시적으로 분리한다.

```
fail로 판단하는 경우:
- 조건값이 코드 매핑에 없음
- 필요한 컬럼이 테이블에 없음

success를 유지하는 경우 (결과 정확성에 영향 없는 불확실성):
- 날짜 범위 해석, 정렬 방향, 행 제한 수
```

### 4.7 Positive Form 우선 원칙 (필수)

**무엇이 원칙인가**: 제약을 표현할 때 "허용되는 것"을 적는 positive form을 기본으로 하고, "금지되는 것"을 적는 negative constraint는 예외적 상황에만 쓴다.

**왜 그런가**: Qwen3 계열을 포함한 대부분의 instruction-tuned LLM은 "~하지 마라" 형태의 negative constraint보다 "~만 허용한다" 형태의 positive form에서 지시 준수율이 유의하게 높다. negative 문장은 금지 대상을 토큰으로 먼저 생성하게 되어 오히려 해당 패턴을 "떠올리게" 하는 priming 부작용이 있다. IFBench/IFEval 류 평가에서도 동일한 경향이 반복적으로 보고된다. (R-gen)

**어떻게 적용하는가**: `[RULES]`·`[HARD_CONSTRAINTS]`·`[TASK]`에 쓰는 문장은 가급적 "A만 사용한다", "B로만 구성한다", "C일 때 D를 수행한다" 형태로 변환한다. 아래 변환 예시를 참고하라.

나쁜 예 → 좋은 예:

- "INSERT/UPDATE/DELETE를 사용하지 말 것" → "생성하는 SQL은 SELECT 구문으로만 구성한다"
- "스키마에 없는 컬럼을 쓰지 말 것" → "[INPUT_CONTEXT]의 컬럼 목록에 있는 컬럼만 사용한다"
- "설명을 붙이지 말 것" → "JSON 객체 하나만 출력한다"

단, `[HALLUCINATION_GUARD]` 블록의 "위반 예시"는 **구체 예시 제시 목적**이므로 negative 형태로 두되 반드시 "올바른 예"와 쌍으로 제시한다(§8 참조).

### 4.8 식별자 표기

지식 항목, 스텝, 가설 등 식별자가 필요한 경우:

```
# 지식 항목 — 소문자 괄호
(k1) measure: 평균 여신 잔액
 - 상태: PROBABLE
 - 값: AVG(LN_BAL_AMT)
 - 확인근거: TB_ADW_LNB301M에서 잔액 집계 패턴 확인

# 스텝 — ### [Step N]
### [Step 1] tool_name(input)

# 가설 — H_숫자
H_R1, H_R2
```

식별자에 대괄호(`[K1]`)를 쓰지 않는다. 지시 태그 `[TAG]`와 혼동 방지.

### 4.9 부연설명

- 짧은 부연: 항목 뒤 `(부연 내용)` 형식
- 긴 부연: 별도 항목 또는 평문
- `[NOTE]` 같은 지시 태그 형태 부연 금지

### 4.10 블록 배치 순서 원칙 (필수)

**무엇을 정하는가**: 하나의 프롬프트 안에서 `[ROLE]`·`[RULES]`·`[CONTEXT]`·`[TOOLS]`·`[EXAMPLES]`·`[OUTPUT_CONTRACT]`·`[TASK]` 등 블록들이 **어느 순서로** 놓여야 하는지를 원칙화한다. §3의 유형별 골격은 블록 "집합"만 정했고, 순서는 본 절에서 원칙 기반으로 정한다.

**왜 순서가 중요한가**: 디코더 LLM은 앞→뒤 순서로 토큰을 읽고, attention은 "맨 앞"과 "맨 뒤"에 쏠린다(lost-in-the-middle + recency bias). 동일한 블록 집합이라도 순서를 바꾸면 모델이 무엇을 더 강하게 참고할지가 달라진다. 특히 **가변 CONTEXT**(플레이스홀더로 치환되는 런타임 데이터)를 중간에 두면 매 호출마다 위치가 흔들려서 규칙과의 상대 거리가 변하고, 이것이 품질 편차의 숨은 원인이 된다.

**순서 설계 4원칙**:

1. **불변 → 가변 순서**: 매 호출마다 동일한 블록(`[ROLE]`, `[RULES]`, `[TOOLS]`, `[EXAMPLES]`, `[OUTPUT_CONTRACT]`)을 먼저, 런타임에 치환되는 블록(`[CONTEXT]`)을 뒤에 배치한다. 캐시 친화적이고 규칙 해석이 안정적이다.
2. **lost-in-the-middle 방어**: 핵심 규칙은 **최상단**(`[RULES]` 또는 `[HARD_CONSTRAINTS]`)과 **최하단**(`[TASK]` 재강조) 양쪽에 배치한다. 중간에는 트리밍된 요약만 둔다.
3. **Recency bias 활용**: 가장 중요한 "출력 제약 + 현재 과제"는 **반드시 마지막**에 온다 → `[TASK]`는 항상 최종 블록. Few-shot의 마지막 예시는 recency bias에 가장 가까워 템플릿으로 작용하므로 "가장 대표적인 케이스"를 배치한다(§7.4).
4. **Few-shot과 OUTPUT_CONTRACT의 상대 위치**: `[EXAMPLES]` 블록 자체는 `[OUTPUT_CONTRACT]` **앞**에 둔다. 이유는 예시가 스키마 선언을 구체화된 형태로 "검증"해주는 역할이기 때문 — 예시를 본 뒤 contract를 보면 모델이 contract를 예시와 조응하여 해석한다. 반대 순서는 예시 해석이 불안정해진다.

**가변 CONTEXT 배치의 결정적 규칙**: `[CONTEXT]`가 플레이스홀더로 치환되는 변동 데이터(`{tool_results}`, `{knowledge_state}` 등)를 담는다면, **`[TASK]` 바로 직전**에 둔다. 중간이 아니다. 이렇게 해야 recency bias가 "가장 최신의 컨텍스트 → 즉시 과제 수행"으로 이어진다. `context_interpreter`의 긴 `{tool_results}`도 동일 원칙이며, 내부 배치는 §6.3 참고.

**프롬프트 유형별 권장 블록 순서**:

| 유형 | 권장 순서 |
|---|---|
| 단순형 (intent_classifier, analyzer_viz_judgment, analyzer_viz_svg) | `[ROLE]` → `[RULES]` → `[EXAMPLES]` → `[OUTPUT_CONTRACT]` → `[CONTEXT]` → `[TASK]` |
| 분석 판정형 — thinking OFF (query_normalizer_phase1/phase2, sql_validator) | `[ROLE]` → `[RULES]` → `[HALLUCINATION_GUARD]` → `[EXAMPLES]` → `[OUTPUT_CONTRACT]` → `[CONTEXT]` → `[TASK]` |
| 분석 판정형 — thinking ON long context (context_interpreter) | `[ROLE]` → `[RULES]` → `[HALLUCINATION_GUARD]` → `[EXAMPLES]` → `[OUTPUT_CONTRACT]` → `[CONTEXT]`(§6.3 내부 순서) → `[TASK]`(핵심 규칙 재강조) |
| 분석 판정형 — thinking ON 생성 (analyzer) | `[ROLE]` → `[RULES]` → `[EXAMPLES]` → `[OUTPUT_CONTRACT]` → `[CONTEXT]` → `[TASK]`(reasoning_summary 재명시) |
| 계획 수립형 — 도구 사용 (recovery_agent) | `[ROLE]` → `[RULES]` → `[TOOLS]` → `[HALLUCINATION_GUARD]` → `[EXAMPLES]` → `[OUTPUT_CONTRACT]` → `[CONTEXT]` → `[TASK]`(reasoning_summary 재명시) |
| 계획 수립형 — 복잡 생성 (sql_generator) | `[ROLE]` → `[MISSION]` → `[HARD_CONSTRAINTS]` → `[HALLUCINATION_GUARD]` → `[RULES]`(SQL_WRITING/VALUE/DIALECT) → `[EXAMPLES]` → `[OUTPUT_CONTRACT]` → `[CONTEXT]`(INPUT_CONTEXT) → `[TASK]`(reasoning_summary + fail 우선 재명시) |

**공통 규칙**:
- `[TASK]`는 언제나 맨 마지막. 예외 없음.
- 가변 `[CONTEXT]`는 언제나 `[TASK]` 바로 앞. 중간에 두지 않는다.
- `[HALLUCINATION_GUARD]`는 `[RULES]`/`[HARD_CONSTRAINTS]` 직후. 뒤쪽(`[TASK]` 근처)에 두면 "하지 말 것" 토큰이 recency로 반대로 priming된다.
- 여러 `[RULES]` 섹션을 쓸 때는 "가장 중요한 제약"을 첫 번째 섹션에 둔다.

---

## 5. [OUTPUT_CONTRACT] 규칙과 JSON 안정성 3중 방어

### 5.1 OUTPUT_CONTRACT 작성 원칙

**무엇이 원칙인가**: 출력 스키마 선언 블록은 반드시 `[OUTPUT_CONTRACT]` 태그를 쓰고, 필드명과 필드 설명을 같은 줄에 두지 않는다.

**왜 그런가**: 첫째, `[OUTPUT]`보다 `CONTRACT`가 "형식 위반은 불허"라는 의미를 더 강하게 전달한다 — 태그 이름 자체가 약한 언어 신호로 작동한다. 둘째, Qwen3 계열은 필드 이름 옆에 `description` 문자열을 같은 줄에 두면 그 description을 필드 **값**으로 오인하여 스키마를 깨뜨리는 버그(vLLM #23404)가 있다. 필드 선언과 설명을 물리적으로 분리해야 안전하다. (R-Q3)

**어떻게 적용하는가**: 아래 선언 방식·조건부 필드 기술·별도 설명 섹션 분리 원칙을 그대로 따른다. `description=` 인라인 표기는 절대 쓰지 않는다.

`[OUTPUT]` 대신 `[OUTPUT_CONTRACT]`를 사용한다. CONTRACT는 형식을 반드시 지켜야 한다는 의미를 강화한다.

**필드 선언 방식**:

```
"action": "replan" | "give_up"
"confidence": number (0.0~1.0)
"status": "CANDIDATE" | "PROBABLE" | "CONFIRMED" | "CONFLICTED"
```

**조건부 필드**: 상황에 따라 포함/생략/빈값이 달라지는 필드는 선언 바로 아래에 조건을 명시한다.

```
"execution_plan": array
- action이 give_up이면 빈 배열([])로 출력
- action이 replan이면 1~4개 스텝 포함

"new_hypothesis": object | null
- 이전 가설과 다른 접근일 때만 포함
- 없으면 null로 출력 (필드 생략 금지)
```

**필드 설명의 위치**: 필드명 옆 `description` 으로 쓰지 않는다. Qwen3가 description을 필드 값으로 오인하는 버그(vLLM #23404) 방지. 설명은 별도 `### 필드 설명` 섹션으로 분리. (R-Q3)

### 5.2 `reasoning_summary` 필드 신설 (thinking ON 노드 필수)

**무엇인가**: thinking ON으로 호출되는 모든 노드의 `[OUTPUT_CONTRACT]`에 `reasoning_summary: string` 필드를 추가한다. 기존 스키마에 없었으므로 본 전략으로 신설한다.

**왜 필요한가**: thinking ON 모델은 `<think>` 블록에서 도출한 결론과 최종 answer가 어긋나는 "think-answer mismatch"(arXiv:2505.23646) 현상에 노출된다. think 내부 토큰은 코드 레이어에서 직접 파싱·검증하기 어려우므로, 모델이 스스로 "어떤 근거로 이 답을 냈는지"를 JSON 내부 필드로 요약하게 하여 코드 레이어에서 교차 검증할 수 있게 만든다. 이 필드는 사용자에게 보여주는 `explanation`과 목적이 다르다 — 검증용 메타데이터다.

**어떻게 적용하는가**: thinking ON 4개 노드(`context_interpreter`, `recovery_agent`, `sql_generator`, `analyzer`) 프롬프트를 작성·수정할 때 반드시 `reasoning_summary`를 필수 필드로 선언한다. thinking OFF 노드에는 추가하지 않는다(토큰 낭비).

**적용 대상**: `context_interpreter`, `recovery_agent`, `sql_generator`, `analyzer`

**스키마**:

```
"reasoning_summary": string
- 최종 출력(sql/judgment/plan)을 도출한 핵심 근거를 1~3줄 요약
- "어떤 테이블/컬럼/도구 결과를 사용했는가" 와 "어떤 판단을 내렸는가" 를 기재
- 사용자 대상 설명(explanation)과 목적이 다르다:
  - explanation: 사용자에게 보여주는 자연어 설명
  - reasoning_summary: 시스템 검증용, 출력과의 정합성 체크 근거
```

**sql_generator 적용 예시 (기존 스키마 확장)**:

```json
{
  "status": "success",
  "sql": "SELECT ... FROM ADWOWN.TB_ADW_LNB301M WHERE LN_DT >= ...",
  "reasoning_summary": "여신기본마스터(TB_ADW_LNB301M)의 LN_DT를 기준으로 당월 필터, COUNT(*)와 SUM(LN_EXC_AMT) 집계. 참고 SQL EX_042의 DATE_TRUNC 패턴 차용.",
  "failure_reasons": [],
  "assumptions": ["'이번 달 신규'의 해석 → 대출실행일자(LN_DT) 기준 당월 실행 건"],
  "explanation": "이번 달 여신기본마스터에서 신규 실행 건수와 금액 합계를 집계"
}
```

**검증 로직** (코드 레이어에서 구현):

1. `reasoning_summary`에 언급된 테이블/컬럼이 `sql`에 실제 등장하는지 확인
2. 불일치 시 think-answer mismatch 로그 남기고 recovery_agent로 라우팅
3. `reasoning_summary`가 비어 있으면 정합성 검증 불가이므로 fail 처리

**context_interpreter 적용 예시**:

```json
{
  "knowledge_updates": [...],
  "reasoning_summary": "도구 실행 결과 Step 3(search_table_meta)에서 TB_ADW_LNB301M의 LN_DCD 컬럼 확인, Step 5(lookup_code_meta)에서 대출구분코드 매핑 10건 획득. 미해결 항목 k2(연체율 산출식)는 해소 실패 → 업무매뉴얼 재탐색 필요."
}
```

### 5.3 JSON 안정성 3중 방어

**무엇을 방어하는가**: Qwen3 계열이 JSON 출력에서 자주 실패하는 3대 패턴 — ```json 코드 펜스 감쌈, 결과 앞뒤의 설명 텍스트, 필드 설명을 값으로 오인 — 을 차단한다.

**왜 3중인가**: 각 계층마다 커버하는 실패 모드가 다르고, 어느 한 계층만으로는 빠져나가는 케이스가 존재한다. 프롬프트 레이어는 "출력 형식 지시"로 1차 차단, 샘플링 레이어는 "반복 토큰 억제"로 trailing 설명 생성 확률 감소, 코드 레이어는 "이미 오염된 출력을 정제"하여 복구한다. 하나가 뚫려도 다음 계층에서 잡는 구조다.

**어떻게 적용하는가**: 계층 1은 모든 JSON 출력 노드 프롬프트에 필수 반영, 계층 2는 `src/utils/llm/client.py`에서 설정(프롬프트에 쓰지 않는다), 계층 3은 노드 응답 처리 코드에 구현한다. 세 계층의 책임을 섞지 않는다.

**계층 1 — 프롬프트 레이어** (모든 JSON 출력 노드 필수)

`[OUTPUT_CONTRACT]` 말미 또는 별도 `[FORMAT_LOCK]` 블록에 다음 문구를 정확히 포함:

```
출력은 JSON 객체 하나로만 구성한다.
- 출력의 첫 문자는 반드시 { 이어야 한다.
- 마크다운 코드 펜스(```json, ```) 를 포함하지 않는다.
- JSON 이전이나 이후에 어떤 설명 텍스트도 쓰지 않는다.
- 모든 문자열은 큰따옴표만 사용한다.
```

thinking ON 노드에서는 `<think>` 블록 **바깥** 첫 번째 지시로 "최종 출력은 raw JSON만"을 배치한다. 즉, [TASK]에 재명시한다. (R-Q3)

**계층 2 — 샘플링 레이어** (인프라 설정, 프롬프트에 기재 금지)

- no_think 노드: `temperature=0.7`, `presence_penalty=1.5` (동일 토큰 반복으로 인한 trailing 설명 억제) (R-3.5)
- vLLM ≥ 0.9.1: `guided_json` + Pydantic 스키마 전달 권장
- vLLM < 0.9.1: `guided_json` 사용 금지, 프롬프트 + 코드 레이어로만 방어 (§2.5)

**계층 3 — 코드 레이어** (`src/utils/llm/` 또는 각 노드 응답 처리)

```python
import re, json

def extract_json(raw: str) -> dict:
    # 1차: 펜스 제거
    cleaned = re.sub(r'```json?\s*|\s*```', '', raw).strip()
    # 2차: 첫 { 이전 텍스트 제거
    start = cleaned.find('{')
    if start > 0:
        cleaned = cleaned[start:]
    # 3차: 마지막 } 이후 trailing 텍스트 제거
    end = cleaned.rfind('}')
    if end != -1:
        cleaned = cleaned[:end + 1]
    return json.loads(cleaned)
```

재시도 최대 2회, 3회 실패 시 `recovery_agent` 로 라우팅.

---

## 6. thinking ON 노드를 위한 추가 방어

### 6.1 think-answer mismatch 배경

arXiv:2505.23646이 보고한 현상: thinking 모델이 `<think>` 블록에서 도출한 결론과 최종 answer가 다를 수 있다. 특히 긴 reasoning trace에서 중간에 수정한 내용을 final answer에 반영하지 못하는 경우가 자주 발생한다. NL-to-SQL에서는 "think에서 올바른 SQL을 도출했으나 answer에 이전 버전을 출력"하는 형태로 나타나 검출이 어렵다. (R-gen)

### 6.2 방어 장치 4중 구조

**무엇을 하는가**: thinking ON 노드 4개에 대해 프롬프트 레이어 2중 + 코드 레이어 2중 방어를 동시에 건다.

**왜 4중인가**: think-answer mismatch는 확률적 현상이라 단일 방어로는 누락된다. "생성 시점(프롬프트)"과 "검증 시점(코드)"을 각각 이중화하여 어느 한 쪽이 뚫려도 다른 쪽에서 잡도록 설계한다.

**어떻게 적용하는가**: 노드 프롬프트 작성 시 아래 1·2를 빠짐없이 반영하고, 노드 코드에 3·4의 검증 로직을 구현한다. 하나라도 빠지면 방어 구조가 무너진다.

1. **`reasoning_summary` 필드 의무화** (§5.2) — think 결론 요약을 JSON 내부에 가둠. 프롬프트 `[OUTPUT_CONTRACT]`에 선언.
2. **`<think>` 바깥 첫 지시로 출력 형식 재명시** — 프롬프트 `[TASK]` 블록에 "최종 출력은 reasoning_summary 포함 JSON 객체 하나" 명시. `sql_generator`·`analyzer` 같은 생성형 노드에 필수.
3. **코드 레이어 교차 검증** — `reasoning_summary`에 언급된 테이블/컬럼/지표가 실제 출력(sql·insight·plan)에 등장하는지 검사. 불일치 시 경고 로그 또는 fail.
4. **하방 검증자 노드 강화** — `sql_validator`(thinking OFF)는 `sql_generator` 출력을 재검증. `analyzer`의 하방 검증은 별도 노드가 없으므로 코드 레이어에서 `reasoning_summary` vs `insight` 필드 텍스트 매칭으로 갈음한다.

### 6.3 context_interpreter long context 배치 순서

`context_interpreter`는 가장 많은 컨텍스트를 받는다 (도구 결과 + 기존 지식 + 원 질의). Liu et al. TACL 2024의 lost-in-the-middle 원칙에 따라 배치한다. (R-gen)

```
[ROLE]
[RULES]         ← 최상단, 핵심 판단 기준

[CONTEXT]
## 원 질의        ← 가장 짧고 중요
## 미해결 항목     ← 짧고 판단 분기의 기준
## 도구 실행 결과  ← 가장 길고 중간 위치 불가피, 관련도 순 정렬
## 이전 지식 상태  ← 갱신 대상

[EXAMPLES]
[OUTPUT_CONTRACT]
[TASK]          ← 말미, 핵심 규칙 1~2줄 재강조
```

**원칙**:
- 가장 중요한 규칙은 `[RULES]` 최상단 + `[TASK]` 말미 양쪽에 배치
- 중간에는 도구 실행 결과가 올 수밖에 없으므로 **관련도 상위 N개로 트리밍**하여 컨텍스트 압축
- `context_retriever` 노드가 `context_interpreter` 진입 전에 상위 N개로 자르는 책임

---

## 7. Few-shot 설계 규칙

### 7.1 노드별 개수 정책 (확정)

| 노드 | 권장 개수 | 상한 | 근거 |
|---|---|---|---|
| `intent_classifier` | 11 (현재 유지) | 12 | 분류 태스크는 경계 케이스 학습이 주 목적, template trap 거의 없음 (R-gen) |
| `sql_generator` | 6 (현재 v2 유지) | 6 | 생성 태스크지만 각 예시가 서로 다른 구조적 패턴(성공/fail/JOIN/PII/산출식 불명/값 잘림) 커버 |
| `query_normalizer phase1` | 5 | 5 | 슬롯 채움 edge case 학습 (시간 표현, 암묵적 필터, 금액 단위) |
| `recovery_agent` | 5 | 5 | 실패 유형별 재계획 전략 |
| `query_normalizer phase2` | 3 | 4 | R1~R12 중 가장 빈발하는 위반만 (R1, R2, R4) |
| `sql_validator` | 3 | 4 | 체크리스트 경계 케이스 |
| `context_interpreter` | 3 | 4 | 각 예시가 길어 template trap 리스크 ↑ |
| `analyzer` | 3 | 3 | 인사이트 도출은 예시 의존도 낮음 |
| `analyzer_viz_judgment` | 2~3 | 3 | 조건 매칭이 주 로직 |
| `analyzer_viz_svg` | 2 | 3 | 생성 태스크, 과다 예시는 SVG 구조 고착화 |

**원칙**:
- **분류 태스크**(intent_classifier)는 `7 이상 허용`. 각 예시가 독립된 클래스 레이블로 경계를 학습하므로 template trap이 발생하지 않는다.
- **생성 태스크**(sql_generator, query_normalizer, recovery_agent)는 `5~6 상한`. 각 예시가 서로 다른 구조적 패턴을 커버해야 하며, 중복 패턴 예시는 즉시 제거.
- **기타 태스크**는 `3 기본`. 5 초과 금지.
- 7개를 넘기는 것은 **intent_classifier 단 하나만 허용**된다. (R-gen)

### 7.2 예시 선택 기준

- 가장 빈번한 케이스 1개
- 가장 헷갈리기 쉬운 경계 케이스 1개
- 엣지 케이스(give_up, cold start, 조건부 출력, fail) 1개
- (생성형 추가) 서로 다른 구조적 패턴을 커버하는 케이스 N개

중복 패턴 예시는 제거. "단순 집계" 예시가 이미 있으면 또 다른 "단순 집계"를 추가하지 않는다.

### 7.3 예시 내부 구조

```
### 예시 N: 상황 제목 (케이스 유형)
상황: 한 줄 요약
- 핵심 상태 정보만 나열

{JSON 출력 예시}
```

### 7.4 배치 순서 — Recency Bias 활용

**무엇이 원칙인가**: Few-shot 예시들의 **순서**가 모델 출력에 직접 영향을 주므로, 가장 영향력 있는 위치인 "마지막 슬롯"을 전략적으로 사용한다.

**왜 그런가**: Qwen 계열을 포함한 디코더 LLM은 attention이 최근 토큰에 치우치는 recency bias 때문에, 동일한 예시 세트라도 배치 순서만 바꾸면 출력 분포가 뚜렷이 달라진다. 특히 Few-shot 블록 안에서는 마지막 예시가 바로 뒤따라오는 [TASK]에 가장 강한 템플릿 영향을 준다. 이를 "단점"이 아니라 "활용할 도구"로 취급한다. (R-gen)

**어떻게 적용하는가**:

- **가장 유사한 예시를 마지막에 배치**한다. 첫 예시가 아니라 마지막 예시가 모델의 출력 템플릿을 결정한다.
- 정적 프롬프트에서는 "가장 복잡하고 실전적인 패턴"을 마지막에, 단순 패턴을 앞쪽에 둔다.
- 골든셋 기반 동적 retrieval 구조라면, 검색된 유사 케이스를 맨 마지막 슬롯에 고정한다(나머지 슬롯은 커버리지 확보용).
- 테스트 시 동일 예시 세트의 순서만 바꿔서 품질 차이를 확인하는 것을 루틴 절차로 삼는다.

### 7.5 예시 JSON 품질 기준

**계획 수립형**:
- `analysis`: 왜 실패했는지 원인을 **구체적으로** 기술. "코드값 부족"이 아니라 "LN_STCD 코드값 매핑 없음 — WHERE 조건에 대출상태 필터 필요"
- `lessons_learned`: 이 케이스에서만 배울 수 있는 교훈. 일반론 금지
- `execution_plan`: 실제 실행 가능한 도구 조합만
- `depends_on`: 선행 스텝 필요 시 스텝 번호, 독립 실행이면 null

**분석 판정형**:
- `insight`: 관찰 내용을 한 문장으로 요약, 판단 포함
- `knowledge_updates`: key는 `unresolved_items`의 key를 그대로 사용
- `confidence`: 조건-결과 매핑 기준에 맞는 값
- `reason`: 판정 사유를 구체적으로 (SELECTED/REJECTED 근거 명시)
- 관찰 도구 스텝 예시에는 `explored_*` 배열을 포함하지 않는다

### 7.7 Few-shot 커버리지 매트릭스 (예시 선택 방법론)

**무엇을 하는가**: 3~6개라는 빠듯한 예시 예산 안에서 "어떤 케이스를 꼭 넣을지"를 결정하기 위해 **2차원 커버리지 매트릭스**를 먼저 그리고, 각 칸을 덮는 최소 예시 집합을 고른다.

**왜 필요한가**: §7.1에서 대부분의 노드는 3~6개로 예시 개수가 제한된다. 이 상황에서 "가장 흔한 케이스 + 가장 헷갈리는 케이스" 같은 직관적 선택은 커버리지에 구멍이 생긴다. 같은 축의 케이스를 중복으로 넣거나, 중요한 분기 하나를 놓치는 일이 반복된다. 매트릭스를 먼저 그려야 "각 예시가 어떤 축을 커버하는지"가 가시화되어 중복을 즉시 식별할 수 있고, 골든셋이 생겼을 때 "어떤 칸이 비었으니 예시를 교체한다"는 의사결정 기준이 된다.

**어떻게 작성하는가**:

1. **축 선정**: 해당 노드의 실패 유형을 기준으로 2개 축을 고른다. 한 축은 "케이스 유형(성공/fail/edge)", 다른 축은 "구조적 패턴(집계 방식, 조인 구조, 출력 분기 등)"이 일반적이다.
2. **각 칸에 케이스 배치**: 노드 단위 실패 분석이나 회귀 테스트 결과를 기반으로 빈도 높은 칸을 우선 채운다.
3. **최소 커버 집합 선택**: 3~6개 예시로 가장 많은 칸을 덮도록 선택한다. 한 예시가 2칸을 동시에 덮으면 효율적이다.
4. **중복 제거 규칙**: 같은 칸에 속하는 예시는 절대 둘 이상 넣지 않는다. 새 예시를 추가할 때는 "어느 칸이 비어 있는가?"부터 확인한다.
5. **갱신 원칙**: 골든셋에서 회귀 실패가 반복 발생하는 패턴이 나오면, 매트릭스에 빠진 칸을 식별하고 기존 예시 중 가장 중복도가 높은 하나를 교체한다.

**sql_generator 예시 매트릭스 (현재 v2의 6개 예시 매핑)**:

| 축: 구조 ↓ / 결과 → | 성공 (SQL 생성) | 성공적 fail (fail 선언) |
|---|---|---|
| 단순 집계 | EX1: 월별 신규 여신 집계 | - |
| 다중 JOIN | EX2: 고객-계좌-거래 조인 | - |
| PII 마스킹 / 값 잘림 | EX3: 고객 전화번호 마스킹 | - |
| 코드값 매핑 불명 | - | EX4: LN_STCD 매핑 미확인 |
| 산출식 불명 | - | EX5: 연체율 산출식 미확인 |
| 테이블 미확인 | - | EX6: 대상 테이블 재검색 필요 |

- 커버 축: 성공형에서 "집계 복잡도 + 도메인 요구(PII)", fail형에서 "환각 트리거 3가지(코드값·산출식·테이블)"
- 이 매트릭스가 있으면 "LN_STCD 매핑 실패 예시가 이미 있다" → "UV_CUST_STCD 매핑 실패 예시 추가는 중복"이라는 결정을 즉시 할 수 있다.

**노드별 필수 커버리지 축 (§7.1 보완)**:

| 노드 | 축 1 | 축 2 |
|---|---|---|
| `intent_classifier` | 의도 레이블 전수 | 경계 케이스(애매한 의도) |
| `sql_generator` | 성공 vs 성공적 fail | 환각 트리거 유형(코드값·산출식·테이블·컬럼) |
| `query_normalizer_phase1` | 슬롯 유형(시간·금액·필터) | 암묵 표현 vs 명시 표현 |
| `query_normalizer_phase2` | 교차검증 규칙(R1~R12) | 위반 유형 |
| `recovery_agent` | 실패 유형(TERM/TABLE/CODE/META) | 재계획 전략(재탐색·우회·give_up) |
| `sql_validator` | 검증 규칙 카테고리 | pass vs fail 경계 |
| `context_interpreter` | 도구 유형(탐색·관찰) | 지식 갱신 결과(SELECTED·REJECTED·CONFLICTED) |
| `analyzer` | 인사이트 유형(추이·비교·이상치) | 데이터 충분성(충분·불충분) |
| `analyzer_viz_judgment` | 시각화 적합성(YES·NO) | 데이터 유형(시계열·분포·범주) |
| `analyzer_viz_svg` | 차트 유형(bar·line) | 데이터 스케일 |

프롬프트 작성자는 **새 예시를 추가하거나 기존 예시를 교체하기 전에 반드시 이 매트릭스를 먼저 그려야 한다**. 매트릭스 없이 "좋아 보이는 예시"를 추가하면 거의 항상 중복이 발생한다.

---

### 7.6 Few-shot 예시에서 Negative Example 처리

**무엇이 원칙인가**: Few-shot 블록 **내부에는 성공 예시만** 포함한다. 실패/위반 예시는 `[HALLUCINATION_GUARD]` 블록에 분리한다(§8).

**왜 그런가**: Few-shot은 인간에게는 "good vs bad 대조 학습"이 효과적이지만, 디코더 LLM에게는 그렇지 않다. Qwen3를 포함한 대부분의 모델은 few-shot 영역에 있는 토큰 패턴을 "따라 쓸 템플릿"으로 해석하기 때문에, "이렇게 틀리면 안 된다"라고 써둔 예시조차 그 토큰 분포를 모방할 확률이 높아진다. few-shot의 역할은 "따라 할 패턴" 제시이며, "피해야 할 패턴" 교육은 `[HALLUCINATION_GUARD]`의 "위반 예시 + 올바른 대응" 쌍 구조가 담당한다. 두 역할을 한 블록에 섞지 않아야 각 블록이 제 기능을 한다. (R-gen)

**어떻게 적용하는가**:

- Few-shot에는 "성공한 출력" 또는 "성공적으로 fail을 선언한 출력"만 둔다. 후자는 sql_generator의 `status: "fail"` 출력처럼 "실패 상황에서 올바르게 fail을 선언한 예시"로, **success 카테고리의 일종**이다(v2의 EX_4, EX_5, EX_6 참고).
- "SQL에 존재하지 않는 컬럼을 쓴 잘못된 예시" 같은 것은 Few-shot에 절대 넣지 않고 `[HALLUCINATION_GUARD]`로 옮긴다.
- 예시 검토 시 "이 예시의 토큰 패턴을 모델이 그대로 따라 써도 괜찮은가?"를 자문하여 필터링한다.

---

## 8. `[HALLUCINATION_GUARD]` 블록

### 8.1 목적

모델이 존재하지 않는 테이블/컬럼/코드값을 생성하는 것을 방지한다. SKILL.md 구판의 "환각 방지 규칙 기술 패턴"을 발전시켜 은행 도메인 위반 예시와 함께 독립 블록으로 분리한다.

### 8.2 블록 구조

```
[HALLUCINATION_GUARD]

원칙:
- [INPUT_CONTEXT]에 명시된 테이블·컬럼·코드값만 사용한다
- 샘플 데이터 목록이 제공되면 그 값만 WHERE 조건에 사용한다
- 불확실하면 fail로 출력한다

위반 예시 (금지 패턴 — 이 형태로 출력하지 않는다):

- 위반 1: confirmed_terms에 ADWOWN.TB_ADW_LNB301M만 있는데 TB_ADW_LNB302M를 사용
- 위반 2: 컬럼 목록에 LN_NO, EDPS_CSN, LN_BAL_AMT만 있는데 OVERDUE_RATE를 사용
- 위반 3: 코드값 "01=정상, 02=연체"만 확인됐는데 WHERE LN_STCD='ACTIVE'
- 위반 4: FROM TB_ADW_DEP201P (스키마 누락) — 반드시 FROM ADWOWN.TB_ADW_DEP201P

올바른 대응 (위반 상황에서 따를 행동):
- 위반 1 상황 → fail로 출력, failure_reasons에 "재검색 필요: 해당 테이블 미확인" 기재
- 위반 2 상황 → fail로 출력, failure_reasons에 "컬럼 미확인 — {컬럼의 비즈니스 의미}" 기재
- 위반 3 상황 → fail로 출력, failure_reasons에 "코드값 매핑 미확인" 기재
- 위반 4 상황 → 스키마 포함하여 수정
```

### 8.3 원칙

**무엇이 원칙인가**: `[HALLUCINATION_GUARD]` 블록을 작성할 때 아래 4가지를 모두 지킨다.

**왜 그런가**: negative 예시만 나열하면 §7.6에서 설명한 토큰 패턴 모방 부작용이 발생하고, 위반 예시가 너무 많아지면 MoE 라우팅 중간 드롭(§4.3) 대상이 되며, 일반적 예시(다른 도메인)는 은행 테이블 스키마·코드값과 결합되지 않아 실제 환각 방어 효과가 없다. 이 원칙들은 negative 학습의 부작용을 억제하면서 도메인 특화 방어력을 확보하기 위한 최소 조건이다.

**어떻게 적용하는가**:

- **위반 예시는 카테고리당 최대 5개**. 초과 시 카테고리를 분리한다.
- **위반 예시마다 "올바른 대응"을 반드시 쌍으로 제시**한다. negative만 두지 않는다.
- **프로젝트 도메인 특화**: 반드시 은행·금융 용어, 실제 스키마명(`ADWOWN.TB_ADW_LNB301M` 등)으로 기재한다. 일반적 "Table A / Column B" 예시는 금지.
- **배치 위치**: `[HALLUCINATION_GUARD]`는 `[HARD_CONSTRAINTS]` 직후 또는 `[RULES]` 직후에 배치한다.

### 8.4 가드레일 적용 범위 원칙 (무엇을 넣고 무엇을 빼는가)

**무엇이 원칙인가**: `[HALLUCINATION_GUARD]`와 그 주변 `[RULES]`·`[HARD_CONSTRAINTS]`에 **"넣어야 할 것"**과 **"넣으면 안 되는 것"**을 명확히 구분한다. 프로젝트 업무 특성을 반영하되, 범위는 "중복/오역/환각 방지"에 한정한다.

**왜 범위를 제한하는가**: 가드레일을 너무 넓게 잡으면 두 가지 문제가 생긴다. 첫째, 금융 도메인 지식(계수산출식·업무 프로세스·상품 설명·전체 코드값 매핑) 자체를 프롬프트에 박아넣으면 토큰 폭발 + MoE 라우팅 드롭(§4.3) + 지식 업데이트 시 프롬프트 전면 개편이라는 3중 비용이 발생한다. 둘째, 이런 지식은 이미 RAG(Qdrant 업무매뉴얼·상품설명서) 또는 메타 조회(MongoDB 코드/테이블 메타)라는 **단일 진실원(single source of truth)** 을 갖고 있다. 프롬프트에 넣으면 소스가 이원화되어 불일치가 발생한다. 가드레일의 본분은 "모델이 도메인 지식을 외우게 만드는 것"이 아니라, "도메인 지식 조회 결과를 정확히 해석하고 환각을 억제하는 것"이다.

**어떻게 적용하는가**:

**IN — `[HALLUCINATION_GUARD]`·`[RULES]`에 포함할 항목**:

- 유사 테이블/컬럼 혼동 방지 규칙 (예: "LNB301M vs LNB302M 중 명시 확인된 것만 사용")
- 금융 용어 오역 방지 예시 (예: "여신 ≠ 대출 ≠ 여신금" 구분)
- 코드값 함정 (예: "LN_STCD는 메타 조회로 확인된 값만, 'ACTIVE' 같은 영문 상수 금지")
- PII 마스킹 규칙 (`.claude/rules/data-security.md` 참조)
- 불완전 메타 대응 규칙 (예: "컬럼 미확인 시 추측 금지, fail 선언")
- 스키마 누락 방지 (예: "FROM 절에 반드시 ADWOWN. prefix")

**OUT — `[HALLUCINATION_GUARD]`·`[RULES]`에 넣지 않을 항목**:

- 금융 계수산출식(연체율·BIS·LCR 등 구체 공식) → RAG(업무매뉴얼) 조회 결과로 런타임 주입
- 전체 테이블 카탈로그 → `context_retriever`가 상위 N개로 전달
- 업무 프로세스 설명(여신 심사·상품 가입 플로우 등) → RAG(업무매뉴얼)
- 상품 설명 일반 지식 → RAG(상품설명서)
- 모든 코드값 매핑 나열 → `lookup_code_meta` 도구 호출 결과로 런타임 주입

**예시 커버리지 판단 기준**: 가드레일 예시가 충분한지는 "도메인 지식을 몇 개 나열했는가"가 아니라 **"실제 실패 유형을 얼마나 다양하게 커버하는가"** 로 판단한다. 즉, "유사 테이블 혼동·용어 오역·코드값 함정·PII·스키마 누락"이라는 **실패 유형 축**에서 각각 1개 이상의 예시가 있으면 충분하다. 같은 실패 유형의 예시를 여러 개 나열하는 것은 가드레일 확장이 아니라 중복이다.

---

## 9. User 말미 재강조 패턴

### 9.1 원칙

**무엇을 하는가**: system 블록 상단에서 선언한 핵심 제약 1~2개를 user 메시지 말미 또는 `[TASK]` 블록에 한 번 더 짧게 반복한다.

**왜 필요한가**: 긴 프롬프트에서는 앞쪽에 선언한 제약이 Liu et al. TACL 2024의 lost-in-the-middle 효과로 attention이 약해지고, 모델이 블록 중간의 예시·context 설명에만 집중하여 최종 지시를 놓치는 경우가 발생한다. 말미 재강조는 "가장 마지막에 본 지시를 따른다"는 recency bias를 활용해 이 효과를 보상한다. (R-gen)

**어떻게 적용하는가**: 재강조는 **정말 중요한 1~2개 제약에만** 한정한다. 모든 규칙을 재반복하면 토큰만 낭비되고 오히려 "앞의 지시와 뒤의 지시가 서로 다르지 않나?"라는 혼동을 유발한다. 출력 형식과 "정확성 보장 불가 시 fail" 같은 최상위 제약만 반복하는 것이 안전하다.

### 9.2 재강조 내용 기본값

```
[TASK]

- 위의 입력을 근거로 {목표 작업}을 수행하라.
- 출력은 [OUTPUT_CONTRACT]의 JSON 객체 하나만. 마크다운 코드 펜스·설명 텍스트 금지.
```

**2줄 상한**. 이 이상은 토큰 낭비 + 과잉 반복으로 인한 역효과.

### 9.3 고위험 노드 추가 재강조

`sql_generator`, `context_interpreter` 등 고위험 노드는 1~2줄 추가 허용 (총 3~4줄 상한):

```
[TASK]

- [INPUT_CONTEXT]의 확인된 테이블·컬럼·코드값만 사용하여 {작업} 수행.
- 정확성을 보장할 수 없으면 추측하지 말고 fail로 출력.
- 출력은 [OUTPUT_CONTRACT]의 JSON 객체 하나만. reasoning_summary 필드 필수.
```

### 9.4 재강조 금지 내용

다음은 user 말미에 **반복하지 않는다**:

- 금지 패턴 리스트 (이미 [RULES]에 있음)
- 전체 규칙 나열
- few-shot 패턴 설명
- 도메인 용어 설명

system에서 말한 "판단 기준"을 user 말미에 또 쓰면 모델이 두 버전을 대조하며 혼동한다. **user 말미는 출력 형식과 최상위 제약 1~2개만**.

---

## 10. [CONTEXT] 변수 주입 규칙

### 10.1 플레이스홀더 의미 명시

플레이스홀더의 "형식"은 코드에서 치환되므로 프롬프트에서 재정의할 필요가 없다. 대신 **무엇이고 어떻게 활용해야 하는지** 의미를 명시한다.

```
## 도구 실행 결과

{tool_results}

위는 지금까지의 모든 도구 실행 결과입니다.
- 이미 실행한 검색을 반복하지 마세요.
- 결과가 부족했다면 page=N으로 다음 페이지를 조회하세요.
```

의미가 자명한 단순 스칼라(`{query}`, `{today}` 등)는 별도 설명 불필요.

### 10.2 JSON을 입력으로 주입하지 않는다

JSON은 출력 형식 전용. 입력 데이터는 `(kN)` 레이블 형식 또는 `### [Step N]` 블록 형식 사용.

| 데이터 성격 | 권장 형식 |
|---|---|
| 단일 스칼라 값 | 인라인 자연어 |
| 상태+근거가 있는 항목 | `(kN)` 레이블 형식 |
| 실패/오류 기록 | `- [TYPE] 설명 \n  교훈: ...` |
| 복수 반복 블록 | `### [Step N]` + `---` 구분자 |

### 10.3 플레이스홀더 순서 원칙

모델이 추론할 때 참고하는 순서와 동일하게 배치한다.

```
원 질의 → 확인된 것 → 미확인된 것 → 도구 결과 → 이력 → 요약
```

### 10.4 선택지 설명 분리

변수 값이 여러 경우의 수를 가질 때, 선택지 설명을 플레이스홀더와 같은 블록에 두지 않는다. `[RULES]` 섹션에 별도 기술.

잘못된 방식:
```
[CONTEXT]
진입 경로: {entry_source}
  - readiness_gate: 초기 탐색 불충분
  - sql_validator: SQL 검증 실패
```

올바른 방식:
```
[CONTEXT]
진입 경로: {entry_source}

[RULES]
## 진입 경로 해석
### readiness_gate
초기 탐색이 불충분하여 추가 탐색이 필요하다.
### sql_validator
SQL 검증이 실패하여 원인 분석과 재생성이 필요하다.
```

---

### 10.5 [ROLE] 작성 3요소 (에이전트 모듈 전용)

역할 선언은 반드시 3가지를 포함한다.

- **이 모듈의 정체**: "너는 X 모듈이다"
- **이 모듈이 하는 것**: 핵심 동작 1문장
- **이 모듈이 하지 않는 것**: 혼동 방지를 위한 명시적 제외

```
[ROLE]
너는 SQL 생성 에이전트의 recovery 모듈이다.
현재 상태를 분석하고 탐색 계획(execution_plan)을 수립한다.
도구를 직접 실행하지 않는다. 계획 수립만 담당한다.
```

"하지 않는 것"을 명시하지 않으면 모델이 역할 경계를 침범한다. 예: recovery가 직접 SQL을 작성하거나, context_interpreter가 재계획을 수립하는 등.

---

### 10.6 [RULES] — 교차 참조 지시 기술

여러 스텝 결과를 연결해서 해석해야 할 때 명시적으로 기술한다.

```
- 활용사례(search_use_cases)에서 확인된 조인 구조를 테이블 메타 해석에 반영한다
- 활용사례 SQL에서 사용된 테이블이 메타에서도 확인되면 confidence를 한 단계 높인다
```

교차 참조를 명시하지 않으면 모델이 스텝 간 연결을 놓치고 낮은 confidence를 유지하는 경향이 생긴다.

---

### 10.7 [RULES] — 도구 타입별 출력 분기 (분석 판정형 전용)

도구 타입에 따라 출력해야 할 필드가 달라질 때 명시한다.

```
탐색 도구 (search_*, lookup_*):
- insight, knowledge_updates, explored_* 배열 모두 출력

관찰 도구 (get_*, read_*):
- insight, knowledge_updates만 출력
- explored_* 배열 출력하지 않음
```

이 분기를 명시하지 않으면 모델이 관찰 도구 스텝에서도 판정 배열을 출력하거나 누락하는 오류가 발생한다.

---

### 10.8 [RULES] — 판정 기준 엔티티 유형별 분리 (분석 판정형 전용)

SELECTED/REJECTED 판정이 필요한 엔티티 유형별로 독립적으로 기술한다. 여러 엔티티의 판정 기준을 하나의 섹션에 섞으면 모델이 어느 기준을 어느 엔티티에 적용해야 할지 혼동한다.

```
### 용어사전 판정 기준
- SELECTED: SQL 변환(집계 방식, 필터 조건, 산출식)에 구체적인 힌트를 제공하는 경우
- REJECTED: 일반적 업무 설명에 그쳐 SQL 변환에 영향을 주지 않는 경우
- reason 기재: "어떤 SQL 변환 힌트를 제공하는지" 또는 "SQL 변환과 무관한 이유"

### 테이블 메타 판정 기준
- SELECTED: 질의의 measure/dimension이 해당 테이블의 컬럼과 직접 매칭되는 경우
- REJECTED: 주제영역이 일치하지만 컬럼이 매칭되지 않는 경우
- reason 기재: "어떤 컬럼이 어떤 measure/dimension에 매핑되는지"

### 활용사례 판정 기준
- SELECTED: 집계 방식, JOIN 구조, WHERE 패턴 중 하나 이상이 재사용 가능한 경우
- REJECTED: 주제는 유사하나 구조가 완전히 다른 경우
- reason 기재: "재사용 가능한 구조 요소"
```

---

### 10.9 [TOOLS] 도구 명세 규칙 (계획 수립형 전용)

### 그룹핑 원칙

도구를 기능 성격으로 그룹화한다. **번호 목록을 쓰지 않는다** (번호는 우선순위 순서로 오해를 유발).

```
지정 조회 도구 (이름/키를 알고 있을 때):
- lookup_table_meta(table_name): 영문 테이블명으로 단일 테이블 메타 조회 (page 미지원)
- lookup_code_meta(column_name): 코드 컬럼명으로 코드값 매핑 조회 (page 지원)

탐색 도구 (키워드/의미 기반 탐색):
- search_table_meta(query, page=N): 업무 키워드로 테이블/컬럼 메타 검색
- search_use_cases(query, page=N): 과거 유사 SQL 벡터 검색
```

각 그룹 아래에 "언제 이 그룹을 쓰는가"를 한 줄로 명시한다.

### 도구 기술 형식

```
도구명(파라미터): 한 줄 설명 (page 지원 여부)
```

### 도구 input 형식 규칙

```
단일 파라미터: "TB_ADW_LNB301M"
복수 파라미터: "테이블명,컬럼명,키워드" (쉼표 구분 명시)
page 포함: "검색어, page=N"
```

### 페이징 규칙

- `page` 지원/미지원 도구를 명확히 구분
- `page=3` 이상은 효율이 낮으므로 키워드 변경을 권장한다는 원칙 명시
- 동일 키워드로 이미 조회한 페이지를 반복 조회하지 않는다

---

## 11. 모듈 간 데이터 전달 규칙

에이전트 루프에서 한 모듈의 출력이 다음 모듈의 입력으로 전달된다. 이 모듈의 `[OUTPUT_CONTRACT]`에 명시된 형식이 다음 모듈이 플레이스홀더로 받는 형식과 **정확히 동일**해야 한다.

```
이 모듈 [OUTPUT_CONTRACT]:
  "dead_ends": [{"type": "TERM_UNRESOLVABLE", "lesson": "..."}]

다음 모듈 플레이스홀더 형식 명시:
  {dead_ends_summary} 는 아래 형식으로 제공된다:
  - [TYPE] 설명
    교훈: ...
```

형식 불일치 시 다음 모듈이 데이터를 잘못 파싱한다. 프롬프트 수정 시 업스트림/다운스트림 양쪽을 동시에 점검한다.

---

## 12. 사고 과정 강제 패턴

모델이 특정 순서로 판단해야 하는 경우 STEP 번호로 강제한다.

```
## 사고 과정

STEP 1: 사용자가 원하는 데이터가 무엇인지 파악
STEP 2: 확인된 지식 항목에서 테이블/컬럼을 찾음
STEP 3: 참고 SQL이 있으면 조인 구조·집계 방식을 참고
STEP 4: 충분성 판단 — STEP 2~3에서 모두 확인되었는가?
        하나라도 빠지면 즉시 fail을 출력한다
```

**주의**: thinking ON 노드에서는 STEP 강제가 `<think>` 블록의 자유 추론과 충돌할 수 있다. thinking ON 노드는 STEP 강제 대신 `[RULES]`에 판단 기준만 기술하고 순서는 모델 자율에 맡긴다.

thinking OFF 노드에서만 STEP 강제 패턴 사용.

---

## 13. 조건 분기 섹션 (dialect 등)

런타임 변수 값에 따라 적용 규칙이 달라질 때 사용한다.

```
## dialect별 규칙

### Sybase IQ (dialect = tsql)
- 행 제한: SELECT TOP N
- 문자열 결합: col1 + col2

### PostgreSQL (dialect = postgresql)
- 행 제한: LIMIT N
- 문자열 결합: col1 || col2
```

각 분기를 독립 `###` 섹션으로 분리하고, 섹션 제목에 분기 조건을 명시한다.

---

## 14. 테이블 형식 조건-결과 매핑

케이스가 많고 구조가 일정할 때 `→` 나열 대신 테이블을 쓴다.

```
| 단위 | 승수       | 예시  | 변환        |
|------|------------|-------|-------------|
| 천   | ×1,000     | 5천   | 5,000       |
| 만   | ×10,000    | 15만  | 150,000     |
| 억   | ×100,000,000 | 1억 | 100,000,000 |
```

3개 이하 케이스는 `→` 형식, 4개 이상은 테이블 형식이 가독성 높음.

---

## 15. 금지 패턴

**무엇을 금지하는가**: 아래 패턴은 모든 프롬프트에서 예외 없이 금지한다.

**왜 금지인가**: 각 항목은 과거 실패 사례나 Qwen3 계열의 알려진 불안정성(이모지 출력 떨림, description 오인, MoE 라우팅 중간 드롭, recency bias 부작용 등)에서 도출된 실증적 규칙이다. "이번엔 괜찮을 것 같다"는 예외 허용이 품질 회귀의 주 원인이 되어 왔다.

**어떻게 적용하는가**: 프롬프트 작성·수정 후 `[SELF_CHECK]`(§17)에서 각 항목을 반드시 체크한다. 발견 시 즉시 수정하고, 예외를 두지 않는다.

- 구분선 (`────`, `===`) — markdown 호환 안 되고 토큰 낭비
- 이모지 — Qwen3 출력에서 불안정
- 4단계 이상 들여쓰기 — 파싱 혼란
- 볼드 남용 — 본문 강조는 헤더로, bullet 내부에서 `**text**` 사용 최소화
- API 파라미터 기재 (`enable_thinking`, `temperature`, `top_p` 등) — 프롬프트에 쓰지 않는다 (§2)
- `[TASK]`를 중간에 배치 — 반드시 마지막 (recency bias 활용 불가 + 예시·규칙을 무시한 조기 응답 유발)
- 도구 명세에 번호 목록 — 우선순위 오해 유발, 카테고리 헤더 + 하이픈 사용
- 조건-결과 매핑을 산문으로 기술 — `→` 형식 사용
- 식별자에 대괄호 (`[K1]` → `(k1)`) — 지시 태그 `[TAG]`와 혼동
- 입력 데이터에 JSON 사용 — JSON은 출력 전용
- Few-shot에 negative 예시 포함 — `[HALLUCINATION_GUARD]`로 분리
- thinking 모드 관련 지시를 프롬프트 본문에 기재 — 인프라 계층 책임
- 필드 옆 인라인 description — vLLM #23404 (Qwen3 description 값 오인 버그) 트리거

---

## 16. 언어 정책

- **태그**: 영어 대문자 (`[ROLE]`, `[OUTPUT_CONTRACT]`)
- **필드명**: 영어 스네이크 케이스 (`failure_reasons`, `reasoning_summary`)
- **본문 설명**: 한국어 (사용자 언어)
- **예시 JSON의 string 값**: 한국어 허용
- **테이블/컬럼 식별자**: 프로젝트 실제 스키마 그대로 (`ADWOWN.TB_ADW_LNB301M`, `LN_DT`)
- **코드 레이어 변수명**: 영어

---

## 17. [SELF_CHECK] 출력 규칙 (프롬프트 작성·수정 시)

프롬프트 파일을 생성 또는 수정한 후, 스킬 에이전트(claude/Codex)는 반드시 아래를 검증한다. 프롬프트 본문에 포함되는 것이 아니라 **작성 작업 완료 리포트**로 출력한다.

```
[SELF_CHECK]
- 플레이스홀더명이 치환 코드(.replace())의 키와 일치: (일치/불일치 — 불일치 시 항목 나열)
- [OUTPUT_CONTRACT]가 다음 모듈의 플레이스홀더와 정합: (정합/불일치 — 해당 없으면 N/A)
- reasoning_summary 필드 적용 (thinking ON 노드에 한함): (적용/미적용/해당없음)
- 규칙 카테고리당 5 이하, 총 20 이하: (준수/초과 — 초과 시 카테고리 나열)
- 금지 패턴 없음: (완료/미완료 — 위반 나열)
- 기존 프롬프트와 스타일 일관성: (일관/차이 — 차이 시 사유)
- thinking/샘플링 파라미터 기재 없음: (확인/발견 — 발견 시 위치)
```

---

## 18. 현재 프롬프트 파일 마이그레이션 계획

### 18.1 본 전략 적용 대상 파일 목록

| 파일 | 유형 | thinking | few-shot | reasoning_summary | 주요 변경 |
|---|---|---|---|---|---|
| `interpret/intent_classifier_system.txt` | 단순 | OFF | 11 유지 | 불필요 | 금지 패턴 점검만 |
| `interpret/query_normalizer_phase1_system.txt` | 분석판정 | OFF | 5로 보강 | 불필요 | v3를 기준으로 통합, 구분선 제거 |
| `interpret/query_normalizer_phase2_system.txt` | 분석판정 | OFF | 3 | 불필요 | [TAG] 체계 적용, R1~R12 그룹화 |
| `reason/sql_generator_system.txt` | 계획수립 | **ON** | 6 유지 | **추가** | v2 기준, reasoning_summary 필드 신설, [FORMAT_LOCK] 강화 |
| `reason/sql_validator_system.txt` | 분석판정 | OFF | 3 | 불필요 | 마크다운 코드블록 제거, reasoning_summary 교차 검증 규칙 추가 |
| `reason/context_interpreter_system.txt` | 분석판정 | **ON** | 3 유지 | **추가** | long context 배치 순서 재정렬, [RULES] 최상단/[TASK] 말미 |
| `reason/recovery_agent_system.txt` | 계획수립 | **ON** | 5 | **추가** | 도구 명세 번호 제거, reasoning_summary 필드 신설 |
| `present/analyzer_system.txt` | 분석판정 | **ON** | 3 | **추가** | thinking ON 전환, reasoning_summary 필드 신설, `[TASK]` 말미 재강조 |
| `present/analyzer_viz_judgment_system.txt` | 단순 | OFF | 2~3 | 불필요 | 번호 목록을 카테고리 + 하이픈으로 전환 |
| `present/analyzer_viz_svg_system.txt` | 단순 | OFF | 2 | 불필요 | 금지 패턴 점검 |

### 18.2 v2, v3 버전 파일 처리

현재 실험 중인 `_v2`, `_v3` 파일(`query_normalizer_phase1_system_v2.txt`, `_v3.txt`, `sql_generator_system_v2.txt`):

- `query_normalizer_phase1`: **v3를 기준으로 통합**. v1, v2는 본 전략 승인 후 삭제
- `sql_generator`: **v2를 기준**. v1은 본 전략 승인 후 삭제. v2에 reasoning_summary 추가

### 18.3 마이그레이션 순서

1. 본 전략 문서 승인 (사용자 검토)
2. `SKILL.md`, `prompt-rules.md`를 본 전략 내용으로 대체
3. 각 프롬프트 파일별로 점검·수정 PR 단위 분할:
   - PR1: `intent_classifier`, `analyzer_viz_*` (위험도 낮은 것부터)
   - PR2: `query_normalizer_phase1/phase2` (v3 통합 포함)
   - PR3: `sql_generator` (reasoning_summary 신설, v2 통합)
   - PR4: `sql_validator` (reasoning_summary 교차 검증 규칙 추가)
   - PR5: `context_interpreter`, `recovery_agent` (reasoning_summary 추가 + long context 배치)
   - PR6: `analyzer`
4. 각 PR마다 골든셋 회귀 테스트 실행
5. 전체 완료 후 `_v2`, `_v3` 실험 파일 삭제

### 18.4 코드 레이어 변경 동반 사항

프롬프트 수정과 함께 아래 코드 변경이 필요하다:

- `src/agents/nodes/reason/sql_generator.py`: 응답 파싱에 `reasoning_summary` 필드 수신, 미포함 시 경고 로그
- `src/agents/nodes/reason/sql_validator.py`: `reasoning_summary` vs `sql` 정합성 교차 검증 로직 추가
- `src/agents/nodes/reason/context_interpreter.py`: `reasoning_summary` 필드 수신, State 필드 확장
- `src/agents/nodes/reason/recovery_agent.py`: `reasoning_summary` 필드 수신
- `src/agents/nodes/present/analyzer.py`: thinking ON 모드 인자 전달, `reasoning_summary` 필드 수신, 코드 레이어에서 `reasoning_summary` vs `insight` 텍스트 매칭 검증 추가
- `src/utils/llm/` 또는 해당 노드: JSON fence/trailing 제거 재시도 로직 (§5.3 계층 3)
- 노드 호출 시 `mode="on" | "off"` 인자 전달 (이미 `_resolve_thinking_params` 지원). thinking ON 노드: sql_generator, context_interpreter, recovery_agent, analyzer

---

## 19. 기각된 대안 (향후 혼선 방지)

### 19.1 "sql_generator thinking OFF"

두 전문가 모두 OFF를 권고했으나 프로젝트 결정은 ON. 이유: 은행 도메인의 복잡 SQL(다중 JOIN, 파생 측정값, 산출식 추론)에서 열린 추론 공간이 필요하다는 사용자 판단. think-answer mismatch 위험은 `reasoning_summary` 필드 + sql_validator 교차 검증 + 코드 레이어 파싱 재시도의 4중 방어로 완화한다.

### 19.2 "전 노드 thinking ON 통일"

매력적(추론 품질 일관). 기각 이유:
- intent_classifier·sql_validator 같은 결정성 노드에서 latency 폭증
- JSON 안정성 저하 (vLLM guided_json 충돌 가능성)
- think-answer mismatch 위험이 노드마다 재현됨

### 19.3 "전체 DB 스키마를 context에 주입"

262K context를 활용해 전체 스키마 주입 시도 가능. 기각 이유:
- 20+ 테이블 제공 시 테이블 선택 오류 증가 (커뮤니티 관측)
- lost-in-the-middle로 중간 테이블 무시
- `context_retriever`로 상위 N개만 전달하는 현재 구조가 우월

### 19.4 "Few-shot 10개 이상 포함"

정확도 극대화 관점에서 매력적. 기각 이유:
- 7+ 에서 template trap 확률 급증 (R-gen)
- recency bias로 마지막 예시만 따르는 경향 심화
- **예외**: intent_classifier는 분류 태스크 특성상 11개 허용

### 19.5 "Negative Few-shot 예시로 환각 억제"

"이렇게 틀린 출력을 하지 마라" 패턴. 기각 이유:
- Qwen3가 negative 예시 토큰 패턴을 오히려 모방
- 대신 `[HALLUCINATION_GUARD]` 블록에서 위반/올바른 대응 쌍으로 제시 (§8)

### 19.6 "프롬프트 본문에 thinking 지시 기재"

`[RULES]`에 "thinking 모드로 답변하라" 같은 문구 추가. 기각 이유:
- thinking 모드는 `chat_template_kwargs`에서 제어, 프롬프트 본문으로는 제어 불가
- 프롬프트 본문의 `<think>`, `/no_think` 텍스트는 chat template이 자동 처리
- 인프라 계층(§2) 책임 원칙 위배

---

## 20. 합의되지 않은 열린 이슈 (추후 결정)

### 20.1 query_normalizer phase2 thinking 정책

전문가 A(ON) vs B(OFF) 대립. 현재 결정: **OFF** (결정성 우선). 단, 골든셋 평가에서 R2/R4/R12 추론 규칙 정확도가 낮게 나오면 ON 재검토.

### 20.2 sql_generator temperature

본 전략은 thinking ON 시 temp=0.6 (3.5 모델카드 권장). 그러나 SQL 생성은 결정성이 높을수록 좋을 수 있음. 골든셋 평가에서 temp=0.6 vs 0.3 비교 필요.

### 20.3 골든셋 구축 후 Few-shot 동적 retrieval

현재는 정적 few-shot. 골든셋이 충분히 축적되면 검색 기반 동적 few-shot(진입 쿼리와 가장 유사한 상위 N개 삽입)으로 전환. 이때 retrieval된 예시는 맨 마지막 슬롯 고정(recency bias 활용).

### 20.4 vLLM 폐쇄망 버전 확인

폐쇄망 vLLM 버전이 0.9.1 이상인지 확인 필요. 확인되지 않은 상태에서는 `guided_json` 사용을 전제로 한 프롬프트 설계 금지.

### 20.5 reasoning_summary 필드 검증 엄격도

코드 레이어 교차 검증(§5.2)에서 "reasoning_summary 언급 테이블이 sql에 없으면 무조건 fail" vs "경고만 로그" 중 어느 정책을 쓸지는 운영 데이터 수집 후 결정.

---

## 21. 근거 레퍼런스

### Qwen 공식 자료

- [Qwen3.5-397B-A17B Model Card (HuggingFace)](https://huggingface.co/Qwen/Qwen3.5-397B-A17B)
- [Qwen3 Technical Report — arXiv:2505.09388](https://arxiv.org/abs/2505.09388)
- [Qwen3 Blog: Think Deeper, Act Faster](https://qwenlm.github.io/blog/qwen3/)
- [The 4 Things Qwen-3's Chat Template Teaches Us — HuggingFace Blog](https://huggingface.co/blog/qwen-3-chat-template-deep-dive)
- [Qwen Key Concepts Documentation](https://qwen.readthedocs.io/en/latest/getting_started/concepts.html)
- [Qwen Structured Output — Alibaba Cloud Model Studio](https://www.alibabacloud.com/help/en/model-studio/qwen-structured-output)

### 서빙 이슈

- [vLLM Issue #18819 — Broken Structured Output with Qwen3 when enable_thinking=False](https://github.com/vllm-project/vllm/issues/18819)
- [vLLM Issue #23404 — Qwen3 Structured Output Field Description Bug](https://github.com/vllm-project/vllm/issues/23404)
- [SGLang Issue #6675 — Broken Structured Outputs with enable_thinking=False in Qwen3](https://github.com/sgl-project/sglang/issues/6675)

### 연구 논문

- Liu et al., "Lost in the Middle: How Language Models Use Long Contexts," TACL 2024 — [arXiv:2307.03172](https://arxiv.org/abs/2307.03172)
- "Are Reasoning Models More Prone to Hallucination?" — [arXiv:2505.23646](https://arxiv.org/abs/2505.23646)
- "Hallucination Detection for LLM-based Text-to-SQL" — [arXiv:2512.22250](https://arxiv.org/pdf/2512.22250)
- "QwenLong-CPRS: Context Compression Framework" — [arXiv:2505.18092](https://arxiv.org/html/2505.18092v1)

### 커뮤니티

- [How to Fine-Tune Qwen3 on Text2SQL — Oxen.ai](https://ghost.oxen.ai/how-to-fine-tune-qwen3-to-gpt-4o-level-performance/)

---

## 22. 변경 이력

| 날짜 | 버전 | 변경 내용 |
|---|---|---|
| 2026-04-10 | 0.1 (초안) | 초안 작성. 두 전문가 자문 합의 반영. 사용자 검토 중 |
| 2026-04-10 | 0.2 | 5대 보강: (1) thinking ON 노드에 `analyzer` 추가(총 4개), (2) 핵심 지침 3요소(what/why/how) 포맷으로 확장, (3) §4.10 블록 배치 순서 원칙 + 노드별 권장 순서표 신설, (4) §7.7 Few-shot 커버리지 매트릭스 방법론 신설, (5) §8.4 가드레일 적용 범위 원칙(IN/OUT) 신설 |

---

## 23. 적용 체크리스트 (승인 후)

- [ ] 본 문서 승인
- [ ] `.claude/skills/prompt-engineer/SKILL.md` 내용을 §3~§17로 대체
- [ ] `docs/agent-guides/prompt-rules.md` 내용을 에이전트 모듈 전용 섹션으로 대체
- [ ] `src/utils/llm/client.py` thinking mode 노드별 호출 지점 확인
- [ ] 각 노드 코드에 `mode="on"|"off"` 인자 전달 확인 (sql_generator, context_interpreter, recovery_agent: on)
- [ ] JSON fence/trailing 제거 재시도 로직 구현 (§5.3 계층 3)
- [ ] 프롬프트 파일 PR1~PR6 순차 적용 (§18.3)
- [ ] 각 PR마다 골든셋 회귀 테스트
- [ ] `_v2`, `_v3` 실험 파일 삭제
- [ ] 폐쇄망 vLLM 버전 확인 및 `guided_json` 사용 정책 확정
