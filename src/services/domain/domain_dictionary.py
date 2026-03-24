"""금융 도메인 사전.

금융 업무 용어와 DB 테이블/컬럼 간의 매핑을 관리한다.
사용자의 자연어 표현을 DB 스키마로 변환하는 데 활용.

resources/domain/domain_dictionary.yaml 이 존재하면 이 파일의 기본 사전 대신
외부 YAML 파일을 사용한다. 파일이 없으면 아래 기본 사전이 적용된다.

카테고리 목록:
  고객      - 고객 유형, 등급, 상태
  여신      - 대출 상품, 자산건전성, 연체 관련
  수신      - 예금 상품, 금리, 잔액 관련
  거래      - 입출금, 이체 등 거래 유형
  카드      - 신용/체크카드, 이용 관련
  외환      - 환율, 외화예금, 해외송금
  금융지표  - NIM, BIS, LCR 등 경영/건전성 지표
  조직      - 지점, 부서 등 조직 정보
  시간      - 당월/전월 등 기간 표현

핵심 함수:
    - lookup_terms: 사용자 질의에서 term/aliases 부분 일치로 도메인 용어를 검색
    - format_domain_context: 매칭된 용어를 SQL 생성 프롬프트에 주입할 텍스트로 변환
    - format_domain_context_grouped: 카테고리별 그룹화된 프롬프트 텍스트 변환
    - get_terms_by_category: 특정 카테고리의 용어 목록 반환
    - get_all_categories: 등록된 카테고리 목록 반환 (중복 제거, 순서 유지)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.utils.logger import get_logger

_logger = get_logger(__name__)


@dataclass
class DomainTerm:
    """도메인 용어 정의."""

    term: str  # 사용자가 사용하는 표현
    aliases: list[str] = field(default_factory=list)  # 동의어/유사 표현
    table_name: str = ""  # 관련 테이블 (향후 추가될 테이블 포함)
    column_name: str = ""  # 관련 컬럼
    condition: str = ""  # SQL 조건식
    description: str = ""  # 설명
    category: str = ""  # 분류 (여신, 수신, 고객 등)


# 금융 도메인 용어 사전
DOMAIN_DICTIONARY: list[DomainTerm] = [
    # === 고객 관련 ===
    DomainTerm(
        term="신규 고객",
        aliases=["신규고객", "새 고객", "신규 가입", "새로 가입"],
        table_name="TB_CUST_INFO",
        column_name="REG_DT",
        condition="REG_DT >= DATE_TRUNC('month', CURRENT_DATE)",
        description="해당 기간 내 신규 등록된 고객",
        category="고객",
    ),
    DomainTerm(
        term="개인 고객",
        aliases=["개인고객", "개인", "리테일"],
        table_name="TB_CUST_INFO",
        column_name="CUST_TYPE_CD",
        condition="CUST_TYPE_CD = '01'",
        description="개인 고객 유형",
        category="고객",
    ),
    DomainTerm(
        term="기업 고객",
        aliases=["기업고객", "기업", "법인", "법인고객"],
        table_name="TB_CUST_INFO",
        column_name="CUST_TYPE_CD",
        condition="CUST_TYPE_CD = '02'",
        description="기업 고객 유형",
        category="고객",
    ),

    # === 여신(대출) 관련 ===
    DomainTerm(
        term="여신",
        aliases=["대출", "론", "대여금", "여신거래"],
        table_name="TB_LOAN_INFO",
        description="대출 관련 데이터",
        category="여신",
    ),
    DomainTerm(
        term="신용대출",
        aliases=["신용여신", "무담보대출", "신용론"],
        table_name="TB_LOAN_INFO",
        column_name="LOAN_TYPE_CD",
        condition="LOAN_TYPE_CD = '01'",
        category="여신",
    ),
    DomainTerm(
        term="담보대출",
        aliases=["담보여신", "유담보대출", "주담대", "주택담보대출"],
        table_name="TB_LOAN_INFO",
        column_name="LOAN_TYPE_CD",
        condition="LOAN_TYPE_CD = '02'",
        category="여신",
    ),
    DomainTerm(
        term="보증대출",
        aliases=["보증여신", "보증부대출"],
        table_name="TB_LOAN_INFO",
        column_name="LOAN_TYPE_CD",
        condition="LOAN_TYPE_CD = '03'",
        category="여신",
    ),
    DomainTerm(
        term="연체",
        aliases=["연체대출", "미상환", "부실"],
        table_name="TB_LOAN_INFO",
        column_name="OVERDUE_YN",
        condition="OVERDUE_YN = 'Y'",
        category="여신",
    ),
    DomainTerm(
        term="대출 실행",
        aliases=["대출실행", "여신실행", "대출 집행", "신규 대출"],
        table_name="TB_LOAN_INFO",
        column_name="LOAN_DT",
        description="대출이 실행된 건",
        category="여신",
    ),

    # === 수신(예금) 관련 ===
    DomainTerm(
        term="수신",
        aliases=["예금", "예적금", "수신거래"],
        table_name="TB_DEPOSIT_INFO",
        description="예금 관련 데이터",
        category="수신",
    ),
    DomainTerm(
        term="예금 잔액",
        aliases=["수신잔액", "잔액", "잔고", "예금잔고"],
        table_name="TB_DEPOSIT_INFO",
        column_name="ACCT_BAL",
        description="계좌 잔액 합계",
        category="수신",
    ),
    DomainTerm(
        term="정상 계좌",
        aliases=["활성계좌", "살아있는 계좌"],
        table_name="TB_DEPOSIT_INFO",
        column_name="ACCT_STATUS_CD",
        condition="ACCT_STATUS_CD = '01'",
        category="수신",
    ),
    DomainTerm(
        term="휴면 계좌",
        aliases=["휴면", "장기미거래계좌"],
        table_name="TB_DEPOSIT_INFO",
        column_name="ACCT_STATUS_CD",
        condition="ACCT_STATUS_CD = '03'",
        category="수신",
    ),

    # === 거래 관련 ===
    DomainTerm(
        term="입금",
        aliases=["입금거래", "자금유입"],
        table_name="TB_TRANSACTION",
        column_name="TXN_TYPE_CD",
        condition="TXN_TYPE_CD = '01'",
        category="거래",
    ),
    DomainTerm(
        term="출금",
        aliases=["출금거래", "자금유출", "인출"],
        table_name="TB_TRANSACTION",
        column_name="TXN_TYPE_CD",
        condition="TXN_TYPE_CD = '02'",
        category="거래",
    ),
    DomainTerm(
        term="이체",
        aliases=["계좌이체", "송금", "자금이체"],
        table_name="TB_TRANSACTION",
        column_name="TXN_TYPE_CD",
        condition="TXN_TYPE_CD = '03'",
        category="거래",
    ),

    # === 지점 관련 ===
    DomainTerm(
        term="지점",
        aliases=["영업점", "부점", "점포"],
        table_name="TB_BRANCH_INFO",
        description="지점 정보",
        category="조직",
    ),

    # === 시간 관련 ===
    DomainTerm(
        term="이번 달",
        aliases=["금월", "당월", "이달"],
        condition=">= DATE_TRUNC('month', CURRENT_DATE)",
        description="현재 월 시작일부터",
        category="시간",
    ),
    DomainTerm(
        term="지난 달",
        aliases=["전월", "작달", "지난달"],
        condition=(
            "BETWEEN DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '1 month'"
            " AND DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '1 day'"
        ),
        description="전월 1일~말일",
        category="시간",
    ),
    DomainTerm(
        term="올해",
        aliases=["금년", "당해", "이번 해"],
        condition=">= DATE_TRUNC('year', CURRENT_DATE)",
        description="현재 연도 시작일부터",
        category="시간",
    ),
    DomainTerm(
        term="지난 분기",
        aliases=["전분기", "직전분기"],
        condition=(
            "BETWEEN DATE_TRUNC('quarter', CURRENT_DATE) - INTERVAL '3 months'"
            " AND DATE_TRUNC('quarter', CURRENT_DATE) - INTERVAL '1 day'"
        ),
        description="직전 분기",
        category="시간",
    ),

    # ---------------------------------------------------------------
    # 아래는 보강 용어 (2026-03-18 추가)
    # ---------------------------------------------------------------

    # === 고객 심화 ===
    DomainTerm(
        term="VIP 고객",
        aliases=["VIP", "우수고객", "프리미엄고객", "우량고객", "TOP고객"],
        table_name="TB_CUST_INFO",
        column_name="CUST_GRADE_CD",
        condition="CUST_GRADE_CD IN ('VIP', 'VVIP', '01', '02')",
        description="은행 내부 기준 우수 등급 고객. 정확한 코드값은 TB_CUST_GRADE 테이블 참조",
        category="고객",
    ),
    DomainTerm(
        term="일반 고객",
        aliases=["일반고객", "일반등급", "비우수고객"],
        table_name="TB_CUST_INFO",
        column_name="CUST_GRADE_CD",
        condition="CUST_GRADE_CD NOT IN ('VIP', 'VVIP', '01', '02')",
        description="VIP 이외의 일반 등급 고객",
        category="고객",
    ),
    DomainTerm(
        term="휴면 고객",
        aliases=["장기미거래고객", "비활성고객", "미거래고객"],
        table_name="TB_CUST_INFO",
        column_name="CUST_STATUS_CD",
        condition="CUST_STATUS_CD = '03'",
        description="일정 기간(통상 1년) 이상 거래가 없는 고객",
        category="고객",
    ),
    DomainTerm(
        term="탈퇴 고객",
        aliases=["해지고객", "이탈고객", "거래종료고객"],
        table_name="TB_CUST_INFO",
        column_name="CUST_STATUS_CD",
        condition="CUST_STATUS_CD = '09'",
        description="모든 거래를 해지하고 탈퇴한 고객",
        category="고객",
    ),

    # === 여신 심화 ===
    DomainTerm(
        term="가계대출",
        aliases=["가계여신", "개인대출", "소매대출", "리테일대출"],
        table_name="TB_LOAN_INFO",
        column_name="LOAN_SECTOR_CD",
        condition="LOAN_SECTOR_CD = '10'",
        description="개인(가계)을 대상으로 실행된 대출. 주택담보, 신용, 전세 등 포함",
        category="여신",
    ),
    DomainTerm(
        term="기업대출",
        aliases=["기업여신", "법인대출", "기업금융", "corporate loan"],
        table_name="TB_LOAN_INFO",
        column_name="LOAN_SECTOR_CD",
        condition="LOAN_SECTOR_CD = '20'",
        description="기업(법인·개인사업자)을 대상으로 실행된 대출",
        category="여신",
    ),
    DomainTerm(
        term="주택담보대출",
        aliases=["주담대", "아파트담보대출", "부동산담보대출", "주택저당대출", "모기지"],
        table_name="TB_LOAN_INFO",
        column_name="LOAN_TYPE_CD",
        condition="LOAN_TYPE_CD = '11'",
        description="주택(아파트·단독주택 등)을 담보로 제공하고 받는 대출",
        category="여신",
    ),
    DomainTerm(
        term="전세대출",
        aliases=["전세자금대출", "전세보증대출", "임차보증금대출"],
        table_name="TB_LOAN_INFO",
        column_name="LOAN_TYPE_CD",
        condition="LOAN_TYPE_CD = '12'",
        description="전세 보증금을 마련하기 위한 목적성 대출",
        category="여신",
    ),
    DomainTerm(
        term="정책자금대출",
        aliases=["정책대출", "정부지원대출", "기금대출", "햇살론", "새희망홀씨"],
        table_name="TB_LOAN_INFO",
        column_name="LOAN_TYPE_CD",
        condition="LOAN_TYPE_CD = '30'",
        description="정부·공공기금 재원으로 취약계층·소상공인에게 지원하는 대출",
        category="여신",
    ),
    DomainTerm(
        term="한도대출",
        aliases=["마이너스통장", "마통", "한도성대출", "한도여신", "credit line"],
        table_name="TB_LOAN_INFO",
        column_name="LOAN_TYPE_CD",
        condition="LOAN_TYPE_CD = '40'",
        description="약정 한도 내에서 자유롭게 인출·상환할 수 있는 대출(마이너스통장 포함)",
        category="여신",
    ),
    DomainTerm(
        term="여신 잔액",
        aliases=["대출잔액", "여신잔고", "대출금잔액", "outstanding"],
        table_name="TB_LOAN_INFO",
        column_name="LOAN_BAL",
        description="현재 시점 미상환 대출 원금 잔액",
        category="여신",
    ),
    DomainTerm(
        term="여신 만기",
        aliases=["대출만기", "만기일", "여신만기일", "상환기일"],
        table_name="TB_LOAN_INFO",
        column_name="LOAN_MATUR_DT",
        description="대출 원금 전액을 상환해야 하는 기일",
        category="여신",
    ),
    DomainTerm(
        term="만기도래",
        aliases=["만기예정", "만기도래여신", "만기임박"],
        table_name="TB_LOAN_INFO",
        column_name="LOAN_MATUR_DT",
        condition=(
            "LOAN_MATUR_DT BETWEEN CURRENT_DATE"
            " AND CURRENT_DATE + INTERVAL '30 days'"
        ),
        description="30일 이내 만기가 도래하는 여신. 기간은 업무 요건에 따라 조정",
        category="여신",
    ),
    # 연체등급
    DomainTerm(
        term="1개월 이상 연체",
        aliases=["1개월연체", "30일이상연체", "단기연체"],
        table_name="TB_LOAN_INFO",
        column_name="OVERDUE_MONTH_CNT",
        condition="OVERDUE_MONTH_CNT >= 1",
        description="상환 지연 기간이 1개월(30일) 이상인 여신",
        category="여신",
    ),
    DomainTerm(
        term="3개월 이상 연체",
        aliases=["3개월연체", "90일이상연체", "중기연체"],
        table_name="TB_LOAN_INFO",
        column_name="OVERDUE_MONTH_CNT",
        condition="OVERDUE_MONTH_CNT >= 3",
        description="상환 지연 기간이 3개월(90일) 이상인 여신",
        category="여신",
    ),
    DomainTerm(
        term="6개월 이상 연체",
        aliases=["6개월연체", "180일이상연체", "장기연체"],
        table_name="TB_LOAN_INFO",
        column_name="OVERDUE_MONTH_CNT",
        condition="OVERDUE_MONTH_CNT >= 6",
        description="상환 지연 기간이 6개월(180일) 이상인 장기 연체 여신",
        category="여신",
    ),
    # 자산건전성 분류
    DomainTerm(
        term="정상여신",
        aliases=["정상자산", "건전여신"],
        table_name="TB_LOAN_INFO",
        column_name="ASSET_HLTH_CD",
        condition="ASSET_HLTH_CD = '10'",
        description=(
            "자산건전성 분류 1단계. "
            "채무상환 능력이 양호하고 연체 없는 여신"
        ),
        category="여신",
    ),
    DomainTerm(
        term="요주의",
        aliases=["요주의여신", "요주의자산"],
        table_name="TB_LOAN_INFO",
        column_name="ASSET_HLTH_CD",
        condition="ASSET_HLTH_CD = '20'",
        description="자산건전성 분류 2단계. 1개월 이상 3개월 미만 연체 또는 잠재적 부실 가능성 있는 여신",
        category="여신",
    ),
    DomainTerm(
        term="고정",
        aliases=["고정여신", "고정자산", "부실여신"],
        table_name="TB_LOAN_INFO",
        column_name="ASSET_HLTH_CD",
        condition="ASSET_HLTH_CD = '30'",
        description="자산건전성 분류 3단계. 3개월 이상 연체 또는 채무상환 불가 가능성이 높은 여신",
        category="여신",
    ),
    DomainTerm(
        term="회수의문",
        aliases=["회수의문여신", "회수의문자산"],
        table_name="TB_LOAN_INFO",
        column_name="ASSET_HLTH_CD",
        condition="ASSET_HLTH_CD = '40'",
        description="자산건전성 분류 4단계. 회수 가능성이 매우 낮은 여신",
        category="여신",
    ),
    DomainTerm(
        term="추정손실",
        aliases=["추정손실여신", "추정손실자산", "상각여신"],
        table_name="TB_LOAN_INFO",
        column_name="ASSET_HLTH_CD",
        condition="ASSET_HLTH_CD = '50'",
        description="자산건전성 분류 5단계. 회수 불능으로 판단되어 손실 처리가 필요한 여신",
        category="여신",
    ),
    DomainTerm(
        term="부실채권",
        aliases=["NPL", "고정이하여신", "부실자산", "부실여신"],
        table_name="TB_LOAN_INFO",
        column_name="ASSET_HLTH_CD",
        condition="ASSET_HLTH_CD IN ('30', '40', '50')",
        description=(
            "자산건전성 분류 고정 이하(고정·회수의문·추정손실)에 해당하는 여신."
            " NPL(Non-Performing Loan)"
        ),
        category="여신",
    ),
    DomainTerm(
        term="충당금",
        aliases=["대손충당금", "충당금적립", "loan loss reserve"],
        table_name="TB_LOAN_RESERVE",
        column_name="RESERVE_AMT",
        description="부실 가능성에 대비하여 자산건전성 분류별로 적립하는 손실 준비금",
        category="여신",
    ),

    # === 수신 심화 ===
    DomainTerm(
        term="요구불예금",
        aliases=["요구불", "보통예금", "당좌예금", "수시입출금"],
        table_name="TB_DEPOSIT_INFO",
        column_name="DEPOSIT_TYPE_CD",
        condition="DEPOSIT_TYPE_CD IN ('10', '11', '12')",
        description="만기 없이 언제든지 입출금이 가능한 예금. 보통예금·당좌예금 포함",
        category="수신",
    ),
    DomainTerm(
        term="저축성예금",
        aliases=["정기예금", "정기적금", "적금", "목돈마련"],
        table_name="TB_DEPOSIT_INFO",
        column_name="DEPOSIT_TYPE_CD",
        condition="DEPOSIT_TYPE_CD IN ('20', '21', '22')",
        description="일정 기간 예치하는 만기형 예금. 정기예금·정기적금 포함",
        category="수신",
    ),
    DomainTerm(
        term="MMDA",
        aliases=["시장금리부수시입출금식예금", "Money Market Deposit Account"],
        table_name="TB_DEPOSIT_INFO",
        column_name="DEPOSIT_TYPE_CD",
        condition="DEPOSIT_TYPE_CD = '13'",
        description="시장금리를 적용하는 수시입출금식 예금. 요구불이지만 잔액에 따라 차등금리 적용",
        category="수신",
    ),
    DomainTerm(
        term="CD",
        aliases=["양도성예금증서", "Certificate of Deposit"],
        table_name="TB_DEPOSIT_INFO",
        column_name="DEPOSIT_TYPE_CD",
        condition="DEPOSIT_TYPE_CD = '30'",
        description="제3자에게 양도 가능한 만기부 예금증서. 시장 금리 지표로도 활용",
        category="수신",
    ),
    DomainTerm(
        term="RP",
        aliases=["환매조건부채권", "repo", "레포", "Repurchase Agreement"],
        table_name="TB_DEPOSIT_INFO",
        column_name="DEPOSIT_TYPE_CD",
        condition="DEPOSIT_TYPE_CD = '40'",
        description="일정 기간 후 재매입 조건으로 채권을 매도하는 단기 자금 운용 상품",
        category="수신",
    ),
    DomainTerm(
        term="예금 금리",
        aliases=["수신금리", "예금이율", "금리", "적용금리"],
        table_name="TB_DEPOSIT_INFO",
        column_name="APPLY_RATE",
        description="개별 예금 계좌에 적용되는 연이율(%)",
        category="수신",
    ),
    DomainTerm(
        term="가중평균금리",
        aliases=["평균금리", "weighted average rate", "수신가중평균금리"],
        table_name="TB_DEPOSIT_RATE_STAT",
        column_name="WAVG_RATE",
        description="잔액(또는 신규취급액) 가중 평균 금리. 산출식: SUM(잔액 × 금리) / SUM(잔액)",
        category="수신",
    ),
    DomainTerm(
        term="신규 수신",
        aliases=["신규예금", "신규취급", "예금신규", "수신신규"],
        table_name="TB_DEPOSIT_INFO",
        column_name="OPEN_DT",
        condition="OPEN_DT >= DATE_TRUNC('month', CURRENT_DATE)",
        description="해당 기간 내 신규로 개설된 예금 계좌",
        category="수신",
    ),
    DomainTerm(
        term="만기 예금",
        aliases=["만기도래예금", "만기예정예금", "만기수신"],
        table_name="TB_DEPOSIT_INFO",
        column_name="MATUR_DT",
        condition=(
            "MATUR_DT BETWEEN CURRENT_DATE"
            " AND CURRENT_DATE + INTERVAL '30 days'"
        ),
        description="30일 이내 만기가 도래하는 예금. 기간은 업무 요건에 따라 조정",
        category="수신",
    ),

    # === 카드 관련 ===
    DomainTerm(
        term="신용카드",
        aliases=["크레딧카드", "credit card", "후불카드"],
        table_name="TB_CARD_INFO",
        column_name="CARD_TYPE_CD",
        condition="CARD_TYPE_CD = '01'",
        description="후불 결제 방식의 카드. 월 이용금액을 다음 달 합산 청구",
        category="카드",
    ),
    DomainTerm(
        term="체크카드",
        aliases=["직불카드", "debit card", "즉시결제카드"],
        table_name="TB_CARD_INFO",
        column_name="CARD_TYPE_CD",
        condition="CARD_TYPE_CD = '02'",
        description="결제 즉시 계좌에서 출금되는 카드",
        category="카드",
    ),
    DomainTerm(
        term="카드 이용금액",
        aliases=["카드결제금액", "카드사용금액", "카드매출금액", "카드이용액"],
        table_name="TB_CARD_USAGE",
        column_name="USE_AMT",
        description="카드 결제 승인 금액 합계",
        category="카드",
    ),
    DomainTerm(
        term="카드 이용건수",
        aliases=["카드사용건수", "카드결제건수", "카드승인건수"],
        table_name="TB_CARD_USAGE",
        column_name="USE_CNT",
        description="카드 결제 승인 건수 합계",
        category="카드",
    ),
    DomainTerm(
        term="카드 매출",
        aliases=["카드매출", "가맹점매출", "카드결제매출"],
        table_name="TB_CARD_SALES",
        column_name="SALES_AMT",
        description="가맹점 기준 카드 결제 매출 금액",
        category="카드",
    ),
    DomainTerm(
        term="할부",
        aliases=["할부결제", "할부구매", "분할납부", "installment"],
        table_name="TB_CARD_USAGE",
        column_name="INSTALL_MONTH_CNT",
        condition="INSTALL_MONTH_CNT > 1",
        description="카드 대금을 분할하여 납부하는 결제 방식. 2개월 이상 분할",
        category="카드",
    ),
    DomainTerm(
        term="일시불",
        aliases=["일시불결제", "전액결제"],
        table_name="TB_CARD_USAGE",
        column_name="INSTALL_MONTH_CNT",
        condition="INSTALL_MONTH_CNT = 1",
        description="카드 대금을 다음 달 한 번에 전액 납부하는 결제 방식",
        category="카드",
    ),
    DomainTerm(
        term="카드론",
        aliases=["카드대출", "장기카드대출"],
        table_name="TB_CARD_LOAN",
        column_name="LOAN_AMT",
        description="신용카드를 이용한 장기 대출. 카드사에서 직접 자금을 대여",
        category="카드",
    ),
    DomainTerm(
        term="현금서비스",
        aliases=["단기카드대출", "카드현금서비스"],
        table_name="TB_CARD_CASH_ADV",
        column_name="CASH_ADV_AMT",
        description="신용카드로 단기 현금을 인출하는 서비스",
        category="카드",
    ),

    # === 외환 관련 ===
    DomainTerm(
        term="환율",
        aliases=["외환환율", "매매기준율", "기준환율", "exchange rate"],
        table_name="TB_EXCHANGE_RATE",
        column_name="BASE_RATE",
        description="원화 대비 외화 교환 비율. 매매기준율 기준",
        category="외환",
    ),
    DomainTerm(
        term="외화예금",
        aliases=["외화수신", "FX예금", "달러예금", "외화계좌"],
        table_name="TB_FX_DEPOSIT",
        column_name="CURR_CD",
        condition="CURR_CD != 'KRW'",
        description="원화 이외 외국 통화로 보유하는 예금",
        category="외환",
    ),
    DomainTerm(
        term="해외송금",
        aliases=["외화송금", "해외이체", "국제송금", "전신환송금", "remittance"],
        table_name="TB_FX_REMITTANCE",
        column_name="TXN_TYPE_CD",
        condition="TXN_TYPE_CD = 'OUT'",
        description="국내에서 해외 계좌로 외화를 송금하는 거래",
        category="외환",
    ),
    DomainTerm(
        term="해외입금",
        aliases=["외화입금", "해외수취", "수취전신환"],
        table_name="TB_FX_REMITTANCE",
        column_name="TXN_TYPE_CD",
        condition="TXN_TYPE_CD = 'IN'",
        description="해외에서 국내 계좌로 외화를 수취하는 거래",
        category="외환",
    ),
    DomainTerm(
        term="환전",
        aliases=["외화환전", "currency exchange", "원화환전"],
        table_name="TB_FX_EXCHANGE",
        description="원화와 외화 또는 외화 간 교환 거래",
        category="외환",
    ),
    DomainTerm(
        term="외화대출",
        aliases=["외화여신", "FX대출", "외화론"],
        table_name="TB_LOAN_INFO",
        column_name="CURR_CD",
        condition="CURR_CD != 'KRW'",
        description="외화로 실행된 대출",
        category="외환",
    ),

    # === 금융지표 ===
    DomainTerm(
        term="NIM",
        aliases=["순이자마진", "net interest margin", "이자마진"],
        table_name="TB_MGMT_INDICATOR",
        column_name="NIM",
        description=(
            "순이자마진. 산출식: (이자수익 - 이자비용) / 이자수익창출자산 평잔 × 100. "
            "은행의 대표적 수익성 지표"
        ),
        category="금융지표",
    ),
    DomainTerm(
        term="BIS비율",
        aliases=["BIS자기자본비율", "자기자본비율", "capital adequacy ratio", "CAR"],
        table_name="TB_MGMT_INDICATOR",
        column_name="BIS_RATIO",
        description=(
            "국제결제은행(BIS) 기준 자기자본비율. "
            "산출식: 자기자본 / 위험가중자산 × 100. "
            "바젤 협약상 최소 8% 이상 유지 의무"
        ),
        category="금융지표",
    ),
    DomainTerm(
        term="LCR",
        aliases=["유동성커버리지비율", "Liquidity Coverage Ratio", "유동성비율"],
        table_name="TB_MGMT_INDICATOR",
        column_name="LCR",
        description=(
            "유동성커버리지비율. "
            "산출식: 고유동성자산 / 향후 30일간 순현금유출액 × 100. "
            "100% 이상 유지 의무"
        ),
        category="금융지표",
    ),
    DomainTerm(
        term="충당금적립률",
        aliases=["대손충당금적립률", "충당금커버리지", "loan loss coverage ratio"],
        table_name="TB_MGMT_INDICATOR",
        column_name="RESERVE_RATIO",
        description=(
            "부실여신 대비 충당금 적립 수준. "
            "산출식: 대손충당금 잔액 / 고정이하여신(NPL) × 100"
        ),
        category="금융지표",
    ),
    DomainTerm(
        term="ROA",
        aliases=["총자산이익률", "return on assets", "자산수익률"],
        table_name="TB_MGMT_INDICATOR",
        column_name="ROA",
        description=(
            "총자산이익률. "
            "산출식: 당기순이익 / 총자산 평잔 × 100. "
            "자산 운용 효율성을 나타내는 수익성 지표"
        ),
        category="금융지표",
    ),
    DomainTerm(
        term="ROE",
        aliases=["자기자본이익률", "return on equity", "자본수익률"],
        table_name="TB_MGMT_INDICATOR",
        column_name="ROE",
        description=(
            "자기자본이익률. "
            "산출식: 당기순이익 / 자기자본 평잔 × 100. "
            "주주 관점 수익성 지표"
        ),
        category="금융지표",
    ),
    DomainTerm(
        term="연체율",
        aliases=["연체비율", "부실률", "delinquency rate"],
        table_name="TB_LOAN_OVERDUE_STAT",
        column_name="OVERDUE_RATE",
        description=(
            "연체율. "
            "산출식: 연체원금 / 총 여신 원금 잔액 × 100. "
            "1개월 이상 연체 기준 적용이 일반적이나 3개월·6개월 기준도 사용"
        ),
        category="금융지표",
    ),
    DomainTerm(
        term="고정이하여신비율",
        aliases=["NPL비율", "부실여신비율", "NPL ratio"],
        table_name="TB_MGMT_INDICATOR",
        column_name="NPL_RATIO",
        description=(
            "전체 여신 중 고정이하(NPL) 여신 비율. "
            "산출식: 고정이하여신잔액 / 총 여신잔액 × 100"
        ),
        category="금융지표",
    ),
    DomainTerm(
        term="예대율",
        aliases=["예대금리차", "loan to deposit ratio"],
        table_name="TB_MGMT_INDICATOR",
        column_name="LOAN_DEPOSIT_RATIO",
        description=(
            "대출 총액 대비 예금 총액 비율. "
            "산출식: 원화대출금 / 원화예수금 × 100. "
            "100% 초과 시 예금보다 대출이 많은 상태"
        ),
        category="금융지표",
    ),
    DomainTerm(
        term="이자수익",
        aliases=["이자이익", "interest income", "이자수입"],
        table_name="TB_PL_SUMMARY",
        column_name="INT_INCOME",
        description="대출·유가증권 등 자산 운용에서 발생한 이자 수입",
        category="금융지표",
    ),
    DomainTerm(
        term="이자비용",
        aliases=["이자지출", "interest expense", "조달비용"],
        table_name="TB_PL_SUMMARY",
        column_name="INT_EXPENSE",
        description="예금·차입금 등 부채에 지급하는 이자 비용",
        category="금융지표",
    ),

    # === 시간 표현 보강 ===
    DomainTerm(
        term="전년동기",
        aliases=["전년동기간", "작년같은기간", "YoY", "year over year"],
        condition=(
            "BETWEEN DATE_TRUNC('year', CURRENT_DATE) - INTERVAL '1 year' "
            "AND (DATE_TRUNC('year', CURRENT_DATE) - INTERVAL '1 year') + "
            "(CURRENT_DATE - DATE_TRUNC('year', CURRENT_DATE))"
        ),
        description="전년도 같은 기간. 올해 1월 1일~현재와 동일한 작년 구간",
        category="시간",
    ),
    DomainTerm(
        term="전년동월",
        aliases=["작년같은달", "전년동월비교", "전년동월대비"],
        condition=(
            "BETWEEN DATE_TRUNC('month', CURRENT_DATE - INTERVAL '1 year')"
            " AND DATE_TRUNC('month', CURRENT_DATE - INTERVAL '1 year')"
            " + INTERVAL '1 month' - INTERVAL '1 day'"
        ),
        description="전년도의 같은 달(월). 작년 동월 1일~말일",
        category="시간",
    ),
    DomainTerm(
        term="직전영업일",
        aliases=["전영업일", "어제(영업일기준)", "전일"],
        condition=(
            "= (SELECT MAX(BSNS_DT) FROM TB_BSNS_DAY "
            "WHERE BSNS_DT < CURRENT_DATE AND HLDY_YN = 'N')"
        ),
        description=(
            "가장 최근 영업일. 공휴일·주말을 제외한 직전 거래 가능 날짜. "
            "TB_BSNS_DAY 영업일 테이블 필요"
        ),
        category="시간",
    ),
    DomainTerm(
        term="월초",
        aliases=["이달초", "월초일", "당월초"],
        condition="= DATE_TRUNC('month', CURRENT_DATE)",
        description="현재 월의 첫째 날 (1일)",
        category="시간",
    ),
    DomainTerm(
        term="월말",
        aliases=["이달말", "월말일", "당월말"],
        condition=(
            "= DATE_TRUNC('month', CURRENT_DATE)"
            " + INTERVAL '1 month' - INTERVAL '1 day'"
        ),
        description="현재 월의 마지막 날",
        category="시간",
    ),
    DomainTerm(
        term="분기초",
        aliases=["당분기초", "이번분기초"],
        condition="= DATE_TRUNC('quarter', CURRENT_DATE)",
        description="현재 분기의 첫째 날 (1월·4월·7월·10월 1일)",
        category="시간",
    ),
    DomainTerm(
        term="분기말",
        aliases=["당분기말", "이번분기말"],
        condition=(
            "= DATE_TRUNC('quarter', CURRENT_DATE)"
            " + INTERVAL '3 months' - INTERVAL '1 day'"
        ),
        description="현재 분기의 마지막 날 (3월·6월·9월·12월 말일)",
        category="시간",
    ),
    DomainTerm(
        term="이번 분기",
        aliases=["당분기", "현재분기", "금분기"],
        condition=">= DATE_TRUNC('quarter', CURRENT_DATE)",
        description="현재 분기 시작일부터 현재까지",
        category="시간",
    ),
    DomainTerm(
        term="상반기",
        aliases=["1반기", "전반기", "H1"],
        condition=(
            "BETWEEN DATE_TRUNC('year', CURRENT_DATE)"
            " AND DATE_TRUNC('year', CURRENT_DATE)"
            " + INTERVAL '6 months' - INTERVAL '1 day'"
        ),
        description="해당 연도의 1월 1일 ~ 6월 30일",
        category="시간",
    ),
    DomainTerm(
        term="하반기",
        aliases=["2반기", "후반기", "H2"],
        condition=(
            "BETWEEN DATE_TRUNC('year', CURRENT_DATE) + INTERVAL '6 months'"
            " AND DATE_TRUNC('year', CURRENT_DATE)"
            " + INTERVAL '1 year' - INTERVAL '1 day'"
        ),
        description="해당 연도의 7월 1일 ~ 12월 31일",
        category="시간",
    ),
    DomainTerm(
        term="지난해",
        aliases=["작년", "전년", "전년도", "지난년도"],
        condition=(
            "BETWEEN DATE_TRUNC('year', CURRENT_DATE) - INTERVAL '1 year' "
            "AND DATE_TRUNC('year', CURRENT_DATE) - INTERVAL '1 day'"
        ),
        description="직전 연도 1월 1일 ~ 12월 31일",
        category="시간",
    ),
    DomainTerm(
        term="회계연도",
        aliases=["결산연도", "fiscal year", "FY"],
        condition=(
            "EXTRACT(YEAR FROM ACCT_DT)"
            " = EXTRACT(YEAR FROM CURRENT_DATE)"
        ),
        description=(
            "회계 결산 기준 연도. 은행 회계연도는 통상 1월 1일 ~ 12월 31일. "
            "컬럼명은 ACCT_DT 또는 BASE_YM 등 테이블마다 상이"
        ),
        category="시간",
    ),
    DomainTerm(
        term="기준일",
        aliases=["기준날짜", "기준시점", "base date", "기준년월일"],
        table_name="",
        column_name="BASE_DT",
        description=(
            "데이터 적재 또는 집계 기준이 되는 날짜. "
            "정보계 테이블의 BASE_DT, BASE_YM 등 컬럼에 해당"
        ),
        category="시간",
    ),
]


def lookup_terms(query: str) -> list[DomainTerm]:
    """사용자 질의에서 매칭되는 도메인 용어를 찾는다.

    term 및 aliases를 공백 제거 후 부분 일치로 검색한다.
    동일 term이 중복 매칭되지 않도록 처리한다.
    """
    query_lower = query.lower().replace(" ", "")
    matched: list[DomainTerm] = []

    for term_def in DOMAIN_DICTIONARY:
        all_expressions = [term_def.term] + term_def.aliases
        for expr in all_expressions:
            if expr.replace(" ", "").lower() in query_lower:
                matched.append(term_def)
                break

    from src.utils.tracker import (
        get_current_tracker,
    )
    _tracker = get_current_tracker()
    if _tracker and _tracker.enabled:
        _tracker.track_context_retrieval(
            source="domain_dictionary",
            query=query[:200],
            results_count=len(matched),
            results_summary=[
                f"{t.term} ({t.category})"
                for t in matched[:10]
            ],
            latency_ms=0.0,
        )

    return matched


def get_terms_by_category(category: str) -> list[DomainTerm]:
    """카테고리별 도메인 용어를 반환한다.

    유효 카테고리: 고객, 여신, 수신, 거래, 카드, 외환, 금융지표, 조직, 시간
    """
    return [t for t in DOMAIN_DICTIONARY if t.category == category]


def get_all_categories() -> list[str]:
    """사전에 등록된 카테고리 목록을 중복 없이 반환한다."""
    seen: set[str] = set()
    categories: list[str] = []
    for t in DOMAIN_DICTIONARY:
        if t.category and t.category not in seen:
            seen.add(t.category)
            categories.append(t.category)
    return categories


def format_domain_context(terms: list[DomainTerm]) -> str:
    """매칭된 도메인 용어를 프롬프트에 포함할 문자열로 변환한다.

    SQL 생성 LLM이 테이블·컬럼·조건·설명을 한눈에 파악할 수 있도록
    구조화된 텍스트로 반환한다.
    """
    if not terms:
        return ""

    lines = ["## 매칭된 도메인 용어"]
    for t in terms:
        line = f"- '{t.term}'"
        if t.table_name:
            line += f" → 테이블: {t.table_name}"
        if t.column_name:
            line += f", 컬럼: {t.column_name}"
        if t.condition:
            line += f", 조건: {t.condition}"
        if t.description:
            line += f" ({t.description})"
        lines.append(line)

    return "\n".join(lines)


def _group_terms_by_category(
    terms: list[DomainTerm],
) -> dict[str, list[DomainTerm]]:
    """용어 목록을 카테고리 키로 묶어 반환한다."""
    groups: dict[str, list[DomainTerm]] = {}
    for t in terms:
        groups.setdefault(t.category or "기타", []).append(t)
    return groups


def _format_single_term_grouped(t: DomainTerm) -> str:
    """그룹화 포맷에서 단일 용어를 한 줄(또는 두 줄) 문자열로 변환한다."""
    line = f"  - '{t.term}'"
    if t.table_name:
        line += f" → 테이블: {t.table_name}"
    if t.column_name:
        line += f", 컬럼: {t.column_name}"
    if t.condition:
        line += f", 조건: {t.condition}"
    if t.description:
        line += f"\n    설명: {t.description}"
    return line


def format_domain_context_grouped(terms: list[DomainTerm]) -> str:
    """매칭된 도메인 용어를 카테고리별로 그룹화하여 반환한다.

    카테고리가 여럿인 복합 질의에서 가독성을 높이기 위해 사용한다.
    """
    if not terms:
        return ""

    groups = _group_terms_by_category(terms)
    lines = ["## 매칭된 도메인 용어 (카테고리별)"]
    for category, group_terms in groups.items():
        lines.append(f"\n### {category}")
        for t in group_terms:
            lines.append(_format_single_term_grouped(t))

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────
# resources/domain/domain_dictionary.yaml 외부 파일 오버라이드
# ──────────────────────────────────────────────────────────────

def _load_custom_dictionary() -> list[DomainTerm] | None:
    """resources/domain/domain_dictionary.yaml 에서 도메인 사전을 로드한다.

    파일이 없으면 None을 반환하여 기본 DOMAIN_DICTIONARY를 사용하도록 한다.
    """
    from src.utils.resource_loader import load_yaml

    data = load_yaml("domain/domain_dictionary.yaml", None)
    if data is None:
        return None

    terms_raw = data.get("terms", [])
    if not terms_raw:
        return None

    loaded: list[DomainTerm] = []
    for item in terms_raw:
        loaded.append(DomainTerm(
            term=item.get("term", ""),
            aliases=item.get("aliases", []),
            table_name=item.get("table_name", ""),
            column_name=item.get("column_name", ""),
            condition=item.get("condition", ""),
            description=item.get("description", ""),
            category=item.get("category", ""),
        ))

    _logger.info("커스텀 도메인 사전 로드 완료", count=len(loaded))
    return loaded


_custom = _load_custom_dictionary()
if _custom is not None:
    DOMAIN_DICTIONARY = _custom
