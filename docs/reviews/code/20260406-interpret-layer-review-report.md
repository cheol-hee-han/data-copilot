# Interpret 계층 코드 리뷰 리포트

- 리뷰 일자: 2026-04-06
- 리뷰 대상: Interpret 계층 (의도 분류, 쿼리 정규화, 명확화 처리) 전체 13개 파일
- 리뷰어: Code Reviewer Agent

---

## 목차

1. [Critical 이슈](#1-critical-이슈)
2. [Warning 이슈](#2-warning-이슈)
3. [Info 이슈](#3-info-이슈)
4. [프롬프트 관련 이슈](#4-프롬프트-관련-이슈)
5. [요약 및 권장 조치](#5-요약-및-권장-조치)

---

## 0. 재검증 결과 (2026-04-06 2차 검토)

아래 이슈들은 실제 코드와 대조 검증한 결과, 오탐 또는 심각도 조정이 필요한 것으로 확인되었습니다.

| 원래 ID | 판정 | 사유 |
|---------|------|------|
| **C-01** | ❌ **제외** | in-place mutation은 프로젝트 전체 노드의 표준 패턴 (reason/interpret/present 모든 노드 동일). 이 객체만 `model_copy` 패턴으로 변경하면 일관성 파괴. `frozen=True` 전환 계획 없음 |
| **C-03** | **Critical → Info** | 호출부 `_rewrite_for_analysis`에 `if extraction:` 가드(intent_classifier.py 노드)가 존재하여 빈 문자열이 `preprocessed_input`에 설정되는 경로 없음. API 계약 불명확만 남음 |

---

## 1. Critical 이슈

### C-01. clarification_handler_node에서 AmbiguitySignal을 in-place mutation하는 패턴 (frozen 모델 전환 시 즉시 장애)

- 파일: `src/agents/nodes/interpret/clarification_handler.py` (L136-149, L182-189)
- 등급: Critical
- 현상: `signals = state.pending_signals`로 참조를 받은 뒤 `s.turn_id = ...`, `s.decision = "ASK"`, `s.override_reason = ...`, `best.answer = ...`, `best.resolved_at = ...` 등으로 직접 변경한다. 코드 자체도 주석(L135)에서 "AmbiguitySignal에 frozen=True 설정 시 이 코드가 깨지므로 주의"라고 경고하고 있다.
- 문제점:
  - Pydantic v2에서 `model_config = {"frozen": True}` 전환 시 즉시 런타임 에러 발생
  - LangGraph State의 불변성 원칙 위반 -- 노드는 새 dict를 반환해야 하며 입력 state를 직접 변경하면 안 됨
  - 동일 시그널 객체가 여러 경로에서 참조되면 의도하지 않은 부작용 발생 가능
- 개선안:
  ```python
  # 가드레일 적용 시 새 인스턴스 생성
  corrected = s.model_copy(update={"decision": "ASK", "override_reason": override})
  # resolve 시에도 동일
  resolved = best.model_copy(update={"answer": validated, "resolved_at": datetime.now()})
  ```
  `model_copy(update=...)` 패턴으로 전환하면 frozen 전환과 무관하게 안전하다.

### C-02. input_sanitizer의 SQL 인젝션 패턴이 자연어 오탐 위험 (은행 업무 용어 차단)

- 파일: `src/services/input_sanitizer.py` (L39-50)
- 등급: Critical
- 현상: `--` 패턴(SQL 단행 주석)이 자연어에서 흔히 사용되는 문맥을 구분하지 못한다.
  - 예: "2024-03--2024-06 기간의 대출 현황" 또는 "고객수 -- 신규 기준"
  - `r"/\*"` 패턴도 "금리 3.5/4.0 *연 기준*" 같은 입력에서 오탐 가능
- 문제점: 정상적인 은행 업무 질의가 차단되어 사용자 경험 저하. 금융 도메인 사용자는 IT 지식이 없어 왜 차단되는지 이해 불가.
- 개선안:
  ```python
  # --는 앞뒤 공백 없이 연속되는 경우만 감지 (날짜 범위 오탐 방지)
  (r"(?<!\w)--(?!\d)", "SQL 단행 주석 패턴"),
  # /* 블록 주석은 */ 닫기가 있을 때만 (수식 오탐 방지)
  (r"/\*.*?\*/", "SQL 블록 주석 패턴"),
  ```
  또는 자연어 입력 전용 sanitizer에서는 SQL 패턴을 `security.py`의 `FORBIDDEN_SQL_PATTERNS`와 분리하고, SQL 키워드가 SQL 문맥(세미콜론 후, 괄호 내 등)에서만 탐지되도록 맥락 인지 패턴을 적용해야 한다.

### C-03. rewrite_analysis_query에서 타임아웃/재시도 없이 LLM 직접 호출

- 파일: `src/services/intent_classifier.py` (L304-311)
- 등급: Critical
- 현상: `rewrite_analysis_query`는 `llm_call_with_parse_retry`를 사용하지 않고 `client.messages.create`를 직접 호출한다. 타임아웃은 `settings.llm_default_timeout`으로 설정되어 있지만, 네트워크 불안정 시 재시도가 없고, 응답이 빈 경우에 대한 처리도 빈 문자열 반환뿐이다.
- 문제점:
  - 폐쇄망 환경에서 로컬 LLM의 응답 지연/실패가 잦을 수 있음
  - 빈 응답이 그대로 `preprocessed_input`에 설정되면 후속 정규화 노드에 빈 문자열이 전달됨
  - `llm_call_with_parse_retry`에는 LLM 호출 로깅, 노드 컨텍스트 관리가 포함되어 있으나 직접 호출 시 이 관측성(observability)이 누락됨
- 개선안:
  - 빈 응답 시 원본 질의를 반환하는 방어 로직 추가
  - 최소한 1회 재시도 로직 추가 또는 `llm_call_with_parse_retry`의 plain text 모드 지원 검토
  ```python
  if not result:
      logger.warning("재작성 결과가 비어있음, 원본 유지", original=query)
      return query  # 빈 문자열 대신 원본 반환
  ```

---

## 2. Warning 이슈

### W-01. intent_classifier 노드에서 함수 내부 import 패턴 (지연 임포트 남용)

- 파일: `src/agents/nodes/interpret/intent_classifier.py` (L149, L253-256)
- 등급: Warning
- 현상: `from src.config import settings`와 `from src.utils.tracker.dispatch import ...`가 함수 본문 내부에서 import된다.
- 문제점:
  - 순환 참조 회피가 목적이라면 아키텍처 문제 -- config와 tracker는 순환 참조 대상이 아님
  - 매 호출마다 import 오버헤드 발생 (미미하지만 패턴으로서 바람직하지 않음)
  - 동일 프로젝트 내 다른 노드 파일에서는 모듈 레벨 import를 사용하므로 일관성 부재
- 개선안: 모듈 상단으로 이동. `settings`는 이미 `services/intent_classifier.py`에서 모듈 레벨로 import하고 있다.

### W-02. normalize_query_node에서도 동일한 함수 내부 import

- 파일: `src/agents/nodes/interpret/query_normalizer.py` (L48, L108-110)
- 등급: Warning
- 현상: `build_clarification_context`와 `dispatch_tracking_event`가 함수 내부에서 import된다.
- 개선안: W-01과 동일하게 모듈 상단으로 이동.

### W-03. IntentClassifyResult가 dataclass인데 Pydantic BaseModel이 아님

- 파일: `src/services/intent_classifier.py` (L94-118)
- 등급: Warning
- 현상: `IntentClassifyResult`는 `@dataclass`로 정의되어 있다. 프로젝트 전체적으로 데이터 모델은 Pydantic v2 BaseModel을 사용하는 패턴인데, 이 클래스만 dataclass를 사용한다.
- 문제점:
  - `ambiguities: list[dict] | None = None`에 대한 런타임 타입 검증이 없음
  - `__post_init__`으로 None 처리하는 패턴이 Pydantic의 `Field(default_factory=list)`로 더 안전하게 표현 가능
  - `is_error: bool = False`가 `__post_init__` 아래에 선언되어 dataclass 필드 순서 규칙에 의존적
  - 다른 모델(`AmbiguitySignal`, `NormalizedQuery`)은 모두 Pydantic이므로 일관성 부재
- 개선안: Pydantic BaseModel로 전환 검토. 단, 이 클래스가 내부 전달용이고 직렬화 필요가 없다면 현행 유지도 수용 가능하나, `ambiguities` 필드의 `list[dict]` 타입은 `list[dict[str, Any]]`로 명시하는 것이 바람직하다.

### W-04. _CATEGORY_INTENT_MAP에 하위 호환 항목과 AMBIGUOUS 매핑이 동일 IntentType으로 수렴

- 파일: `src/services/intent_classifier.py` (L45-53)
- 등급: Warning
- 현상: `DATA_QUERY`, `CLARIFICATION`, `AMBIGUOUS` 3가지가 모두 기존 IntentType에 매핑되며, `DATA_QUERY -> DATA_EXTRACTION`은 하위 호환 주석이 있지만, 나머지 2개는 주석이 없다.
- 문제점: `CLARIFICATION`과 `AMBIGUOUS`가 모두 `CLARIFICATION_NEEDED`로 매핑되는데, LLM은 프롬프트에서 `AMBIGUOUS`만 출력하도록 안내받고 있어 `CLARIFICATION` 매핑은 사실상 죽은 코드일 수 있다.
- 개선안: `CLARIFICATION` 매핑이 실제로 사용되는 경로가 있는지 확인. 없다면 제거하고 주석으로 이유를 남기거나, `_parse_response`의 L242에서 이미 `CLARIFICATION -> AMBIGUOUS`로 변환하고 있으므로 여기서의 `CLARIFICATION` 항목은 제거 가능.

### W-05. confidence_scorer의 _is_unresolvable_conflict가 문자열 파싱에 의존

- 파일: `src/services/confidence_scorer.py` (L192-211)
- 등급: Warning
- 현상: `evidence` 문자열에서 `word.startswith("TB_")`로 테이블 참조를 추출한다.
- 문제점:
  - evidence 형식이 "TB_로 시작하는 단어가 공백으로 구분된 텍스트"라는 암묵적 계약에 의존
  - 테이블명이 "TB_"로 시작하지 않는 경우(폐쇄망 환경에서 다른 네이밍 규칙) 탐지 실패
  - evidence가 구조화된 필드(예: `table_refs: list[str]`)라면 파싱이 불필요
- 개선안: `KnowledgeItem`에 `table_refs: list[str]` 같은 구조화된 필드를 추가하는 것이 중장기적으로 바람직. 단기적으로는 정규식 패턴(`r"\b\w+\.\w+"` 등)으로 테이블명 추출 로직을 강화.

### W-06. query_normalizer 서비스에서 동의어 사전과 약어 확장이 비활성화되었으나 코드가 잔존

- 파일: `src/services/query_normalizer.py` (L69-78, L86-103, L600-603)
- 등급: Warning
- 현상: `ALL_SYNONYMS`, `ABBREVIATION_MAP`이 모듈 레벨에서 로드되고, `serialize_synonym_dict`도 import되지만, 실제로는 주석 처리되어 사용되지 않는다. 불필요한 YAML 파일 로드와 import가 발생한다.
- 문제점:
  - 모듈 로드 시 YAML 파일을 읽는 I/O 발생 (성능)
  - 죽은 코드가 유지보수 혼란 유발
  - `_preprocess_for_normalization` 함수는 `re.sub(r"[~~~]+", "~", text)` 1줄만 수행하여 거의 no-op
- 개선안:
  - 2026-04-03 NOTE가 확정 결정이라면 관련 코드를 정리하고 `serialize_synonym_dict` import 제거
  - YAML 로드를 lazy loading으로 전환하거나, 사용하지 않을 경우 완전 제거
  - `_preprocess_for_normalization`이 틸데 정규화만 수행한다면 인라인화 또는 제거 검토

### W-07. NormalizedQuery.ambiguities가 list[dict] 타입으로 비구조화 상태

- 파일: `src/agents/models/normalization.py` (L374)
- 등급: Warning
- 현상: `ambiguities: list[dict] = Field(default_factory=list)` -- LLM 출력의 임의 dict를 그대로 저장한다.
- 문제점:
  - 소비자 코드(`query_normalizer.py` L153-164)에서 `amb.get("ambiguity_type", "CONTEXT")` 같은 키 접근 시 키 누락 위험
  - 타입 안전성이 없어 LLM이 예상과 다른 키를 출력하면 조용히 무시됨
  - `AmbiguitySignal` 모델이 이미 존재하지만 여기서는 사용하지 않음
- 개선안: `ambiguities: list[AmbiguitySignal] = Field(default_factory=list)` 또는 별도의 `NormalizationAmbiguity` Pydantic 모델을 정의하여 LLM 출력을 검증. 다만 AmbiguitySignal과 정규화 시점의 ambiguity 스키마가 완전히 동일하지 않으므로 별도 모델이 적합할 수 있다.

### W-08. EntitySlot.confidence 필드가 검증기에서 제거되지만 모델에 남아있음

- 파일: `src/agents/models/normalization.py` (L265), `src/services/query_normalizer.py` (L194)
- 등급: Warning
- 현상: `EntitySlot`에 `confidence: str = "MEDIUM"` 필드가 정의되어 있지만, `_validate_entities`에서 `e.pop("confidence", None)`으로 제거한다. Pydantic 모델과 검증 로직이 불일치.
- 개선안: `EntitySlot`에서 `confidence` 필드를 제거하거나, 제거하지 않을 것이라면 검증기에서 pop하지 않기.

### W-09. system_prompts.py의 모듈 레벨 파일 로드 -- 테스트 환경에서 resources 경로 문제

- 파일: `src/agents/nodes/system_prompts.py` (L83-131 전체)
- 등급: Warning
- 현상: 모듈 import 시 모든 프롬프트 파일이 즉시 로드된다. `load_text_required`는 파일이 없으면 예외를 발생시킨다.
- 문제점:
  - 단위 테스트에서 이 모듈을 import하는 코드를 테스트하려면 모든 프롬프트 파일이 존재해야 함
  - 프롬프트 파일 1개가 누락되면 전체 애플리케이션 시작 실패
  - 프롬프트 20개 이상을 한꺼번에 파일 I/O하므로 콜드 스타트 시간 증가
- 개선안: lazy loading 패턴 적용. 예:
  ```python
  @functools.cache
  def get_intent_classifier_system() -> str:
      return _interpret("intent_classifier_system.txt")
  ```
  또는 현행 eager loading을 유지하되 테스트용 fixture를 제공.

### W-10. thinking_modes.py에 query_normalizer가 reason 계층 주석 아래 위치

- 파일: `src/agents/nodes/thinking_modes.py` (L29)
- 등급: Warning
- 현상: `"query_normalizer": "auto"`가 `# -- Reason 계층 (추론 필요) --` 주석 아래에 위치하지만, query_normalizer는 Interpret 계층 노드다.
- 개선안: Interpret 계층 주석 블록으로 이동:
  ```python
  # -- Interpret 계층 --
  "intent_classifier": "off",
  "query_normalizer": "auto",     # <-- 이동
  "clarification_handler": "off",
  ```

---

## 3. Info 이슈

### I-01. _format_history의 max_turns 매직 넘버

- 파일: `src/services/intent_classifier.py` (L73)
- 등급: Info
- 현상: `max_turns: int = 4`가 하드코딩되어 있다.
- 개선안: `settings.intent_history_max_turns` 등으로 설정화. 대화가 길어질 경우 4턴이 부족할 수 있다.

### I-02. validate_answer에서 옵션 매칭이 완전 일치만 지원

- 파일: `src/agents/nodes/interpret/clarification_handler.py` (L101-103)
- 등급: Info
- 현상: `answer == str(i) or answer == opt`로만 매칭한다.
- 문제점: 사용자가 "1번", "1)", "대출 잔액" (옵션: "대출 잔액 조회") 등으로 응답하면 매칭 실패.
- 개선안: 부분 일치 또는 정규화된 매칭 로직 추가:
  ```python
  normalized = answer.rstrip("번.)").strip()
  if normalized == str(i) or normalized == opt or opt.startswith(normalized):
      return opt
  ```

### I-03. MeasureSlot, DimensionSlot 등에 confidence 필드가 남아있으나 VALID_CONFIDENCE 검증 없음

- 파일: `src/agents/models/normalization.py` (L275, L287 등)
- 등급: Info
- 현상: `confidence: str = "MEDIUM"` 필드가 여러 슬롯에 존재하지만, `_validate_structure`에서 이 필드를 검증하지 않는다.
- 개선안: 사용하지 않는다면 슬롯에서 제거. 사용한다면 `_validate_enum`으로 검증 추가.

### I-04. _PRIORITY 맵에 모든 AmbiguityType이 등록되지 않으면 기본값 99

- 파일: `src/agents/nodes/interpret/clarification_handler.py` (L74-82)
- 등급: Info
- 현상: `_PRIORITY`에 등록되지 않은 `AmbiguityType`이 추가되면 우선순위 99로 폴백된다.
- 개선안: `AmbiguityType` Enum 변경 시 `_PRIORITY`도 함께 업데이트하도록 단위 테스트 추가:
  ```python
  def test_all_ambiguity_types_have_priority():
      for t in AmbiguityType:
          assert t in _PRIORITY, f"{t} missing from _PRIORITY"
  ```

### I-05. query_normalizer_phase1_system.txt에 잔존하는 특수문자 포맷

- 파일: `resources/prompts/interpret/query_normalizer_phase1_system.txt` (L485-488, L270-271)
- 등급: Info
- 현상: 프롬프트 포맷 통일 작업(docs/todo/20260402-prompt-format-unification.md)에서 변환 대상인 특수문자가 일부 잔존:
  - L485: `(U+2501) 구분선` -- 프롬프트 포맷 통일 대상
  - L270-271: `(black square U+25A0)` -- `### `로 전환 대상
- 개선안: 프롬프트 포맷 통일 작업의 잔여분으로 처리.

### I-06. intent_classifier_system.txt의 CONTINUE few-shot 예제에 extraction_focus 미반영

- 파일: `resources/prompts/interpret/intent_classifier_system.txt`
- 등급: Info
- 현상: DATA_ANALYSIS 관련 few-shot 예제(L157-218)에서 `continuity.context`가 시각화 지시어를 포함한 채로 작성되어 있다 (예: "최근 6개월 수신잔액 추이를 차트로 보여줘"). preprocessed_input 재설계에서 extraction_focus 방향으로 전환 중이므로, few-shot도 정렬 필요.
- 개선안: 재설계 완료 후 few-shot 예제의 context 필드에서 시각화 지시어를 제거하고 추출 중심 표현으로 교체.

### I-07. clarify_unified_node 하위 호환 별칭이 잔존

- 파일: `src/agents/nodes/interpret/clarification_handler.py` (L198)
- 등급: Info
- 현상: `clarify_unified_node = clarification_handler_node` -- 이전 이름의 별칭.
- 개선안: 호출 지점을 검색하여 모두 `clarification_handler_node`로 전환 후 별칭 제거.

### I-08. _parse_response에서 VALID_QUERY_CATEGORIES를 함수 내부에서 import

- 파일: `src/services/intent_classifier.py` (L238-239)
- 등급: Info
- 현상: `from src.agents.models.normalization import VALID_QUERY_CATEGORIES` -- 파싱 함수 내부의 지연 import.
- 개선안: 모듈 상단으로 이동. normalization 모델은 순환 참조 대상이 아님.

### I-09. OutputHintSlot에 confidence 필드가 남아있으나 _validate_output_hint에서 처리 안 함

- 파일: `src/agents/models/normalization.py` (L337)
- 등급: Info
- 현상: `confidence: str = "MEDIUM"` -- output_hint 검증기(L295-313)에서 이 필드를 검증하지 않는다.
- 개선안: W-08과 동일 맥락. 사용하지 않는다면 제거.

### I-10. query_normalizer 서비스의 Phase 1/2 User 템플릿이 이중으로 존재

- 파일: `src/services/query_normalizer.py` (L534-564), `resources/prompts/interpret/query_normalizer_phase1_user.txt`, `resources/prompts/interpret/query_normalizer_phase2_user.txt`
- 등급: Info
- 현상: 서비스 코드 내에 `_PHASE1_USER_TEMPLATE`/`_PHASE2_USER_TEMPLATE` 인라인 템플릿이 있고, 동시에 resources 파일에도 동일한 역할의 템플릿이 있다. `system_prompts.py`에서 resources 파일을 로드하여 노드가 서비스에 주입하는 구조인데, 서비스 내부의 인라인 템플릿이 `phase1_user_template or _PHASE1_USER_TEMPLATE` 폴백으로 사용된다.
- 문제점: 두 곳의 템플릿 내용이 달라질 위험 (resources 파일 vs 인라인 코드). resources 파일이 Phase 1의 `{synonym_dict}` 플레이스홀더를 포함하지 않아 이미 분기되어 있다.
- 개선안: resources 파일을 단일 진실 공급원으로 확정하고, 인라인 폴백 템플릿을 제거하거나 폴백용 resources 파일과 동기화 메커니즘을 마련.

---

## 4. 프롬프트 관련 이슈

### P-01. intent_classifier_system.txt -- Few-shot 과다 (토큰 비용)

- 파일: `resources/prompts/interpret/intent_classifier_system.txt` (512행, 약 16개 예제)
- 등급: Warning
- 현상: Few-shot 예제가 16개로 시스템 프롬프트가 매우 길다. 매 호출마다 전체 전송.
- 문제점: 폐쇄망 소형 모델(Solar Pro 2 70B)은 컨텍스트 윈도우가 제한적. 대화 이력까지 포함하면 입력 토큰이 과도.
- 개선안:
  - 핵심 예제 6-8개로 압축 (CONTINUE+DATA_EXTRACTION 2개, CONTINUE+DATA_ANALYSIS 1개, NEW 3개, UNSURE 1개, AMBIGUOUS 1개)
  - 나머지는 golden set 테스트에 활용
  - 또는 동적으로 관련 예제만 선택하는 dynamic few-shot 패턴 검토

### P-02. query_normalizer_phase1_system.txt -- 출력 스키마가 너무 크고 프롬프트 내 중복

- 파일: `resources/prompts/interpret/query_normalizer_phase1_system.txt` (494행)
- 등급: Warning
- 현상: 8개 슬롯의 허용값, 규칙, JSON 스키마, 4개 Few-shot 예제가 모두 포함되어 매우 길다.
- 문제점: P-01과 동일 -- 소형 모델에서 컨텍스트 압박. 슬롯 허용값이 시스템 프롬프트와 Pydantic Enum에 이중 정의.
- 개선안:
  - 슬롯별 허용값을 간결한 표 형식으로 압축
  - Few-shot 예제를 2개로 축소 (단순 EXTRACT 1개 + COMPARE/AGGREGATE 복합 1개)
  - 허용값 목록은 Enum에서 자동 생성하여 프롬프트에 주입하는 방식 검토

### P-03. intent_classifier_query_rewriter.txt -- 17개 Few-shot 과다

- 파일: `resources/prompts/interpret/intent_classifier_query_rewriter.txt` (189행, 17개 예제)
- 등급: Info
- 현상: 시각화 유형별 변환 예시가 17개. 재작성 작업은 비교적 단순한 패턴이므로 예제 수 대비 효과가 체감적으로 감소.
- 개선안: 핵심 패턴 5-6개로 축소 (단순 제거, SQL 구조어 보존, 암시적 보충 각 2개).

---

## 5. 요약 및 권장 조치

### 우선순위별 조치 항목

| 순번 | ID | 등급 | 핵심 내용 | 예상 공수 |
|------|------|------|-----------|-----------|
| 1 | C-01 | Critical | AmbiguitySignal in-place mutation -> model_copy 전환 | 1h |
| 2 | C-02 | Critical | input_sanitizer SQL 패턴 오탐 수정 (자연어 맥락 인지) | 2h |
| 3 | C-03 | Critical | rewrite_analysis_query 빈 응답 방어 + 재시도 | 0.5h |
| 4 | W-06 | Warning | 비활성화된 동의어/약어 코드 정리 | 0.5h |
| 5 | W-07 | Warning | NormalizedQuery.ambiguities 타입 구조화 | 1h |
| 6 | W-08 | Warning | EntitySlot.confidence 불일치 해소 | 0.5h |
| 7 | W-10 | Warning | thinking_modes.py 계층 주석 오류 수정 | 5min |
| 8 | P-01/P-02 | Warning | 프롬프트 Few-shot 축소 (토큰 최적화) | 2h |
| 9 | W-01/W-02 | Warning | 함수 내부 import -> 모듈 레벨 이동 | 0.5h |
| 10 | I-02 | Info | validate_answer 옵션 매칭 완화 | 0.5h |

### 아키텍처 수준 소견

1. **노드-서비스 분리 패턴**: 잘 유지되고 있다. 노드는 상태 관리 + 라우팅, 서비스는 비즈니스 로직에 집중. 다만 `intent_classifier_node`가 직접 `rewrite_analysis_query`를 호출하는 부분은 서비스 계층 호출을 노드에서 오케스트레이션하는 패턴으로 적절하다.

2. **AmbiguitySignal 통합 모델**: 감지-보정-질문-해소의 전체 생명주기를 단일 모델로 관리하는 설계는 합리적이다. 다만 in-place mutation 패턴(C-01)을 반드시 해소해야 한다.

3. **2-Phase 정규화**: Phase 1(슬롯 추출) + Phase 2(교차 검증)의 구조는 견고하나, Phase 2의 프롬프트가 상당히 길어 소형 모델에서 비용 대비 효과가 의문. `settings.normalization_phase2_enabled` 설정으로 비활성화 가능한 것은 좋은 설계.

4. **가드레일 보정**: INFER->ASK 단방향 보정 정책은 금융 도메인에 적합한 안전 방향 설계. 규칙 기반(LLM 호출 0)으로 구현한 것도 적절.

5. **프롬프트 크기 최적화 필요**: Interpret 계층에서만 시스템 프롬프트 3개(intent_classifier, query_rewriter, normalizer_phase1)의 합이 약 1,200행에 달한다. 폐쇄망 소형 모델의 컨텍스트 윈도우를 고려하면 압축이 시급하다.
