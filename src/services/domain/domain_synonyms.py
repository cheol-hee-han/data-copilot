"""자연어 질의 정규화용 동의어 사전 및 문서유형 레지스트리.

역할:
  1. Phase 1 프롬프트에 주입 → LLM이 normalized_term 채울 때 사용
  2. 전처리 단계에서 약어 확장
  3. 검색 키워드 확장(query expansion)
  4. OUTPUT_TEMPLATE_REGISTRY: "명세", "현황" 등 문서유형별 기대 컬럼 매핑

은행 도메인 특화:
  기존 도메인 사전(domain_dictionary.py)은 테이블/컬럼 매핑용이고,
  이 사전은 LLM 프롬프트 주입 및 용어 표준화용이다.
  resources/domain/domain_synonyms.yaml 로 외부 오버라이드 가능.

핵심 함수:
    - get_synonym_prompt_text: 전체 동의어 사전을 LLM 프롬프트 주입 텍스트로 변환
    - get_output_template_prompt_text: OUTPUT_TEMPLATE_REGISTRY를 프롬프트 주입 텍스트로 변환
    - build_reverse_lookup: 동의어 → 표준용어 역매핑 딕셔너리 생성 (검색 키워드 확장용)
"""

from __future__ import annotations

from src.utils.logger import get_logger

logger = get_logger(__name__)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. MEASURE (측정값/지표) 동의어 — 은행 도메인 특화
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_DEFAULT_MEASURE_SYNONYMS: dict[str, list[str]] = {
    # 여신 (대출)
    "여신잔액": ["대출잔액", "대출금액", "론잔액", "여신금액", "대여금잔액"],
    "신규여신액": ["신규대출액", "신규대출금액", "신규실행액", "여신실행액"],
    "연체금액": ["연체액", "연체잔액", "부실채권액"],
    "연체율": ["연체비율", "연체률"],
    "부실채권비율": ["NPL비율", "NPL율", "고정이하여신비율"],
    # 수신 (예금)
    "예금잔액": ["수신잔액", "예금금액", "수신금액", "예적금잔액"],
    "신규수신액": ["신규예금액", "신규예치액"],
    # 거래
    "거래금액": ["이체금액", "송금금액", "거래액", "입출금액"],
    "거래건수": ["이체건수", "송금건수", "트랜잭션수"],
    # 고객
    "고객수": ["회원수", "가입자수", "고객건수", "명수"],
    "신규고객수": ["신규가입자수", "신규회원수"],
    # 금융지표
    "BIS비율": ["BIS자기자본비율", "자기자본비율", "BIS ratio"],
    "NIM": ["순이자마진", "net interest margin"],
    "LCR": ["유동성커버리지비율", "liquidity coverage ratio"],
    "ROA": ["총자산이익률", "총자산수익률"],
    "ROE": ["자기자본이익률", "자기자본수익률"],
    # 일반
    "매출액": ["매출", "수익", "수익금", "revenue"],
    "이익": ["영업이익", "순이익", "당기순이익", "profit"],
    "비용": ["원가", "경비", "지출", "cost"],
    "건수": ["횟수", "카운트", "count"],
    "금액": ["액수", "합계", "총액"],
    "잔액": ["밸런스", "balance", "현재잔액"],
    "비율": ["퍼센트", "비중", "%", "점유율"],
    "평균": ["평균값", "avg", "mean"],
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. ENTITY (엔티티) 동의어 — 은행 도메인 특화
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_DEFAULT_ENTITY_SYNONYMS: dict[str, list[str]] = {
    "고객": ["회원", "차주", "예금주", "수신고객", "여신고객", "거래고객", "customer"],
    "개인고객": ["개인", "개인회원", "리테일고객", "소매고객"],
    "기업고객": ["법인", "기업", "법인고객", "기업체", "사업자"],
    "계좌": ["어카운트", "통장", "예금계좌", "수신계좌", "account"],
    "대출": ["여신", "론", "대여금", "대출금", "credit", "loan"],
    "예금": ["수신", "예적금", "예치금", "deposit"],
    "거래": ["트랜잭션", "이체", "송금", "입출금", "transaction"],
    "카드": ["신용카드", "체크카드", "카드거래", "card"],
    "지점": ["영업점", "지사", "브랜치", "branch", "영업부서"],
    "직원": ["행원", "임직원", "담당자", "사원", "employee"],
    "상품": ["금융상품", "상품코드", "product"],
    "담보": ["담보물", "보증", "collateral"],
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. DIMENSION (차원) 동의어
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_DEFAULT_DIMENSION_SYNONYMS: dict[str, list[str]] = {
    "지역": ["지방", "권역", "시도", "도시", "영업구역", "area", "region"],
    "기간": ["기간", "날짜", "일자", "년도", "연도", "분기", "반기", "월", "주", "일"],
    "채널": ["채널", "경로", "유입경로", "channel", "영업채널"],
    "등급": ["등급", "신용등급", "고객등급", "tier", "grade", "VIP"],
    "부서": ["부서", "팀", "본부", "실", "department"],
    "상품유형": ["상품종류", "상품분류", "상품군", "product type"],
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. TIME (시간) 동의어
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_DEFAULT_TIME_SYNONYMS: dict[str, list[str]] = {
    "올해": ["올해", "금년", "이번 해", "이번 년도"],
    "작년": ["작년", "전년", "지난해", "전년도", "직전년도"],
    "이번달": ["이번달", "이번 달", "당월", "금월"],
    "지난달": ["지난달", "지난 달", "전월", "전달", "직전월"],
    "이번분기": ["이번분기", "이번 분기", "당분기", "금분기"],
    "지난분기": ["지난분기", "지난 분기", "전분기", "직전분기"],
    "상반기": ["상반기", "1반기", "H1"],
    "하반기": ["하반기", "2반기", "H2"],
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. ABBREVIATION MAP (약어 → 풀네임)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_DEFAULT_ABBREVIATION_MAP: dict[str, str] = {
    "YoY": "전년동기대비",
    "MoM": "전월대비",
    "QoQ": "전분기대비",
    "YTD": "연초부터현재까지",
    "MTD": "월초부터현재까지",
    "QTD": "분기초부터현재까지",
    "NPL": "고정이하여신",
    "BIS": "BIS자기자본비율",
    "NIM": "순이자마진",
    "LCR": "유동성커버리지비율",
    "ROA": "총자산이익률",
    "ROE": "자기자본이익률",
    "ARPU": "객단가",
    "CVR": "전환율",
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 6. OUTPUT TEMPLATE REGISTRY (문서유형별 기대 컬럼 사전) — 은행 도메인 특화
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_DEFAULT_OUTPUT_TEMPLATE_REGISTRY: dict[str, dict] = {
    # ── 명세서 유형 ──
    "거래명세": {
        "triggers": ["거래 명세", "거래명세서", "거래 내역서", "입출금명세"],
        "format": "SPEC_SHEET",
        "expected_columns": [
            "거래일자", "거래번호", "계좌번호", "거래유형",
            "입금액", "출금액", "잔액", "거래상대", "적요",
        ],
        "required_entities": ["거래", "계좌"],
        "note": None,
    },
    "여신명세": {
        "triggers": ["여신 명세", "대출 명세", "대출명세서", "여신내역"],
        "format": "SPEC_SHEET",
        "expected_columns": [
            "대출번호", "고객명", "대출종류", "실행일자", "만기일자",
            "대출금액", "잔액", "금리", "상환방식", "담보유형",
        ],
        "required_entities": ["대출", "고객"],
        "note": None,
    },
    "수신명세": {
        "triggers": ["수신 명세", "예금 명세", "예금명세서", "수신내역"],
        "format": "SPEC_SHEET",
        "expected_columns": [
            "계좌번호", "고객명", "상품명", "개설일자", "만기일자",
            "잔액", "금리", "계좌상태",
        ],
        "required_entities": ["예금", "고객", "계좌"],
        "note": None,
    },
    "연체명세": {
        "triggers": ["연체 명세", "연체내역", "연체현황서", "부실채권명세"],
        "format": "SPEC_SHEET",
        "expected_columns": [
            "대출번호", "고객명", "대출종류", "대출잔액",
            "연체금액", "연체일수", "연체등급", "담당지점",
        ],
        "required_entities": ["대출", "고객"],
        "note": "연체등급 = 연체일수 기반 분류 (1개월 미만, 1~3개월, 3개월 이상 등)",
    },
    "카드거래명세": {
        "triggers": ["카드 명세", "카드거래명세", "카드내역", "카드이용명세"],
        "format": "SPEC_SHEET",
        "expected_columns": [
            "거래일자", "카드번호", "가맹점명", "거래유형",
            "거래금액", "할부개월", "승인번호",
        ],
        "required_entities": ["카드", "거래"],
        "note": None,
    },
    # ── 현황/요약 유형 ──
    "여신현황": {
        "triggers": ["여신 현황", "대출 현황", "여신현황표"],
        "format": "SUMMARY",
        "expected_columns": [
            "기간", "총대출잔액", "신규실행건수", "신규실행액",
            "상환건수", "상환액", "연체건수", "연체금액", "연체율",
        ],
        "required_entities": ["대출"],
        "note": "연체율 = 연체금액 / 총대출잔액 × 100",
    },
    "수신현황": {
        "triggers": ["수신 현황", "예금 현황", "수신현황표"],
        "format": "SUMMARY",
        "expected_columns": [
            "기간", "총예금잔액", "신규개설건수", "신규개설액",
            "해지건수", "해지액", "정기예금잔액", "보통예금잔액",
        ],
        "required_entities": ["예금"],
        "note": None,
    },
    "고객현황": {
        "triggers": ["고객 현황", "회원 현황", "고객현황표"],
        "format": "SUMMARY",
        "expected_columns": [
            "기간", "총고객수", "신규고객수", "이탈고객수",
            "활성고객수", "휴면고객수",
        ],
        "required_entities": ["고객"],
        "note": None,
    },
    "지점실적현황": {
        "triggers": ["지점 실적", "영업점 현황", "지점현황", "지점별 실적"],
        "format": "SUMMARY",
        "expected_columns": [
            "지점명", "여신잔액", "수신잔액", "신규고객수",
            "거래건수", "수익", "목표달성률",
        ],
        "required_entities": ["지점"],
        "note": "목표달성률 = 실적 / 목표 × 100",
    },
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 7. 외부 오버라이드 로딩
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _load_synonyms() -> dict[str, dict[str, list[str]]]:
    """resources/domain/domain_synonyms.yaml 에서 동의어 사전을 로드한다."""
    from src.utils.resource_loader import load_yaml

    data = load_yaml("domain/domain_synonyms.yaml", None)
    if data is None:
        return {
            "measures": _DEFAULT_MEASURE_SYNONYMS,
            "entities": _DEFAULT_ENTITY_SYNONYMS,
            "dimensions": _DEFAULT_DIMENSION_SYNONYMS,
            "time": _DEFAULT_TIME_SYNONYMS,
        }
    return {
        "measures": data.get("measures", _DEFAULT_MEASURE_SYNONYMS),
        "entities": data.get("entities", _DEFAULT_ENTITY_SYNONYMS),
        "dimensions": data.get("dimensions", _DEFAULT_DIMENSION_SYNONYMS),
        "time": data.get("time", _DEFAULT_TIME_SYNONYMS),
    }


def _load_output_templates() -> dict[str, dict]:
    """resources/domain/output_templates.yaml 에서 템플릿을 로드한다."""
    from src.utils.resource_loader import load_yaml

    data = load_yaml("domain/output_templates.yaml", None)
    if data is None:
        return _DEFAULT_OUTPUT_TEMPLATE_REGISTRY
    return data.get("templates", _DEFAULT_OUTPUT_TEMPLATE_REGISTRY)


def _load_abbreviations() -> dict[str, str]:
    """resources/domain/domain_synonyms.yaml 에서 약어 매핑을 로드한다."""
    from src.utils.resource_loader import load_yaml

    data = load_yaml("domain/domain_synonyms.yaml", None)
    if data is None:
        return _DEFAULT_ABBREVIATION_MAP
    return data.get("abbreviations", _DEFAULT_ABBREVIATION_MAP)


# 모듈 로드 시 초기화
ALL_SYNONYMS: dict[str, dict[str, list[str]]] = _load_synonyms()
OUTPUT_TEMPLATE_REGISTRY: dict[str, dict] = _load_output_templates()
ABBREVIATION_MAP: dict[str, str] = _load_abbreviations()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 8. 유틸리티 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_synonym_prompt_text() -> str:
    """동의어 사전을 LLM 프롬프트에 주입할 텍스트로 변환한다."""
    lines: list[str] = []
    for category, syn_dict in ALL_SYNONYMS.items():
        lines.append(f"\n[{category}]")
        for standard, variants in syn_dict.items():
            lines.append(f'  "{standard}" ← {", ".join(variants)}')
    return "\n".join(lines)


def get_output_template_prompt_text() -> str:
    """OUTPUT_TEMPLATE_REGISTRY를 LLM 프롬프트에 주입할 텍스트로 변환한다."""
    lines: list[str] = []
    for doc_type, info in OUTPUT_TEMPLATE_REGISTRY.items():
        triggers = ", ".join(info["triggers"])
        columns = ", ".join(info["expected_columns"])
        lines.append(f'\n  "{doc_type}":')
        lines.append(f"    트리거: {triggers}")
        lines.append(f'    format: {info["format"]}')
        lines.append(f"    기대컬럼: [{columns}]")
        lines.append(f'    필요엔티티: {info["required_entities"]}')
        if info.get("note"):
            lines.append(f'    참고: {info["note"]}')
    return "\n".join(lines)


def build_reverse_lookup() -> dict[str, str]:
    """동의어 → 표준용어 역매핑 딕셔너리를 생성한다."""
    reverse: dict[str, str] = {}
    for syn_dict in ALL_SYNONYMS.values():
        for standard, variants in syn_dict.items():
            for v in variants:
                reverse[v.lower()] = standard
    return reverse
