"""파이프라인 열거형 정의.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

의도 유형(IntentType), 쿼리 처리 상태(QueryStatus),
시각화 유형(VisualizationType) 등 파이프라인 전 계층에서
공유하는 열거형을 정의한다.

graph → nodes → services → connectors 모든 계층에서 참조하며,
agents/state/state.py 를 거치지 않고 직접 import 가능하다.
"""

from enum import Enum


class HistoryDecision(str, Enum):
    """대화 이력 해소 판정."""

    CONTINUE = "CONTINUE"  # 이전 맥락 이어짐 → 재작성
    NEW = "NEW"  # 새 독립 질의 → 원본 유지
    UNSURE = "UNSURE"  # 불확실 → 명확화 질문
    SKIP = "SKIP"  # LLM 호출 없이 통과 (이력 없음 등)


class IntentType(str, Enum):
    """사용자 의도 유형."""

    DATA_EXTRACTION = "data_extraction"  # 데이터 추출
    DATA_ANALYSIS = "data_analysis"  # 데이터 분석
    CLARIFICATION_NEEDED = "clarification_needed"  # 명확화 필요
    GENERAL_QUESTION = "general_question"  # 일반 질문
    CASUAL_TALK = "casual_talk"  # 일반 대화 (인사, 잡담)
    META_QUESTION = "meta_question"  # 메타 질의 (테이블/시스템 질문)
    UNKNOWN = "unknown"


class QueryStatus(str, Enum):
    """쿼리 처리 상태."""

    PENDING = "pending"
    PREPROCESSING = "preprocessing"
    INTENT_CLASSIFIED = "intent_classified"
    QUERY_NORMALIZED = "query_normalized"  # 질의 정규화 완료
    AWAITING_CLARIFICATION = "awaiting_clarification"  # 명확화 응답 대기 중
    # continue_orchestrator 라우팅 대기 (CONTINUE 판정 + snapshot 존재)
    CONTINUE_ORCHESTRATION_PENDING = "continue_orchestration_pending"
    CONTEXT_COLLECTED = "context_collected"
    SQL_GENERATED = "sql_generated"
    SQL_VALIDATED = "sql_validated"
    SQL_RETRY = "sql_retry"  # SQL 재생성 재시도 중
    EXECUTED = "executed"
    ANALYZED = "analyzed"
    FORMATTED = "formatted"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"


class ContinueRoute(str, Enum):
    """CONTINUE 턴 라우팅 카테고리 (4-way, Path F').

    continue_orchestrator 노드가 판정하여 PipelineState.route에 기록한다.
    하류 노드(visualizer, analyzer, sql_generator, query_normalizer 등)는 이 값과
    `reference_turns`, `handoff_note`를 참조하여 스냅샷 활용 방식을 결정한다.

    모든 route는 하류 노드로만 향하므로 순환 위험이 없다. orchestrator가
    판정에 실패(LLM 파싱 오류·빈 스냅샷)하면 `error_end` 로 즉시 종료하며
    상류 회귀는 수행하지 않는다.

    판정 우선순위(하류 비용 낮은 순):
        REDISPLAY → ANALYZE → REGENERATE → REFINE

    Attributes:
        REDISPLAY: SQL·결과 동일. 시각화 재생성·포맷·단위·엑셀 등 "보이는 방식"만
            변경. visualizer 직행 후 정적 엣지로 formatter 합류.
            대표 스냅샷의 result_data + visualization을 hydrate 후 재렌더.
        ANALYZE: 기존 결과로 분석·해석·인사이트 요청. analyzer 직행 후 정적
            엣지로 visualizer→formatter 합류.
            대표 스냅샷의 result_data를 sql_result로 hydrate 후 SQL 재실행 스킵.
        REGENERATE: 정규화·해석은 동일하고 SQL 표현만 재작성(일시 오류 복구,
            성능 튜닝 힌트, 컬럼 표기 교정 등). sql_generator 직행하여
            정상 DOWNSTREAM 합류. handoff_note는 1섹션(`### SQL 생성 지시`).
            대표 스냅샷의 NormalizedQuery·knowledge_items·query_decomposition·
            target_db를 ReasoningState에 복원(route-agnostic 전량 복원)하여
            reasoning_preparer 없이 SQL 재생성.
        REFINE: 질의 자체를 수정(WHERE/GROUP BY/집계/테이블/기간/컬럼 등).
            query_normalizer 경유로 정상 플로우에 합류하여 정규화부터 재수행.
            handoff_note는 1섹션(`### 연속 처리 의도`). 유일하게 state
            hydration을 건너뛰어 정규화 결과가 스냅샷에 오염되지 않도록 한다.
    """

    REDISPLAY = "redisplay"
    ANALYZE = "analyze"
    REGENERATE = "regenerate"
    REFINE = "refine"


class VisualizationType(str, Enum):
    """시각화 유형 (judgment 프롬프트의 19종 + table_only/none 정렬)."""

    NONE = "none"  # 시각화 불필요
    TABLE_ONLY = "table_only"  # 표만 표시 (legacy)

    # ── 정보 카드 ──
    INFO_CARD = "info_card"  # 단일/소수 KPI

    # ── 정량 차트 (10종) ──
    BAR_CHART = "bar_chart"  # 세로 막대
    HORIZONTAL_BAR = "horizontal_bar"  # 가로 막대 (다수/긴 레이블)
    GROUPED_BAR = "grouped_bar"  # 그룹 막대 (다계열 절대값 비교)
    STACKED_BAR = "stacked_bar"  # 누적 막대 (구성 비율 변화)
    LINE_CHART = "line_chart"  # 꺾은선 (시계열 추이)
    PIE_CHART = "pie_chart"  # 원형 (구성 비율)
    DONUT_CHART = "donut_chart"  # 도넛 (구성 비율 + 중앙 KPI)
    SCATTER_PLOT = "scatter_plot"  # 산점도 (두 연속형 상관관계)
    WATERFALL_CHART = "waterfall_chart"  # 폭포 (증감 누적 분해)
    HEATMAP = "heatmap"  # 히트맵 (격자 빈도/강도)

    # ── 다이어그램 (8종) ──
    FLOWCHART = "flowchart"  # 분기 포함 순서도
    TIMELINE = "timeline"  # 시간순 이벤트
    MIND_MAP = "mind_map"  # 계층 분류
    ORG_CHART = "org_chart"  # 조직도
    PROCESS_DIAGRAM = "process_diagram"  # 선형 프로세스
    VENN_DIAGRAM = "venn_diagram"  # 집합 교집합
    MATRIX_CHART = "matrix_chart"  # 2축 4사분면
    VALUE_CHAIN = "value_chain"  # 가치사슬


class ConfidenceLevel(str, Enum):
    """LLM의 판정 확신도 — float 대신 이산값으로 제한.

    근거: LLM self-calibration 부정확(arXiv 2508.14056),
    모델 교체 시(Solar→Qwen) float 임계값 재튜닝 불필요.
    """

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ConfidenceStatus(str, Enum):
    """지식 항목의 확신도 상태."""

    UNRESOLVED = "UNRESOLVED"    # 미확인
    CANDIDATE = "CANDIDATE"      # 후보 (단일 출처)
    PROBABLE = "PROBABLE"        # 유력 (복수 출처 일치)
    CONFIRMED = "CONFIRMED"      # 확정 (DB 검증 완료)
    CONFLICTED = "CONFLICTED"    # 충돌 (출처 간 불일치)


class FailureType(str, Enum):
    """SQL 생성/검증 실패 유형."""

    NO_KNOWLEDGE = "NO_KNOWLEDGE"            # 지식 항목 없음 (질의 정규화 실패)
    NO_TABLE = "NO_TABLE"                    # 사용 가능 테이블 없음
    TERM_UNRESOLVABLE = "TERM_UNRESOLVABLE"  # 용어 매핑 불가
    SQL_SYNTAX = "SQL_SYNTAX"                # SQL 구문 오류
    SQL_SEMANTIC_LOCAL = "SQL_SEMANTIC_LOCAL"  # 의미 오류 (로컬 수정 가능)
    SQL_STRUCTURAL = "SQL_STRUCTURAL"        # 구조적 오류 (재계획 필요)
    EMPTY_RESULT = "EMPTY_RESULT"            # 빈 결과
    DB_ERROR = "DB_ERROR"                    # DB 실행 오류
    GENERATION_FAILED = "GENERATION_FAILED"  # SQL Generator가 정보 부족으로 생성 거부


class Phase(str, Enum):
    """에이전틱 추론 루프의 진행 단계."""

    PLANNING = "PLANNING"        # 초기 계획 수립
    EXPLORING = "EXPLORING"      # 컨텍스트 탐색
    GENERATING = "GENERATING"    # SQL 생성
    VALIDATING = "VALIDATING"    # SQL 검증
    REPLANNING = "REPLANNING"    # 재계획
    DONE = "DONE"                # 완료


class HypothesisStatus(str, Enum):
    """탐색 가설의 진행 상태."""

    PENDING = "PENDING"    # 대기
    ACTIVE = "ACTIVE"      # 현재 탐색 중
    SUCCESS = "SUCCESS"    # 성공
    FAILED = "FAILED"      # 실패


class StepStatus(str, Enum):
    """실행 스텝의 진행 상태."""

    PENDING = "PENDING"    # 대기
    DONE = "DONE"          # 완료
    SKIPPED = "SKIPPED"    # 스킵
    FAILED = "FAILED"      # 실패


class SelectionStatus(str, Enum):
    """LLM 선택/탈락 판정 상태.

    테이블·매뉴얼·용어사전 등 후보 항목이 SQL 생성이나
    질의 해석에 사용할 만한지 LLM이 판정한 결과를 표현한다.
    """

    PENDING = "PENDING"        # 미판정 (초기)
    SELECTED = "SELECTED"      # LLM이 적합 판정
    REJECTED = "REJECTED"      # LLM이 부적합 판정
    REFERENCE = "REFERENCE"    # use_case SQL 참고용 (LLM 판정 대상 아님)


class FinalStatus(str, Enum):
    """추론 루프의 최종 결과."""

    PENDING = "pending"    # 진행 중
    SUCCESS = "success"    # 성공
    CANCELLED = "cancelled"  # 사용자 취소
    FAILURE = "failure"    # 실패
    AWAITING_CLARIFICATION = "awaiting_clarification"  # 명확화 응답 대기 중


class TargetDbStatus(str, Enum):
    """target_db 결정 상태.

    sql_generator 진입 직전(readiness_gate에서 GENERATING 전이 시)
    target_db_resolver가 결정한 라우팅 상태를 표현한다.
    """

    FORCED = "FORCED"              # settings.target_db_code 강제 지정
    SINGLE = "SINGLE"              # SELECTED 테이블이 단일 DB 소스
    AMBIGUOUS = "AMBIGUOUS"        # 복수 DB 혼재 → 우선순위로 자동 선택
    NO_SELECTION = "NO_SELECTION"  # SELECTED 테이블 없음 → fail
