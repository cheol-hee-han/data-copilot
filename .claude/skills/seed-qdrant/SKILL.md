---
name: seed-qdrant
description: |
  Qdrant 벡터 스토어에 업무 매뉴얼(biz_manual)과 SQL 실행이력(sql_history) 컬렉션을 생성·적재합니다.
  sql_history는 실제 SELECT SQL + 추론 description 쌍 10,000건 이상을 임베딩합니다.
  Qdrant 초기 구축, 컬렉션 추가, 벡터 데이터 증강 시 사용하세요.
user-invocable: true
---
# 역할

Qdrant 벡터 데이터 시딩 전문가. 업무 매뉴얼과 SQL 실행이력을 벡터화하여 적재.

# 핵심 원칙

- **요구사항 문서가 단일 진실 소스(Single Source of Truth)**
  - 목표 건수, 복잡도/도메인 분포 비율, 필수 주제 목록 등 모든 구체적 수치는 요구사항 문서를 참조
  - SKILL.md에는 절차와 원칙만 기술하고, 구체적 데이터 상세는 하드코딩하지 않음
- 반드시 `docs/agent-guides/test-data-requirements.md` 섹션 8의 **최신** 요구사항을 준수
  - `biz_manual`: 목표 건수, 주제영역별 필수 매뉴얼 목록, 생성 시 준수사항
  - `sql_history`: 목표 건수, 복잡도/도메인 분포, 필수 포함 시나리오, 필수 SQL 구문 패턴
- 반드시 `docs/agent-guides/test-data-seeding-reference.py`의 `seed_qdrant()` 구조 참고
- `.env` 기반 연결 정보 사용 (`QDRANT_HOST`, `QDRANT_PORT`, `EMBEDDING_MODEL`)
- **임베딩 모델 일치 필수**: 워크플로우가 사용하는 모델과 동일해야 함
- 재실행 안전성: 컬렉션 삭제 후 재생성 방식
- **Python 3.14에서 fastembed 빌드 실패 시**: `python:3.12-slim` Docker 컨테이너에서 실행

# 대상 컬렉션

| 컬렉션 | 임베딩 대상 필드 | 내용 |
|--------|--------------|------|
| `biz_manual` | `content` | 주제영역별 업무 매뉴얼 청크 (규정, 절차, 산출식, 코드체계) |
| `sql_history` | `description` | 실행 SQL + 추론 description |

> 각 컬렉션의 목표 건수, 분포 비율, 필수 주제는 요구사항 문서 섹션 8 참조

# 임베딩 설정

```python
EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
EMBEDDING_DIM = 384
DISTANCE = Distance.COSINE
```

- fastembed (`TextEmbedding`) 사용
- 최초 실행 시 모델 다운로드 (~130MB) 발생 가능

# sql_history — 핵심 설계

## 개념 (폐쇄망 원본 재현)

실제 폐쇄망에서는 임직원들이 실행한 **SELECT SQL 원문**이 이력으로 축적됨.
SQL 원문만으로는 벡터 검색이 어려우므로, 각 SQL을 분석하여
**"이 SQL이 어떤 데이터를 추출한 것인지"를 자연어로 설명한 description**을 추론 생성.
이 **description을 임베딩**하여 Qdrant에 적재.

에이전트 동작 흐름:
1. 사용자 자연어 질의 임베딩
2. `sql_history`에서 유사 description 벡터 검색
3. 매칭된 기존 SQL을 참조하여 새 SQL 생성

## 페이로드 구조

```json
{
  "sql": "SELECT ...",
  "description": "지점별 여신 잔액 상위 10개 지점의 대출 건수와 총 잔액을 조회한 데이터",
  "tables_used": ["TB_ADW_LNB301M", "TB_ADW_COM001M"],
  "domain": "LON",
  "complexity": "multi_join",
  "exec_user": "user03",
  "exec_dt": "2026-03-15"
}
```

- `description`: **(임베딩 대상)** — SQL 분석을 통해 추론한 데이터 추출 목적 설명
- `sql`: 실제 실행된 SELECT SQL 원문 (biz_schema 테이블 참조)
- `complexity`: 요구사항 문서 섹션 8의 복잡도 유형 참조

## 데이터 생성 방법

LLM을 호출하지 않고, 현재 **업무 테이블 설계(섹션 5) + 업무 매뉴얼(biz_manual)**을 참고하여 프로그래밍으로 대량 생성:

1. 섹션 5의 테이블 카탈로그에서 테이블 + 컬럼 정보 로드
2. 도메인별·복잡도별 SQL 템플릿 정의 (WHERE 조건, JOIN 조합, 집계 패턴 등)
3. 랜덤 조합으로 유니크한 SQL + description 쌍 생성
4. description을 임베딩하여 Qdrant 적재

> 복잡도 분포, 도메인 분포, 필수 포함 시나리오, 필수 SQL 구문 패턴은 요구사항 문서 섹션 8 참조

# biz_manual 작성 원칙

> **핵심: IT 용어 사용 금지. 비즈니스 용어로만 작성.**
>
> 업무 매뉴얼은 현업 직원이 작성한 것이므로 테이블명(TB_xxx), 컬럼명(XXX_CD), 스키마명 등
> IT 메타 정보를 절대 포함하지 않는다.
> "TB_ADW_CSC101M의 CUS_GRD_CD" 대신 → **"고객등급"**
> "TB_ADW_DEP201P와 TB_ADW_DEP202S의 차이" 대신 → **"당일 잔액과 전일 잔액의 차이"**

> 주제영역별 필수 매뉴얼 목록과 건수 배분은 요구사항 문서 섹션 8 `biz_manual` 상세 명세 참조

# 작업 절차

> **절대 규칙:** 이미 데이터가 존재하더라도 스킬이 호출되면 반드시 Phase 1(사전 검증)을 수행한다.
> 요구사항과 현재 상태가 하나라도 불일치하면 전체 삭제 후 재생성한다.
> 시딩 완료 후에는 반드시 Phase 3(사후 검증)을 수행하고 결과 테이블을 출력한다.

## Phase 1: 사전 검증 (Drift Detection)

1. `.env` 파일에서 Qdrant 연결 정보 및 임베딩 모델 확인
2. `test-data-requirements.md` 섹션 8의 **최신** 요구사항을 읽고 파악:
   - `sql_history` 목표 포인트 수
   - 임베딩 대상 필드가 `description`인지
   - 복잡도·도메인 분포 요건
   - `biz_manual` 목표 건수 및 필수 주제
3. 현재 Qdrant에 적재된 데이터와 요구사항을 **API로 비교**:
   - 각 컬렉션 존재 여부 및 포인트 수
   - 벡터 차원이 EMBEDDING_DIM과 일치하는지
   - `sql_history` 페이로드에 `description` 필드가 있는지 (기존 `nl_query` 구조이면 불일치)
   - 포인트 수가 목표 이상인지
   - `biz_manual`에 필수 주제가 모두 포함되어 있는지
4. **불일치 항목이 하나라도 있으면** → Phase 2로 진행 (전체 재생성)
5. **모든 항목 일치** → Phase 3(사후 검증)만 수행하고 "변경 없음" 보고 후 종료

## Phase 2: 시딩 실행

1. PG `biz_schema`의 실제 테이블/컬럼 구조를 `information_schema`에서 조회 (SQL 생성 재료)
2. 도메인별·복잡도별 SQL 템플릿 기반으로 SQL + description 쌍 생성
3. fastembed 모델 로딩 (Python 3.14 빌드 실패 시 Docker 컨테이너 사용)
4. `biz_manual` 컬렉션: 삭제 → 재생성 → 임베딩 → upsert
5. `sql_history` 컬렉션: 삭제 → 재생성 → description 임베딩 → upsert (배치 처리)
6. 적재 건수 즉시 출력

## Phase 3: 사후 검증 (필수 — 절대 생략 금지)

시딩 완료 후 **반드시** 아래 항목을 검증하고 결과 테이블을 출력한다:

1. **포인트 수 검증**: 각 컬렉션이 요구사항의 목표 건수 이상인지
2. **벡터 차원 검증**: 384
3. **페이로드 구조 검증**: sql_history 샘플 포인트에 `description`, `sql`, `tables_used`, `complexity`, `domain` 필드 존재
4. **임베딩 대상 검증**: description이 임베딩되었는지 (nl_query가 아닌지)
5. **복잡도 분포 검증**: 복잡도별 포인트 수가 요구사항의 비율 ±5% 이내인지
6. **도메인 분포 검증**: 도메인별 포인트 수가 요구 비율 대략 준수
7. **biz_manual 필수 주제 검증**: 요구사항 섹션 8의 필수 주제 포함 여부

검증 결과는 아래 형식으로 출력:

```
| 검증 항목 | 기대값 | 실제값 | 판정 |
|----------|--------|--------|------|
| sql_history 포인트수 | ≥N (문서 기준) | N | ✅ |
| biz_manual 포인트수 | ≥N (문서 기준) | N | ✅ |
| 벡터 차원 | 384 | 384 | ✅ |
| 페이로드에 description 존재 | Y | Y | ✅ |
| multi_join 비율 | ~N% (문서 기준) | N% | ✅ |
| LON 도메인 비율 | ~N% (문서 기준) | N% | ✅ |
| ...      | ...    | ...    | ... |
```

**하나라도 ❌ 판정이면** 원인을 파악하고 해당 부분만 수정 후 Phase 3을 재실행한다.

# 기술 참고사항 (트러블슈팅)

## 실행 환경

- 호스트에서 직접 실행 가능 (fastembed이 로컬에 설치된 경우)
- 실행 명령:
  ```bash
  PYTHONIOENCODING=utf-8 python standalone/scripts/seed_qdrant.py
  ```
- Python 3.14에서 fastembed 빌드 실패 시 Docker 사용 (아래 참조)

## 스크립트 아키텍처

- `seed_qdrant.py`는 메인 드라이버 (컬렉션 생성 + 임베딩 + upsert)
- `qdrant_data_generators.py`는 데이터 생성기 (biz_manual + sql_history)
- 두 파일이 같은 디렉토리(`standalone/scripts/`)에 위치해야 함 (`sys.path.insert`로 임포트)

## .env 경로

- `.env` 파일 경로: `Path(__file__).resolve().parent.parent.parent / ".env"` (standalone/scripts/ → 프로젝트 루트)
- 기존 `.parent.parent`는 오류 — `.parent.parent.parent`가 올바른 경로

## 임베딩 모델 경고

- fastembed 0.5.x+에서 mean pooling으로 변경됨 — `UserWarning: The model ... now uses mean pooling` 경고 발생
- 기능에 영향 없음 (경고만). 워크플로우에서도 동일 모델/버전 사용 시 일관성 유지됨

## 대용량 임베딩 처리

- sql_history 10,000건 임베딩에 약 5~10분 소요
- BATCH_SIZE=500으로 배치 upsert 수행
- 중간 진행 상태 출력 (`upsert N/10000`)

## biz_manual 생성

- biz_manual은 **IT 용어 사용 금지** — 테이블명/컬럼명 포함 불가
- 기본 ~340건의 업무 매뉴얼 + 변형 생성으로 500건 이상 확보
- 변형 시 원본 content에 규정/절차 관련 suffix 추가

## sql_history SQL 내 테이블명

- SQL 내 테이블명은 **신규 명명규칙(TB_ADW_xxx)** 사용
- `biz_schema.tb_adw_csc101m` (소문자) 형식으로 참조
- PG DDL이 먼저 존재해야 SQL이 유효하므로 **seed-postgres 이후에 실행**

# 산출물 위치

- 시딩 스크립트: `standalone/scripts/seed_qdrant.py`
- 데이터 생성기: `standalone/scripts/qdrant_data_generators.py`

# 인자 사용법

- `$ARGUMENTS` 없이 호출: 전체 컬렉션 시딩
- `$ARGUMENTS`에 컬렉션명 지정: 해당 컬렉션만 시딩 (예: `/seed-qdrant sql_history`)
- `$ARGUMENTS`에 `augment` 지정: 기존 컬렉션에 포인트 추가 (삭제 없이)

# 실행 환경 참고

Python 3.14에서 fastembed 빌드가 실패하는 경우, Docker 컨테이너 사용:

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network host \
  -v "$(pwd)/standalone/scripts:/app/standalone/scripts:ro" \
  -v "$(pwd)/.env:/app/.env:ro" \
  -w /app python:3.12-slim \
  sh -c "pip install -q fastembed qdrant-client python-dotenv && python standalone/scripts/seed_qdrant.py"
```
