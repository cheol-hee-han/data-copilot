# Data Copilot 프로젝트 추진 전략 (v2)

> **v2 변경 사항**: 1차 구현 경험과 design-critic 검토 결과를 반영하여
> 서브에이전트 간 협업 구조, 교차 검증 게이트, 병렬 작업 흐름을 전면 재설계함.

## 접근법: Walking Skeleton → 점진적 확장 + 서브에이전트 협업 루프

AI 에이전트 프로젝트는 불확실성이 크기 때문에
**가장 얇은 end-to-end 흐름을 먼저 만들고**, 반복적으로 두껍게 만드는 방식으로 추진한다.

### v1에서 얻은 교훈

| 교훈 | 원인 | v2 반영 |
|------|------|---------|
| 서브에이전트 16종 중 1종만 활용됨 | Phase별 투입 시점만 명시하고 협업 흐름이 없었음 | 각 Phase에 에이전트 간 입출력 의존관계를 명시 |
| 여러 에이전트가 같은 파일을 동시 수정하여 충돌 발생 | 병렬 실행 시 작업 범위 분리가 없었음 | 파일 소유권(ownership) 규칙과 순차/병렬 구분을 명시 |
| 에이전트 결과물의 통합 검증이 없었음 | 개별 에이전트 완료 후 cross-check 절차 부재 | 각 Phase 종료 시 **게이트 리뷰** 단계 추가 |
| design-critic의 지적사항이 이미 다른 에이전트가 해결한 경우 있음 | design-critic이 가장 먼저 실행되지 않았음 | **설계 → 비판 → 구현 → 검증** 순서를 강제 |

---

## 서브에이전트 역할 정의

### 설계 그룹 (Design)

| 에이전트 | 핵심 역할 | 산출물 |
|---------|----------|--------|
| **project-planner** | 전체 마일스톤, 에이전트 간 작업 조율 | project-plan.md |
| **pipeline-designer** | LangGraph 그래프 설계, 노드·엣지·상태 정의 | pipeline.py, state.py |
| **design-critic** | 설계안 비판적 검토, 실패 시나리오 도출 | design-review.md |

### 구현 그룹 (Build)

| 에이전트 | 핵심 역할 | 산출물 |
|---------|----------|--------|
| **nl-sql-developer** | LangGraph 노드 구현, SQL 생성 로직 | src/agents/nodes/*.py |
| **api-integrator** | MongoDB·PostgreSQL·Qdrant·Neo4j·Redis 커넥터 구현 (2026-04 ES 제거) | src/connectors/*.py |
| **schema-architect** | DB 스키마 분석, 메타데이터 문서화, 유사 테이블 구분 규칙 | 스키마 문서, MongoDB 스키마 매핑 |

### 지식 그룹 (Knowledge)

| 에이전트 | 핵심 역할 | 산출물 |
|---------|----------|--------|
| **domain-researcher** | 금융 도메인 용어·계수산출식·업무 프로세스 조사 | finance_terms.py |
| **prompt-engineer** | LLM 프롬프트 설계, Few-shot 예제, 소형 LLM 최적화 | src/agents/nodes/prompts/system_prompts.py |

### 검증 그룹 (Verify)

| 에이전트 | 핵심 역할 | 산출물 |
|---------|----------|--------|
| **test-generator** | 골든셋·엣지 케이스·통합 테스트 작성 | evaluation/, tests/ |
| **sql-evaluator** | 골든셋 기반 SQL 정확도 측정, 회귀 테스트 | 평가 보고서 |
| **security-guard** | SQL 인젝션·프롬프트 인젝션·PII 취약점 검증 | security-audit.md |
| **code-reviewer** | 코드 품질, 에러 처리, 타입 안전성 검토 | 리뷰 코멘트 |
| **benchmark-runner** | 응답시간·비용·처리량 벤치마크 | 벤치마크 보고서 |

### 고도화 그룹 (Enhance)

| 에이전트 | 핵심 역할 | 산출물 |
|---------|----------|--------|
| **data-analyst** | 분석 로직 (통계·추이·이상치 탐지) | src/agents/nodes/ (analyzer) |
| **output-formatter** | 결과 포맷팅 (보고서·엑셀·시각화) | formatter.py |
| **doc-writer** | 설계 문서, API 문서, 운영 가이드 | docs/ |

---

## Phase 0: 프로젝트 셋업 + 설계 (토대)

```
project-planner ──→ pipeline-designer ──→ design-critic
     │                    │                     │
 마일스톤 정의      그래프 설계 초안        설계 비판 보고서
 에이전트 역할 배분   State 스키마 정의      실패 시나리오 도출
                    노드 입출력 계약       대안 비교·권고
```

### 작업 흐름 (순차)

**Step 0-1: project-planner** (단독)
- [ ] 프로젝트 디렉토리 구조 생성
- [ ] pyproject.toml, .gitignore, .env.example
- [ ] 기본 로깅·설정 모듈
- [ ] 마일스톤 정의, Phase별 산출물 명확화

**Step 0-2: pipeline-designer** (← project-planner 완료 후)
- [ ] PipelineState 스키마 설계 (전 노드가 공유하는 상태)
- [ ] LangGraph 그래프 초안 (노드 + 엣지 + 조건부 분기)
- [ ] 각 노드의 입출력 계약 정의 (인터페이스만, 구현 없음)
- [ ] SQL 재생성 루프·멀티턴 명확화 설계 포함

**Step 0-3: design-critic** (← pipeline-designer 완료 후)
- [ ] 설계안의 암묵적 가정 검증
- [ ] 실패 시나리오 5개 이상 도출
- [ ] 폐쇄망 로컬 LLM 전환 리스크 분석
- [ ] 대안 비교 (Self-Correction vs 분해 생성 vs 템플릿 하이브리드)
- [ ] 산출물: `docs/design-review.md`

**Step 0-4: pipeline-designer** (← design-critic 피드백 반영)
- [ ] design-critic 권고사항 중 수용 항목 반영하여 설계 확정
- [ ] 산출물: 확정된 `pipeline.py`, `state.py`

### 게이트 0: 설계 확정 리뷰
- [ ] design-critic의 P0/P1 지적사항이 모두 해소되었는지 확인
- [ ] 설계 문서(architecture.md)와 코드가 일치하는지 확인
- **통과 조건**: P0 지적사항 0건, P1 지적사항 해소 계획 수립

**산출물**: 확정된 설계 + `pip install -e .`가 되는 빈 프로젝트

---

## Phase 1: Walking Skeleton + 도메인 기반 (뼈대)

```
        ┌──── 병렬 트랙 A ────┐   ┌──── 병렬 트랙 B ────┐
        │                      │   │                      │
  nl-sql-developer        domain-researcher      prompt-engineer
        │                      │                      │
  노드 Stub 구현         금융 용어 사전 구축     시스템 프롬프트 설계
  LLM 연동 (의도분류,    동의어·코드값 매핑     Few-shot 예제
   SQL생성)              계수산출식 조사        소형 LLM 대응 전략
        │                      │                      │
        └──────────┬───────────┘                      │
                   │                                   │
            nl-sql-developer ←─── prompt-engineer 산출물 통합
                   │
           Walking Skeleton 완성
                   │
              test-generator ──→ security-guard
                   │                    │
            기본 테스트 작성       입력 전처리 보안 검증
```

### 병렬 트랙 A: 파이프라인 구현

**Step 1-A1: nl-sql-developer** (← Phase 0 설계 확정 후)
- [ ] 각 노드를 Stub으로 구현 (입출력 계약만, 내부는 Mock)
- [ ] LLM 연동: 의도 분류 노드 (Claude API)
- [ ] LLM 연동: SQL 생성 노드 (Claude API)
- [ ] LLM 연동: 명확화 질문 노드
- [ ] SQL 검증 노드 (정규식 + sqlglot)
- [ ] SQL 재생성 루프 구현 (validate → generate 역방향 엣지)
- [ ] 멀티턴 명확화 상태 관리 구현
- [ ] **파일 소유권**: `src/agents/nodes/*.py`, `src/agents/graph/*.py`

### 병렬 트랙 B: 도메인 지식 구축

**Step 1-B1: domain-researcher** (← Phase 0 완료 후, nl-sql-developer와 병렬)
- [ ] 금융 도메인 용어 사전 구축 (목표: 100개 이상)
  - 카테고리: 고객, 여신, 수신, 거래, 카드, 외환, 경영지표, 자산건전성, 조직, 시간
  - 각 용어: term, aliases, table_name, column_name, condition, description
- [ ] 계수산출식 조사 (연체율, BIS비율, NIM, LCR 등)
- [ ] 유사 테이블 구분 기준 정리
- [ ] 기준일자·영업일·금액단위 규칙 정리
- [ ] **파일 소유권**: `src/services/domain/finance_terms.py`

**Step 1-B2: prompt-engineer** (← Phase 0 완료 후, nl-sql-developer와 병렬)
- [ ] 마스터 프롬프트 저장소 구축 (`src/agents/nodes/prompts/system_prompts.py`)
- [ ] 각 노드별 프롬프트 설계 (의도분류, 명확화, SQL생성, 분석, 포맷팅)
- [ ] Few-shot 예제 작성 (노드당 2~3개)
- [ ] Chain-of-Thought 사고 과정 설계
- [ ] 소형 LLM(GPT-3.5급) 대응 축약 프롬프트 세트 별도 작성
- [ ] **파일 소유권**: `src/agents/nodes/prompts/system_prompts.py`

### 통합 (← 트랙 A, B 모두 완료 후)

**Step 1-C1: nl-sql-developer** (← domain-researcher + prompt-engineer 산출물 수신)
- [ ] 도메인 사전을 SQL 생성 프롬프트에 통합
- [ ] 프롬프트를 마스터 저장소에서 import하도록 노드 수정
- [ ] end-to-end 동작 확인: `python -m src.agents.graph.runner "이번 달 신규 고객 수"`

### 검증 (← 통합 완료 후)

**Step 1-D1: test-generator** (← Walking Skeleton 완성 후)
- [ ] 골든셋 초안 구축 (30건: easy 10 / medium 12 / hard 8)
- [ ] 단위 테스트 작성 (전처리, 검증, 도메인 사전, 보안)
- [ ] 통합 테스트 작성 (LLM Mock 기반 E2E)
- [ ] **파일 소유권**: `evaluation/`, `tests/`

**Step 1-D2: security-guard** (← test-generator와 병렬)
- [ ] 입력 전처리 보안 검증 (SQL 인젝션 우회 시뮬레이션)
- [ ] 프롬프트 인젝션 방어 검증 (영어 + 한국어 패턴)
- [ ] 유니코드 정규화 우회 검증
- [ ] 산출물: `docs/reviews/design/20260321-security-audit.md`
- [ ] **파일 소유권**: `src/utils/security.py`, `src/agents/nodes/interpret/preprocessor.py`(보안 부분만)

### 게이트 1: Walking Skeleton 검증
- [ ] `python -m src.agents.graph.runner "이번 달 신규 고객 수"` → Mock 결과 반환
- [ ] 단위 테스트 전체 통과
- [ ] security-guard P0 지적사항 0건
- [ ] design-critic이 확인한 P1 항목 중 "SQL 재생성 루프", "멀티턴 명확화"가 구현됨
- **통과 조건**: E2E 동작 + 테스트 통과 + 보안 P0 해소

---

## Phase 2: 외부 시스템 커넥터 연동

```
  schema-architect ──→ api-integrator ──→ nl-sql-developer
        │                    │                   │
  DB 스키마 분석       커넥터 클래스 구현    Mock → 실제 커넥터 교체
  메타데이터 문서화     Dummy/실제 모드      컨텍스트 수집 병렬화
  유사 테이블 구분      연결 풀 최적화       (asyncio.gather)
        │                    │
  domain-researcher    code-reviewer
  (스키마 기반 사전 보강)  (커넥터 코드 품질 검토)
```

### 작업 흐름

**Step 2-1: schema-architect** (단독, 선행)
- [ ] 정보계 DB 스키마 분석 (테이블 목록, 컬럼 정의, FK 관계)
- [ ] 유사 테이블 구분 규칙 문서화 (TB_LOAN_INFO vs TB_LOAN_OVERDUE_STAT 등)
- [ ] MongoDB 메타 스키마 설계 (2026-04 ES→MongoDB)
- [ ] 코드 메타 매핑 (코드 필드별 코드값 정의)
- [ ] **파일 소유권**: 스키마 문서, `src/connectors/impl/mongo_connector.py` (Dummy 데이터 부분)

**Step 2-2: api-integrator** (← schema-architect 완료 후)
- [ ] PostgreSQL 커넥터 (정보계 읽기 전용 + SQL 이력 + 체크포인터)
- [ ] MongoDB 커넥터 (테이블/컬럼/코드/용어사전 메타, 2026-04 ES 대체)
- [ ] Qdrant 커넥터 (업무 매뉴얼 + SQL 이력 하이브리드 검색)
- [ ] Neo4j 커넥터 (온톨로지 그래프)
- [ ] Redis 커넥터 (캐시 - 동일 질의 캐싱)
- [ ] ConnectorManager 통합 관리
- [ ] Dummy/실제 모드 전환 (설정 파일만으로 전환 가능)
- [ ] **파일 소유권**: `src/connectors/*.py`

**Step 2-3 (병렬):**

| 에이전트 | 작업 | 의존 |
|---------|------|------|
| **nl-sql-developer** | Mock → 실제 커넥터 교체, 병렬 컨텍스트 수집 | ← api-integrator |
| **domain-researcher** | 실제 스키마 기반 도메인 사전 보강 | ← schema-architect |
| **code-reviewer** | 커넥터 코드 품질·에러 처리 검토 | ← api-integrator |

### 게이트 2: 커넥터 연동 검증
- [ ] 모든 커넥터 헬스체크 통과 (Dummy 모드)
- [ ] 컨텍스트 수집이 5개 소스를 병렬로 수집하고 결과 통합
- [ ] 개별 소스 장애 시 나머지 소스로 폴백 동작 확인
- [ ] code-reviewer 검토 완료, 미해소 이슈 없음
- **통과 조건**: 커넥터 통합 테스트 통과 + 폴백 동작 확인

---

## Phase 3: 정확도 향상 (핵심 도전)

```
                ┌─────────── 반복 루프 (3~5회) ───────────┐
                │                                          │
  prompt-engineer ←──── sql-evaluator ←──── test-generator │
        │                    │                    │        │
  프롬프트 개선        골든셋 평가 실행      골든셋 확장     │
  (실패 패턴 기반)     정확도 측정          엣지 케이스 추가  │
        │                    │                    │        │
        ▼                    ▼                    ▼        │
  nl-sql-developer ←── domain-researcher                   │
        │                    │                             │
  노드 로직 개선        도메인 사전 보강                     │
  (실패 케이스 대응)    (미등록 용어 추가)                    │
        │                                                  │
        └────────────── 다음 반복 ─────────────────────────┘

  [매 반복 종료 시]
  benchmark-runner → 응답시간·정확도 벤치마크
  design-critic → 개선 방향 검토
```

### 반복 개선 루프 (목표: 골든셋 정확도 ≥ 80%)

**반복 1회의 구조:**

| 순서 | 에이전트 | 작업 | 입력 | 출력 |
|------|---------|------|------|------|
| 1 | **sql-evaluator** | 현재 상태 골든셋 평가 실행 | 골든셋 + 현재 프롬프트 | 평가 보고서 (정확도, 실패 패턴) |
| 2 | **prompt-engineer** | 실패 패턴 분석 → 프롬프트 개선 | 평가 보고서 | 개선된 프롬프트 |
| 3 | **domain-researcher** | 실패 케이스에서 미등록 용어 추출 → 사전 보강 | 평가 보고서 | 보강된 도메인 사전 |
| 4 | **nl-sql-developer** | 실패 케이스 대응 로직 구현 | 평가 보고서 + 개선 프롬프트 | 개선된 노드 로직 |
| 5 | **test-generator** | 실패한 유형의 엣지 케이스 추가 | 평가 보고서 | 확장된 골든셋 |
| 6 | **benchmark-runner** | 성능 벤치마크 | 개선된 파이프라인 | 벤치마크 보고서 |
| 7 | **design-critic** | 개선 방향 적절성 검토 | 전체 반복 결과 | 다음 반복 권고사항 |

### 구체 작업

**Step 3-1: 초기 평가 (sql-evaluator)**
- [ ] 골든셋 30건 전체 평가 실행
- [ ] 4차원 정확도 측정 (의도분류, 테이블선택, SQL패턴, 구문검증)
- [ ] Execution Accuracy 도입 (Dummy DB에서 기대SQL vs 생성SQL 결과 비교)
- [ ] 실패 패턴 분류 (테이블 오선택, 조건 누락, 집계 오류, 코드값 오류 등)

**Step 3-2: 프롬프트 튜닝 (prompt-engineer ← sql-evaluator)**
- [ ] 실패 패턴별 Few-shot 예제 추가
- [ ] CoT 사고 과정 보강 (STEP별 판단 근거 출력)
- [ ] 소형 LLM용 축약 프롬프트 동시 업데이트
- [ ] 컨텍스트 윈도우 토큰 예산 관리 (소형 모델 4K 이내)

**Step 3-3: 도메인 사전 보강 (domain-researcher ← sql-evaluator)**
- [ ] 미등록 용어 추가 (실패 케이스에서 추출)
- [ ] 유사 테이블 구분 힌트 보강
- [ ] 기준일자, 금액 단위, 영업일 규칙 반영
- [ ] 코드값 매핑 검증 (실제 DB 코드값과 일치하는지)

**Step 3-4: 노드 로직 개선 (nl-sql-developer)**
- [ ] intent_confidence 임계값(< 0.7) 라우팅 구현
- [ ] 테이블별 필수 조건 검사 (TB_TRANSACTION → TXN_DT 필수)
- [ ] 한국어 숫자 표현 변환 ("삼천만원" → 30000000)
- [ ] 부정형 표현 처리 ("연체가 아닌" → OVERDUE_YN = 'N')

### 게이트 3: 정확도 목표 달성
- [ ] 골든셋 정확도 ≥ 80% (의도분류 ≥ 90%, 테이블선택 ≥ 85%)
- [ ] 보안 회귀 없음 (security-guard 재검증)
- [ ] 응답시간 p95 ≤ 15초 (benchmark-runner 측정)
- **통과 조건**: 3개 지표 모두 충족

---

## Phase 4: 데이터 분석 + 결과 포맷팅 + 자동 시각화

```
  data-analyst ──→ output-formatter ──→ nl-sql-developer
       │                  │                   │
  분석 로직 구현     포맷팅 로직 구현     파이프라인 통합
  통계·추이·이상치   금액 단위 변환       분석 분기 추가
  LLM 분석 노드     코드값→한국어 변환    조건부 엣지
  시각화 판단+생성   SVG 렌더링·다운로드   시각화 데이터 전달
       │                  │
  prompt-engineer    design-critic
  분석+시각화 프롬프트  분석·시각화 설계 검토
```

### 작업 흐름

**Step 4-1 (병렬):**

| 에이전트 | 작업 | 파일 소유권 |
|---------|------|-----------|
| **data-analyst** | 통계 엔진 (요약통계, 추이감지, Z-score 이상치, 비교분석), 템플릿 차트 생성기 (bar/line/pie SVG) | `src/agents/nodes/ (analyzer)` |
| **output-formatter** | 금액 단위 변환, 코드값→한국어, 마크다운 표, 엑셀 내보내기, 프론트엔드 SVG 렌더링·새니타이징 | `src/agents/nodes/present/formatter.py`, `src/main.py` (HTML 부분) |
| **prompt-engineer** | 분석 프롬프트 최적화, action_items 도출, 시각화 판단(VISUALIZATION_JUDGMENT) + SVG 생성(VISUALIZATION_SVG_GENERATION) 프롬프트 | `src/agents/nodes/prompts/system_prompts.py` |

**Step 4-2: nl-sql-developer** (← 4-1 완료 후)

- [x] 분석 노드를 파이프라인에 통합 (조건부 분기)
- [x] 분석 결과 + 통계 엔진 결과를 LLM 프롬프트에 동시 주입
- [x] 100행 샘플 + 전체 통계를 LLM에 전달하는 구조 구현
- [x] 시각화 판단 → LLM SVG 생성 → 템플릿 폴백 3단계 하이브리드 로직
- [x] PipelineResult에 VisualizationData 전달 경로 추가
- [x] WebSocket/REST 응답에 visualization 필드 추가

**Step 4-3: design-critic** (← 4-2 완료 후)

- [ ] 분석 흐름의 데이터 누락 위험 검토 (100행 제한 문제 등)
- [ ] 분석 결과의 정합성 검증 방안 검토
- [ ] 시각화 SVG 보안 (XSS 벡터) 검토

### 게이트 4: 분석 + 시각화 기능 검증

- [x] "연체율 추이 분석해줘" → 인사이트 + 통계 + action_items 포함 응답
- [ ] "대출 유형별 비교 분석해줘" → SVG 차트가 프론트엔드에 렌더링
- [x] 분석 골든셋 5건 통과
- [x] 차트 생성기 단위 테스트 28건 통과 (보안·기능·엣지케이스)
- **통과 조건**: 분석 응답에 summary + insights + statistics 포함 + 적절한 차트 자동 생성

---

## Phase 5: 챗봇 UI + 서비스화 + 보안 강화

```
  nl-sql-developer ──→ security-guard ──→ doc-writer
       │                     │                │
  FastAPI 서버          최종 보안 감사      운영 문서 작성
  WebSocket 챗봇        전체 파이프라인     API 문서
  세션 관리             공격 시뮬레이션     배포 가이드
       │                     │
  code-reviewer         benchmark-runner
  전체 코드 품질 검토    최종 성능 벤치마크
```

### 작업 흐름

**Step 5-1: nl-sql-developer**
- [ ] FastAPI + WebSocket 서버
- [ ] 챗봇 프론트엔드 HTML
- [ ] 멀티턴 세션 관리 (Redis 기반)
- [ ] REST API 엔드포인트 (WebSocket 대안)
- [ ] 사용자 인증/권한 연동 인터페이스

**Step 5-2 (← 5-1 완료 후, 병렬):**

| 에이전트 | 작업 |
|---------|------|
| **security-guard** | 최종 보안 감사 (전체 파이프라인 공격 시뮬레이션) |
| **code-reviewer** | 전체 코드 품질 검토 (보안·성능·타입·에러처리) |
| **benchmark-runner** | 최종 성능 벤치마크 (동시 사용자, 응답시간, 메모리) |

**Step 5-3: doc-writer** (← 5-2 완료 후)
- [ ] 아키텍처 문서 최종 업데이트
- [ ] API 문서 (OpenAPI/Swagger)
- [ ] 운영 가이드 (배포, 모니터링, 장애 대응)
- [ ] 도메인 사전 관리 가이드

### 게이트 5: 서비스 출시 준비
- [ ] security-guard 최종 감사 P0 0건
- [ ] 동시 사용자 10명 기준 p95 응답시간 ≤ 20초
- [ ] 운영 문서 완비
- **통과 조건**: 보안 P0 해소 + 성능 기준 충족 + 문서 완비

---

## 서브에이전트 협업 매트릭스

### 정보 흐름 (누가 누구에게 무엇을 전달하는가)

```
  domain-researcher ─── 도메인 사전 ──────→ prompt-engineer
         │                                       │
         │              도메인 사전 ──────→ nl-sql-developer
         │                                       │
  schema-architect ─── 스키마 문서 ──────→ api-integrator
         │                                       │
         │              스키마 문서 ──────→ domain-researcher
         │                                       │
  sql-evaluator ────── 평가 보고서 ──────→ prompt-engineer
         │                                       │
         │              평가 보고서 ──────→ domain-researcher
         │                                       │
         │              평가 보고서 ──────→ nl-sql-developer
         │                                       │
  design-critic ────── 설계 리뷰 ────────→ pipeline-designer
         │                                       │
         │              설계 리뷰 ────────→ nl-sql-developer
         │                                       │
  security-guard ───── 보안 감사 ────────→ nl-sql-developer
         │                                       │
         │              보안 감사 ────────→ code-reviewer
```

### 파일 소유권 규칙 (충돌 방지)

동시에 같은 파일을 수정하면 충돌이 발생한다.
**한 파일의 소유자는 한 에이전트만** 가질 수 있다.

| 파일/디렉토리 | 소유 에이전트 | 수정 요청 방법 |
|-------------|-------------|--------------|
| `src/agents/graph/pipeline.py` | pipeline-designer | 다른 에이전트는 설계 리뷰로 요청 |
| `src/agents/state/state.py` | pipeline-designer | 필드 추가 요청 시 의존관계 명시 |
| `src/agents/nodes/*.py` | nl-sql-developer | security-guard는 보안 부분만 수정 가능 |
| `src/connectors/*.py` | api-integrator | schema-architect는 Dummy 데이터만 수정 |
| `src/services/domain/finance_terms.py` | domain-researcher | 단독 소유 |
| `src/agents/nodes/prompts/system_prompts.py` | prompt-engineer | 단독 소유 |
| `src/utils/security.py` | security-guard | 단독 소유 |
| `src/agents/nodes/present/analyzer.py` | data-analyst | 단독 소유 |
| `evaluation/`, `tests/` | test-generator | sql-evaluator는 평가 실행만 |
| `docs/*.md` | doc-writer | 다른 에이전트는 초안만 작성 가능 |

### 병렬/순차 실행 규칙

```
[같은 파일을 건드리지 않는 에이전트들] → 병렬 실행 가능
[A의 산출물이 B의 입력인 경우]       → 순차 실행 (A → B)
[같은 파일을 수정하는 경우]          → 순차 실행 (소유자 우선)
[검증 에이전트들]                    → 구현 완료 후 병렬 실행
```

---

## Phase 간 관계 (v2)

```
Phase 0 ──→ Phase 1 ──→ Phase 2 ──→ Phase 3 ──→ Phase 4 ──→ Phase 5
설계+토대    뼈대+도메인   커넥터연동   정확도향상   분석기능    서비스화
  │            │            │          │           │          │
  │            │            │     ┌────┴────┐      │          │
  │            │            │     │반복 루프 │      │          │
  │            │            │     │3~5회    │      │          │
  │            │            │     └─────────┘      │          │
  Gate0       Gate1        Gate2      Gate3       Gate4      Gate5
 설계확정    E2E동작     커넥터통합   정확도≥80%  분석기능    출시준비
```

**핵심 원칙:**
1. **Phase 0을 충실히** — 설계가 확정되어야 모든 에이전트가 같은 방향으로 작업 가능
2. **Phase 1에서 도메인 지식을 병렬 구축** — v1에서 도메인 사전이 나중에 추가되어 통합 비용 발생
3. **Phase 3의 반복 루프가 핵심** — sql-evaluator → prompt-engineer → domain-researcher → nl-sql-developer 루프를 3~5회 반복
4. **매 Phase 종료 시 게이트 리뷰** — design-critic + security-guard + code-reviewer가 교차 검증
5. **파일 소유권 엄격 준수** — 동시 수정 충돌 방지

---

## 서브에이전트 투입 시점 (v2)

| Phase | 설계 그룹 | 구현 그룹 | 지식 그룹 | 검증 그룹 | 고도화 그룹 |
|-------|----------|----------|----------|----------|-----------|
| **0** | project-planner → pipeline-designer → **design-critic** | - | - | - | - |
| **1** | pipeline-designer (피드백 반영) | **nl-sql-developer** | **domain-researcher**, **prompt-engineer** | test-generator, **security-guard** | - |
| **2** | - | nl-sql-developer, **api-integrator** | domain-researcher (보강) | **code-reviewer** | - |
| **3** | **design-critic** (매 반복) | nl-sql-developer | domain-researcher, prompt-engineer | **sql-evaluator**, test-generator, **benchmark-runner** | - |
| **4** | design-critic | nl-sql-developer | prompt-engineer | test-generator | **data-analyst**, **output-formatter** |
| **5** | - | nl-sql-developer | - | security-guard, code-reviewer, benchmark-runner | **doc-writer** |

**굵은 글씨** = 해당 Phase에서 처음 투입되는 에이전트

### v1 대비 변경점

| 변경 | 이유 |
|------|------|
| Phase 0에 design-critic 추가 | 설계 단계에서 비판적 검토를 거쳐야 구현 단계 재작업 최소화 |
| Phase 1에서 도메인 지식 구축을 병렬화 | v1에서 Phase 3까지 도메인 사전이 30개뿐이어서 정확도가 낮았음 |
| Phase 3에 반복 루프 도입 | sql-evaluator 평가 → 개선 → 재평가 사이클이 정확도 향상의 핵심 |
| Phase별 게이트 리뷰 추가 | v1에서 에이전트 결과물 간 불일치(설계문서 vs 코드)가 발생 |
| 파일 소유권 규칙 도입 | v1에서 6개 에이전트 동시 실행 시 같은 파일 수정으로 충돌 발생 |
| schema-architect를 Phase 2로 이동 | Dummy 모드에서는 불필요, 실제 DB 연동 시 필수 |

---

## 부록: 게이트 리뷰 체크리스트

### 공통 체크리스트 (모든 게이트에서 확인)

- [ ] 전체 테스트 통과 (`python -m pytest tests/ -v`)
- [ ] security-guard P0 지적사항 0건
- [ ] design-critic P0 지적사항 0건
- [ ] 설계 문서와 코드 구현이 일치
- [ ] 파일 소유권 규칙 위반 없음 (동시 수정 충돌 없음)

### Phase별 추가 체크리스트

| Gate | 추가 조건 |
|------|----------|
| Gate 0 | 설계 확정, 모든 에이전트가 참조할 인터페이스 문서 완비 |
| Gate 1 | E2E 동작, 도메인 사전 100개+, 프롬프트 마스터 저장소 구축 |
| Gate 2 | 모든 커넥터 헬스체크 통과, 병렬 컨텍스트 수집 동작 |
| Gate 3 | 골든셋 정확도 ≥ 80%, 응답시간 p95 ≤ 15초 |
| Gate 4 | 분석 골든셋 통과, 분석 응답에 인사이트 + 통계 포함 |
| Gate 5 | 보안 최종 감사 통과, 동시 사용자 성능 기준 충족, 운영 문서 완비 |
