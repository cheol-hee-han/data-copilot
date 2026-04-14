"""시스템 프롬프트 모음 — resources/prompts/ 외부 파일 로더.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

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
  v2.0 (2026-03-26): TABLE_COMPARISON_SYSTEM 추가 (유사 테이블 비교 판정)
  v2.1 (2026-03-29): 변수명을 파일명과 일치시키는 네이밍 통일

모든 노드가 이 파일에서 프롬프트 변수를 import하여 사용한다.
프롬프트 본문은 resources/prompts/ 하위 3계층 디렉토리에서 읽어온다.

디렉토리 구조:
  interpret/  — 질의 해석 계층 (의도분류, 정규화, 이력해소, 명확화)
  reason/     — 추론 계층 (탐색, SQL생성, 검증, 재계획)
  present/    — 표현 계층 (분석, 시각화, 포맷팅)

명명 규칙:
  {노드파일명}[_{하위기능}][_{phase}]_{역할}.txt
  역할: system | user | section

명명 규칙 — 변수명 = 파일명(확장자 제외).upper():
  예) analyzer_system.txt → ANALYZER_SYSTEM

파일 매핑 (interpret/):
  INTENT_CLASSIFIER_SYSTEM         ← intent_classifier_system.txt
  INTENT_CLASSIFIER_USER           ← intent_classifier_user.txt
  INTENT_CLASSIFIER_QUERY_REWRITER ← intent_classifier_query_rewriter.txt
  QUERY_NORMALIZER_PHASE1_SYSTEM  ← query_normalizer_phase1_system.txt
  QUERY_NORMALIZER_PHASE1_USER    ← query_normalizer_phase1_user.txt
  QUERY_NORMALIZER_PHASE2_SYSTEM  ← query_normalizer_phase2_system.txt
  QUERY_NORMALIZER_PHASE2_USER    ← query_normalizer_phase2_user.txt

파일 매핑 (reason/):
  CONTEXT_INTERPRETER_SYSTEM        ← context_interpreter_system.txt
  SQL_GENERATOR_SYSTEM_BY_DIALECT   ← sql_generator_system_{db}.txt (4종)
    tsql    ← sql_generator_system_sybase_iq.txt
    hive    ← sql_generator_system_impala.txt
    oracle  ← sql_generator_system_oracle.txt
    postgres← sql_generator_system_postgres.txt
  SQL_GENERATOR_FIX_SECTION         ← sql_generator_fix_section.txt
  SQL_VALIDATOR_SYSTEM              ← sql_validator_system.txt
  RECOVERY_AGENT_SYSTEM             ← recovery_agent_system.txt

파일 매핑 (present/):
  ANALYZER_SYSTEM                 ← analyzer_system.txt
  ANALYZER_USER                   ← analyzer_user.txt
  ANALYZER_VIZ_JUDGMENT_SYSTEM    ← analyzer_viz_judgment_system.txt
  ANALYZER_VIZ_JUDGMENT_USER      ← analyzer_viz_judgment_user.txt
  ANALYZER_VIZ_SVG_SYSTEM_BASE    ← analyzer_viz_svg_system_base.txt
  ANALYZER_VIZ_SVG_EXAMPLES       ← analyzer_viz_svg_example_{chart_type}.txt
  ANALYZER_VIZ_SVG_USER           ← analyzer_viz_svg_user.txt
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

# 통합 노드 프롬프트 (resolve_history + classify_intent → intent_classifier)
INTENT_CLASSIFIER_SYSTEM = _interpret("intent_classifier_system.txt")
INTENT_CLASSIFIER_USER = _interpret("intent_classifier_user.txt")
INTENT_CLASSIFIER_QUERY_REWRITER = _interpret(
    "intent_classifier_query_rewriter.txt",
)

QUERY_NORMALIZER_PHASE1_SYSTEM = _interpret(
    "query_normalizer_phase1_system.txt",
)
QUERY_NORMALIZER_PHASE1_USER = _interpret(
    "query_normalizer_phase1_user.txt",
)
QUERY_NORMALIZER_PHASE2_SYSTEM = _interpret(
    "query_normalizer_phase2_system.txt",
)
QUERY_NORMALIZER_PHASE2_USER = _interpret(
    "query_normalizer_phase2_user.txt",
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# reason/ — 추론 계층
#
# JSON few-shot 예제를 포함하므로 Python .format() 사용 불가.
# 노드에서 .replace() 로 플레이스홀더를 치환해야 한다.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CONTEXT_INTERPRETER_SYSTEM = _reason("context_interpreter_system.txt")

# SQL Generator는 대상 DB(dialect)에 따라 최적화된 프롬프트를 선택한다.
# dialect → prompt 매핑. 키는 DatabaseConnector.dialect 반환값.
SQL_GENERATOR_SYSTEM_BY_DIALECT: dict[str, str] = {
    "tsql": _reason("sql_generator_system_sybase_iq.txt"),
    "hive": _reason("sql_generator_system_impala.txt"),
    "oracle": _reason("sql_generator_system_oracle.txt"),
    "postgres": _reason("sql_generator_system_postgres.txt"),
}


def get_sql_generator_system(dialect: str) -> str:
    """dialect에 맞는 SQL 생성 시스템 프롬프트를 반환한다.

    알 수 없는 dialect는 postgres 프롬프트로 폴백한다(외부망 기본값).
    """
    return SQL_GENERATOR_SYSTEM_BY_DIALECT.get(
        dialect,
        SQL_GENERATOR_SYSTEM_BY_DIALECT["postgres"],
    )


SQL_GENERATOR_FIX_SECTION = _reason(
    "sql_generator_fix_section.txt",
)
SQL_VALIDATOR_SYSTEM = _reason("sql_validator_system.txt")
RECOVERY_AGENT_SYSTEM = _reason("recovery_agent_system.txt")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# present/ — 표현 계층
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ANALYZER_SYSTEM = _present("analyzer_system.txt")
ANALYZER_USER = _present("analyzer_user.txt")
ANALYZER_VIZ_JUDGMENT_SYSTEM = _present("analyzer_viz_judgment_system.txt")
ANALYZER_VIZ_JUDGMENT_USER = _present("analyzer_viz_judgment_user.txt")
ANALYZER_VIZ_SVG_SYSTEM_BASE = _present(
    "analyzer_viz_svg_system_base.txt",
)
# 차트 타입별 예제. 키는 VisualizationType.value와 동일해야 한다.
ANALYZER_VIZ_SVG_EXAMPLES: dict[str, str] = {
    "bar_chart": _present("analyzer_viz_svg_example_bar_chart.txt"),
    "line_chart": _present("analyzer_viz_svg_example_line_chart.txt"),
    "pie_chart": _present("analyzer_viz_svg_example_pie_chart.txt"),
    "horizontal_bar": _present(
        "analyzer_viz_svg_example_horizontal_bar.txt",
    ),
    "flowchart": _present("analyzer_viz_svg_example_flowchart.txt"),
    "timeline": _present("analyzer_viz_svg_example_timeline.txt"),
    "donut_chart": _present(
        "analyzer_viz_svg_example_donut_chart.txt",
    ),
    "mind_map": _present("analyzer_viz_svg_example_mind_map.txt"),
}
ANALYZER_VIZ_SVG_USER = _present("analyzer_viz_svg_user.txt")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 인라인 템플릿 (코드 로직과 결합)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SQL_VALIDATION_FEEDBACK_SECTION = """\
[이전 시도에서 발견된 오류 — 반드시 수정하세요]
아래 문제가 이전에 생성한 SQL에서 발생했습니다. 동일한 실수를 반복하지 마세요:
{feedback}

"""
