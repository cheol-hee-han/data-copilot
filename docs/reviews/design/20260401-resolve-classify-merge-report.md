# 설계 확정안: resolve_history + classify_intent 통합

- **작성일**: 2026-04-01
- **상태**: 검토 완료, 보완사항 반영 확정본
- **대상**: Interpret 계층 두 노드(resolve_history, classify_intent) → `context_classifier` 통합
- **관련 파일**:
  - `src/agents/nodes/interpret/history_resolver.py` (노드)
  - `src/agents/nodes/interpret/intent_classifier.py` (노드)
  - `src/services/history_resolver.py` (서비스)
  - `src/services/intent_resolver.py` (서비스)
  - `src/agents/graph/pipeline.py` (그래프 정의)
  - `resources/prompts/interpret/history_resolver_system.txt`
  - `resources/prompts/interpret/intent_classifier_system.txt`

---

## 1. 통합 배경

현재 `resolve_history`와 `classify_intent`는 책임이 중첩되어 있다.

- `classify_intent`의 CLARIFICATION 카테고리는 `resolve_history`의 실패를 보완하는 안전망
- 비데이터 의도(CASUAL_TALK, META_QUESTION)는 처리 경로 없이 `clarification_handler`로 우회
- 이력이 있는 멀티턴 대화에서 LLM 2회 호출 (맥락 해소 1회 + 의도 분류 1회)

통합으로 달성하는 것:
- **LLM 라운드트립 1회 절감** (멀티턴 대화 레이턴시 개선)
- **책임 경계 명확화** (CLARIFICATION 카테고리 제거)
- **프롬프트 컨텍스트 공유** (맥락 파악한 LLM이 바로 의도도 판단)
- **질의 재작성 제거** — 원본 질의 + 대화 이력 + 명확화 이력을 하류 노드에 그대로 전달.
  연속 여부(`is_continuation`)와 의도만 판정. CONTINUE 시에는 `continue_context`로
  대화 맥락이 반영된 질문 해석을 보조 힌트로 함께 전달.
- **`awaiting_clarification` 레거시 플래그 제거** — 체크포인터 기반 멀티턴 아키텍처에서
  interrupt/resume으로 명확화 흐름이 관리되므로 불필요. `bool(conversation_history)`로 대체.
- **프롬프트 단일화** — Path A/B 분리를 제거하고 하나의 통합 프롬프트 사용.
  카테고리 정의 동기화 부담 해소. 이력 없으면 LLM이 `SKIP` 판정.
- **비데이터 의도 직접 응답** — CASUAL_TALK, META_QUESTION이 `clarification_handler`로
  잘못 라우팅되던 문제 해결. 경량 `simple_responder` 노드에서 즉시 응답 생성.

---

## 2. 노드 구조

```
context_classifier_node
    → 시스템 프롬프트: context_classifier_system.txt (통합 — 연속 여부 + 의도 분류)
    → 유저 프롬프트: context_classifier_user.txt
      (이력 있으면 [이전 대화] 섹션 포함, 없으면 생략)
    → LLM 1회 호출
    → 출력:
    │
    ├─ SKIP — 이력 없음 → { resolution: "SKIP", category, confidence, reason }
    ├─ CONTINUE — 이어지는 대화
    │   → { resolution: "CONTINUE", category, confidence, continue_reason, continue_context }
    │   (원본 질의는 변경하지 않음. continue_context는 하류 노드의 보조 힌트로 활용)
    ├─ NEW — 독립 질의 → { resolution: "NEW", category, confidence, reason }
    ├─ UNSURE — 맥락 모호 → AmbiguitySignal 생성 + 의도 분류 결과도 state에 저장
    │
    └─ 폴백 (LLM 호출 실패 시)
        → resolve_history(LLM) + 규칙 기반 의도 분류(LLM 없음)
```

> **[P1-1 개정]** 이전에는 Path A/B를 별도 프롬프트로 분리했으나,
> 카테고리 정의 동기화 부담이 더 큰 리스크로 판단하여 **단일 프롬프트로 통합**.
> resolution은 항상 출력하며, 이력이 없으면 LLM이 `SKIP`으로 판정.
> 폐쇄망 모델에서 조건부 스키마 문제가 발생하면 그때 분리하는 것으로 결정.

---

## 3. 파일 변경 계획

### 신규 생성

| 파일 | 용도 |
|------|------|
| `src/agents/nodes/interpret/context_classifier.py` | 통합 노드 함수 |
| `src/services/context_classifier.py` | 통합 비즈니스 로직 (파싱, 매핑, 폴백) |
| `resources/prompts/interpret/context_classifier_system.txt` | 통합 시스템 프롬프트 (연속 여부 + 의도 분류) |
| `resources/prompts/interpret/context_classifier_user.txt` | 통합 유저 프롬프트 |
| `src/agents/nodes/present/simple_responder.py` | 비데이터 의도(CASUAL_TALK, META_QUESTION) 경량 응답 노드 |

### 수정

| 파일 | 변경 내용 |
|------|----------|
| `src/agents/graph/pipeline.py` | 노드 등록 변경, 라우팅 함수 통합, `simple_responder` 엣지 추가, `_LEGACY_TARGET_MAP` 추가 |
| `src/agents/state/state.py` | `is_continuation: bool = False`, `continue_context: str = ""` 추가. `awaiting_clarification` 제거 |
| `src/agents/nodes/system_prompts.py` | 통합 프롬프트 변수 추가 (2개: system + user) |
| `src/models/enums.py` | `QueryCategory`에서 `CLARIFICATION` 제거 |
| `src/agents/models/normalization.py` | `VALID_QUERY_CATEGORIES` 자동 반영 |
| `src/services/history_resolver.py` | `needs_history_resolve()`에서 `awaiting_clarification` 파라미터 제거 |

### 삭제 (deprecated → 다음 릴리스에서 제거)

| 파일 | 사유 |
|------|------|
| `src/agents/nodes/interpret/history_resolver.py` | 통합 노드로 대체 |
| `src/agents/nodes/interpret/intent_classifier.py` | 통합 노드로 대체 |
| `resources/prompts/interpret/history_resolver_system.txt` | 통합 프롬프트로 대체 |
| `resources/prompts/interpret/history_resolver_user.txt` | 통합 프롬프트로 대체 |
| `resources/prompts/interpret/intent_classifier_system.txt` | 통합 프롬프트로 대체 |
| `resources/prompts/interpret/intent_classifier_user.txt` | 통합 프롬프트로 대체 |

> `src/services/history_resolver.py`와 `src/services/intent_resolver.py`는
> 폴백 경로 및 기존 테스트(10개 파일)에서 참조하므로 즉시 삭제하지 않고 deprecated 처리.
> `system_prompts.py`의 기존 4개 변수(`HISTORY_RESOLVER_*`, `INTENT_CLASSIFIER_*`)는
> 폴백 함수 내부에서 직접 로드하도록 변경하고 모듈 레벨에서는 제거.

---

## 4. 그래프 변경

**Before:**
```
entry → resolve_history ─→ classify_intent ─→ normalize_query → ...
              │                    │
              └→ clarification_handler   └→ clarification_handler
```

**After:**
```
entry → context_classifier ─→ normalize_query → ...
              │
              ├→ clarification_handler (UNSURE / AMBIGUOUS)
              ├→ simple_responder (CASUAL_TALK / META_QUESTION) → format_response → END
              └→ error_end
```

### 라우팅 함수

```python
def _route_after_context_classifier(
    state: PipelineState,
) -> str:
    """통합 노드 후 라우팅."""
    # 1. 명확화 신호 우선
    if state.pending_signals:
        return "clarification_handler"

    # 2. 에러
    if state.status == QueryStatus.ERROR:
        return "error_end"

    # 3. 비데이터 의도 → 경량 응답 노드에서 직접 처리
    if state.intent in (
        IntentType.CASUAL_TALK,
        IntentType.META_QUESTION,
    ):
        return "simple_responder"

    # 4. 데이터 의도 → 정규화 또는 planner
    return _next_after_intent()
```

### clarification_handler 복귀 대상 (F4 반영)

```python
_VALID_RETURN_TARGETS = frozenset({
    "context_classifier",
    "normalize_query",
    "sql_generator",
    "readiness_gate",
    "result_finalizer",
})

# 배포 과도기 호환 — 기존 세션의 source_node가 구 이름일 수 있음
_LEGACY_TARGET_MAP = {
    "resolve_history": "context_classifier",
    "classify_intent": "context_classifier",
    "resolve_and_classify": "context_classifier",
}


def _route_after_clarify(
    state: PipelineState,
) -> str:
    """clarification_handler 후 라우팅 — source_node로 복귀."""
    if state.resolved_signals:
        target = state.resolved_signals[-1].source_node
        target = _LEGACY_TARGET_MAP.get(target, target)
        if target in _VALID_RETURN_TARGETS:
            return target
        logger.error("Invalid return target", target=target)
    return "context_classifier"
```

---

## 5. 통합 서비스 — `src/services/context_classifier.py`

```python
"""연속 여부 판정 + 의도 분류 통합 서비스.

단일 프롬프트로 SKIP/CONTINUE/NEW/UNSURE + 카테고리를 동시 판정한다.
질의 재작성은 수행하지 않는다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from src.models.enums import IntentType
from src.services.history_resolver import (
    HistoryDecision,
    build_unsure_clarification,
    _format_history,
)
from src.services.intent_resolver import (
    _map_category_to_intent,
)
from src.config import settings
from src.utils.llm import ParseError, llm_call_with_parse_retry
from src.utils.logger import get_logger

logger = get_logger(__name__)


# ── 규칙 기반 간이 의도 분류 (폴백용, LLM 호출 없음) ──

_ANALYSIS_SIGNALS = {
    "분석", "추이", "트렌드", "비교", "대비",
    "증감", "변화", "통계", "상관", "예측",
    "왜", "원인", "이유", "어때", "차트", "그래프", "시각화",
}
_EXTRACTION_SIGNALS = {
    "건수", "금액", "잔액", "목록", "리스트",
    "뽑아", "조회", "알려", "보여",
}
_CASUAL_SIGNALS = {
    "안녕", "감사", "수고", "됐어", "그만", "넘어가",
}


def classify_by_rules(query: str) -> tuple[str, str]:
    """규칙 기반 간이 의도 분류. Returns (category, confidence_str)."""
    q = query.strip()
    if any(s in q for s in _CASUAL_SIGNALS) and len(q) <= 15:
        return "CASUAL_TALK", "MEDIUM"
    if any(s in q for s in _ANALYSIS_SIGNALS):
        return "DATA_ANALYSIS", "MEDIUM"
    if any(s in q for s in _EXTRACTION_SIGNALS):
        return "DATA_EXTRACTION", "MEDIUM"
    return "AMBIGUOUS", "LOW"


@dataclass
class ContextClassifyResult:
    """통합 판정 결과."""

    # 연속 여부 판정 (질의 재작성 없음)
    resolution: HistoryDecision

    # 의도 분류
    intent: IntentType = IntentType.UNKNOWN
    confidence: float = 0.0
    category: str = ""
    reason: str = ""  # SKIP/NEW/UNSURE 시 분류 근거

    # CONTINUE 전용 필드
    continue_reason: str = ""    # 왜 CONTINUE라고 판단했는지
    continue_context: str = ""   # 대화 맥락을 반영한 실제 질문 풀어쓰기

    is_error: bool = False


async def context_classifier(
    query: str,
    conversation_history: list[dict[str, str]],
    *,
    system_prompt: str,
    user_template: str,
    clarification_history: str = "",
) -> ContextClassifyResult:
    """연속 여부 판정 + 의도 분류를 단일 LLM 호출로 수행한다.

    단일 프롬프트를 사용하며, 이력 유무에 관계없이 동일한 스키마로 응답받는다.
    이력 없으면 LLM이 resolution=SKIP으로 판정.

    ※ 질의 재작성은 수행하지 않는다.
    """
    # 유저 프롬프트 조립 — 이력 있으면 포함, 없으면 생략
    history_text = _format_history(conversation_history) if conversation_history else ""
    user_prompt = user_template.format(
        history=history_text,
        query=query,
        clarification_history=clarification_history,
    )

    try:
        _, parsed = await llm_call_with_parse_retry(
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            parse_fn=_parse_response,
            max_tokens=settings.llm_default_max_tokens,
            timeout=settings.llm_default_timeout,
            node_name="context_classifier",
        )
    except (ParseError, Exception) as e:
        logger.warning("LLM 호출 실패, 폴백", error=str(e))
        try:
            return await _fallback(query, conversation_history)
        except Exception as fb_err:
            logger.error("폴백도 실패", error=str(fb_err))
            return ContextClassifyResult(is_error=True)

    resolution = parsed["resolution"]
    category = parsed.get("category", "AMBIGUOUS")
    confidence_str = parsed.get("confidence", "MEDIUM")

    intent, confidence = _map_category_to_intent(
        category, confidence_str,
    )

    # CONTINUE일 때만 continue_reason / continue_context 추출
    if resolution == HistoryDecision.CONTINUE:
        return ContextClassifyResult(
            resolution=resolution,
            intent=intent,
            confidence=confidence,
            category=category,
            continue_reason=parsed.get("continue_reason", ""),
            continue_context=parsed.get("continue_context", ""),
        )

    return ContextClassifyResult(
        resolution=resolution,
        intent=intent,
        confidence=confidence,
        category=category,
        reason=parsed.get("reason", ""),
    )


async def _fallback(
    query: str,
    conversation_history: list[dict[str, str]],
) -> ContextClassifyResult:
    """LLM 호출 실패 시 폴백.

    이력이 있으면 기존 history_resolver(LLM)로 연속 여부만 판정하고,
    의도 분류는 규칙 기반으로 수행하여 LLM 호출을 최소화한다.
    """
    if not conversation_history:
        # 이력 없음 — 규칙 기반만
        cat, conf_str = classify_by_rules(query)
        intent, confidence = _map_category_to_intent(cat, conf_str)
        return ContextClassifyResult(
            resolution=HistoryDecision.SKIP,
            intent=intent,
            confidence=confidence,
            category=cat,
            reason="폴백: 규칙 기반 의도분류",
        )

    # 이력 있음 — history_resolver(LLM) + 규칙 기반 의도 분류
    from src.services.history_resolver import resolve_history
    from src.utils.resource_loader import load_text_required

    hr_system = load_text_required("prompts/interpret/history_resolver_system.txt")
    hr_user = load_text_required("prompts/interpret/history_resolver_user.txt")

    hr = await resolve_history(
        query, conversation_history,
        system_prompt=hr_system,
        user_template=hr_user,
    )

    cat, conf_str = classify_by_rules(query)
    intent, confidence = _map_category_to_intent(cat, conf_str)

    return ContextClassifyResult(
        resolution=hr.decision,
        intent=intent,
        confidence=confidence,
        category=cat,
        reason="폴백: 연속여부판정(LLM) + 의도분류(규칙 기반)",
    )


# ── 파싱 함수 ──

def _parse_response(raw: str) -> dict:
    """LLM 응답 JSON 파싱.

    SKIP/NEW/UNSURE: { resolution, category, confidence, reason }
    CONTINUE:        { resolution, category, confidence, continue_reason, continue_context }
    """
    cleaned = re.sub(r"```(?:json)?\s*", "", raw)
    cleaned = cleaned.replace("```", "").strip()
    data = json.loads(cleaned)

    raw_resolution = data.get("resolution", "").upper()
    try:
        resolution = HistoryDecision(raw_resolution)
    except ValueError:
        raise ValueError(f"허용되지 않는 resolution: {raw_resolution}")

    from src.agents.models.normalization import VALID_QUERY_CATEGORIES
    cat = data.get("category", "").upper()
    if cat == "CLARIFICATION":
        cat = "AMBIGUOUS"
    if cat not in VALID_QUERY_CATEGORIES:
        cat = "AMBIGUOUS"
    data["category"] = cat
    data["resolution"] = resolution
    return data
```

---

## 6. 통합 노드 — `src/agents/nodes/interpret/context_classifier.py`

```python
"""연속 여부 판정 + 의도 분류 통합 노드."""

from __future__ import annotations

from src.agents.models.clarification import (
    AmbiguitySignal,
    AmbiguityType,
    ConfidenceLevel,
)
from src.agents.nodes.system_prompts import (
    CONTEXT_CLASSIFIER_SYSTEM,
    CONTEXT_CLASSIFIER_USER,
)
from src.agents.state.state import (
    PipelineState,
    QueryStatus,
    add_trace,
)
from src.services.history_resolver import (
    HistoryDecision,
    build_unsure_clarification,
)
from src.services.context_classifier import context_classifier
from src.utils.logger import get_logger
from src.utils.truncate import truncate_log

logger = get_logger(__name__)


async def context_classifier_node(
    state: PipelineState,
) -> dict:
    """대화 연속 여부를 판정하고 의도를 분류한다. 질의 재작성은 하지 않는다."""
    query = state.preprocessed_input
    history = state.conversation_history

    # 명확화 이력 조립 (있을 때만)
    clarification_hist = ""
    if state.clarification_question or state.clarification_response:
        parts = []
        if state.clarification_question:
            parts.append(f"시스템: {state.clarification_question}")
        if state.clarification_response:
            parts.append(f"사용자: {state.clarification_response}")
        clarification_hist = "[명확화 이력]\n" + "\n".join(parts)

    result = await context_classifier(
        query,
        history,
        system_prompt=CONTEXT_CLASSIFIER_SYSTEM,
        user_template=CONTEXT_CLASSIFIER_USER,
        clarification_history=clarification_hist,
    )

    # ── 에러 ──
    if result.is_error:
        return {
            "intent": result.intent,
            "intent_confidence": result.confidence,
            "status": QueryStatus.ERROR,
            "error_message": "질의 해석에 실패했습니다. 다시 시도해주세요.",
        }

    # ── UNSURE: AmbiguitySignal 생성 ──
    # [P1-2 반영] UNSURE에서도 intent/category를 state에 저장
    if result.resolution == HistoryDecision.UNSURE:
        clarification_q = build_unsure_clarification(history)
        signal = AmbiguitySignal(
            source_node="context_classifier",
            ambiguity_type=AmbiguityType.CONTEXT,
            decision="ASK",
            confidence=ConfidenceLevel.LOW,
            question=clarification_q,
            reasoning="대화 이력에서 맥락을 추론할 수 없음",
        )
        return {
            "pending_signals": [signal],
            "intent": result.intent,
            "intent_confidence": result.confidence,
            "query_category": result.category,
            "trace_log": add_trace(
                state, "맥락분류",
                f"UNSURE — 명확화 신호 생성 (의도: {result.intent.value})",
                f"질문: {clarification_q}",
            ),
        }

    # ── AMBIGUOUS → T2 AmbiguitySignal ──
    if result.category == "AMBIGUOUS":
        reason_text = result.reason or "의도가 불명확합니다"
        signal = AmbiguitySignal(
            source_node="context_classifier",
            ambiguity_type=AmbiguityType.INTENT,
            decision="ASK",
            confidence=ConfidenceLevel.LOW,
            question=f"요청하신 내용을 좀 더 구체적으로 알려주시겠어요?\n{reason_text}",
            reasoning=reason_text,
        )
        return {
            "intent": result.intent,
            "intent_confidence": result.confidence,
            "query_category": result.category,
            "pending_signals": [signal],
            "status": QueryStatus.INTENT_CLASSIFIED,
            "trace_log": add_trace(
                state, "맥락분류",
                "AMBIGUOUS — 명확화 신호 생성",
                f"근거={reason_text}",
            ),
        }

    # ── 정상 경로: SKIP / NEW / CONTINUE ──
    # ※ 질의 재작성 없음 — preprocessed_input(원본 질의)을 변경하지 않는다.
    #   CONTINUE 시 continue_context를 통해 맥락이 반영된 질문 해석을 하류에 전달.
    updates: dict = {
        "intent": result.intent,
        "intent_confidence": result.confidence,
        "query_category": result.category,
        "is_continuation": result.resolution == HistoryDecision.CONTINUE,
        "status": QueryStatus.INTENT_CLASSIFIED,
    }

    if result.resolution == HistoryDecision.CONTINUE:
        updates["continue_context"] = result.continue_context
        updates["trace_log"] = add_trace(
            state, "맥락분류",
            f"CONTINUE+{result.category}",
            f"사유: {result.continue_reason}\n"
            f"맥락반영: {truncate_log(result.continue_context)}",
        )
    elif result.resolution == HistoryDecision.NEW:
        updates["trace_log"] = add_trace(
            state, "맥락분류",
            f"NEW+{result.category}",
            f"독립 질의, 의도: {result.intent.value}",
        )
    else:
        # SKIP (이력 없음)
        updates["trace_log"] = add_trace(
            state, "맥락분류",
            f"SKIP+{result.category}",
            f"의도: {result.intent.value} "
            f"(신뢰도 {result.confidence:.0%})",
        )

    # ── 추적 이벤트 ──
    from src.utils.tracker.dispatch import (
        dispatch_tracking_event,
        DECISION_INTENT,
    )
    await dispatch_tracking_event(DECISION_INTENT, {
        "node": "context_classifier",
        "decision_type": "intent_classification",
        "resolution": result.resolution.value,
        "chosen": result.intent.value,
        "confidence": result.confidence,
        "reason": result.continue_reason if result.resolution == HistoryDecision.CONTINUE else result.reason,
    })

    return updates
```

---

## 7. 프롬프트

### 7.1 `context_classifier_system.txt` (통합 시스템 프롬프트)

```text
당신은 은행 데이터 분석 시스템의 질의 해석 전문가입니다.
사용자의 현재 입력을 받아 2가지 판단을 동시에 수행합니다:
  1) 이전 대화와의 연속 여부 판정 (resolution)
  2) 질의의 의도 분류 (category)

※ 질의를 재작성하지 마세요. 연속 여부와 의도만 판단합니다.


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[STEP 1] 연속 여부 — resolution 판정
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SKIP — 이전 대화가 없음 (첫 질문이거나 대화 이력이 제공되지 않음)

CONTINUE — 현재 입력이 이전 대화의 연장선
  ✓ 이전 데이터 질의에 조건을 추가/변경
  ✓ 이전 명확화 질문에 대한 답변 (번호 선택, 구체화)
  ✓ 이전 결과에 대한 추가 요청

NEW — 현재 입력이 이전 대화와 무관
  ✓ 완전히 다른 주제의 데이터 요청
  ✓ 인사, 감사, 잡담 등
  ✓ "됐어", "그만" 등 이전 맥락 종료 표현

UNSURE — 연결 여부를 확신할 수 없음
  ✓ 중간에 인사/잡담 턴이 끼어 맥락이 끊긴 경우
  ✓ 짧은 입력인데 이전 대화와 연결되는지 모호
  ✓ 여러 이전 질의 중 어느 것에 연결되는지 불분명


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[STEP 2] 의도 분류 — category 판정
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

현재 입력을 기준으로 분류하세요.
CONTINUE인 경우 이전 대화 맥락을 참고하되, 현재 입력의 의도를 판단합니다.
UNSURE라도 현재 입력에서 의도를 파악할 수 있으면 해당 카테고리로 분류하세요.

DATA_ANALYSIS — 데이터에 대한 분석/비교/인사이트/판단 요청
  판별 기준 (1개 이상 충족):
  ✓ 비교/추이/변화 요청 (추이, 비교, 대비, 증감, 변화, 전망, 트렌드)
  ✓ 원인/이유를 묻는 질문 (왜, 원인, 이유)
  ✓ 평가/판단 요청 (어때, 괜찮아, 잘 되고 있어?)
  ✓ "~별" 분류축 + 수치가 있어 비교 암시 (지점별 실적, 등급별 연체율)
  ✓ 시각화/차트 요청 (차트로, 그래프로, 시각화)
  ✓ 분석을 명시적으로 요청 (분석해줘, 통계, 요약해줘)
  핵심 구분: 사용자가 원하는 것이 숫자 자체가 아닌 "해석/인사이트/비교 결과"

DATA_EXTRACTION — 데이터를 있는 그대로 조회/추출
  판별 기준 (2개 이상 충족):
  ✓ 구체적 비즈니스 엔티티 언급 (고객, 대출, 예금, 계좌, 지점, 카드 등)
  ✓ 수치/지표 언급 (잔액, 건수, 비율, 금액 등)
  ✓ 조회/추출 동사 (뽑아줘, 조회, 알려줘, 보여줘 등)
  ✓ 조건/범위 명시 (지난달, 서울, VIP, 1억 이상 등)
  핵심 구분: 사용자가 원하는 것이 "숫자/목록 자체"

  ⚠ DATA_EXTRACTION과 DATA_ANALYSIS 구분이 어려우면 DATA_ANALYSIS로 분류하세요.

CASUAL_TALK — 일반 대화 (인사, 감사, 잡담)
  판별 기준: 데이터/비즈니스 엔티티 언급 없이 일상적 대화

META_QUESTION — 데이터/시스템 자체에 대한 질문
  판별 기준: 데이터를 "조회"하는 게 아니라 데이터에 "대해" 묻는 질문
  ✓ "~가 뭐야?", "~의 의미", "~테이블 있어?", "~설명해줘"
  ✓ 시스템 기능/범위에 대한 질문

AMBIGUOUS — 판단 불가
  판별 기준: 위 카테고리 중 어디에도 확신 있게 분류할 수 없음
  ✓ 엔티티만 언급되고 액션 없음
  ✓ 맥락 없이는 의미 파악 불가


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[출력 형식]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

JSON만 출력하세요. 설명 텍스트, 마크다운 코드블록 기호를 포함하지 마세요.

■ CONTINUE인 경우:
{
  "resolution": "CONTINUE",
  "category": "카테고리값",
  "confidence": "HIGH | MEDIUM | LOW",
  "continue_reason": "왜 이전 대화의 연장선이라고 판단했는지 근거",
  "continue_context": "대화 맥락을 반영하여, 사용자가 실제로 무엇을 질문하고 있는지 풀어서 작성"
}

■ SKIP / NEW / UNSURE인 경우:
{
  "resolution": "SKIP | NEW | UNSURE",
  "category": "카테고리값",
  "confidence": "HIGH | MEDIUM | LOW",
  "reason": "분류 근거를 1줄로 설명"
}

[continue_context 작성 규칙]
- CONTINUE일 때만 작성합니다. NEW/UNSURE는 reason만 출력하세요.
- 이전 대화에서 어떤 맥락이 이어지는지 반영하여, 현재 입력의 실제 의미를 풀어쓰세요.
- 사용자의 말투와 표현 수준을 유지하세요 (기술 용어 추가 금지).
- 이전 대화의 결과값(숫자 등)은 포함하지 마세요. 조건과 의도만 반영.
- 원본 질의의 의도를 훼손하지 않도록, 최대한 충실하게 풀어쓰세요.


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[Few-shot 예시]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

--- CONTINUE + DATA_EXTRACTION ---

이전 대화:
  사용자: 이번 달 신규 고객 수 알려줘
  시스템: 이번 달 신규 고객은 총 1,234명입니다.
현재 입력: 그 중에서 VIP 등급은 몇 명이야?
→ {"resolution": "CONTINUE", "category": "DATA_EXTRACTION", "confidence": "HIGH", "continue_reason": "이전 신규 고객 질의 결과에 VIP 조건을 추가로 요청", "continue_context": "이번 달 신규 고객 중 VIP 등급 고객 수 알려줘"}

--- CONTINUE + DATA_ANALYSIS ---

이전 대화:
  사용자: 지점별 여신잔액 현황 보여줘
  시스템: (지점별 여신잔액 표)
현재 입력: 지난달이랑 비교해줘
→ {"resolution": "CONTINUE", "category": "DATA_ANALYSIS", "confidence": "HIGH", "continue_reason": "이전 지점별 여신잔액 현황에 대해 지난달과의 비교를 추가 요청", "continue_context": "지점별 여신잔액을 이번 달과 지난달 비교해줘"}

--- CONTINUE + 명확화 응답 ---

이전 대화:
  시스템: 어떤 데이터가 필요하신가요? 1) 고객 수 2) 대출 잔액 3) 직접 입력
현재 입력: 2번
→ {"resolution": "CONTINUE", "category": "DATA_EXTRACTION", "confidence": "HIGH", "continue_reason": "시스템 명확화 질문에 대한 선택 응답 (2번=대출 잔액)", "continue_context": "대출 잔액 알려줘"}

--- CONTINUE + 테이블 선택 응답 ---

이전 대화:
  사용자: 연체 현황 보여줘
  시스템: TB_LOAN_INFO(건별 현황)와 TB_LOAN_OVERDUE_STAT(월별 통계) 중 어떤 데이터인가요?
현재 입력: 월별 통계
→ {"resolution": "CONTINUE", "category": "DATA_EXTRACTION", "confidence": "HIGH", "continue_reason": "테이블 선택 명확화에 월별 통계를 선택한 응답", "continue_context": "월별 연체 통계 현황 보여줘"}

--- NEW + DATA_ANALYSIS ---

이전 대화:
  사용자: 이번 달 연체 현황 알려줘
  시스템: (연체 현황 보고서)
현재 입력: 올해 수신 실적 추이 분석해줘
→ {"resolution": "NEW", "category": "DATA_ANALYSIS", "confidence": "HIGH", "reason": "이전 연체와 무관한 새 주제, 추이 분석 요청"}

--- NEW + CASUAL_TALK ---

이전 대화:
  시스템: 어떤 데이터가 필요하신가요? 1) 고객 수 2) 대출 잔액
현재 입력: 됐어
→ {"resolution": "NEW", "category": "CASUAL_TALK", "confidence": "HIGH", "reason": "이전 맥락 종료 표현, 데이터 요청 아님"}

--- UNSURE + 의도는 파악 가능 (DATA_EXTRACTION) ---

이전 대화:
  사용자: 이번 달 신규 고객 수 알려줘
  시스템: 1,234명입니다.
  사용자: 감사합니다
  시스템: 네, 더 필요한 게 있으시면 말씀해주세요.
현재 입력: 지점별 잔액?
→ {"resolution": "UNSURE", "category": "DATA_EXTRACTION", "confidence": "MEDIUM", "reason": "중간에 인사 턴이 끼어 맥락 불분명하나, 지점별+잔액으로 데이터 추출 의도는 파악 가능"}

--- UNSURE + 의도도 불분명 (AMBIGUOUS) ---

이전 대화:
  사용자: 작년 4분기 대출 연체율 알려줘
  시스템: 1.23%입니다.
현재 입력: 그거
→ {"resolution": "UNSURE", "category": "AMBIGUOUS", "confidence": "LOW", "reason": "지시대명사만으로는 맥락도 의도도 파악 불가"}

--- SKIP + DATA_EXTRACTION (이력 없음) ---

이전 대화: (없음)
현재 입력: 이번 달 신규 고객 수 알려줘
→ {"resolution": "SKIP", "category": "DATA_EXTRACTION", "confidence": "HIGH", "reason": "이전 대화 없음, 신규 고객 건수 조회"}

--- SKIP + CASUAL_TALK (이력 없음) ---

이전 대화: (없음)
현재 입력: 안녕하세요
→ {"resolution": "SKIP", "category": "CASUAL_TALK", "confidence": "HIGH", "reason": "이전 대화 없음, 인사"}
```

### 7.2 `context_classifier_user.txt` (통합 유저 프롬프트)

```text
{history}
{clarification_history}

[현재 입력]

{query}

연속 여부(resolution) + 의도 분류(category)를 JSON으로 출력하세요.
```

> - `{history}` — 대화 이력이 있으면 `[이전 대화]\n사용자: …\n시스템: …` 형태로 삽입, 없으면 빈 문자열.
> - `{clarification_history}` — 명확화 이력이 있으면 `[명확화 이력]\n시스템: …\n사용자: …` 형태로 삽입, 없으면 빈 문자열.
> - 이력이 모두 없으면 `[현재 입력]` 섹션만 남게 되어, LLM이 자연스럽게 `SKIP`으로 판정.

---

## 8. system_prompts.py 변경

```python
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# interpret/ — 질의 해석 계층
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# 통합 노드 프롬프트 (신규 — 단일 프롬프트)
CONTEXT_CLASSIFIER_SYSTEM = _interpret("context_classifier_system.txt")
CONTEXT_CLASSIFIER_USER = _interpret("context_classifier_user.txt")

# 기존 프롬프트는 모듈 레벨에서 제거.
# 폴백에서 필요 시 load_text_required()로 직접 로드.
# (HISTORY_RESOLVER_*, INTENT_CLASSIFIER_*, CLASSIFY_ONLY_* 모두 삭제)

QUERY_NORMALIZER_PHASE1_SYSTEM = _interpret(
    "query_normalizer_phase1_system.txt",
)
# ... (이하 기존과 동일)
```

---

## 9. 테스트 마이그레이션 계획

| 단계 | 작업 | 대상 파일 |
|------|------|----------|
| 1 | 통합 노드/서비스 단위 테스트 신규 작성 | `tests/auto/unit/test_context_classifier.py` |
| 2 | 기존 e2e 테스트의 노드 이름 변경 | `test_pipeline_e2e.py`, `test_node_chain.py` |
| 3 | 기존 단위 테스트 deprecated 표시 | `test_classify_intent.py`, `test_history_resolve_scenarios.py` |
| 4 | fixtures의 스냅샷/mock 업데이트 | `llm_snapshot.py`, `conftest.py` |
| 5 | 다음 릴리스에서 deprecated 테스트 제거 | 3단계 파일들 |

---

## 10. 이번 scope에 포함된 TODO

| 과제 | 설명 | 비고 |
|------|------|------|
| `simple_responder` 노드 구현 | CASUAL_TALK → 정형 응답, META_QUESTION → 간단 LLM 호출 또는 시스템 안내. `format_response` → END로 라우팅. 구현 시 `is_continuation=False`, `continue_context=""` 초기화 포함 | §4 그래프 참조 |
| `awaiting_clarification` 전역 제거 | state 필드 삭제, `needs_history_resolve()` 파라미터 제거, `clarification_handler_node()`의 레거시 리셋 제거, `runner.py`의 결과 필드 제거 | 체크포인터 interrupt/resume으로 대체 완료 |

## 11. 후속 과제 (이번 scope 밖)

| 과제 | 설명 | 우선순위 |
|------|------|---------|
| QueryCategory에서 CLARIFICATION 완전 제거 | enum 값 삭제 + 참조하는 코드/테스트 정리 | P3 |
| `QueryCategory` → `IntentType` 통합 | CR-04에서 지적된 개념 중첩 해소. `QueryCategory`는 Intent Gate 중간 검증용 잔재이므로 `IntentType`으로 일원화하고 `VALID_QUERY_CATEGORIES`, `_map_category_to_intent()` 등 매핑 레이어 제거 | P3 |
| 폴백 서비스 제거 | 통합 노드 안정화 후 `_fallback` 및 기존 서비스 deprecated 코드 완전 삭제. 구 프롬프트 파일(`history_resolver_*.txt`) 삭제와 반드시 동기화 | P3 |
| 프롬프트 분리 (조건부) | 폐쇄망 모델(Solar Pro 2 70B)에서 조건부 스키마 혼동 발생 시 system prompt를 Path A/B로 재분리 | 필요 시 |

---

## 부록: 비판적 검토 이력

아래는 초안에서 발견되어 본문에 반영 완료된 보완사항 목록이다.

### 1차 검토 (초안 → 확정본)

| 등급 | ID | 사항 | 반영 위치 |
| --- | --- | --- | --- |
| [P1] | P1-1 | 2종 JSON 스키마가 폐쇄망 모델에서 혼동 → 프롬프트 분리 | ~~§2, §7~~ → **2차에서 통합으로 재결정** |
| [P1] | P1-2 | UNSURE 시 의도 분류 결과가 버려짐 → state에 저장 | §6 노드 코드 |
| [P1] | P1-3 | UNSURE Few-shot이 항상 AMBIGUOUS 유도 → 2가지 예시 분리 | §7.1 Few-shot |
| [P1] | F4 | 배포 과도기에 구 source_node 호환 → `_LEGACY_TARGET_MAP` | §4 라우팅 |
| [P2] | P2-1 | 비데이터 의도가 여전히 clarification_handler 경유 | ~~TODO~~ → **2차에서 simple_responder로 해결** |
| [P2] | P2-3 | 폴백 시 LLM 최대 6회 호출 → 규칙 기반 대체 | §5 `_fallback`, `classify_by_rules` |
| [P3] | P3-1 | system_prompts.py deprecated 변수 정리 | §5, §8 |

### 2차 검토 (확정본 → 최종본)

| 등급 | ID | 사항 | 반영 위치 |
| --- | --- | --- | --- |
| [P1] | R1 | 질의 재작성 제거 — 원본 질의 + 이력을 하류에 그대로 전달 | §1~§7 전체 |
| [P1] | R2 | CONTINUE 시 `continue_reason` + `continue_context` 추가 | §5 데이터클래스, §6 노드, §7 프롬프트 |
| [P1] | R3 | 노드 리네이밍 `resolve_and_classify` → `context_classifier` | 전체 |
| [P1] | R4 | `awaiting_clarification` 제거 — 체크포인터로 대체 완료 | §3, §5, §6, §10 |
| [P1] | R5 | Path A/B 프롬프트 통합 → 단일 프롬프트 + SKIP 판정 | §2, §3, §5, §7, §8 |
| [P1] | R6 | 비데이터 의도 → `simple_responder` 경량 노드로 직접 응답 | §4, §10 |
