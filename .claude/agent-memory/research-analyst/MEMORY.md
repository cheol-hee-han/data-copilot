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
- [project_langgraph_cancel_abort.md](project_langgraph_cancel_abort.md) — LangGraph 실행 중단 패턴 (2026-04-06 업데이트): #5682·#6726·#6950 전부 미해결, 1.0에 cancel 기능 미추가, 권고 변경 없음(interrupt()+thread 방기+앱 레벨 플래그), 업데이트 보고서: 20260406
- [project_langgraph_parallel_patterns.md](project_langgraph_parallel_patterns.md) — LangGraph 병렬 fan-out/fan-in 패턴 확정 (2026-04-04): 정적 fan-out 권고, Annotated reducer 필수, 비균형 브랜치 서브그래프 캡슐화, defer=True, Send API 활용처
- [project_depends_on_wave_scheduling.md](project_depends_on_wave_scheduling.md) — depends_on+wave scheduling 패턴 확정 (2026-04-04): LLMCompiler(ICML 2024)가 정식 선례, LangGraph 공식은 flat List[str]만 제공, Data Copilot 권고=패턴 A, 보고서 위치 포함
- [project_langgraph_tool_node_patterns.md](project_langgraph_tool_node_patterns.md) — LangGraph tool-as-node 패턴 확정 (2026-04-04): 단일 ToolNode가 공식 권장, Data Copilot은 커스텀 노드(State 직접 갱신), ToolNode v1/v2 차이, 보고서 위치 포함
- [project_conversation_history.md](project_conversation_history.md) — PostgreSQL 대화 이력 하이브리드 2-계층 확정 (2026-04-05): Checkpointer BYTEA 불투명→별도 JSONB 테이블 필수, conversation_turns 월별파티션+audit.agent_actions INSERT-only RLS, 보고서 위치 포함
- [project_multiturn_context_management.md](project_multiturn_context_management.md) — LangGraph+NL2SQL 멀티턴 컨텍스트 관리 (2026-04-16): TurnSnapshot 패턴+Dual-Channel State 권고, CoE-SQL/Track-SQL 근거, observation masking이 LLM summarization보다 우월
- [project_continue_orchestrator.md](project_continue_orchestrator.md) — CONTINUE 오케스트레이터 노드 설계 (2026-04-16): 5개 라우팅 카테고리, SParC 4종 턴 관계 분류, CoE-SQL 편집 체인 기준, LangGraph Command 패턴
