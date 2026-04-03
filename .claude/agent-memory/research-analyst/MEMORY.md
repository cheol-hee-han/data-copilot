# Research Analyst Agent Memory Index

## Project Context

- [project_reranker_optimization.md](project_reranker_optimization.md) — BGE-Reranker-v2-m3 CPU 최적화 리서치 (2026-03-21): FlagEmbedding ONNX 미지원 확인, ONNX O3+INT8이 최적 전략, 보고서 위치 포함
- [project_impyla_kerberos.md](project_impyla_kerberos.md) — impyla + CDP 7.1.9 Kerberos GSSAPI 의존성 확정 (2026-03-23): kerberos>=1.3.0 필수, sasl C 확장 불필요, OS libkrb5 필요, 보고서 위치 포함
- [project_sybase_iq_driver.md](project_sybase_iq_driver.md) — Sybase IQ 16.1 non-ODBC Python 드라이버 확정 (2026-03-23): sqlanydb 공식 권장, dbcapi 네이티브 라이브러리 필요, jconn4는 ASE 전용, 보고서 위치 포함
- [project_sqlglot_parsing.md](project_sqlglot_parsing.md) — SQLGlot 파싱 정확도 리서치 (2026-03-24): find_all(exp.Table) 금지(CTE 오인), error_level=RAISE 필수, Impala→hive 매핑, Sybase IQ→None+regex fallback, 보고서 위치 포함
- [project_graph_db_ontology.md](project_graph_db_ontology.md) — SQL 온톨로지 그래프 DB 선정 (2026-03-25): Neo4j CE(GPLv3) 권고, ArangoDB/Memgraph/FalkorDB BSL·SSPL로 기각, Apache AGE async 미지원 기각, 노드·엣지 모델 설계, 보고서 위치 포함
- [project_qwen35_model_analysis.md](project_qwen35_model_analysis.md) — Qwen3.5-397B-A17B 모델 분석 (2026-03-26): 실존 MoE 모델 확인, GPU 8장 요구 폐쇄망 제약, 한국어 영어편향 취약점, 폐쇄망 실용 후보는 27B·35B-A3B, 보고서 위치 포함
- [project_langgraph_production_patterns.md](project_langgraph_production_patterns.md) — LangGraph graph.compile() 싱글턴 확정 (2026-03-30): 스레드 세이프 불변 객체, FastAPI lifespan 1회 컴파일+checkpointer 사후주입, 폐쇄망 트레이싱은 BaseCallbackHandler+config 주입, 보고서 위치 포함
- [project_langchain_custom_events.md](project_langchain_custom_events.md) — adispatch_custom_event + on_custom_event 확정 (2026-03-30): langchain-core 0.2.15 도입, Python 3.12 config 자동추출, astream_events v2 필수, AsyncCallbackHandler 오버라이드 패턴, 보고서 위치 포함
- [project_langgraph_checkpointer.md](project_langgraph_checkpointer.md) — AsyncPostgresSaver 프로덕션 권고 (2026-03-30): autocommit=True+prepare_threshold=0 필수, interrupt() try/except 금지, stream_mode="messages" 토큰 스트리밍, 보고서 위치 포함
- [project_hitl_clarification_unification.md](project_hitl_clarification_unification.md) — 5개 명확화 트리거 통합 (2026-03-30): clarification_handler 단일 노드+Strategy 패턴, ClarificationRequest 스키마로 UI 명세 포함, return_to로 복귀 지점 명시, 보고서 위치 포함
- [project_clarification_context.md](project_clarification_context.md) — 명확화 컨텍스트 관리 Approach B 확정 (2026-03-30): original_query 불변+ClarificationEntry 누적, Query Rewriting 오류전파로 기각, AmbiSQL 패턴 선택적 적용, 보고서 위치 포함
- [project_clarification_trigger.md](project_clarification_trigger.md) — 명확화 트리거 기준 확정 (2026-03-31): AmbiSQL 7종 분류 체계, 집중형 단일 노드 근거(Sphinteract+42%, EIG 76.6%), SELECT컬럼 모호성→복수SQL, 신뢰도임계값 기각, 보고서 위치 포함
- [project_langgraph_cancel_abort.md](project_langgraph_cancel_abort.md) — LangGraph 실행 중단 패턴 (2026-04-04): asyncio.cancel() 서브그래프 미전파(#5682)·ToolNode 파손(#6726) 버그, interrupt()+thread 방기 권고, 폐쇄망=앱 레벨 플래그 필수
