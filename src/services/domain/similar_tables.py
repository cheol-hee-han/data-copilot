"""유사 테이블 그룹 정의.

은행 정보계 DB에는 같은 도메인의 유사 테이블이 다수 존재한다.
예: TB_LOAN_INFO(건별 현재 상태) vs TB_LOAN_OVERDUE_STAT(월말 집계 통계)

이 모듈은 유사 테이블 그룹의 **데이터 정의**만 담당한다:
  - SimilarTable / SimilarTableGroup 데이터 클래스
  - 기본 그룹 정의 (5개 그룹: 여신연체, 수신잔액, 여신상세/요약, 거래상세/요약, 고객현재/이력)
  - resources/domain/similar_tables.yaml 오버라이드
  - 빠른 검색을 위한 테이블→그룹 인덱스 (TABLE_TO_GROUPS)

검증·추천·프롬프트 생성 등 **로직**은 similar_table_resolver.py 에 위치한다.

핵심 함수:
    - _load_custom_similar_tables: YAML에서 커스텀 그룹 정의를 로드하여 기본값 대체

주요 데이터:
    - SIMILAR_TABLE_GROUPS: 활성 유사 테이블 그룹 목록 (커스텀 우선)
    - TABLE_TO_GROUPS: 테이블명 → 소속 그룹 목록 인덱스 (O(1) 검색)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class SimilarTable:
    """유사 그룹 내 개별 테이블 정의."""

    table_name: str
    purpose: str  # 이 테이블의 용도
    update_cycle: str  # 갱신주기 (일배치, 월배치, 실시간 등)
    suitable_for: list[str] = field(default_factory=list)  # 적합한 요청 유형
    unsuitable_for: list[str] = field(default_factory=list)  # 부적합한 요청 유형
    signal_keywords: list[str] = field(default_factory=list)  # 이 테이블을 선택해야 하는 신호어


@dataclass
class SimilarTableGroup:
    """유사 테이블 그룹."""

    group_id: str
    domain: str  # 도메인 (여신, 수신, 거래 등)
    description: str
    tables: dict[str, SimilarTable]  # table_name → SimilarTable
    disambiguation_rule: str  # 구분 핵심 규칙 (한 문장)


# ──────────────────────────────────────────────────────────────
# 기본 유사 테이블 그룹 정의
# ──────────────────────────────────────────────────────────────

_DEFAULT_SIMILAR_TABLE_GROUPS: list[SimilarTableGroup] = [
    SimilarTableGroup(
        group_id="loan_overdue",
        domain="여신",
        description="대출 연체 관련 테이블 그룹",
        tables={
            "TB_LOAN_INFO": SimilarTable(
                table_name="TB_LOAN_INFO",
                purpose="건별 현재 대출 상태 (개별 대출의 연체 여부, 금액, 금리)",
                update_cycle="일배치",
                suitable_for=[
                    "현재 연체 중인 대출 건수",
                    "개별 대출 목록",
                    "연체 대출의 평균 금리",
                    "특정 조건의 대출 필터링",
                    "대출 유형별 건수/금액",
                ],
                unsuitable_for=[
                    "월별 연체율 추이",
                    "지점별 연체율 통계",
                    "연체율 산출 (정확한 비율)",
                    "기간별 연체 통계 비교",
                ],
                signal_keywords=[
                    "건수", "목록", "리스트", "건별", "개별",
                    "현재", "지금", "금리", "잔액",
                ],
            ),
            "TB_LOAN_OVERDUE_STAT": SimilarTable(
                table_name="TB_LOAN_OVERDUE_STAT",
                purpose="월말 기준 연체 통계 집계 (지점별/유형별 연체율, 연체금액)",
                update_cycle="월배치",
                suitable_for=[
                    "연체율 추이",
                    "지점별 연체율 현황",
                    "월별 연체 통계",
                    "연체율 산출",
                    "연체 통계 비교",
                ],
                unsuitable_for=[
                    "개별 대출 목록",
                    "특정 대출의 연체 여부",
                    "실시간 연체 현황",
                    "대출 금리 정보",
                ],
                signal_keywords=[
                    "연체율", "추이", "통계", "비율", "월별",
                    "분기별", "지점별", "산출", "비교",
                ],
            ),
        },
        disambiguation_rule=(
            "건별 현황/목록/건수 → TB_LOAN_INFO, "
            "연체율/추이/통계/비율 산출 → TB_LOAN_OVERDUE_STAT"
        ),
    ),
    SimilarTableGroup(
        group_id="deposit_balance",
        domain="수신",
        description="예금 잔액 관련 테이블 그룹",
        tables={
            "TB_DEPOSIT_INFO": SimilarTable(
                table_name="TB_DEPOSIT_INFO",
                purpose="계좌별 현재 상태 (잔액, 상품, 계좌상태)",
                update_cycle="일배치",
                suitable_for=[
                    "현재 계좌 수",
                    "현재 총 예금 잔액",
                    "상품별 잔액 현황",
                    "계좌 상태별 현황",
                    "고객별 예금 현황",
                ],
                unsuitable_for=[
                    "일별 잔액 변동",
                    "기간별 잔액 추이",
                    "평균 잔액 산출 (기간)",
                ],
                signal_keywords=[
                    "현재", "현황", "잔액", "계좌 수",
                    "상품별", "총", "합계",
                ],
            ),
            "TB_DEPOSIT_DAILY_BAL": SimilarTable(
                table_name="TB_DEPOSIT_DAILY_BAL",
                purpose="계좌별 일별 잔액 스냅샷 (기간별 추이 분석용)",
                update_cycle="일배치",
                suitable_for=[
                    "일별 잔액 추이",
                    "기간별 잔액 변동",
                    "평균 잔액 산출",
                    "잔액 증감 분석",
                ],
                unsuitable_for=[
                    "현재 시점 잔액 조회",
                    "계좌 상태 확인",
                    "상품 정보 조회",
                ],
                signal_keywords=[
                    "추이", "변동", "증감", "일별", "평균 잔액",
                    "기간", "변화",
                ],
            ),
        },
        disambiguation_rule=(
            "현재 시점 잔액/현황 → TB_DEPOSIT_INFO, "
            "기간별 추이/변동/평균 잔액 → TB_DEPOSIT_DAILY_BAL"
        ),
    ),
    SimilarTableGroup(
        group_id="loan_detail_vs_summary",
        domain="여신",
        description="대출 상세 vs 요약 테이블 그룹",
        tables={
            "TB_LOAN_INFO": SimilarTable(
                table_name="TB_LOAN_INFO",
                purpose="건별 대출 상세 정보 (개별 대출의 모든 속성)",
                update_cycle="일배치",
                suitable_for=[
                    "대출 실행 건수/금액",
                    "대출 유형별 현황",
                    "고객별 대출 목록",
                    "특정 조건 대출 필터링",
                    "개별 대출 상세 조회",
                ],
                unsuitable_for=[
                    "지점별 월말 집계 통계",
                    "경영 지표 산출",
                ],
                signal_keywords=[
                    "건수", "금액", "목록", "실행", "유형별",
                    "고객별", "신규", "대출",
                ],
            ),
            "TB_LOAN_MONTHLY_STAT": SimilarTable(
                table_name="TB_LOAN_MONTHLY_STAT",
                purpose="월말 기준 대출 종합 통계 (지점별/유형별 잔액, 건수 집계)",
                update_cycle="월배치",
                suitable_for=[
                    "월별 대출 잔액 추이",
                    "지점별 대출 실적 비교",
                    "대출 성장률 산출",
                ],
                unsuitable_for=[
                    "개별 대출 조회",
                    "고객별 대출 상세",
                    "실시간 대출 현황",
                ],
                signal_keywords=[
                    "월별", "추이", "실적", "성장률", "통계",
                ],
            ),
        },
        disambiguation_rule=(
            "건별 상세/실행/목록 → TB_LOAN_INFO, "
            "월별 통계/추이/실적 비교 → TB_LOAN_MONTHLY_STAT"
        ),
    ),
    SimilarTableGroup(
        group_id="transaction_detail_vs_summary",
        domain="거래",
        description="거래 상세 vs 요약 테이블 그룹",
        tables={
            "TB_TRANSACTION": SimilarTable(
                table_name="TB_TRANSACTION",
                purpose="건별 거래 내역 (대용량, 반드시 날짜 조건 필요)",
                update_cycle="실시간",
                suitable_for=[
                    "특정 기간 거래 건수/금액",
                    "거래 유형별 현황",
                    "개별 거래 내역 조회",
                    "일별 거래 현황",
                ],
                unsuitable_for=[
                    "월별 거래 통계 추이 (장기간)",
                    "지점별 거래 실적 비교 (월 단위)",
                ],
                signal_keywords=[
                    "거래", "이체", "입금", "출금", "건별",
                    "내역", "이번 달",
                ],
            ),
            "TB_TXN_MONTHLY_STAT": SimilarTable(
                table_name="TB_TXN_MONTHLY_STAT",
                purpose="월별 거래 집계 통계 (지점별/유형별 월 합산)",
                update_cycle="월배치",
                suitable_for=[
                    "월별 거래량 추이",
                    "지점별 거래 실적 비교",
                    "거래량 성장률",
                ],
                unsuitable_for=[
                    "개별 거래 내역",
                    "일별 거래 현황",
                    "특정 계좌 거래 조회",
                ],
                signal_keywords=[
                    "월별", "추이", "실적", "성장", "통계",
                ],
            ),
        },
        disambiguation_rule=(
            "건별 거래 내역/특정 기간 → TB_TRANSACTION, "
            "월별 거래 통계/추이 → TB_TXN_MONTHLY_STAT"
        ),
    ),
    SimilarTableGroup(
        group_id="customer_snapshot_vs_history",
        domain="고객",
        description="고객 현재 상태 vs 이력 테이블 그룹",
        tables={
            "TB_CUST_INFO": SimilarTable(
                table_name="TB_CUST_INFO",
                purpose="고객 현재 기본 정보 (이름, 유형, 등록일, 지점)",
                update_cycle="일배치",
                suitable_for=[
                    "현재 고객 수",
                    "신규 고객 현황",
                    "고객 유형별 현황",
                    "지점별 고객 수",
                ],
                unsuitable_for=[
                    "고객 등급 변동 이력",
                    "고객 이탈 추이",
                ],
                signal_keywords=[
                    "고객 수", "신규", "유형별", "현황",
                    "목록", "등록",
                ],
            ),
            "TB_CUST_GRADE_HIST": SimilarTable(
                table_name="TB_CUST_GRADE_HIST",
                purpose="고객 등급 변동 이력 (월별 등급 변경 추적)",
                update_cycle="월배치",
                suitable_for=[
                    "등급 변동 추이",
                    "VIP 승급/강등 현황",
                    "등급별 고객 수 추이",
                ],
                unsuitable_for=[
                    "현재 고객 기본 정보",
                    "신규 고객 현황",
                ],
                signal_keywords=[
                    "등급 변동", "승급", "강등", "이력",
                    "등급 추이",
                ],
            ),
        },
        disambiguation_rule=(
            "현재 고객 정보/신규/목록 → TB_CUST_INFO, "
            "등급 변동/승급/강등 이력 → TB_CUST_GRADE_HIST"
        ),
    ),
]

# ──────────────────────────────────────────────────────────────
# resources/domain/similar_tables.yaml 외부 파일 오버라이드
# ──────────────────────────────────────────────────────────────


def _load_custom_similar_tables() -> list[SimilarTableGroup] | None:
    """resources/domain/similar_tables.yaml 에서 유사 테이블 그룹을 로드한다."""
    from src.utils.resource_loader import load_yaml

    data = load_yaml("domain/similar_tables.yaml", None)
    if data is None:
        return None

    groups_raw = data.get("groups", [])
    if not groups_raw:
        return None

    loaded: list[SimilarTableGroup] = []
    for g in groups_raw:
        tables: dict[str, SimilarTable] = {}
        for tname, tinfo in g.get("tables", {}).items():
            tables[tname] = SimilarTable(
                table_name=tname,
                purpose=tinfo.get("purpose", ""),
                update_cycle=tinfo.get("update_cycle", ""),
                suitable_for=tinfo.get("suitable_for", []),
                unsuitable_for=tinfo.get("unsuitable_for", []),
                signal_keywords=tinfo.get(
                    "signal_keywords", [],
                ),
            )
        loaded.append(SimilarTableGroup(
            group_id=g.get("group_id", ""),
            domain=g.get("domain", ""),
            description=g.get("description", ""),
            tables=tables,
            disambiguation_rule=g.get(
                "disambiguation_rule", "",
            ),
        ))

    logger.info(
        "커스텀 유사 테이블 그룹 로드 완료",
        count=len(loaded),
    )
    return loaded


_custom_groups = _load_custom_similar_tables()
SIMILAR_TABLE_GROUPS: list[SimilarTableGroup] = (
    _custom_groups
    if _custom_groups is not None
    else _DEFAULT_SIMILAR_TABLE_GROUPS
)

# 빠른 검색을 위한 인덱스
TABLE_TO_GROUPS: dict[str, list[SimilarTableGroup]] = {}
for _group in SIMILAR_TABLE_GROUPS:
    for _tname in _group.tables:
        TABLE_TO_GROUPS.setdefault(_tname, []).append(_group)
