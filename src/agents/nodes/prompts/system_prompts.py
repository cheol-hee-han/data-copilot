"""시스템 프롬프트 모음 — resources/prompts/ 외부 파일 로더.

Prompt Version: 1.6 (2026-03-21)
변경 이력:
  v1.0 (2026-03-18): 초기 프롬프트 설계
  v1.1 (2026-03-19): 코드 리뷰 반영, 버전 관리 도입
  v1.2 (2026-03-19): 테이블 설명 보강 프롬프트 추가
  v1.3 (2026-03-19): 시각화 판단 + SVG 생성 프롬프트 추가
  v1.4 (2026-03-21): resources/ 외부화 지원 — resources/prompts/*.txt 우선 로드
  v1.5 (2026-03-21): 프롬프트 본문을 resources/prompts/*.txt 로 완전 이관
  v1.6 (2026-03-21): 질의 정규화 프롬프트 추가 (Intent Gate, Phase 1/2)

모든 노드가 이 파일에서 프롬프트 변수를 import하여 사용한다.
프롬프트 본문은 resources/prompts/*.txt 파일에서 읽어온다.

파일 매핑:
  INTENT_CLASSIFICATION         ← resources/prompts/intent_classification.txt
  CLARIFICATION                 ← resources/prompts/clarification.txt
  SQL_GENERATION_RULES          ← resources/prompts/sql_generation.txt
  RESULT_FORMATTING             ← resources/prompts/result_formatting.txt
  DATA_ANALYSIS                 ← resources/prompts/data_analysis.txt
  TABLE_DESCRIPTION_ENRICHMENT  ← resources/prompts/table_enrichment.txt
  VISUALIZATION_JUDGMENT        ← resources/prompts/visualization_judgment.txt
  VISUALIZATION_SVG_GENERATION  ← resources/prompts/visualization_svg.txt
  INTENT_GATE                   ← resources/prompts/intent_gate.txt
  NORMALIZATION_PHASE1          ← resources/prompts/normalization_phase1.txt
  NORMALIZATION_PHASE2          ← resources/prompts/normalization_phase2.txt
  INTENT_GATE_USER              ← resources/prompts/intent_gate_user.txt
  INTENT_FORMAT_HINT            ← resources/prompts/intent_format_hint.txt
  NORMALIZATION_PHASE1_USER     ← normalization_phase1_user.txt
  NORMALIZATION_PHASE2_USER     ← normalization_phase2_user.txt
  ANALYSIS_USER                 ← analysis_user.txt
  ANALYSIS_FORMAT_HINT          ← analysis_format_hint.txt
  VIZ_JUDGMENT_FORMAT_HINT      ← viz_judgment_format_hint.txt
  VIZ_SVG_SYSTEM                ← resources/prompts/viz_svg_system.txt
  FORMATTING_USER               ← resources/prompts/formatting_user.txt
  ENRICHMENT_SYSTEM             ← resources/prompts/enrichment_system.txt
  ENRICHMENT_FORMAT_HINT        ← resources/prompts/enrichment_format_hint.txt
"""

from src.utils.resource_loader import load_text_required


def _read(filename: str) -> str:
    """resources/prompts/ 에서 프롬프트 파일을 읽는다."""
    return load_text_required(f"prompts/{filename}")


# ---------------------------------------------------------------------------
# 프롬프트 변수 — resources/prompts/*.txt 에서 로드
# ---------------------------------------------------------------------------

INTENT_CLASSIFICATION = _read("intent_classification.txt")
CLARIFICATION = _read("clarification.txt")
SQL_GENERATION_RULES = _read("sql_generation.txt")
RESULT_FORMATTING = _read("result_formatting.txt")
DATA_ANALYSIS = _read("data_analysis.txt")
TABLE_DESCRIPTION_ENRICHMENT = _read("table_enrichment.txt")
VISUALIZATION_JUDGMENT = _read("visualization_judgment.txt")
VISUALIZATION_SVG_GENERATION = _read("visualization_svg.txt")

# 질의 정규화 프롬프트
INTENT_GATE = _read("intent_gate.txt")
NORMALIZATION_PHASE1 = _read("normalization_phase1.txt")
NORMALIZATION_PHASE2 = _read("normalization_phase2.txt")

# 대화 이력 해소 프롬프트
HISTORY_RESOLVE = _read("history_resolve.txt")
HISTORY_RESOLVE_USER = _read("history_resolve_user.txt")
HISTORY_RESOLVE_FORMAT_HINT = _read("history_resolve_format_hint.txt")

# 유저 프롬프트 템플릿
INTENT_GATE_USER = _read("intent_gate_user.txt")
INTENT_FORMAT_HINT = _read("intent_format_hint.txt")
NORMALIZATION_PHASE1_USER = _read("normalization_phase1_user.txt")
NORMALIZATION_PHASE2_USER = _read("normalization_phase2_user.txt")
ANALYSIS_USER = _read("analysis_user.txt")
ANALYSIS_FORMAT_HINT = _read("analysis_format_hint.txt")
VIZ_JUDGMENT_FORMAT_HINT = _read("viz_judgment_format_hint.txt")
VIZ_SVG_SYSTEM = _read("viz_svg_system.txt")
FORMATTING_USER = _read("formatting_user.txt")
ENRICHMENT_SYSTEM = _read("enrichment_system.txt")
ENRICHMENT_FORMAT_HINT = _read("enrichment_format_hint.txt")

# ---------------------------------------------------------------------------
# SQL 재생성 시 검증 피드백을 주입하는 섹션 템플릿.
# sql_generator.py 에서 validation_feedback 이 있을 때만 삽입한다.
# 이 템플릿은 코드 로직과 결합되어 있으므로 여기에 유지한다.
# ---------------------------------------------------------------------------
SQL_VALIDATION_FEEDBACK_SECTION = """\
[이전 시도에서 발견된 오류 — 반드시 수정하세요]
아래 문제가 이전에 생성한 SQL에서 발생했습니다. 동일한 실수를 반복하지 마세요:
{feedback}

"""
