# 🔗 INTEGRATION WITH EXISTING ENVIRONMENT — 기존 환경 통합 지침

> **⚠️ 이 문서는 다른 모든 문서보다 우선권을 가집니다.**
> 98_DATA_GENERATION_V2.md, HOW_TO_INSTRUCT_CLAUDE_CODE.md 등에 기술된 저장소·스크립트 구조·실행 명령은 **참고용 예시**일 뿐이며, 본 문서의 원칙과 충돌 시 본 문서가 우선합니다.

---

## 🎯 핵심 원칙

**이 패키지의 본질은 _메타데이터와 규칙_이지 _시딩 실행 방식_이 아닙니다.**

- **가치 있는 것**: `meta/*.json` 5개 (catalog, fk_graph, distributions, cardinalities, business_rules) + 79개 정합성 규칙
- **참고일 뿐인 것**: `seeding/` 아키텍처 예시, PostgreSQL DDL 명령, Python 스크립트 구조

클로드 코드는 **기존 프로젝트의 저장소·스크립트·규약을 존중**해야 합니다.

---

## 📋 사용자 환경 전제 (사용자 확인)

본 패키지는 다음 **기존 환경**에 통합되어야 합니다:

### 저장소 이원 구조
- **메타데이터**: **MongoDB**에 시딩하는 기존 방식이 정해져 있음
- **실데이터 + 스키마**: **PostgreSQL**에 시딩하는 기존 방식이 정해져 있음

### 기존 규약이 정의한 것들
- 메타 저장소의 컬렉션 스키마·인덱스 정책
- PostgreSQL 데이터베이스명·스키마명·네이밍 규칙
- 시딩 스크립트의 위치·명명·CLI 규약
- 로깅·에러 처리·재시도 정책
- 환경 변수·설정 파일 패턴

**이 모든 규약은 본 패키지가 아니라 _사용자의 기존 프로젝트_를 따릅니다.**

---

## ✅ 클로드 코드가 해야 할 것

### Phase 0. 기존 환경 학습 (가장 먼저)

시딩 스크립트 작성에 **들어가기 전에** 다음을 반드시 수행:

1. **기존 프로젝트 구조 탐색**
   - 프로젝트 루트 `ls` 
   - `README.md`, `CONTRIBUTING.md`, `CLAUDE.md`, `docs/` 등 프로젝트 문서 확인
   - 기존 시딩 스크립트·디렉토리 식별

2. **MongoDB 메타 시딩 방식 파악**
   - 기존에 메타를 어떻게 MongoDB에 넣는지
   - 컬렉션 이름 규칙, 인덱스, 업서트 전략
   - 어떤 스크립트가 담당하는지

3. **PostgreSQL 데이터·스키마 시딩 방식 파악**
   - 기존 DDL 생성 방식 (마이그레이션 툴 사용 여부: Alembic/Flyway/Liquibase 등)
   - 시딩 실행 스크립트 위치
   - 연결 설정(config) 관리 방식
   - 프로파일(dev/stg/prod) 개념 있는지

4. **기존 규약 문서화**
   - 찾은 규약을 `DISCOVERED_CONVENTIONS.md`에 정리
   - 사용자에게 확인 요청: **"이 규약이 맞나요?"**

### Phase 1. 본 패키지를 기존 환경에 맞추기

1. **메타데이터를 MongoDB 스키마에 맞게 변환**
   - 본 패키지의 `meta/*.json`은 플랫 구조
   - 사용자의 MongoDB 컬렉션 설계에 맞게 **재구성**
   - 예: 한 컬렉션에 catalog 전체 vs 테이블별 문서로 분할 등

2. **PostgreSQL DDL을 기존 마이그레이션 체계에 통합**
   - 기존이 Alembic이면 Alembic 리비전으로
   - 기존이 raw SQL이면 raw SQL로
   - 기존이 Liquibase면 XML/YAML changeset으로
   - **스키마명·테이블 접두사도 기존 규약 준수**

3. **시딩 스크립트를 기존 구조에 배치**
   - `seeding/` 경로를 **강제하지 말 것**
   - 기존이 `src/data_seeder/`면 거기에
   - 기존이 `scripts/seed/`면 거기에
   - **기존 명명·구조를 따름**

4. **실행 CLI를 기존 패턴에 맞춤**
   - 기존이 `make seed`면 Makefile에 타겟 추가
   - 기존이 `poetry run seed`면 poetry script로
   - 기존이 `python manage.py seed`면 Django 명령어로

---

## 🛑 절대 하지 말 것

1. ❌ `createdb bank_v2` 같은 DB 이름 임의 생성
2. ❌ `CREATE SCHEMA adw_v2` 같은 스키마명 강제
3. ❌ 기존 프로젝트에 없는 `seeding/` 루트 디렉토리 생성
4. ❌ `pip install` 로 기존 의존성과 독립된 패키지 추가
5. ❌ 기존 `config`, `logger`, `db connector` 무시하고 신규 작성
6. ❌ 본 패키지 문서의 코드 예시를 복붙
7. ❌ 기존 세션에서 발견한 규약을 무시하고 98/HOW_TO 문서 기계적 따르기

---

## 📦 본 패키지에서 _실제로 사용할_ 파일

기존 환경과 무관하게 **반드시 참조해야 할 파일**:

| 파일 | 왜 필요한가 | 어떻게 사용 |
|---|---|---|
| `meta/catalog_v2.json` | 1,441 테이블 스키마 정의 | 기존 DDL 마이그레이션 체계로 **변환** |
| `meta/fk_graph.json` | 시딩 순서 DAG | 시딩 실행 시 **테이블 순서 결정** |
| `meta/distributions.json` | 값 생성 규칙 | 기존 Generator 체계에서 **참조** |
| `meta/cardinalities.json` | 시딩 볼륨 | 기존 시딩에서 **볼륨 결정** |
| `meta/business_rules.json` | 정합성 제약 | 시딩 중 + 검증 시 **규칙 참조** |
| `meta/verification_queries.sql` | 정합성 검증 SQL | 기존 검증 체계에 **통합** |

**모든 `build_*.py` 스크립트들은 _메타데이터 재생성용_이지 기존 환경에 설치하는 것이 아닙니다.** 필요 시 개발자가 별도로 실행.

---

## 🔄 권장 통합 워크플로

```
1. 기존 환경 학습 
   └─> DISCOVERED_CONVENTIONS.md 작성 + 사용자 확인

2. 본 패키지 메타 분석
   └─> meta/*.json 구조 파악

3. 통합 설계 수립
   └─> INTEGRATION_PLAN.md 작성
       - 메타데이터 → MongoDB 컬렉션 매핑 설계
       - 1,441 테이블 → PostgreSQL 마이그레이션 방식
       - 시딩 스크립트 → 기존 구조 내 위치
   └─> 사용자 승인 받기

4. 단계적 구현
   └─> 먼저 메타 시딩 (MongoDB)
   └─> 다음 스키마 마이그레이션 (PostgreSQL)
   └─> 다음 샘플 테이블 시딩 (Level 0 일부)
   └─> 검증 후 확대

5. 전체 시딩 + 검증
   └─> 기존 로깅·모니터링으로 진행 상황 추적
   └─> verification_queries.sql을 기존 테스트 체계에 통합
```

---

## 📝 권장 첫 프롬프트 (기존 환경 파악용)

사용자가 클로드 코드에게 줄 첫 프롬프트:

```
bank_v2 패키지를 수령했어. 작업 시작 전에 먼저 INTEGRATION_WITH_EXISTING_ENV.md를 
읽고, 현재 프로젝트의 기존 환경을 파악해줘:

1. 현재 프로젝트 루트 구조 (ls -la + 주요 디렉토리 탐색)
2. 프로젝트 문서 확인: README.md, CLAUDE.md, docs/, CONTRIBUTING.md 등
3. 기존 시딩 관련 파일·디렉토리 식별:
   - MongoDB 메타 시딩 관련 스크립트·모듈
   - PostgreSQL 데이터·스키마 시딩 관련 스크립트·모듈  
   - 마이그레이션 툴 사용 여부 (Alembic/Flyway/Liquibase 등)
4. 기존 의존성·CLI 진입점 파악 (pyproject.toml, requirements.txt, Makefile 등)
5. 기존 config·connector·logger 모듈 파악

발견한 내용을 DISCOVERED_CONVENTIONS.md로 정리해서 내게 보여주고, 
확인을 받은 다음에 통합 계획을 수립하자. 
98_DATA_GENERATION_V2.md와 HOW_TO_INSTRUCT_CLAUDE_CODE.md의 
PostgreSQL·seeding/ 등은 강제 지침이 아니라 참고용 예시일 뿐이야. 
기존 환경 규약이 우선이야.
```

---

## 🎬 통합 설계 체크리스트

클로드 코드가 작업 시작 전 확인:

### 저장소
- [ ] MongoDB 연결 방식은? (연결 문자열, 인증, 드라이버)
- [ ] MongoDB 데이터베이스명·컬렉션명 규칙은?
- [ ] PostgreSQL 연결 방식은?
- [ ] PostgreSQL 데이터베이스명·스키마명 규칙은?
- [ ] 두 DB 간 트랜잭션 일관성은 어떻게 보장?

### 스키마 관리
- [ ] 마이그레이션 툴 사용하나? 어떤 것?
- [ ] 마이그레이션 파일 위치·명명 규칙?
- [ ] 기존 테이블과 충돌하지 않게 하려면?

### 시딩
- [ ] 기존 시딩 스크립트·패키지 있나?
- [ ] 실행 CLI 패턴은? (make, poetry, python -m 등)
- [ ] 프로파일(dev/stg/prod) 개념 있나?
- [ ] 로깅·모니터링 방식은?
- [ ] 멱등성·재실행 정책은?

### 코드 관리
- [ ] Python 버전·의존성 관리 도구 (poetry/pip/pipenv)?
- [ ] 코드 스타일·린터 (black/ruff/flake8)?
- [ ] 기존 config/connector/logger 모듈 재사용 가능?

### 검증
- [ ] 기존 테스트 프레임워크는? (pytest/unittest 등)
- [ ] 검증 쿼리 실행을 기존 테스트에 통합?

---

## 💡 요약

> **이 패키지가 _강제_하는 것은 오직 두 가지뿐입니다:**
> 
> 1. `meta/*.json` 5개의 **내용** (1,441 테이블 메타·규칙)
> 2. 79개 **정합성 규칙** (업무 로직)
> 
> **나머지 모든 것은 _기존 환경_을 따릅니다.**

---

**End of Integration Guide**
