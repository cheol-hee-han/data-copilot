---
name: project_context
description: NL-to-SQL 파이프라인 구조, table_meta_enricher 설계 의도, search_context_assembler 병렬 수집 전략
type: project
---

search_context_assembler.py는 4개 소스(ES 테이블 메타, ES 보고서 SQL, 이력 DB, Qdrant)와 코드 메타를 asyncio.gather로 병렬 수집한 뒤, enrich_table_descriptions를 순차 호출하는 2-stage 구조.

table_meta_enricher.py는 불충분한 테이블 설명(길이 < 20자 또는 3가지 관점 미충족)을 LLM으로 보강. 보강 대상 테이블들은 asyncio.gather로 병렬 호출. timeout=15.0 설정.

sql_generator.py는 enriched_description을 _build_table_info에서 "[상세 설명]" 접두어로 주입. validation_feedback 재주입으로 재시도 루프 지원.

state.py: TableMeta.enriched_description 필드가 str = "" 기본값으로 정의됨. Pydantic v2 BaseModel 사용. PipelineState.conversation_history는 list[dict[str, str]]로 Any 없이 정의.

src/agents/nodes/prompts/system_prompts.py: SQL_GENERATION_RULES에서 {validation_feedback_section} 치환자가 [최종 지시] 바로 앞에 위치 — 줄바꿈 없이 붙어 있는 구조적 문제 존재.

**Why:** table_meta_enricher는 v1.2(2026-03-19) 신규 추가된 모듈. search_context_assembler는 enrich 단계 통합으로 수정됨.

**How to apply:** 향후 리뷰 시 enrich 단계가 gather 이후 순차로 실행되는 구조가 의도적임을 인지할 것. enriched_description 주입 경로는 _build_table_info → system_prompt.
