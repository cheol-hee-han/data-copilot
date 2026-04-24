# 🤖 HOW TO INSTRUCT CLAUDE CODE — 클로드 코드 지시 가이드

> **⚠️ 본 문서의 프롬프트는 _기존 환경에 적응하는_ 방식으로 재구성되었습니다.**
> 
> **최우선 원칙:** 본 패키지를 그대로 설치·실행하지 말고, **기존 프로젝트 규약**(MongoDB 메타 시딩, PostgreSQL 데이터 시딩 구조)을 먼저 파악한 뒤 그에 **맞춰** 통합해야 합니다.
> 
> 반드시 [INTEGRATION_WITH_EXISTING_ENV.md](INTEGRATION_WITH_EXISTING_ENV.md)를 먼저 클로드 코드에게 읽히세요.

본 문서는 VSCode 환경에서 **Claude Code에게 bank_v2 메타데이터를 _기존 프로젝트 환경에 통합_하도록 지시하는 방법**을 정리한 실전 가이드다.

---

## 🎯 전체 작업 흐름 (재구성 — 기존 환경 적응 버전)

```
Step 0. 통합 원칙 학습   → INTEGRATION 문서 + 본 패키지 이해
Step 1. 기존 환경 탐색   → 사용자 프로젝트 규약 파악 (⭐중요)
Step 2. 통합 설계 수립   → 메타→MongoDB, 스키마→PG 매핑 설계  
Step 3. 메타 시딩         → MongoDB에 meta/*.json 적재 (기존 방식)
Step 4. 스키마 마이그레이션 → PostgreSQL에 DDL 적용 (기존 방식)
Step 5. PoC 데이터 시딩  → Level 0 일부 샘플 실행·검증
Step 6. 전체 데이터 시딩 → Level 1~7 + Deferred + 품질결함
Step 7. 검증             → verification SQL을 기존 테스트 체계에 통합
```

---

## 📋 준비: VSCode에서 Claude Code 시작 전 체크리스트

1. **기존 프로젝트에 bank_v2 패키지 배치**: 
   - 루트에 `bank_v2/` 폴더로 두거나
   - 기존 프로젝트 구조에 맞게 `docs/bank_v2/` 또는 `data/schemas/bank_v2/` 등
2. **VSCode 열기**: `code <기존 프로젝트 루트>`
3. **Claude Code 터미널 시작**: `Ctrl+`\`` → `claude`

---

## 🚀 Step 0: 통합 원칙 학습 (가장 먼저)

**목적**: 클로드 코드가 본 패키지의 역할을 "참고 자료"로 인식하도록.

### 복붙 프롬프트 0-A (필수 문서 읽기)
```
새로운 bank_v2 패키지를 수령했어. 작업 시작 전에 아래 순서로 문서를 읽어줘:

1. bank_v2/INTEGRATION_WITH_EXISTING_ENV.md (최우선)
2. bank_v2/README_FOR_CLAUDE_CODE.md
3. bank_v2/FILE_INDEX.md

읽은 후 다음을 요약해줘:
- 본 패키지가 _강제_하는 것이 무엇인지
- 본 패키지가 _참고용_으로 제공하는 것이 무엇인지  
- 기존 환경과 충돌 시 어떤 원칙을 따라야 하는지

아직 코드는 작성하지 마. 이해만 확인.
```

---

## 🔍 Step 1: 기존 환경 탐색 (⭐중요)

**목적**: 본 패키지를 기존 프로젝트에 어떻게 녹여낼지 파악.

### 복붙 프롬프트 1-A (프로젝트 규약 발굴)
```
현재 프로젝트의 기존 환경을 파악해줘. 다음을 탐색:

1. 프로젝트 루트 구조
   - ls -la, 주요 디렉토리 3-depth 트리
   - 핵심 문서: README.md, CLAUDE.md, docs/, CONTRIBUTING.md

2. MongoDB 메타 시딩 관련
   - 기존 메타 시딩 스크립트·모듈 위치
   - 사용하는 MongoDB 드라이버 (pymongo/motor 등)
   - 컬렉션 네이밍 규칙
   - 연결 설정 관리 방식

3. PostgreSQL 데이터·스키마 시딩 관련
   - 마이그레이션 툴 (Alembic/Flyway/Liquibase/raw SQL?)
   - 마이그레이션 파일 위치·명명 규칙
   - 시딩 스크립트 위치·CLI 진입점
   - 기존 config/connector/logger 모듈

4. 개발 환경
   - 의존성 관리 (poetry/pip/pipenv)
   - Python 버전
   - 실행 CLI 패턴 (make/poetry run/python -m 등)
   - 테스트 프레임워크

5. 환경 변수·설정 파일
   - .env 패턴 or config/*.yaml
   - 프로파일(dev/stg/prod) 개념

모두 파악한 후 DISCOVERED_CONVENTIONS.md 파일로 정리해서 내게 보여줘. 
내가 내용을 확인한 뒤 다음 단계로 진행할게.
```

### 복붙 프롬프트 1-B (규약 확인 후 승인)
```
DISCOVERED_CONVENTIONS.md 잘 정리됐어. 
[내가 맞다고 확인/수정해서 다시 입력]

이 규약을 기준으로 bank_v2 패키지를 통합할 거야. 
다음 단계 진행해.
```

---

## 🏗 Step 2: 통합 설계 수립

### 복붙 프롬프트 2-A (통합 계획서 작성)
```
INTEGRATION_WITH_EXISTING_ENV.md와 DISCOVERED_CONVENTIONS.md를 
기반으로 INTEGRATION_PLAN.md 를 작성해줘. 포함할 내용:

1. 메타데이터 통합 설계
   - bank_v2/meta/*.json 5개 → 기존 MongoDB 컬렉션에 어떻게 매핑?
   - 컬렉션 설계 (단일 컬렉션 vs 5개 분할 vs 테이블별 문서)
   - 인덱스 전략
   - 업서트 vs 리셋 전략

2. 스키마 마이그레이션 설계
   - 1,441 테이블 DDL → 기존 마이그레이션 체계에 통합 방법
   - 리비전 분할 전략 (1개? 주제영역별 17개? 레벨별 8개?)
   - 롤백 정책
   - 기존 스키마·테이블과 네임스페이스 충돌 방지

3. 데이터 시딩 설계
   - 기존 시딩 스크립트 재사용 가능 범위
   - 추가해야 할 Generator (한국어, 금융 특수 지표 등)
   - 시딩 순서(fk_graph.json levels) 활용 방식
   - 대용량 테이블 샘플링 전략

4. 검증 체계 통합
   - verification_queries.sql → 기존 테스트에 어떻게 통합?
   - pytest fixture? 별도 검증 모듈?
   - CI/CD 파이프라인 포함 여부

5. 단계적 실행 계획
   - 각 단계별 예상 소요 시간
   - 체크포인트·롤백 지점
   - 리스크 포인트

작성 후 내게 보여줘. 검토 후 진행 여부 결정할게.
```

---

## 🛠 Step 3: 메타 시딩 (MongoDB)

### 복붙 프롬프트 3-A (메타 시딩 - 기존 방식 기반)
```
INTEGRATION_PLAN.md 에 따라 bank_v2/meta/*.json 5개를 MongoDB에 시딩해줘.

반드시 지킬 사항:
- 기존 MongoDB 연결 설정 재사용 (새로 만들지 말 것)
- 기존 컬렉션 네이밍 규칙 준수
- 기존 시딩 스크립트 패턴 따르기
- 프로파일(dev/stg/prod) 규약 존중
- 로깅·에러 처리도 기존 방식

실행 전 사용할 MongoDB URI와 컬렉션 이름을 내게 알려줘. 
확인 후 진행.
```

### 복붙 프롬프트 3-B (시딩 검증)
```
메타 시딩이 끝났으면 검증해줘:
- 각 컬렉션 document count 기대치 부합 여부
- catalog에 1,441 테이블 모두 있는지
- fk_graph 엣지 1,386개 모두 있는지
- distributions 컬럼 12,285개 확인
- cardinalities 레벨 0~7 커버
- business_rules 79개 규칙

결과를 간단히 리포트.
```

---

## 🏗 Step 4: 스키마 마이그레이션 (PostgreSQL)

### 복붙 프롬프트 4-A (DDL 생성 - 기존 마이그레이션 체계)
```
catalog_v2.json을 기반으로 PostgreSQL 스키마 마이그레이션을 생성해줘.

반드시 기존 마이그레이션 체계 사용:
- 기존이 Alembic이면 alembic revision
- 기존이 Flyway면 V{n}__{desc}.sql
- 기존이 Liquibase면 XML/YAML changeset
- 기존이 raw SQL이면 기존 디렉토리 규약에 맞춰

규약:
1. FK 제약은 CREATE TABLE에 포함하지 말고 별도 ALTER로
2. PK는 CREATE TABLE에 포함
3. 타입 매핑 NUMERIC/VARCHAR/CHAR/TIMESTAMP/DATE/INT/BIGINT
4. 스키마명·테이블 네이밍 기존 규약 확인 필요

진행 전 생성할 마이그레이션 파일들의 목록과 구조 제안을 
내게 먼저 보여줘. 리비전 분할 전략도 포함.
```

### 복붙 프롬프트 4-B (마이그레이션 적용)
```
생성한 마이그레이션을 dev 환경에 적용해줘. 
기존 마이그레이션 실행 명령으로 (alembic upgrade head 등).
적용 후 테이블 수 1,441 확인.
```

---

## 🧪 Step 5: PoC 데이터 시딩

### 복붙 프롬프트 5-A (Level 0 샘플 시딩)
```
PostgreSQL 스키마 적용이 됐으면 Level 0의 작은 테이블 5개만 
먼저 시딩해서 파이프라인을 검증해줘.

선택:
- CMI021C 영업일코드 (365행)
- CMI022C 공휴일코드 (15행)
- CMI025M 코드마스터 (500행)
- CMI001M 부점 (100행)
- CMI007M 직원 (2,500행)

시딩에 사용할 것:
- meta/distributions.json 컬럼별 분포 규칙
- meta/cardinalities.json 시딩 볼륨
- meta/business_rules.json 관련 규칙

기존 시딩 스크립트·Generator 있으면 재사용하고, 
없는 한국어 Generator는 신규 추가해야 해. 
계획을 먼저 보여줘.
```

### 복붙 프롬프트 5-B (PoC 검증)
```
Level 0 샘플 시딩 결과 검증:
- 행수 실제 vs 기대
- 3개 테이블 샘플 5행 SELECT
- meta/verification_queries.sql 섹션 1 (기본 검증) 실행

이슈 있으면 분석 + 해결방안 제시.
```

---

## 🚀 Step 6: 전체 데이터 시딩

### 복붙 프롬프트 6-A (전체 순차 실행)
```
PoC가 통과했으면 Level 0 전체(300T) + Level 1~7 순차 시딩 진행.

fk_graph.json.levels의 순서대로. 
각 레벨 완료 시 10% 단위 진행 보고.
에러 발생 시 해당 테이블 기록 + 중추 테이블(CSC001M/DPG001M/LNB001M) 
실패는 즉시 중단.

실행 시간, 메모리 사용량 모니터링 포함.
```

### 복붙 프롬프트 6-B (Deferred UPDATE + 품질결함)
```
시딩 완료 후 후처리:
1. fk_graph.json.deferred_edges 3쌍 2-pass UPDATE
2. distributions.json.quality_defects 5개 카테고리 주입
3. FK 제약 ALTER TABLE ADD CONSTRAINT ... NOT VALID

각 단계 영향 행수 리포트.
```

---

## 🔬 Step 7: 검증 (기존 테스트 체계 통합)

### 복붙 프롬프트 7-A (검증 실행)
```
meta/verification_queries.sql 을 실행하되, 기존 테스트 체계에 통합:
- 기존이 pytest면 pytest 픽스처·마커 활용
- 기존이 별도 SQL runner면 그걸 사용
- 결과를 기존 리포트 포맷에 맞춰

분류:
- PASS (violations = 0)
- SOFT 위반 (허용치 내)
- HARD 위반 (반드시 0)

HARD 위반 발견 시 원인 분석 + 수정 방안.
```

### 복붙 프롬프트 7-B (최종 리포트)
```
SEEDING_COMPLETE_REPORT.md 작성:
1. 기존 환경과의 통합 내역 (MongoDB/PG 각각)
2. 전체 테이블·행 수 통계
3. 레벨별·도메인별 시딩 결과
4. 품질결함 주입 비율 검증
5. HARD/SOFT 위반 건수
6. 발견 이슈 + 해결 내역
7. 기존 환경 규약과의 일관성 체크
8. 다음 단계 제안 (에이전트 테스트 환경 승격 여부)
```

---

## 🐛 트러블슈팅 프롬프트 모음

### 실패 시: 단일 테이블 재시딩
```
{테이블명} 시딩이 실패했어. 에러 메시지는 [에러 내용]이야.
해당 테이블의 catalog_v2.json 정의, distributions.json 분포, 
cardinalities.json 볼륨을 출력해서 문제 원인을 분석하고 
수정해서 재시딩해줘. 다른 테이블은 영향 없게.
```

### 메모리 부족 시
```
bulk insert 중 메모리 에러가 났어. orchestrator.py의 배치 크기를 
조정해줘. 현재 10000행 단위인데 1000으로 줄이고, 
generator에서 row를 한 번에 만들지 말고 제너레이터로 
yield 하도록 리팩토링해줘.
```

### FK 제약 추가 실패 시
```
ALTER TABLE ADD CONSTRAINT에서 일부 FK 추가가 실패했어. 
실패 목록을 확인해서 원인 분류해줘:
1. Referenced table doesn't exist → catalog 문제
2. Data type mismatch → 스키마 drift 
3. Orphan rows → 품질결함(허용)  
4. Deadlock → 재시도 필요

각각에 맞는 해결책 제시.
```

### Catalog 불일치 발견 시
```
catalog_v2.json에서 이상을 발견했어: [설명]
meta/catalog_v2.json은 MD에서 자동 파싱된 것이므로 
scripts/build_catalog.py를 검토해서 파싱 오류인지 
MD 오류인지 판단해줘. MD 오류면 해당 MD를 고치고, 
파싱 오류면 스크립트를 고친 뒤 catalog_v2.json을 재생성해줘.
```

---

## 📌 베스트 프랙티스

### 1. 프롬프트 길이
- **너무 긴 지시는 피하기**: 한 번에 5단계 이상 요구하면 품질 저하
- **단계별 분할 권장**: 각 프롬프트는 1-3개 작업만

### 2. 중간 검증 요구
- 각 Step 완료 후 "샘플 출력" 또는 "행수 리포트" 요구
- 클로드 코드가 무조건 "완료"라고만 답하지 않게

### 3. 계획을 먼저 받기
- 큰 작업 전에 "코드 작성하지 말고 계획만 세워줘" 요청
- 계획 검토 후 승인 → 구현 지시

### 4. Context 관리
- 세션이 길어지면 중요 결정사항을 파일로 저장: `DECISIONS.md`
- 새 세션 시작 시 이 파일을 먼저 읽도록 지시

### 5. 테스트 주도 개발
- Generator 구현 → 단위 테스트 10개 샘플
- 한 테이블 시딩 → SELECT 5행
- 한 레벨 시딩 → 행수 확인
- 이 사이클을 지키면 숨은 버그 조기 발견

---

## 🎬 실제 사용 예시 (한 세션 시나리오)

```
[VSCode 터미널에서 claude 실행]

YOU: 
현재 디렉토리의 bank_v2 패키지를 수령했어. 먼저 
README_FOR_CLAUDE_CODE.md와 FILE_INDEX.md를 읽고 전체 구조를 파악해줘.

CLAUDE CODE: [두 문서 읽고 구조 요약]

YOU: 
98_DATA_GENERATION_V2.md도 읽고, 8단계 실행 계획을 세워줘. 
코드는 아직 짜지 마.

CLAUDE CODE: [계획 제시]

YOU: 
계획 좋아. Step 1부터 진행해줘. PostgreSQL 환경 확인부터.

CLAUDE CODE: [psql 확인, Python 패키지 설치]

YOU:
좋아. 이제 seeding/ 패키지 스켈레톤 만들어줘. 
98 문서 섹션 5의 구조 따라서.

CLAUDE CODE: [스켈레톤 생성]

YOU:
config.py와 loader.py부터 구현. 테스트 포함.

CLAUDE CODE: [구현 + 테스트]

... (단계별 진행)

YOU:
좋아. Level 0 시딩 실행해줘.

CLAUDE CODE: [Level 0 시딩, 300개 테이블 처리]

YOU:
결과 PoC 검증해줘. verification 섹션 1 실행.

CLAUDE CODE: [검증 결과 + 이슈 리포트]

YOU:
이슈 없네. Level 1~7 전체 시딩 진행.

CLAUDE CODE: [1시간 후] 완료. 총 14,257,000행 시딩.

YOU:
Deferred UPDATE 실행 + 품질결함 주입 + FK 제약 추가.

CLAUDE CODE: [순차 실행]

YOU:
마지막으로 verification SQL 전체 실행하고 리포트 작성해줘.

CLAUDE CODE: [최종 리포트]
```

---

## 🚦 중간 중단 시 재개 방법

VSCode 세션이 끊기거나 다음날 이어갈 때:

```
이전 세션에서 bank_v2 시딩을 진행하다가 중단됐어.
현재 상태를 파악해줘:
1. pg_stat_user_tables 확인 - 어디까지 시딩됐는지
2. verification_queries.sql 섹션 1 실행 - 완성도 체크
3. 남은 작업 목록 제시

그 다음 재개할 단계부터 이어서 실행해줘.
```

---

## 📞 문제 발생 시 에스컬레이션

클로드 코드가 해결 못하는 문제:
- **메타데이터 자체 오류**: `build_*.py` 스크립트 재실행 필요
- **비즈니스 룰 충돌**: `business_rules.yaml` 수정 후 재생성
- **PostgreSQL 환경 문제**: DB 관리자 도움 필요
- **대규모 실패**: 원 설계자(사용자)와 상의

**권장: Claude Code에게 "현재 상황을 정리하고 내가 결정할 수 있도록 3가지 옵션을 제시해줘"라고 요청.**

---

**End of Claude Code Instruction Guide**
