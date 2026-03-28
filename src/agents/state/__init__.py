"""파이프라인 공유 상태 모듈.

주요 클래스/타입을 패키지 레벨에서 직접 import할 수 있도록 re-export한다.

사용 예시:
    from src.agents.state import (
        PipelineState,
        ReasoningState,
        CandidateTable,
        KeyDateColumn,
        ObservedDateColumn,
    )
"""

from src.agents.state.state import (  # noqa: F401
    CandidateTable,
    ConfidenceStatus,
    DeadEnd,
    ExecutionStep,
    FailureType,
    Hypothesis,
    KeyDateColumn,
    KnowledgeItem,
    LoopGuard,
    MAX_GENERATES,
    MAX_LOCAL_FIXES,
    MAX_REPLANS,
    MAX_TOOL_CALLS,
    ObservedDateColumn,
    Phase,
    PipelineState,
    ReasoningState,
    SqlValidationResult,
    StructuralHints,
    ValidationOverall,
    should_terminate,
)
