"""term_dict 인덱스 증강 — 기존 20건에 180건 추가 (총 200건).

카테고리별:
  여전법/은행법 15건 | 금융지표 약어 20건 | 업무 동의어 30건
  코드값 설명 40건 | 기준일 체계 10건 | 혼동 위험 20건 | 기타 금융 45건

Usage:
    PYTHONIOENCODING=utf-8 python standalone/scripts/augment_term_dict.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_env = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(_env, encoding="utf-8")

ES_URL = (
    f"http://{os.getenv('ES_HOST', 'localhost')}"
    f":{os.getenv('ES_PORT', '9200')}"
)
ES_USER = os.getenv("ES_USER", "elastic")
ES_PASSWORD = os.getenv("ES_PASSWORD", "")

START_ID = 20


def _t(ko, cp, th, defn, syn, cau):
    """term_dict 문서 축약 생성."""
    return {
        "term_ko": ko, "col_pattern": cp, "table_hint": th,
        "definition": defn, "synonym": syn, "caution": cau,
    }


# ═══════════════════════════════════════════════
# 1. 여전법/은행법 (15건)
# ═══════════════════════════════════════════════
def _banking_law():
    return [
        _t("가맹점수수료", "FEE_DCD, MCHT_*", "TB_ADW_CRD425M", "카드사가 가맹점으로부터 결제 대행 수수료로 수취하는 금액", "MDR", "업종별 수수료율 상이"),
        _t("카드론", "ADV_NO", "TB_ADW_CRD418M", "신용카드 한도 내 현금 대출 서비스", "현금서비스, Card Loan", "카드론과 현금서비스는 금리 체계가 다를 수 있음"),
        _t("리볼빙", "CRD_*", "TB_ADW_CRD417M", "카드 결제 대금 일부만 상환하고 나머지를 이월하는 결제 방식", "일부결제금액이월약정", "이월 잔액에 높은 이자 부과"),
        _t("건전성분류", "OVDU_GRD_CD, LN_STCD", "TB_ADW_LNB301M", "여신 상환 가능성을 5단계(정상/요주의/고정/회수의문/추정손실)로 분류", "자산건전성", "연체일수 기반 자동분류 + 수동조정"),
        _t("대손충당금", "PROV_DCD", "TB_ADW_LNB346M, TB_ADW_FIN1333S", "대출금 회수 불능에 대비하여 적립하는 비용", "충당금, Loan Loss Provision", "개별충당금과 집합충당금 구분"),
        _t("기한이익상실", "LN_STCD", "TB_ADW_LNB301M", "채무자 약정 위반으로 전액 상환 의무 발생", "Acceleration", "LN_STCD='02'에 해당"),
        _t("대위변제", "LN_STCD", "TB_ADW_LNB301M", "보증기관이 채무자 대신 은행에 상환", "대위상환", "LN_STCD='04'. 보증대출에서 발생"),
        _t("질권설정", "PLEDGE_SEQ", "TB_ADW_DEP217M", "예금을 담보로 제공하기 위해 설정하는 권리", "예금질권", "질권 설정 예금은 해지/출금 제한"),
        _t("연대보증", "GRNT_NO", "TB_ADW_LNB316M", "주채무자와 동일한 채무를 부담하는 보증", "Joint Guarantee", "2018년 이후 신규 연대보증 폐지. 레거시 존재"),
        _t("약정(코베넌트)", "COVENANT_SEQ", "TB_ADW_LNB339M", "대출 시 차주가 준수할 재무적/비재무적 조건", "Covenant", "위반 시 기한이익상실 사유"),
        _t("무담보대출", "LN_DCD, CLTR_DCD", "TB_ADW_LNB301M", "담보 없이 신용만으로 실행하는 대출", "신용대출", "LN_DCD='01' 또는 CLTR_DCD='04'"),
        _t("어음할인", "BILL_NO", "TB_ADW_TXP720L", "만기 전 어음을 은행에 매각하여 할인 금액 수취", "어음매입", "할인율=(액면-매입가)/액면×365/잔여일수"),
        _t("수표교환", "CHECK_NO", "TB_ADW_TXP718L", "타행 발행 수표를 금융결제원 통해 교환·정산", "어음교환", "교환 소요시간에 따라 부도 확인 필요"),
        _t("전자어음", "BILL_NO", "TB_ADW_TXP719M", "종이어음 대체하여 전자적으로 발행·유통되는 어음", "e어음", "전자어음 의무발행 대상 기업 확인"),
        _t("예금자보호", "BAL_AMT", "TB_ADW_DEP240M", "금융기관 파산 시 예보가 1인 5천만원까지 보호", "KDIC", "1인 1금융기관 기준 5천만원 한도"),
    ]


# ═══════════════════════════════════════════════
# 2. 금융지표 약어 (20건)
# ═══════════════════════════════════════════════
def _financial_metrics():
    return [
        _t("NIM", "NIM_*", "TB_ADW_FIN1309S", "순이자마진=(이자수익-이자비용)/평균운용자산×100", "Net Interest Margin", "은행 핵심 수익성 지표"),
        _t("NII", "NII_ITEM_CD", "TB_ADW_FIN1310D", "순이자이익=이자수익-이자비용. NIM의 분자", "Net Interest Income", "NIM과 혼동 주의. NII는 금액, NIM은 비율"),
        _t("BIS비율", "IND_CD", "TB_ADW_RSK1101M, TB_ADW_FIN1325M", "자기자본/위험가중자산×100. 국제결제은행 기준 최소 8%", "자본적정성비율, CAR", "Tier1+Tier2 자본 합계 기준"),
        _t("Tier1자본", "IND_CD", "TB_ADW_FIN1325M", "보통주자본+기타기본자본. BIS비율의 핵심 분자", "기본자본", "보통주 Tier1(CET1)이 핵심"),
        _t("Tier2자본", "IND_CD", "TB_ADW_FIN1325M", "보완자본. 후순위채 등 포함", "보완자본", "Tier1보다 손실흡수력 낮음"),
        _t("LCR", "RATIO_CD", "TB_ADW_FIN1324M", "유동성커버리지비율=고유동성자산/30일순현금유출×100. 최소100%", "Liquidity Coverage Ratio", "단기(30일) 유동성 지표"),
        _t("NSFR", "RATIO_CD", "TB_ADW_FIN1324M", "순안정자금조달비율=가용안정자금/필요안정자금×100. 최소100%", "Net Stable Funding Ratio", "중장기(1년) 유동성 지표"),
        _t("DSR", "DSR_*", "TB_ADW_LNA354M", "총부채원리금상환비율=연간총부채원리금상환액/연소득×100", "총부채상환비율", "개인 40~50%, 투기지역 30%"),
        _t("DTI", "DTI_*", "TB_ADW_LNA354M", "총부채상환비율=연간원리금상환액/연소득×100. DSR의 전신", "Debt To Income", "DSR이 DTI를 대체하는 추세"),
        _t("LTV", "LTV_RTO", "TB_ADW_LNA355M", "담보인정비율=대출금액/담보가치×100", "Loan To Value", "일반 70%, 투기지역 40%"),
        _t("PD", "MODEL_CD", "TB_ADW_RSK1108M", "부도확률(Probability of Default). 차주가 1년 내 부도할 확률", "Default Probability", "ECL 산출의 핵심 변수"),
        _t("LGD", "MODEL_CD", "TB_ADW_RSK1109M", "부도시손실률(Loss Given Default). 부도 시 회수 불능 비율", "손실률", "담보 유형에 따라 차이"),
        _t("EAD", "MODEL_CD", "TB_ADW_RSK1110M", "부도시익스포저(Exposure at Default). 부도 시 잔여 대출 잔액", "익스포저", "약정 미사용 한도 포함"),
        _t("ECL", "LN_NO, STD_DT", "TB_ADW_RSK1111M", "기대신용손실=PD×LGD×EAD. IFRS9 기준 충당금 산출", "Expected Credit Loss", "Stage별 산출 방법론 상이"),
        _t("VaR", "PORT_CD, STD_DT", "TB_ADW_RSK1103P", "특정 신뢰수준에서 일정 기간 최대 예상 손실액", "Value at Risk", "99% 신뢰수준 10일 보유 기준"),
        _t("KRI", "KRI_CD", "TB_ADW_RSK1115M", "핵심위험지표. 운영리스크의 사전 경고 지표", "Key Risk Indicator", "임계값 초과 시 경보 발생"),
        _t("FTP", "FTP_*", "TB_ADW_FIN1323M", "자금이전가격=(조달마진+운용마진). 지점 손익 산출 기준", "Funds Transfer Pricing", "조달금리와 운용금리 구분"),
        _t("ROA", "PL_ITEM_CD", "TB_ADW_FIN1306S", "총자산이익률=당기순이익/평균총자산×100", "Return on Assets", "은행 전체 수익성 지표"),
        _t("ROE", "PL_ITEM_CD", "TB_ADW_FIN1306S", "자기자본이익률=당기순이익/평균자기자본×100", "Return on Equity", "주주 관점 수익성 지표"),
        _t("RAROC", "PL_ITEM_CD", "TB_ADW_FIN1321M", "위험조정자본수익률=위험조정이익/경제적자본×100", "Risk-Adjusted ROC", "리스크 대비 수익성 평가"),
    ]


# ═══════════════════════════════════════════════
# 3. 업무 동의어 (30건)
# ═══════════════════════════════════════════════
def _synonyms():
    return [
        _t("잔액", "BAL_AMT, TOT_BAL_AMT", "TB_ADW_DEP201P, TB_ADW_DEP202S", "특정 시점 계좌의 남은 금액", "잔고, 밸런스, Balance", "T+0(당일)과 T+1(전일) 기준 차이 주의"),
        _t("입금", "TR_DCD, TR_AMT", "TB_ADW_TRX701L", "계좌에 자금이 들어오는 거래", "수납, 수취, 입고", "TR_DCD 100번대 중 입금 유형 확인"),
        _t("연체", "OVDU_GRD_CD, OVDU_AMT", "TB_ADW_LNB301M, TB_ADW_LNB310P", "약정 기일까지 원리금을 상환하지 않은 상태", "미납, 미수, 지연", "연체등급 F/Z는 미정의 코드"),
        _t("실행", "LN_EXC_AMT, EXEC_DT", "TB_ADW_LNB301M, TB_ADW_LNB305L", "대출금이 실제로 지급되는 행위", "취급, 기표, Disbursement", "승인과 실행은 다른 시점"),
        _t("상환", "RPAY_AMT, RPAY_DT", "TB_ADW_LNR307L", "대출 원리금을 갚는 행위", "변제, 갚다, Repayment", "원리금균등/원금균등/만기일시 구분"),
        _t("여신", "LN_NO, LN_BAL_AMT", "TB_ADW_LNB301M", "은행이 고객에게 자금을 대출하는 행위 또는 그 금액", "대출, 융자, Loan", "LNB301M(잔액) vs LNB302M(승인) 구분"),
        _t("수신", "ACN, BAL_AMT", "TB_ADW_DEP201P", "은행이 고객으로부터 자금을 예치받는 행위", "예금, 적금, Deposit", "계좌유형코드(ACT_DCD) 확인 필수"),
        _t("고객", "EDPS_CSN, CSM", "TB_ADW_CSC101M", "은행과 거래 관계가 있는 개인 또는 법인", "거래처, 손님", "EDPS_CSN으로 식별. 주민번호 직접 조회 금지"),
        _t("금리", "INT_RT, APLY_RT", "TB_ADW_LNB301M, TB_ADW_DEP210M", "자금 대차에 대한 이자 비율(연율 %)", "이율, 이자율, Interest Rate", "고정/변동 구분 필요"),
        _t("만기", "MTRTY_DT", "TB_ADW_LNB301M, TB_ADW_DEP214M", "대출 또는 예금의 약정 종료일", "기한, Maturity", "만기=자동연장/정상만기/기한이익상실 구분"),
        _t("원금", "LN_EXC_AMT, BAL_AMT", "TB_ADW_LNB301M", "이자를 제외한 대출 또는 예금의 본래 금액", "원본, Principal", "원금과 원리금(원금+이자) 구분"),
        _t("이자", "INT_AMT", "TB_ADW_DEP212L, TB_ADW_LNB308L", "자금 사용 대가로 지급하는 금액", "수익, 수입, Interest", "이자수익(수신)과 이자비용(여신) 구분"),
        _t("해지", "CANCEL_DT", "TB_ADW_DEA228L, TB_ADW_INS809L", "계좌/보험 등의 계약을 종료하는 행위", "해약, 폐쇄, 종결", "정상해지/중도해지/자동해지 구분"),
        _t("이체", "TR_AMT, TRNSFR_NO", "TB_ADW_TRX701L, TB_ADW_TXP708M", "한 계좌에서 다른 계좌로 자금을 이동", "송금, 지급, Transfer", "당행이체/타행이체/해외송금 구분"),
        _t("담보", "CLTR_NO, CLTR_DCD", "TB_ADW_LNCL313M", "채무 이행을 보증하기 위해 제공하는 재산", "질권, 보증, Collateral", "부동산/유가증권/예적금/무담보 구분"),
        _t("연장", "EXT_DT", "TB_ADW_LNB331L", "대출 만기를 연장하는 행위", "갱신, 리뉴얼, Extension", "자동연장/수동연장 구분"),
        _t("심사", "REVIEW_NO", "TB_ADW_LNA320M", "대출 신청에 대한 평가·심의 절차", "평가, 리뷰, Review", "자동심사/수동심사/재심사 구분"),
        _t("환전", "EXCH_NO", "TB_ADW_FXB506L", "한 통화를 다른 통화로 교환하는 거래", "외화매입", "환전수수료 = 대고객율 - 매매기준율"),
        _t("적금", "ACN", "TB_ADW_DEPS221M", "정기적으로 일정 금액을 납입하는 저축 상품", "정기적금, Installment Saving", "자유적금/정액적금 구분"),
        _t("지점", "BLNG_BRCD, BR_NM", "TB_ADW_COM001M", "은행의 영업 단위", "영업점, 점포, Branch", "부점코드 3자리. 001=본점영업부"),
        _t("모바일뱅킹", "CHN_CD", "TB_ADW_DGB1004G", "스마트폰 앱을 통한 금융 거래 서비스", "모뱅, 스마트뱅킹", "CHN_CD='03'에 해당"),
        _t("인터넷뱅킹", "CHN_CD", "TB_ADW_DGB1003G", "PC 웹브라우저를 통한 금융 거래 서비스", "인뱅, e뱅킹", "CHN_CD='02'에 해당"),
        _t("ATM", "ATM_ID", "TB_ADW_TRX706G", "현금자동입출금기를 통한 거래", "현금자동입출금기, CD기", "CHN_CD='04'에 해당"),
        _t("공과금", "UTIL_NO", "TB_ADW_TXP716L", "전기/수도/가스 등 공공요금", "유틸리티", "자동이체 설정 가능"),
        _t("가상계좌", "VIRTUAL_ACN", "TB_ADW_TXP729M", "입금 전용으로 부여되는 임시 계좌번호", "VA, Virtual Account", "결제/수납 목적 사용"),
        _t("에스크로", "ESCROW_NO", "TB_ADW_TXP728M", "제3자가 거래 대금을 보관하는 서비스", "제3자예치", "전자상거래 소비자 보호 목적"),
        _t("통장", "ACN", "TB_ADW_DEA203M", "은행 거래 내역을 기록하는 장부 또는 계좌 자체", "계좌, 계정, Account", "통장=계좌를 지칭하는 일상 용어"),
        _t("본점", "BLNG_BRCD", "TB_ADW_COM001M", "은행의 본사 영업부", "본사, Head Office", "BLNG_BRCD='001'"),
        _t("콜센터", "CHN_CD", "TB_ADW_CSC111L", "전화 상담을 통한 고객 서비스 채널", "고객센터, Contact Center", "채널코드와 별도 관리되는 경우 있음"),
        _t("오픈뱅킹", "OB_TR_ID", "TB_ADW_TXP723L", "타 금융기관 계좌를 하나의 앱에서 조회/이체하는 서비스", "공동결제시스템", "CHN_CD='05'(미정의코드 주의)"),
    ]


# ═══════════════════════════════════════════════
# 4. 코드값 설명 (40건)
# ═══════════════════════════════════════════════
def _code_explanations():
    return [
        _t("고객구분코드", "CUS_DCD", "TB_ADW_CSC101M", "01:개인, 02:법인. 03(개인사업자)은 미정의", "고객유형", "NULL 가능성 있음"),
        _t("계좌상태코드", "ACT_STCD", "TB_ADW_DEP201P", "01:정상, 02:해지, 03:휴면", "계좌상태", "해지 계좌도 이력 조회 가능"),
        _t("거래유형코드", "TR_DCD", "TB_ADW_TRX701L", "100~199:공식 정의. 200~299, 999는 미정의", "거래구분", "미정의 코드 3% 존재"),
        _t("여신구분코드", "LN_DCD", "TB_ADW_LNB301M", "01:신용, 02:담보, 03:보증", "대출유형", "보증대출은 보증기관 확인 필요"),
        _t("여신상태코드", "LN_STCD", "TB_ADW_LNB301M", "01:정상~05:상각. 0A는 레거시 미정의", "대출상태", "숫자+문자 혼재 주의"),
        _t("연체등급코드", "OVDU_GRD_CD", "TB_ADW_LNB301M", "A:정상~E:추정손실. F/Z는 미정의", "연체등급", "건전성분류와 매핑 관계"),
        _t("카드구분코드", "CRD_DCD", "TB_ADW_CRD401M", "01:신용, 02:체크, 03:선불. 04는 미정의", "카드유형", "04 포함 여부 확인 필요"),
        _t("채널코드", "CHN_CD", "TB_ADW_TRX701L", "01:영업점, 02:인뱅, 03:모뱅, 04:ATM. 05/06 미정의", "채널구분", "오픈뱅킹/API 채널 추가됨"),
        _t("통화코드", "CCY_CD", "TB_ADW_COM012M", "KRW/USD/EUR/JPY 공식. CNH는 미정의", "통화구분", "CNH(역외위안)는 메타 미등록"),
        _t("딜구분코드", "FX_DL_DCD", "TB_ADW_FXD501L", "01:매입~05:옵션. 06은 미정의", "딜유형", "스왑과 선물환 구분 주의"),
        _t("펀드구분코드", "FND_DCD", "TB_ADW_FND603M", "01:주식형~04:MMF. 99는 미정의", "펀드유형", "혼합형 세부 구분 주의"),
        _t("위험등급코드", "RSK_GRD_CD", "TB_ADW_FND611M", "1:매우높음~5:낮음. 0(미평가)은 미정의", "리스크등급", "투자자 적합성 평가 연계"),
        _t("보험구분코드", "INS_DCD", "TB_ADW_INS803M", "L:생명, N:손해, H:건강. E는 미정의", "보험유형", "방카슈랑스 판매 규정 확인"),
        _t("납입상태코드", "PAY_STCD", "TB_ADW_INS805L", "01:정상, 02:미납, 03:완납. NULL 가능", "납입상태", "NULL은 미확인 상태"),
        _t("연금구분코드", "PN_DCD", "TB_ADW_PNB901M", "DB:확정급여, DC:확정기여, IRP:개인형. HYB 미정의", "연금유형", "HYB(혼합형)은 메타 미등록"),
        _t("신용등급코드", "CRSC_GRD_CD", "TB_ADW_LNA322M", "AAA~D 10단계. NR(미평가)은 미정의", "신용등급", "NR은 신규 고객에 발생"),
        _t("부점유형코드", "BR_DCD", "TB_ADW_COM001M", "01:본점, 02:지점, 03:출장소. 04/99 미정의", "부점유형", "04(디지털점포), 99(폐점) 추가됨"),
        _t("캠페인상태코드", "CAMP_STCD", "TB_ADW_MKT1201M", "01:계획, 02:실행, 03:종료. 04/99 미정의", "캠페인상태", "04(중단), 99(테스트) 미등록"),
        _t("IFRS9단계코드", "RSK_STAGE_CD", "TB_ADW_RSK1111M", "1:정상, 2:유의적증가, 3:신용손상. S 미정의", "Stage분류", "S는 간소화 표기로 미등록"),
        _t("건전성분류등급", "OVDU_GRD_CD", "TB_ADW_LNB301M", "A(정상)~E(추정손실) 5단계 분류 체계", "자산분류등급", "IFRS9 Stage와 매핑: A→S1, B→S2, C~E→S3"),
        _t("투자성향코드", "INVEST_PRFL_CD", "TB_ADW_WMR1407M", "1:안정~5:공격. 0(미평가)은 미정의", "투자성향", "적합성 평가 미실시 고객은 0"),
        _t("KPI유형", "KPI_CD", "TB_ADW_FIN1316M", "수신실적/여신실적/수수료수익/건전성 등 성과 지표 분류", "성과지표유형", "지점/직원 성과 평가 기준"),
        _t("마케팅세그먼트", "SEG_CD", "TB_ADW_MKT1222M", "프리미엄/일반/신규/휴면 등 고객 분류 그룹", "고객세그먼트", "세그먼트별 타겟 마케팅 적용"),
        _t("거래채널구분", "CHN_CD", "TB_ADW_TRX701L", "대면(영업점)/비대면(인뱅,모뱅)/자동화(ATM) 분류", "채널분류", "디지털 전환율 분석 기준"),
        _t("계좌종류", "ACT_DCD", "TB_ADW_DEP201P", "보통예금/정기예금/적금/MMF/ISA 등 상품 유형", "예금종류", "ISA(개인종합자산관리) 별도 관리"),
        _t("대출만기유형", "LN_STCD, MTRTY_DT", "TB_ADW_LNB301M", "정상만기/자동연장/기한이익상실 구분", "만기유형", "자동연장 시 LN_STCD 변경 없음"),
        _t("보험계약상태", "INS_STCD", "TB_ADW_INS803M", "유효/무효/실효/복원 4단계 상태 관리", "계약상태", "실효 후 복원 가능 기간 제한"),
        _t("카드결제방식", "CRD_DCD", "TB_ADW_CRD401M", "일시불/할부/리볼빙 결제 방식 구분", "결제방식", "리볼빙 이월 잔액 이자율 확인"),
        _t("세금구분", "TAX_DCD", "TB_ADW_DEP218M", "이자소득세(15.4%)/비과세/분리과세 구분", "과세유형", "비과세 한도 및 자격 조건 확인"),
        _t("고객생애단계", "SEG_DCD", "TB_ADW_CSP126M", "가입/성장/우량/이탈방지 4단계 생애주기", "생애주기", "단계별 마케팅 전략 차별화"),
        _t("예산항목코드", "ITEM_CD", "TB_ADW_BUDG1314M", "인건비/업무추진비/전산비/시설비 등 분류", "예산분류", "예산 편성/집행/실적 추적"),
        _t("리스크유형", "IND_CD", "TB_ADW_RSK1101M", "신용/시장/운영/유동성 리스크 분류", "위험유형", "IND_CD로 지표 유형 구분"),
        _t("AML경보등급", "ALERT_LVL_CD", "TB_ADW_AML1116M", "고/중/저 3단계 경보 수준", "경보등급", "설명 없음(TYPE-3 MISSING)"),
        _t("감사구분", "AUDIT_ID", "TB_ADW_CMP1127M", "정기/수시/특별 감사 유형", "감사유형", "지적사항 등급에 따른 조치 차이"),
        _t("규제보고유형", "REPORT_NO", "TB_ADW_CMP1132M", "BIS/LCR/KRI/스트레스테스트 보고 구분", "보고유형", "금감원/한국은행 제출 기한 상이"),
        _t("FTP유형", "FTP_*", "TB_ADW_FIN1323M", "조달FTP/운용FTP/마진FTP 구분", "이전가격유형", "지점 손익에 직접 영향"),
        _t("충당금구분", "PROV_DCD", "TB_ADW_FIN1333S", "개별충당금(특정여신)/집합충당금(포트폴리오)", "충당금유형", "IFRS9 ECL과 연계"),
        _t("분개유형", "JOURNAL_NO", "TB_ADW_GLB1303M", "정상분개/취소분개/수정분개 구분", "전표유형", "DR_CR_DCD 양변(B) 발생 케이스"),
        _t("외환결제유형", "SETL_NO", "TB_ADW_FXB511M", "즉시결제/익일정산/네팅 방식 구분", "결제방식", "결제 시점에 따른 환리스크 차이"),
        _t("직원직급", "EMN", "TB_ADW_COM006M", "사원/대리/과장/차장/부장/이사 등 직급 체계", "직급", "성과 평가 기준 직급별 차등"),
    ]


# ═══════════════════════════════════════════════
# 5. 기준일 체계 (10건)
# ═══════════════════════════════════════════════
def _date_conventions():
    return [
        _t("기준일자(STD_DT)", "STD_DT", "TB_ADW_DEP201P, TB_ADW_CSC101M, TB_ADW_LNB301M", "T+0 당일 기준 데이터 적재 시점", "기준일, Standard Date", "BASE_DT(T+1)와 혼동 주의"),
        _t("기준일자(BASE_DT)", "BASE_DT", "TB_ADW_DEP202S, TB_ADW_FXB502M", "T+1 전일 기준 배치 처리 후 확정 데이터", "전일기준일", "STD_DT와 같은 의미이나 적재 시점 1영업일 차이"),
        _t("거래일자(TR_DT)", "TR_DT", "TB_ADW_TRX701L", "실제 거래가 발생한 일자. 파티션 키로 사용", "거래일", "파티션 테이블 조회 시 TR_DT 범위 조건 필수"),
        _t("산출일자(CALC_DT)", "CALC_DT", "TB_ADW_DEP212L, TB_ADW_LNA354M", "이자/DSR/LTV 등 계산이 수행된 일자", "계산일", "산출 결과의 유효 시점"),
        _t("평가일자(EVAL_DT)", "EVAL_DT", "TB_ADW_LNCL314L, TB_ADW_FND602P", "담보감정/펀드평가가 수행된 일자", "감정일", "평가 주기에 따라 최신성 차이"),
        _t("적용시작일자(EFF_DT)", "EFF_DT", "TB_ADW_DEP210M, TB_ADW_LNB325M", "금리/조건이 적용되기 시작하는 일자", "효력개시일", "이전 조건과의 경계 시점"),
        _t("변경일자(CHG_DT)", "CHG_DT", "TB_ADW_CSC109H, TB_ADW_DEP238H", "상태/정보가 변경된 일자. H(이력) 테이블 PK", "변경시점", "이력 테이블에서 시점 추적 기준"),
        _t("상태변경일자(STAT_DT)", "STAT_DT", "TB_ADW_DEA205H", "계좌/계약 상태가 변경된 일자", "상태변경시점", "STD_DT(기준일)와 혼동 위험 높음"),
        _t("기준년월(BASE_YM)", "BASE_YM", "TB_ADW_LNB312S, TB_ADW_FIN1306S", "월별 집계/통계의 기준 년월(YYYYMM)", "기준월", "S(집계) 테이블의 PK로 사용"),
        _t("납입/지급일자(PAY_DT)", "PAY_DT", "TB_ADW_DEP213L, TB_ADW_INS805L", "이자 지급 또는 보험료 납입이 이루어진 일자", "지급일", "예정일과 실제일 차이 가능"),
    ]


# ═══════════════════════════════════════════════
# 6. 혼동 위험 용어 (20건)
# ═══════════════════════════════════════════════
def _confusion_pairs():
    return [
        _t("실행금액 vs 승인금액", "LN_EXC_AMT, LN_APR_AMT", "TB_ADW_LNB301M, TB_ADW_LNB302M", "실행=실제 지급액, 승인=심사 승인 한도. 승인≥실행", "", "LNB301M.LN_EXC_AMT vs LNB302M.LN_APR_AMT"),
        _t("고시환율 vs 체결환율", "BASE_RT, DL_RT", "TB_ADW_FXB502M, TB_ADW_FXD501L", "고시=은행 공시 기준율, 체결=실제 거래 적용율", "", "FXB502M.BASE_RT vs FXD501L.DL_RT"),
        _t("잔고 vs 평가액", "BAL_AMT, EVAL_AMT", "TB_ADW_FND601P, TB_ADW_FND602P", "잔고=투자 원금, 평가액=현재 시가 기준 가치", "", "FND601P(원금) vs FND602P(시가)"),
        _t("영업등급 vs 마케팅등급", "CUS_GRD_CD, MKT_GRD_CD", "TB_ADW_CSC101M, TB_ADW_CSP103M", "영업=거래실적 기준, 마케팅=분석모델 기준. 동일 고객도 다를 수 있음", "", "CSC101M.CUS_GRD_CD vs CSP103M.MKT_GRD_CD"),
        _t("당일잔액 vs 전일잔액", "BAL_AMT, TOT_BAL_AMT", "TB_ADW_DEP201P, TB_ADW_DEP202S", "당일(T+0)=실시간 반영, 전일(T+1)=배치 확정. 동일 날 조회해도 값 다름", "", "DEP201P(T+0) vs DEP202S(T+1)"),
        _t("이자수익 vs 이자비용", "NII_ITEM_CD", "TB_ADW_FIN1310D", "수익=고객에게 받는 이자, 비용=고객에게 지급하는 이자", "", "NII = 이자수익 - 이자비용"),
        _t("조달금리 vs 운용금리", "FTP_*", "TB_ADW_FIN1323M", "조달=자금 조달 비용, 운용=자금 운용 수익. 차이가 마진", "", "FTP 산출 시 만기별 곡선 적용"),
        _t("원금 vs 원리금", "LN_BAL_AMT, RPAY_AMT", "TB_ADW_LNB301M, TB_ADW_LNR307L", "원금=대출 본금, 원리금=원금+이자 합계", "", "상환 시 원리금균등/원금균등 구분"),
        _t("고정금리 vs 변동금리", "INT_RT", "TB_ADW_LNB301M", "고정=만기까지 불변, 변동=기준금리 연동 변동", "", "금리변경이력(LNB309H) 확인"),
        _t("기본금리 vs 우대금리", "INT_RT, APLY_RT", "TB_ADW_LNB325M, TB_ADW_DEP211M", "기본=상품 기준 금리, 우대=조건 충족 시 추가 할인/가산", "", "적용금리 = 기본금리 + 가산금리 - 우대금리"),
        _t("담보대출 vs 신용대출", "LN_DCD, CLTR_DCD", "TB_ADW_LNB301M, TB_ADW_LNCL313M", "담보=부동산 등 담보 제공, 신용=담보 없이 신용 평가만", "", "LN_DCD='01'(신용) vs '02'(담보)"),
        _t("건전성등급 vs 연체등급", "OVDU_GRD_CD", "TB_ADW_LNB301M", "동일 코드(A~E) 사용하나 산출 기준이 다를 수 있음", "", "자동분류(연체일수) vs 수동조정(심사)"),
        _t("DB형 vs DC형(연금)", "PN_DCD", "TB_ADW_PNB912M, TB_ADW_PNB913M", "DB=확정급여(회사 운용), DC=확정기여(개인 운용)", "", "퇴직급여 산정 방식이 근본적으로 다름"),
        _t("잔여기간 vs 경과기간", "MTRTY_DT, LN_DT", "TB_ADW_LNB301M", "잔여=만기까지 남은 기간, 경과=실행 후 지난 기간", "", "잔여+경과 = 총 대출 기간"),
        _t("총잔액 vs 평균잔액", "BAL_AMT", "TB_ADW_DEP201P", "총잔액=시점 기준 합계, 평균잔액=기간 평균", "", "평균잔액은 별도 산출 필요"),
        _t("보험료 vs 보험금", "AMT", "TB_ADW_INS805L, TB_ADW_INS808L", "보험료=고객이 납입, 보험금=보험사가 지급", "", "INS805L(납입) vs INS808L(지급)"),
        _t("매입 vs 매도", "FX_DL_DCD", "TB_ADW_FXD501L", "매입=외화 구매(은행 기준), 매도=외화 판매", "", "은행 기준 방향. 고객 기준은 반대"),
        _t("상환 vs 조기상환", "RPAY_DT, PREPAY_DT", "TB_ADW_LNR307L, TB_ADW_LNR332L", "상환=정기 원리금 납부, 조기상환=만기 전 전액/일부 상환", "", "조기상환 수수료 부과 가능"),
        _t("개인 vs 개인사업자", "CUS_DCD", "TB_ADW_CSC101M", "개인(01)=급여소득자, 개인사업자(03)=사업소득자. 세무/심사 기준 다름", "", "CUS_DCD '03'은 메타 미정의"),
        _t("실적 vs 목표", "KPI_CD", "TB_ADW_FIN1318S, TB_ADW_FIN1317M", "실적=실제 달성 수치, 목표=계획 수치. 달성률=실적/목표", "", "FIN1318S(실적) vs FIN1317M(목표)"),
    ]


# ═══════════════════════════════════════════════
# 7. 기타 금융용어 (45건)
# ═══════════════════════════════════════════════
def _misc_finance():
    return [
        _t("수표", "CHECK_NO", "TB_ADW_TXP717M", "일정 금액 지급을 은행에 위탁하는 유가증권", "Check", "부도 수표 주의"),
        _t("어음", "BILL_NO", "TB_ADW_TXP719M", "일정 기일에 금액 지급을 약속하는 유가증권", "Bill, Note", "만기일 관리 필수"),
        _t("한도", "LIMIT_DCD", "TB_ADW_LNB318M, TB_ADW_CRD421M", "대출 또는 카드 이용 가능한 최대 금액", "Limit, Line", "이용한도/일한도/월한도 구분"),
        _t("상각", "WRITEOFF_DT", "TB_ADW_LNB329L", "회수 불능으로 판단된 대출을 장부에서 제거", "Write-off", "상각 후에도 회수 추심 계속"),
        _t("회수", "RECOVERY_DT", "TB_ADW_LNB330L", "상각 처리된 채권에서 자금을 회수하는 행위", "Recovery", "회수율 = 회수금액/상각금액"),
        _t("구조조정", "RESTRUCTURE_DT", "TB_ADW_LNB328L", "상환 곤란 채무자의 대출 조건을 변경하는 절차", "Workout", "금리인하/만기연장/원금감면 등"),
        _t("파티션", "TR_DT", "TB_ADW_TRX701L", "대용량 테이블을 월별로 분할 저장하는 기법", "Partition", "TR_DT 범위 조건 없이 조회 시 전체 스캔"),
        _t("배치", "JOB_ID", "TB_ADW_COM016M", "일정 시간에 대량 데이터를 일괄 처리하는 작업", "Batch", "일배치/월배치/실시간 구분"),
        _t("정산", "SETL_DT", "TB_ADW_FXB512L", "거래 당사자 간 채권채무를 최종 확정하는 절차", "Settlement", "T+0/T+1/T+2 정산 주기"),
        _t("마감", "STD_DT", "TB_ADW_DEP201P", "영업일 종료 후 당일 거래를 확정하는 절차", "Closing", "마감 후 당일 거래 변경 불가"),
        _t("원장", "GL_ACCT_CD", "TB_ADW_GLB1301M", "은행의 모든 거래를 기록하는 공식 장부", "General Ledger", "계정과목 체계로 분류"),
        _t("전표", "JOURNAL_NO", "TB_ADW_GLB1303M", "회계 거래를 기록하는 증빙 문서", "Journal Entry", "차변/대변 분개 기록"),
        _t("분개", "JOURNAL_NO, LINE_SEQ", "TB_ADW_GLB1304D", "거래를 차변과 대변으로 나누어 기록하는 행위", "Journalizing", "차변 합계 = 대변 합계"),
        _t("계정과목", "GL_ACCT_CD", "TB_ADW_GLB1301M", "거래의 성격을 분류하는 회계 항목 체계", "Account Code", "대분류/중분류/소분류 계층 구조"),
        _t("대차대조표", "BS_ITEM_CD", "TB_ADW_FIN1308S", "특정 시점의 자산/부채/자본 현황표", "BS, Balance Sheet", "자산 = 부채 + 자본"),
        _t("손익계산서", "PL_ITEM_CD", "TB_ADW_FIN1306S", "일정 기간의 수익과 비용 현황표", "PL, Income Statement", "영업이익/경상이익/당기순이익 구분"),
        _t("시산표", "GL_ACCT_CD, BASE_YM", "TB_ADW_GLB1305S", "모든 계정의 잔액을 한눈에 보여주는 표", "Trial Balance", "차변 합계 = 대변 합계 검증"),
        _t("감가상각", "ASSET_NO, BASE_YM", "TB_ADW_FIN1330M", "고정자산의 가치 감소분을 비용으로 인식하는 회계 처리", "Depreciation", "정액법/정률법 선택"),
        _t("배당", "DIV_DCD, FY", "TB_ADW_FIN1327M", "주주에게 이익을 분배하는 행위", "Dividend", "중간배당/기말배당 구분"),
        _t("IFRS", "ADJ_NO", "TB_ADW_FIN1334M", "국제회계기준. K-IFRS는 한국 도입 기준", "국제재무보고기준", "IFRS9는 금융상품 기준"),
        _t("포트폴리오", "EDPS_CSN, STD_DT, SEQ", "TB_ADW_WMB1402M", "투자 자산의 구성 조합", "Portfolio", "분산 투자를 통한 위험 관리"),
        _t("리밸런싱", "REBAL_DT", "TB_ADW_WMB1404L", "포트폴리오 자산 배분 비율을 원래 목표로 재조정", "Rebalancing", "정기/비정기 리밸런싱"),
        _t("자산배분", "ASSET_CLASS_CD", "TB_ADW_WMB1403M", "투자 자산을 주식/채권/부동산 등으로 배분", "Asset Allocation", "전략적/전술적 배분 구분"),
        _t("투자성향", "INVEST_PRFL_CD", "TB_ADW_WMR1407M", "투자자의 위험 수용 성향(안정~공격 5단계)", "Risk Profile", "적합성 평가 의무"),
        _t("자산승계", "PLAN_NO", "TB_ADW_WMB1415M", "상속/증여를 통한 자산 이전 계획", "Succession Planning", "세무 컨설팅 연계"),
        _t("마이데이터", "ORG_CD, ASSET_DCD", "TB_ADW_MYDT1013M", "개인이 자신의 금융 데이터를 통합 조회하는 서비스", "MyData", "동의 기반 데이터 수집"),
        _t("오픈뱅킹", "OB_TR_ID", "TB_ADW_TXP723L", "금융기관 간 계좌 조회/이체를 표준화한 플랫폼", "Open Banking", "금융결제원 운영"),
        _t("핀테크", "PARTNER_CD", "TB_ADW_DGB1030M", "금융(Finance)과 기술(Technology)의 결합 서비스", "FinTech", "제휴사 코드로 관리"),
        _t("디지털지갑", "WALLET_ID", "TB_ADW_DGB1033M", "모바일 기기에서 결제 수단을 관리하는 서비스", "Digital Wallet", "간편결제 연동"),
        _t("생체인증", "BIO_DCD", "TB_ADW_DGA1011M", "지문/얼굴/홍채 등 생체 정보 기반 본인 확인", "Biometric Auth", "FIDO 표준 기반"),
        _t("OTP", "OTP_SEQ", "TB_ADW_DGA1010M", "일회용비밀번호. 거래 인증에 사용되는 시간 기반 코드", "One-Time Password", "하드웨어/소프트웨어 OTP 구분"),
        _t("전자서명", "SIGN_NO", "TB_ADW_DGA1019M", "전자적 방법으로 서명의 효력을 가지는 인증 행위", "Digital Signature", "공동인증서/금융인증서 구분"),
        _t("벤치마크", "BM_CD", "TB_ADW_FND612M", "펀드 성과를 비교 평가하는 기준 지표", "Benchmark", "코스피200/국고채3년 등"),
        _t("NAV", "NAV_DT", "TB_ADW_FND605P", "순자산가치. 펀드 1좌의 기준가격", "Net Asset Value", "매일 산출. 기준가 × 보유좌수 = 평가액"),
        _t("ELS", "ELS_CD", "TB_ADW_ELS626M", "주가연계증권. 기초자산 가격에 따라 수익률 결정", "Equity Linked Securities", "녹인/녹아웃 조건 확인"),
        _t("DLS", "DLS_CD", "TB_ADW_ELS629M", "파생결합증권. 금리/환율/원자재 등에 연계", "Derivative Linked Securities", "ELS와 기초자산 유형 차이"),
        _t("교차판매", "PD_GRP_CD", "TB_ADW_MKT1215M", "기존 고객에게 보유하지 않은 다른 상품을 판매", "Cross-sell", "예금→대출, 대출→카드 등"),
        _t("이탈방지", "CALC_DT", "TB_ADW_MKT1218M", "이탈 위험 고객을 사전 식별하여 유지하는 전략", "Retention", "이탈예측스코어 기반"),
        _t("CLV", "CALC_DT", "TB_ADW_MKT1217M", "고객생애가치. 고객이 미래에 창출할 총수익 예측값", "Customer Lifetime Value", "장기 관점 수익성 지표"),
        _t("NPS", "SURVEY_DT", "TB_ADW_MKT1221M", "순추천지수. 고객 충성도를 측정하는 지표", "Net Promoter Score", "추천자 비율 - 비추천자 비율"),
        _t("NBA", "EDPS_CSN, STD_DT", "TB_ADW_MKT1216M", "다음 최적 행동. 고객별 최적 상품/서비스 추천 모델", "Next Best Action", "실시간 추천 엔진"),
        _t("캠페인", "CAMP_CD", "TB_ADW_MKT1201M", "특정 목적을 위해 기획·실행하는 마케팅 활동", "Campaign", "기획→실행→성과분석 사이클"),
        _t("로보어드바이저", "ROBO_ID", "TB_ADW_FND637M", "알고리즘 기반 자동화된 투자 자문 서비스", "Robo-Advisor", "투자성향 평가 후 자동 배분"),
        _t("챗봇", "SESSION_ID", "TB_ADW_DGB1017M", "AI 기반 자동 응답 고객 상담 서비스", "Chatbot", "FAQ 응답 + 업무 안내"),
        _t("AB테스트", "TEST_ID", "TB_ADW_DGB1024M", "두 가지 버전을 비교하여 효과를 측정하는 실험", "A/B Test", "마케팅/UI 최적화에 활용"),
    ]


def main():
    if sys.stdout.encoding != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    from elasticsearch import Elasticsearch
    from elasticsearch.helpers import bulk

    es = Elasticsearch(ES_URL, basic_auth=(ES_USER, ES_PASSWORD))
    if not es.ping():
        print("ES 연결 실패")
        sys.exit(1)

    cur = es.count(index="term_dict")["count"]
    print(f"[term_dict] 현재 건수: {cur}건")

    docs = (
        _banking_law()
        + _financial_metrics()
        + _synonyms()
        + _code_explanations()
        + _date_conventions()
        + _confusion_pairs()
        + _misc_finance()
    )
    print(f"[term_dict] 추가 대상: {len(docs)}건 (_id {START_ID}~{START_ID + len(docs) - 1})")

    actions = []
    for i, doc in enumerate(docs):
        actions.append({
            "_index": "term_dict",
            "_id": str(START_ID + i),
            "_source": doc,
        })

    ok, errs = bulk(es, actions, raise_on_error=False)
    err_cnt = len(errs) if errs else 0
    print(f"[term_dict] 적재 완료: {ok}건 성공 / {err_cnt}건 오류")

    es.indices.refresh(index="term_dict")
    total = es.count(index="term_dict")["count"]
    print(f"[term_dict] 최종 총 건수: {total}건")
    es.close()


if __name__ == "__main__":
    main()
