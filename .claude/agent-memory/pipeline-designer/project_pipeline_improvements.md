---
name: pipeline_improvements_2026_03
description: SQL 재생성 루프, 멀티턴 명확화, 병렬 컨텍스트 수집 개선 작업 (2026-03-18)
type: project
---

2026-03-18 에 다음 세 가지 파이프라인 개선을 완료했다.

**Why:** 검증 실패 시 무조건 ERROR 종료, 명확화 후 END 에서 단절, 컨텍스트 소스를 순차 호출하는 성능 문제를 해결하기 위함.

**How to apply:** 향후 파이프라인 관련 작업 시 아래 설계 결정을 참고할 것.

---

## 1. SQL 재생성 루프 (최대 2회)

- `state.sql_retry_count`: generate_sql_node 진입 시마다 +1 (최초=1, 재시도=2)
- `state.validation_feedback`: validate_sql_node 가 실패 SQL + 오류 목록을 문자열로 생성
- `_route_after_validation`: sql_validation_errors 있으면 sql_retry_count < SQL_MAX_RETRY(2) 일 때 generate_sql 로 되돌아감
- validate_sql_node 는 검증 실패 시 status=SQL_GENERATED 유지 (ERROR 로 설정하면 conditional_edges 가 호출되지 않음)
- generate_sql_node 는 재진입 시 validation_feedback 을 `## 이전 시도에서 발견된 문제` 섹션으로 프롬프트에 주입

## 2. 멀티턴 명확화

- `state.awaiting_clarification` (bool): clarify_node 가 True 로 설정 후 END
- `state.clarification_response` (str): 챗봇 레이어가 사용자 응답을 여기에 채워 재진입
- `state.clarification_turns` (int): 왕복 횟수. CLARIFICATION_MAX_TURNS(2) 초과 시 collect_context 로 강제 진행
- `preprocess_node`: awaiting_clarification=True + clarification_response 있으면 원래 질의 + 응답을 합성, conversation_history 업데이트, 플래그 초기화
- clarify_node: conversation_history 최근 4턴을 LLM 에 전달하여 중복 질문 방지

## 3. 병렬 컨텍스트 수집

- `search_context_assembler.py`: asyncio.gather 로 5개 코루틴 동시 실행
  - `_fetch_table_metas` (ES)
  - `_fetch_report_sqls` (ES)
  - `_fetch_past_sqls` (이력 DB)
  - `_fetch_manual_refs` (Qdrant)
  - `_fetch_code_meta` (ES 전체 로드)
- 각 fetch 함수는 try/except 로 독립 실패 처리 → 한 소스 장애가 전체를 막지 않음
- 기존 순차 호출 대비 이론적 최대 지연 = 가장 느린 단일 소스 응답 시간

## 변경된 파일

- `src/agents/state/state.py`: QueryStatus.AWAITING_CLARIFICATION, SQL_RETRY 추가; PipelineState 에 sql_retry_count, validation_feedback, awaiting_clarification, clarification_turns 필드 추가
- `src/agents/graph/pipeline.py`: _route_after_validation 재시도 루프 엣지 추가; _route_after_intent 명확화 횟수 초과 처리; validate_sql → generate_sql 루프 엣지
- `src/agents/nodes/preprocessor.py`: _handle_clarification_response 분기 추가
- `src/agents/nodes/clarifier.py`: awaiting_clarification=True 설정; _build_messages 히스토리 포함
- `src/agents/nodes/sql_generator.py`: sql_retry_count 증가; validation_feedback 섹션 주입
- `src/agents/nodes/sql_validator.py`: validation_feedback 생성; 검증 실패 시 status=SQL_GENERATED 유지
- `src/services/search_context_assembler.py`: asyncio.gather 병렬화; 소스별 독립 폴백
