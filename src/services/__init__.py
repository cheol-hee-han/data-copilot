"""서비스 모듈.

파이프라인 노드가 사용하는 서비스 계층:
  - domain/: 금융 도메인 정적 참조 데이터 (사전, 동의어, 유사 테이블)
  - input_sanitizer: 입력 정제 (정규화, 인젝션 감지, 명확화 합성)
  - intent_resolver: 의도 분류 (Gate, Legacy, 세분류)
  - query_normalizer: 자연어 → 8-Slot 정규화
  - sql_prompt_assembler: SQL 생성 프롬프트 조립 + LLM 호출
  - sql_safety_checker: SQL 안전성 검증 (패턴/PII/구문)
  - data_analyzer: 데이터 분석 + 시각화 생성
  - response_formatter: 결과 보고서 포맷팅
  - search_context_assembler: 병렬 다중 소스 검색 결과 조립·통합
  - search_query_builder: 멀티소스 검색 쿼리 빌더
  - similar_table_resolver: 유사 테이블 구분·검증·추천
  - table_meta_enricher: LLM 기반 테이블 메타 설명 보강
  - search_query_embedder / reranker: 벡터 인프라
"""
