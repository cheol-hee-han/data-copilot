"""validator 프롬프트 시뮬레이션 — 실제 INPUT이 어떻게 전달되는지 확인.

_serialize_normalized_for_validation()의 출력과
프롬프트 템플릿 치환 결과를 보여준다.
"""
from __future__ import annotations

import sys
import os

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.agents.models.normalization import (
    NormalizedQuery,
    IntentSlot,
    MeasureSlot,
    DimensionSlot,
    FilterSlot,
    ModifierSlot,
    OutputHintSlot,
)
from src.agents.nodes.reason.sql_validator import (
    _serialize_normalized_for_validation,
)
from src.utils.llm.prompt import render_prompt


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 테스트 케이스: 다양한 질의 유형
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

test_cases: list[tuple[str, NormalizedQuery]] = [
    # 1. 단순 AGGREGATE
    ("지점별 이번 달 신규 대출 건수", NormalizedQuery(
        original_query="지점별 이번 달 신규 대출 건수",
        intent=IntentSlot(primary="AGGREGATE"),
        measures=[MeasureSlot(term="건수", measure_type="RAW", agg_function="COUNT")],
        dimensions=[DimensionSlot(term="지점", role="GROUP")],
        filters=[
            FilterSlot(target="기간", filter_type="GTE", position="PRE_AGG", values=["이번 달"]),
            FilterSlot(target="신규", filter_type="IMPLICIT", position="PRE_AGG", note="대출 상태가 '신규'인 건만 포함"),
        ],
    )),
    # 2. RANK 질의
    ("수신잔액 상위 10개 지점", NormalizedQuery(
        original_query="수신잔액 상위 10개 지점",
        intent=IntentSlot(primary="RANK"),
        measures=[MeasureSlot(term="잔액", measure_type="RAW", agg_function="SUM")],
        dimensions=[DimensionSlot(term="지점", role="GROUP")],
        modifiers=[ModifierSlot(type="RANK", direction="DESC", limit=10, by="잔액")],
    )),
    # 3. RATIO 질의 (핵심 — 이전에 오판 유발)
    ("영업점별 연체율", NormalizedQuery(
        original_query="영업점별 연체율",
        intent=IntentSlot(primary="AGGREGATE"),
        measures=[MeasureSlot(
            term="연체율", measure_type="RATIO", agg_function="NONE",
            note="연체대출잔액 합계 / 총대출잔액 합계",
        )],
        dimensions=[DimensionSlot(term="영업점", role="GROUP")],
    )),
    # 4. EXTRACT (단순 추출, measures 없음)
    ("서울 VIP 고객 여신 명세", NormalizedQuery(
        original_query="서울 VIP 고객 여신 명세",
        intent=IntentSlot(primary="EXTRACT"),
        filters=[
            FilterSlot(target="지역", filter_type="EQUALS", position="PRE_AGG", values=["서울"]),
            FilterSlot(target="고객등급", filter_type="EQUALS", position="PRE_AGG", values=["VIP"]),
        ],
        output_hint=OutputHintSlot(
            format="SPEC_SHEET", doc_type="여신명세",
            expected_columns=["고객명", "계좌번호", "대출금액", "대출일자", "만기일자"],
        ),
    )),
    # 5. DELTA 질의 (계산 가공)
    ("이번 달 영업점별 여신 실행 건수 전월 대비 증감", NormalizedQuery(
        original_query="이번 달 영업점별 여신 실행 건수 전월 대비 증감",
        intent=IntentSlot(primary="COMPARE"),
        measures=[MeasureSlot(term="실행 건수", measure_type="RAW", agg_function="COUNT")],
        dimensions=[DimensionSlot(term="영업점", role="GROUP")],
        modifiers=[ModifierSlot(type="DELTA", by="실행 건수", note="전월 대비")],
    )),
    # 6. PARTITION + POST_AGG 필터
    ("지점별 잔액 합계가 100억 이상인 지점의 고객별 잔액 순위", NormalizedQuery(
        original_query="지점별 잔액 합계가 100억 이상인 지점의 고객별 잔액 순위",
        intent=IntentSlot(primary="RANK"),
        measures=[MeasureSlot(term="잔액", measure_type="RAW", agg_function="SUM")],
        dimensions=[
            DimensionSlot(term="지점", role="GROUP"),
            DimensionSlot(term="지점", role="PARTITION"),
        ],
        filters=[
            FilterSlot(target="잔액 합계", filter_type="GTE", position="POST_AGG", values=["100억"]),
        ],
        modifiers=[ModifierSlot(type="RANK", direction="DESC", by="잔액")],
    )),
]


if __name__ == "__main__":
    print("=" * 80)
    print("Validator 프롬프트 시뮬레이션 — _serialize_normalized_for_validation 출력")
    print("=" * 80)

    for label, nq in test_cases:
        print(f'\n{"━" * 60}')
        print(f'질의: "{label}"')
        print(f'{"━" * 60}')
        result = _serialize_normalized_for_validation(nq)
        print(result)
        print()

    # ── 프롬프트 전체 치환 시뮬레이션 (케이스 3: RATIO) ──
    print("\n" + "=" * 80)
    print("프롬프트 [CONTEXT] 섹션 전체 치환 시뮬레이션 (RATIO 케이스)")
    print("=" * 80)

    nq = test_cases[2][1]  # 영업점별 연체율
    normalized_summary = _serialize_normalized_for_validation(nq)

    # CONTEXT 섹션만 추출하여 치환 시뮬레이션
    context_template = """## 사용자 질의

{original_query}

## 질의 정규화 요약

아래는 질의 정규화에서 추출한 전체 구조 요소이다.
체크 1(filter), 체크 2(group_by), 체크 3(order_rank)의 검증 근거로 사용하며,
체크 6(논리적 정합성)에서 원문 질의와 함께 참조한다.

{normalized_summary}

## 생성된 SQL

```sql
{generated_sql}
```

## 사용된 테이블 스키마

{table_schema}

## 확인된 지식 항목

{confirmed_terms}

## 코드값 매핑

{code_mappings}

## AI 추론 결정사항

{reasoning_decisions}

## 이전에 실패한 접근 방식 (dead_ends)

{dead_ends}

## DB 실행 결과

{db_execution_result}"""

    replacements = {
        "{original_query}": "영업점별 연체율",
        "{normalized_summary}": normalized_summary,
        "{generated_sql}": (
            "SELECT B.BR_NM AS 영업점명,\n"
            "       SUM(CASE WHEN A.LN_STCD = '02' THEN A.LN_BAL_AMT ELSE 0 END) AS 연체잔액,\n"
            "       SUM(A.LN_BAL_AMT) AS 총대출잔액,\n"
            "       ROUND(SUM(CASE WHEN A.LN_STCD = '02' THEN A.LN_BAL_AMT ELSE 0 END)\n"
            "             * 100.0 / NULLIF(SUM(A.LN_BAL_AMT), 0), 2) AS 연체율\n"
            "FROM ADWOWN.TB_ADW_LNB301M A\n"
            "INNER JOIN ADWOWN.TB_ADW_COM001M B ON A.BLNG_BRCD = B.BLNG_BRCD\n"
            "GROUP BY B.BR_NM\n"
            "ORDER BY 연체율 DESC"
        ),
        "{table_schema}": (
            "ADWOWN.TB_ADW_LNB301M — 여신기본(월)\n"
            "  LN_BAL_AMT NUMERIC — 대출잔액\n"
            "  LN_STCD VARCHAR(2) — 대출상태코드\n"
            "  BLNG_BRCD VARCHAR(4) — 소속지점코드\n"
            "ADWOWN.TB_ADW_COM001M — 지점마스터\n"
            "  BLNG_BRCD VARCHAR(4) [PK] — 소속지점코드\n"
            "  BR_NM VARCHAR(50) — 지점명"
        ),
        "{confirmed_terms}": (
            "- TB_ADW_LNB301M: CONFIRMED\n"
            "- TB_ADW_COM001M: CONFIRMED\n"
            "- LN_STCD: CONFIRMED (대출상태코드)"
        ),
        "{code_mappings}": "LN_STCD (대출상태코드): 01=정상, 02=연체, 03=상각",
        "{reasoning_decisions}": '[가정] "연체율" → LN_STCD=\'02\' 기준, 연체잔액/총잔액 산출',
        "{dead_ends}": "(없음)",
        "{db_execution_result}": "PASS (12건 반환)",
    }

    result, _ = render_prompt(context_template, replacements)
    print(result)
