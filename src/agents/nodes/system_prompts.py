"""시스템 프롬프트 모음 — resources/prompts/ 외부 파일 로더.

Prompt Version: 2.0 (2026-03-26)
변경 이력:
  v1.0 (2026-03-18): 초기 프롬프트 설계
  v1.1 (2026-03-19): 코드 리뷰 반영, 버전 관리 도입
  v1.2 (2026-03-19): 테이블 설명 보강 프롬프트 추가
  v1.3 (2026-03-19): 시각화 판단 + SVG 생성 프롬프트 추가
  v1.4 (2026-03-21): resources/ 외부화 지원
  v1.5 (2026-03-21): 프롬프트 본문을 resources/prompts/*.txt 로 완전 이관
  v1.6 (2026-03-21): 질의 정규화 프롬프트 추가 (Intent Gate, Phase 1/2)
  v1.7 (2026-03-25): agentic 프롬프트 외부화
  v1.8 (2026-03-25): 3계층 디렉토리 구조 (interpret/reason/present)
  v1.9 (2026-03-25): 파일 명명규칙 통일 — 노드명 기반 + _system/_user 접미
  v2.0 (2026-03-26): REASON_TABLE_COMPARISON 추가 (유사 테이블 비교 판정)

모든 노드가 이 파일에서 프롬프트 변수를 import하여 사용한다.
프롬프트 본문은 resources/prompts/ 하위 3계층 디렉토리에서 읽어온다.

디렉토리 구조:
  interpret/  — 질의 해석 계층 (의도분류, 정규화, 이력해소, 명확화)
  reason/     — 추론 계층 (탐색, SQL생성, 검증, 재계획)
  present/    — 표현 계층 (분석, 시각화, 포맷팅)

명명 규칙:
  {노드파일명}[_{하위기능}][_{phase}]_{역할}.txt
  역할: system | user | section

파일 매핑 (interpret/):
  INTENT_CLASSIFICATION    ← intent_classifier_legacy_system.txt
  INTENT_GATE              ← intent_classifier_system.txt
  INTENT_GATE_USER         ← intent_classifier_user.txt
  CLARIFICATION            ← clarifier_system.txt
  CLARIFICATION_USER       ← clarifier_user.txt
  NORMALIZATION_PHASE1     ← query_normalizer_phase1_system.txt
  NORMALIZATION_PHASE1_USER← query_normalizer_phase1_user.txt
  NORMALIZATION_PHASE2     ← query_normalizer_phase2_system.txt
  NORMALIZATION_PHASE2_USER← query_normalizer_phase2_user.txt
  HISTORY_RESOLVE          ← history_resolver_system.txt
  HISTORY_RESOLVE_USER     ← history_resolver_user.txt

파일 매핑 (reason/):
  REASON_PLAN_SYSTEM       ← planner_system.txt
  REASON_EXPLORE_OBSERVE   ← context_explorer_system.txt
  REASON_BATCH_INTERPRET   ← batch_interpret_system.txt
  REASON_TABLE_COMPARISON  ← table_comparison_system.txt
  REASON_GENERATE_SQL      ← sql_generator_system.txt
  REASON_GENERATE_SQL_FIX  ← sql_generator_fix_section.txt
  REASON_VALIDATE_LAYER2B  ← sql_validator_system.txt
  REASON_REPLAN            ← recovery_planner_system.txt

파일 매핑 (present/):
  DATA_ANALYSIS            ← analyzer_system.txt
  ANALYSIS_USER            ← analyzer_user.txt
  VIZ_JUDGMENT_SYSTEM      ← analyzer_viz_judgment_system.txt
  VIZ_JUDGMENT_USER        ← analyzer_viz_judgment_user.txt
  VIZ_SVG_SYSTEM           ← analyzer_viz_svg_system.txt
  VIZ_SVG_USER             ← analyzer_viz_svg_user.txt
  RESULT_FORMATTING        ← formatter_system.txt
  FORMATTING_USER          ← formatter_user.txt
"""

from src.utils.resource_loader import load_text_required


def _interpret(filename: str) -> str:
    """resources/prompts/interpret/ 에서 읽는다."""
    return load_text_required(f"prompts/interpret/{filename}")


def _reason(filename: str) -> str:
    """resources/prompts/reason/ 에서 읽는다."""
    return load_text_required(f"prompts/reason/{filename}")


def _present(filename: str) -> str:
    """resources/prompts/present/ 에서 읽는다."""
    return load_text_required(f"prompts/present/{filename}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# interpret/ — 질의 해석 계층
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

INTENT_CLASSIFICATION = _interpret("intent_classifier_legacy_system.txt")
CLARIFICATION = _interpret("clarifier_system.txt")
CLARIFICATION_USER = _interpret("clarifier_user.txt")
INTENT_GATE = _interpret("intent_classifier_system.txt")
INTENT_GATE_USER = _interpret("intent_classifier_user.txt")
NORMALIZATION_PHASE1 = _interpret(
    "query_normalizer_phase1_system.txt",
)
NORMALIZATION_PHASE1_USER = _interpret(
    "query_normalizer_phase1_user.txt",
)
NORMALIZATION_PHASE2 = _interpret(
    "query_normalizer_phase2_system.txt",
)
NORMALIZATION_PHASE2_USER = _interpret(
    "query_normalizer_phase2_user.txt",
)
HISTORY_RESOLVE = _interpret("history_resolver_system.txt")
HISTORY_RESOLVE_USER = _interpret("history_resolver_user.txt")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# reason/ — 추론 계층
#
# JSON few-shot 예제를 포함하므로 Python .format() 사용 불가.
# 노드에서 .replace() 로 플레이스홀더를 치환해야 한다.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

REASON_PLAN_SYSTEM = _reason("planner_system.txt")
REASON_EXPLORE_OBSERVE = _reason("context_explorer_system.txt")
REASON_BATCH_INTERPRET = _reason("batch_interpret_system.txt")
REASON_TABLE_COMPARISON = _reason("table_comparison_system.txt")
REASON_TABLE_VERIFIER = _reason("table_verifier_system.txt")
REASON_TABLE_VERIFIER_USER = _reason(
    "table_verifier_user.txt",
)
REASON_GENERATE_SQL = _reason("sql_generator_system.txt")
REASON_GENERATE_SQL_FIX = _reason(
    "sql_generator_fix_section.txt",
)
REASON_VALIDATE_LAYER2B = _reason("sql_validator_system.txt")
REASON_REPLAN = _reason("recovery_planner_system.txt")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# present/ — 표현 계층
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

DATA_ANALYSIS = _present("analyzer_system.txt")
ANALYSIS_USER = _present("analyzer_user.txt")
VIZ_JUDGMENT_SYSTEM = _present("analyzer_viz_judgment_system.txt")
VIZ_JUDGMENT_USER = _present("analyzer_viz_judgment_user.txt")
VIZ_SVG_SYSTEM = _present("analyzer_viz_svg_system.txt")
VIZ_SVG_USER = _present("analyzer_viz_svg_user.txt")
RESULT_FORMATTING = _present("formatter_system.txt")
FORMATTING_USER = _present("formatter_user.txt")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 인라인 템플릿 (코드 로직과 결합)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SQL_VALIDATION_FEEDBACK_SECTION = """\
[이전 시도에서 발견된 오류 — 반드시 수정하세요]
아래 문제가 이전에 생성한 SQL에서 발생했습니다. 동일한 실수를 반복하지 마세요:
{feedback}

"""
