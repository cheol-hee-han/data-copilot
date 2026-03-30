# 코드 전수 검토 리포트 — 통합 인덱스

- **검토 일시**: 2026-03-30
- **검토 대상**: `src/` 디렉토리 전체 (~80 파일), `resources/` 참조
- **검토 관점**: 11가지 코드 품질 관점 (아래 상세)
- **검토 방법**: 전체 코드 3회 정독 기반 수동 리뷰

---

## 검토 관점 (11가지)

1. 의미가 유사한 기능의 중복 구현
2. 런타임에 호출되지 않는 죽은 코드
3. 모듈과 성격이 맞지 않는 코드 배치
4. 비즈니스 역할과 책임이 과하게 섞인 코드
5. 이름이 역할을 설명하지 못하는 코드
6. 인터페이스는 비슷한데 동작 규약이 제각각인 코드
7. 변경 전파 범위가 지나치게 큰 코드
8. 저수준 구현이 비즈니스 로직에 혼재하여 가독성이 떨어지는 코드
9. 공통화가 오히려 과한 코드
10. 예외처리가 미흡하거나 부적절한 코드
11. 전역변수/멤버변수가 과도하거나 관련도가 낮은 코드

---

## 등급별 전체 집계

| 등급 | 건수 | 설명 |
|------|------|------|
| Critical | **9건** | 보안 취약점, 런타임 오동작, 아키텍처 위반 |
| Warning | **24건** | 유지보수성, 일관성, 가독성, 안정성 |
| Info | **9건** | docstring 동기화, 코딩 스타일, 경미한 개선 |
| **합계** | **42건** | |

---

## 상세 문서 목록

| 문서 | 내용 | 건수 |
|------|------|------|
| [01-critical-issues.md](20260330-01-critical-issues.md) | Critical 등급 전체 상세 (보안, 정합성, 성능, 아키텍처) | 9건 |
| [02-duplicate-implementations.md](20260330-02-duplicate-implementations.md) | 중복 구현 상세 (금지 패턴, JSON 파싱, SELECT 검증, 모델, 커넥터) | 7건 |
| [03-dead-code.md](20260330-03-dead-code.md) | 죽은 코드 / 미사용 코드 상세 | 7건 |
| [04-module-placement-and-responsibility.md](20260330-04-module-placement-and-responsibility.md) | 모듈 배치 부적절 + 책임 과다 혼재 (SRP 위반) | 8건 |
| [05-interface-inconsistency.md](20260330-05-interface-inconsistency.md) | 인터페이스 규약 불일치 + 변경 전파 범위 | 6건 |
| [06-naming-and-readability.md](20260330-06-naming-and-readability.md) | 네이밍 / 가독성 상세 | 10건 |
| [07-error-handling-and-variables.md](20260330-07-error-handling-and-variables.md) | 예외처리 미흡 + 변수 관리 | 10건 |

> 일부 이슈는 여러 관점에 해당하여 문서 간 상호 참조(cross-reference)로 연결됨.

---

## Critical 항목 전체 목록 (우선 수정 대상)

| ID | 카테고리 | 위치 | 한줄 요약 |
|----|---------|------|----------|
| C-01 | (보안) | sql_safety_checker + input_sanitizer | 금지 패턴 이중 관리 — 불일치 시 보안 홀 |
| C-02 | (보안) | sql_safety_checker | MASKING_COLUMNS 로드만 하고 검증 미수행 |
| C-03 | (보안) | sql_executor vs sql_validator | validate_sql_safety 이중 구현, 시그니처 불일치 |
| C-04 | (보안) | hive/impala execute_query | params 파라미터 완전 무시 — 파라미터 바인딩 누락 |
| C-05 | (보안) | config.py 비밀 필드 12개 | SecretStr 미사용 — 로그/직렬화 시 평문 노출 |
| C-06 | (정합성) | pipeline.py _handle_error | SQL_MAX_RETRY(2) vs MAX_GENERATES(4) 불일치 |
| C-07 | (아키텍처) | runner.py + pipeline.py | 매 요청마다 그래프 재빌드 — LangGraph 프로덕션 패턴 미준수 |
| C-08 | (아키텍처) | connectors/impl/reranker.py | ML 추론 서비스가 커넥터 패키지에 위치 |
| C-09 | (보안) | seed_sql_history.py | LIMIT/OFFSET f-string 삽입 |

---

## Warning 항목 전체 목록 (계획적 개선 대상)

| ID | 카테고리 | 위치 | 한줄 요약 |
|----|---------|------|----------|
| D-02 | (유지보수성) | data_analyzer + response.py | JSON 코드펜스 추출 로직 중복 |
| D-03 | (유지보수성) | 4개 커넥터 | SELECT 검증 정규식 4곳 하드코딩 |
| D-04 | (유지보수성) | state.py + context.py | ColumnInfo / ColumnMeta 유사 모델 중복 |
| D-05 | (유지보수성) | hive/impala/sybase | 커넥터 공통 코드 대규모 중복 |
| Z-01 | (유지보수성) | context_explorer.py | 레거시 함수 4개 미호출 |
| Z-04 | (유지보수성) | runner.py | _emit_progress 직접 호출 경로 불명확 |
| M-03 | (유지보수성) | planner.py | 도메인 판정 상수가 노드에 하드코딩 |
| R-01 | (유지보수성) | context_explorer.py 1,171줄 | 6-Phase + 날짜탐지 + 배치해석 단일 파일 |
| R-02 | (유지보수성) | qdrant_connector.py | 벡터검색 + 임베딩 + 리랭킹 3가지 책임 |
| R-03 | (유지보수성) | query_normalizer.py 662줄 | 검증 + 후처리 + LLM 호출 혼재 |
| R-04 | (아키텍처) | client.py → thinking_modes | utils에서 agents 계층 import (레이어 역전) |
| R-05 | (일관성) | clarifier, recovery_planner | 서비스 레이어 없이 노드에서 LLM 직접 호출 |
| I-02 | (일관성) | 4곳 LLM 직접 호출 | llm_call_with_parse_retry 미사용 |
| N-03 | (실수방지) | system_prompts.py | docstring 파일 매핑과 실제 불일치 |
| E-01 | (안정성) | redis_store.py | connect 전 호출 시 AttributeError |
| E-02 | (안정성) | retry.py | LLM 예외 시 노드 컨텍스트 복원 누락 |
| E-03 | (안정성) | neo4j_connector.py | 인메모리 캐시 크기 제한 없음 |
| E-04 | (안정성) | seed_sql_history.py | DB 엔진 타임아웃 미설정 |
| E-05 | (안정성) | HistoryDBConnector | SELECT 검증 없음 — 의도 불명확 |
| P-01 | (유지보수성) | config.py 250줄 | 단일 클래스에 모든 설정 집중 |
| V-01 | (실수방지) | state.py | 모듈 레벨 상수가 import 시점 고정 |
| V-03 | (안정성) | reranker.py | 전역 환경변수 런타임 변경 |
| V-04 | (유지보수성) | config.py | model_config extra 정책 미설정 |
| N-04 | (실수방지) | confidence_scorer.py | docstring 가중치와 코드 가중치 불일치 |

---

## Info 항목 전체 목록 (참고/경미)

| ID | 카테고리 | 위치 | 한줄 요약 |
|----|---------|------|----------|
| Z-02 | (유지보수성) | sql_generator.py | `import time` 미사용 |
| Z-05 | (유지보수성) | services/__init__.py | docstring에 삭제된 모듈 나열 |
| Z-06 | (유지보수성) | query_normalizer.py | 빈 섹션 주석 잔존 |
| Z-07 | (유지보수성) | nodes/__init__.py | docstring 디렉토리명 불일치 |
| N-01 | (실수방지) | nodes/__init__.py | agentic/ → reason/ |
| N-02 | (실수방지) | thinking_modes.py | query_normalizer 계층 분류 오류 |
| I-04 | (일관성) | 노드별 tracker import | 지연 vs 상단 import 혼재 |
| M-02 | (가독성) | insight_builder.py | 범용 유틸이 서비스에 매몰 |
| D-06 | (가독성) | chart_generator.py | 레이아웃 상수 3곳 반복 |

---

## 권장 수정 순서

### Phase 1: 보안/정합성 (즉시)
1. **C-01** 금지 패턴 단일화 → `utils/security.py`
2. **C-03** validate_sql_safety SSOT 통합
3. **C-04** Hive/Impala params 바인딩 구현
4. **C-05** SecretStr 적용 (12개 필드)
5. **C-02** MASKING_COLUMNS 검증 로직 추가
6. **C-06** _handle_error 상수 통일
7. **C-09** seed 스크립트 파라미터 바인딩

### Phase 2: 런타임 안정성 (1주 이내)
1. **C-07** connect_if_needed + app 캐싱
2. **E-02** retry.py try/finally 패턴
3. **E-01** RedisSessionStore 방어 가드
4. **E-03** Neo4j 캐시 maxsize 제한

### Phase 3: 아키텍처/구조 개선 (2주 이내)
1. **C-08** reranker.py → services/ 이동
2. **R-04** thinking_modes 레이어 역전 해소
3. **D-05** BaseSyncDatabaseConnector 추출
4. **D-03** validate_readonly_query 유틸 추출
5. **I-02** LLM 호출 방식 통일

### Phase 4: 리팩토링 (마일스톤 단위)
1. **R-01** context_explorer 3개 모듈 분리
2. **R-03** query_normalizer 검증/후처리 분리
3. **R-05** clarifier/recovery_planner 서비스 레이어 신설
4. **P-01** config.py nested model 분리
5. **D-04** ColumnInfo/ColumnMeta 통합

### Phase 5: 정리 (수시)
- Z-01~Z-07: 죽은 코드/docstring 정리
- N-01~N-04: 네이밍/주석 동기화
- Info 항목 전체
