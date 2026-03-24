# TODO — 질의 정규화 프롬프트 개선

대상 파일: `resources/prompts/normalization_phase1.txt`

## 완료 항목

- [x] #1 JSON 출력 스키마 + few-shot 예제 추가 (2026-03-24)
- [x] #2 INTENT 우선순위 알고리즘 (Q1~Q7) 적용 (2026-03-24)
- [x] #7 시그널 키워드 오감지 방지 규칙 적용 (2026-03-24, #2에 포함)

---

## 미완료 항목

### #3. ENTITY 슬롯 DIRECT/INDIRECT/IMPLIED 기준 명확화

**우선순위:** 중간
**등록일:** 2026-03-24

#### 현황

ENTITY의 type이 DIRECT, INDIRECT, IMPLIED 3가지인데, 각각의 정의와 경계가 프롬프트에 예시 없이 나열만 되어 있어 LLM이 일관성 없이 분류할 수 있다.

#### 문제 상세

- DIRECT vs INDIRECT 구분 기준이 모호함
  - "고객별 대출 잔액" → "고객"은 DIRECT? "대출"은?
  - "지점의 고객 목록" → "지점"이 INDIRECT? DIRECT?
- IMPLIED가 언제 추가되어야 하는지 기준 없음
  - "연체 고객" → "계좌" 엔티티가 IMPLIED로 필요한지?

#### 개선 방향

- 각 type의 명확한 정의 작성:
  - DIRECT: 질의의 주 대상 (최종 결과에 직접 나타나는 엔티티)
  - INDIRECT: 조건이나 관계로 참조되는 엔티티 (JOIN 대상이지만 결과에 직접 안 나옴)
  - IMPLIED: 원문에 명시되지 않았지만 SQL 구성에 필수적인 엔티티
- 각 type별 예시 2~3개 추가

---

### #4. MEASURE agg_function 금융 도메인 기본 규칙 추가

**우선순위:** 중간
**등록일:** 2026-03-24

#### 현황

DIMENSION이 존재하면 agg_function이 NONE이 될 수 없다는 규칙은 있지만, 구체적으로 어떤 집계함수를 선택할지에 대한 도메인 가이드가 없어 LLM이 UNKNOWN으로 남기거나 임의 선택할 가능성이 높다.

#### 문제 상세

- "지점별 대출 잔액" → SUM인지 AVG인지 MAX인지 도메인 지식에 의존
- "고객별 거래 건수" → COUNT인지 COUNT_DISTINCT인지 모호
- 금융 지표(연체율, NIM 등)는 DERIVED로 표시하라고만 되어 있고, 기본 RAW 지표의 집계 방식은 미정의

#### 개선 방향

- 금융 도메인 기본 집계 규칙 테이블 추가:
  ```
  잔액, 금액, 원금, 이자 → SUM
  금리, 이율, 비율       → AVG (가중평균 여부는 ambiguities에 기재)
  고객, 계좌, 거래       → COUNT 또는 COUNT_DISTINCT (문맥에 따라)
  일자, 날짜            → MAX 또는 MIN (최근/최초)
  ```
- 위 규칙으로도 판단 어려우면 UNKNOWN + note에 후보 기재

---

### #5. TIME 슬롯에 기준일(snapshot) 개념 추가

**우선순위:** 높음
**등록일:** 2026-03-24

#### 현황

현재 TIME 슬롯은 기간(range) 중심으로만 설계되어 있어, 은행 정보계 DB에서 핵심적인 **기준일(as-of date)** 질의를 정확히 표현할 수 없다.

#### 문제 상세

- "3월 말 기준 대출 잔액" → RANGE가 아니라 특정 시점(snapshot)
- "전월말 기준 고객수" → 기간이 아닌 시점 기반 조회
- 정보계 DB는 보통 `기준일자(base_date)` 컬럼으로 스냅샷을 관리하며, WHERE base_date = '2026-02-28' 형태로 조회
- 현재 resolve 허용값에 이를 표현할 수단이 없음

#### 개선 방향

- TIME type에 `SNAPSHOT` 추가, 또는 기존 ABSOLUTE 내에서 처리하는 방안 검토
- resolve에 `AS_OF_DATE`, `MONTH_END`, `QUARTER_END`, `YEAR_END` 등 추가 검토
- Pydantic 모델(`normalization.py`)의 TimeType, TimePeriodResolve Enum도 함께 수정 필요
- Phase 2 교차검증 규칙에도 "SNAPSHOT → base_period 필수" 규칙 추가

#### 영향 범위

- `resources/prompts/normalization_phase1.txt` — TIME 슬롯 정의
- `resources/prompts/normalization_phase2.txt` — 교차검증 규칙
- `src/agents/models/normalization.py` — TimeType, TimePeriodResolve Enum

---

### #6. FILTER 슬롯 value 해석 방침 명시

**우선순위:** 중간
**등록일:** 2026-03-24

#### 현황

FILTER의 values 필드에 사용자 원문 표현을 그대로 넣을지, 정규화된 값으로 변환할지 방침이 없다.

#### 문제 상세

- "1억 이상 대출" → values: ["1억"]? ["100000000"]?
- "서울 지점" → values: ["서울"]? 실제 코드값은 "01"일 수 있음
- "지난달" → values에 날짜로 변환? TIME 슬롯과 중복?
- 단위 변환, 코드값 매핑은 정규화 단계에서 할 일인지, 컨텍스트 수집 후에 할 일인지 불명확

#### 개선 방향

- 방침 확정: **Phase 1에서는 사용자 원문 표현을 그대로 보존** (정규화 단계에서 코드값/단위 변환은 하지 않음)
- note 필드에 "단위 변환 필요", "코드값 매핑 필요" 등 후속 처리 힌트 기재
- 시간 관련 필터는 TIME 슬롯으로 이관하고 FILTER에 중복 배치하지 않는 원칙 명시

#### 관련 항목

- DIMENSION role=FILTER와 FILTER 슬롯의 경계도 함께 명확화 필요:
  - DIMENSION(FILTER): GROUP BY에는 포함되지 않지만 SELECT 결과에 표시되는 차원
  - FILTER 슬롯: WHERE/HAVING 조건으로만 사용되는 조건

---

### #8. OUTPUT_HINT 템플릿 플레이스홀더 관리

**우선순위:** 낮음
**등록일:** 2026-03-24

#### 현황

프롬프트 141번 줄의 `{output_template_text}` 플레이스홀더에 주입되는 문서유형별 기대 컬럼 사전의 관리 체계가 불명확하다.

#### 문제 상세

- 이 템플릿이 비어 있거나 불완전하면 OUTPUT_HINT 기능 전체가 무력화됨
- doc_type 결정 로직이 "앞에 붙는 도메인 키워드와 결합"이라는 모호한 규칙에 의존
- LLM이 사전에 없는 doc_type을 자의적으로 생성할 가능성

#### 개선 방향

- 문서유형별 기대 컬럼 사전을 별도 파일(`resources/output_templates.json` 등)로 관리
- doc_type 허용값을 명시적으로 나열 (또는 "사전에 없으면 null" 규칙 강화)
- 사전에 포함할 최소 문서유형 목록:
  - 여신명세, 수신명세, 고객현황, 거래내역, 연체현황, 지점실적 등

---

### #9. 소형 모델 호환성 대응

**우선순위:** 낮음 (폐쇄망 배포 시점에 맞춰 진행)
**등록일:** 2026-03-24

#### 현황

폐쇄망 배포 시 소형 로컬 모델(7B~70B)을 사용할 예정인데, 현재 프롬프트(약 300줄 이상)는 소형 모델에서 지시 따르기 성능이 급격히 떨어질 수 있다.

#### 문제 상세

- 8개 슬롯을 한 번에 출력하라는 요구는 소형 모델에 과도한 부담
- JSON 출력 안정성(키 누락, 중첩 구조 깨짐)이 보장되지 않음
- few-shot 예제가 길어지면 컨텍스트 윈도우 압박

#### 개선 방향

- **경량 프롬프트 버전**: 슬롯 정의를 압축하고, 예제를 1개로 줄인 별도 버전 준비
- **분할 추출 전략**: 8개 슬롯을 2~3개씩 나눠 여러 번 호출하는 방식 검토
  - Pass 1: INTENT + ENTITY + TIME (뼈대)
  - Pass 2: MEASURE + DIMENSION + FILTER (디테일)
  - Pass 3: MODIFIER + OUTPUT_HINT (가공)
- **JSON 강제 포맷**: structured output / function calling 모드 활용
- 대형 모델용 프롬프트와 소형 모델용 프롬프트를 config로 분기

#### 관련 설정

- `docs/guides/migration-guide.md` — 폐쇄망 전환 가이드
- `docs/guides/customization-targets.md` — 커스터마이징 포인트

---

### #10. normalized_term과 동의어 사전 역할 분담 명확화

**우선순위:** 중간
**등록일:** 2026-03-24

#### 현황

user 프롬프트(`normalization_phase1_user.txt`)에서 `{synonym_dict}`를 주입하고 "사전에 있으면 normalized_term에 기재"하라고 하지만, system 프롬프트(Phase 1)의 슬롯 정의에는 `normalized_term` 필드에 대한 언급이 전혀 없다.

#### 문제 상세

- ENTITY, MEASURE, DIMENSION 슬롯 정의에 normalized_term 필드 설명이 누락
- system 프롬프트와 user 프롬프트 간 지시가 분리되어 LLM이 일관성 없이 처리
- 동의어 사전에 없는 용어에 대해 LLM이 자의적으로 normalized_term을 채울 가능성

#### 개선 방향

- Phase 1 system 프롬프트의 ENTITY, MEASURE, DIMENSION 슬롯 정의에 normalized_term 필드 규칙 추가:
  ```
  normalized_term: 동의어 사전에 해당 용어가 있으면 표준 용어를 기재.
                   사전에 없으면 반드시 null. 임의로 표준화하지 않는다.
  ```
- JSON 스키마 섹션에는 이미 반영되어 있으므로, 슬롯 정의 본문만 보완하면 됨
