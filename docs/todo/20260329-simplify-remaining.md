# /simplify 잔여 항목

> 2026-03-29 기준. W-01~W-04, W-06~W-07, W-09~W-12, W-14~W-15 적용 완료.

## 보류 항목

### W-05/W-13 | 매직넘버 추출 → config.py 확장

**현황**: reason 노드 전반에 confidence 임계값(0.8, 0.7, 0.5, 0.4), LLM max_tokens(2048, 1024, 512), priority_map 등이 하드코딩.

**합의 방향**:
- LLM max_tokens, confidence 임계값 → `src/config.py` (settings)에 추가 (폐쇄망 모델 전환 시 조정 필요)
- `PRIORITY_SCORE_MAP` → config 성격 아님. 한쪽에서 import하거나 현행 유지

**미결정 사항**: config에 넣을 때 `Field(ge=0.1, le=1.0)` 밸리데이션 범위, 네이밍 규칙

**관련 파일**:
- `src/agents/nodes/reason/confidence_evaluator.py:77` (0.8)
- `src/services/confidence_scorer.py:107` (0.7)
- `src/agents/nodes/reason/context_explorer.py` (0.5, 0.4, 0.85 등 다수)
- `src/agents/nodes/reason/planner.py:520` (priority_map)
- `src/agents/nodes/reason/recovery_planner.py:327` (priority_map 중복)

---

### W-08 | sql_executor validate_sql_safety 이중 방어 정리

**현황**: `sql_executor.py`가 `utils/security.py`의 약한 validate_sql_safety를 호출. `sql_validator` 노드가 이미 `services/sql_safety_checker.py`의 더 강력한 버전을 통과시킨 뒤이므로 실질 방어 효과 없음.

**선택지**:
- A. 동일 함수로 교체 — `utils/security.py` 대신 `services/sql_safety_checker.py` 호출
- B. 제거 + assertion — 중복 삭제, `assert state.reason.validated_sql` + 방어 주석
- C. 현행 유지 — docstring "이중 방어" 명시 유지 (실효성 낮음)

**관련 파일**:
- `src/agents/nodes/present/sql_executor.py:46-48`
- `src/utils/security.py:173` (약한 버전, 8개 검사)
- `src/services/sql_safety_checker.py:212` (강한 버전, 17개 패턴 + PII + LIMIT)
- `src/agents/graph/pipeline.py:266-267` (validated_sql 라우팅 가드)

---

## 완료 항목 (참고)

| 번호 | 내용 | 상태 |
|------|------|------|
| W-01 | `Optional[X]` → `X \| None` | 완료 |
| W-02 | HypothesisStatus/StepStatus → Enum | 완료 |
| W-03 | FinalStatus → Enum | 완료 |
| W-04 | ConfidenceStatus raw string → `.value` | 완료 |
| W-06 | confirmed/dead text 직렬화 → ReasoningState 메서드 | 완료 |
| W-07 | decomp 4슬롯 직렬화 → `serialize_decomp_slots` | 완료 |
| W-09 | `_parse_llm_json` → `extract_json(strict=True)` 통합 | 완료 |
| W-10 | Sybase IQ `TOP N` LIMIT 인식 | 완료 |
| W-11 | tools.py 보일러플레이트 → `_safe_search` 래퍼 | 완료 |
| W-12 | planner 검색 `asyncio.gather` 병렬화 | 완료 |
| W-14 | insight_builder 함수 내부 import → 상단 이동 | 완료 |
| W-15 | planner `hasattr` 과잉 방어 → `NormalizedQuery` 타입 명시 | 완료 |
