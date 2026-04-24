"""Unified Clarification Framework 스키마 정의.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

단일 AmbiguitySignal 모델이 모호성의 전체 생명주기를 커버한다:
  감지 → 가드레일 보정 → ASK(interrupt→응답) / INFER(자동추론)

프론트엔드는 interrupt 페이로드의 question_type으로 UI를 자동 렌더링한다.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from src.models.enums import ConfidenceLevel as ConfidenceLevel


# ── 모호성 분류 (AmbiSQL 7종, 명칭 단순화) ──


class AmbiguityType(StrEnum):
    """모호성 유형 분류 — AmbiSQL(arXiv 2508.15276) 기반.

    LLM JSON 출력 오기(typo) 방지 + 금융 도메인 의미 직결.
    """

    TABLE = "TABLE"          # AmbiSchema: 테이블/컬럼 참조 모호
    INTENT = "INTENT"        # AmbiIntent: 의도/연산 방식 모호
    VALUE = "VALUE"          # AmbiValue: 코드값 매칭 실패
    FORMULA = "FORMULA"      # AmbiSource: 산출식 출처 모호
    TIMEFRAME = "TIMEFRAME"  # AmbiRef: 기간/시점 모호
    CONTEXT = "CONTEXT"      # AmbiContext: 추론 근거 부족
    CONFLICT = "CONFLICT"    # AmbiFallacy: 모순된 전제


class QuestionType(StrEnum):
    """명확화 질문 유형 — 프론트엔드 UI 렌더링 기준."""

    FREE_TEXT = "free_text"          # 자유 입력 텍스트박스
    SINGLE_SELECT = "single_select"  # 라디오 버튼 / 선택지
    CONFIRM = "confirm"              # 예/아니오 확인


# ── 단일 모델: AmbiguitySignal ──


class AmbiguitySignal(BaseModel):
    """모호성의 전체 생명주기를 하나의 객체로 관리.

    감지 → 가드레일 보정 → ASK(interrupt→응답) / INFER(자동추론)
    모두 이 모델 하나로 처리한다. decision과 answer 유무로 상태를 판별.

    왜 단일 모델인가:
    1. ASK/INFER 혼재: 노드가 [signal_ASK, signal_INFER]를 한꺼번에
       반환 — 타입이 같아야 하나의 리스트에 담김
    2. 가드레일 보정: INFER→ASK 변환 시 필드 변경(decision="ASK")만으로 충분
    3. 감사 추적: 하나의 객체에 감지~해소 전 과정이 기록됨
    4. interrupt 페이로드: model_dump(include=...) — 변환 함수 불필요
    """

    # ── 감지 시점 (노드가 설정) ──
    source_node: str                             # 발생 노드명
    ambiguity_type: AmbiguityType                # 7종 분류
    decision: Literal["ASK", "INFER"]            # LLM의 판정
    confidence: ConfidenceLevel                  # 판정 확신도
    question: str                                # DTE 패턴: "왜 묻는지" 포함
    question_type: QuestionType = QuestionType.FREE_TEXT
    options: list[str] = Field(default_factory=list)

    @field_validator("options", mode="before")
    @classmethod
    def _coerce_options(cls, v: Any) -> list[str]:
        """LLM JSON의 'options': null → 빈 리스트로 변환."""
        return v if v is not None else []

    inferred_value: str | None = None            # INFER 시 추론값
    reasoning: str = ""                          # 판정 근거 (한국어)
    override_reason: str | None = None           # 가드레일 보정 시 사유

    # ── 해소 시점 (clarify_unified가 설정) ──
    answer: str | None = None                    # ASK: resume 후 채워짐
    resolved_at: datetime | None = None

    # ── 턴 격리 ──
    turn_id: str | None = None                   # 소속 턴 식별자 (소비자 필터링용)

    # ── 판별 프로퍼티 ──
    @property
    def is_resolved(self) -> bool:
        """ASK는 answer가 있어야, INFER는 항상 resolved."""
        return self.decision == "INFER" or self.answer is not None

    @property
    def display_value(self) -> str:
        """결과 안내용: ASK면 answer, INFER면 inferred_value."""
        if self.decision == "INFER":
            return self.inferred_value or ""
        return self.answer or ""
