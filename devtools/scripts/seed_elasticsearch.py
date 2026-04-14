"""메타 시딩 공통 데이터/헬퍼 모듈 (ES 의존성 제거 완료).

PG ADWOWN 스키마 추출, 코드 메타, 용어 사전, 컬럼명 매핑 등
seed_mongodb.py / seed_postgres.py 가 공유하는 정적 데이터와 헬퍼를 제공한다.

과거에는 ElasticSearch 시딩 진입점도 포함하였으나, ES 사용 중단(MongoDB +
Qdrant 대체)으로 ES 연결/적재 코드는 모두 제거하였다. 파일명은 하위
import 호환을 위해 유지한다 (seed_mongodb.py 등에서 from seed_elasticsearch
import ... 형태로 참조).

TYPE-2: code_meta에 공식 코드만 등록 (PG 미정의 코드 의도적 누락)
TYPE-3: table/column 설명 품질 혼재 (BEST 15% / GOOD 25% / POOR 40% / MISSING 20%)
"""
from __future__ import annotations

import hashlib
import random
import re
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

_env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(_env_path, encoding="utf-8")

SCHEMA = "adwown"

# ══════════════════════════════════════════════════════════════
# 요구사항 문서 파싱
# ══════════════════════════════════════════════════════════════


def _parse_requirements() -> dict[str, dict]:
    """test-data-requirements.md에서 테이블명 → {한글명, PK, 도메인, is_star} 매핑."""
    req_path = (
        Path(__file__).resolve().parent.parent.parent
        / "docs" / "agent-guides" / "test-data-requirements.md"
    )
    if not req_path.exists():
        req_path = Path("/docs/agent-guides/test-data-requirements.md")

    tables: dict[str, dict] = {}
    current_domain = "COM"
    domain_map = {
        "5.0": "COM", "5.1": "CUS", "5.2": "DEP", "5.3": "LON",
        "5.4": "CRD", "5.5": "FX", "5.6": "TRS", "5.7": "TRX",
        "5.8": "INS", "5.9": "PEN", "5.10": "DIG", "5.11": "RSK",
        "5.12": "MKT", "5.13": "FIN", "5.14": "WM", "5.15": "SYS",
    }
    pattern = re.compile(
        r'\|\s*\d+\s*\|\s*(★\s*)?`(TB_\w+)`\s*\|\s*([^|]+)\|\s*`([^`]+)`\s*\|'
    )

    with open(req_path, encoding="utf-8") as f:
        for line in f:
            # 도메인 섹션 감지
            for sec, dom in domain_map.items():
                if f"### {sec}" in line or f"### {sec} " in line:
                    current_domain = dom
                    break
            m = pattern.search(line)
            if m:
                is_star = bool(m.group(1))
                tbl_name = m.group(2).upper()
                ko_name = m.group(3).strip()
                pk_str = m.group(4)
                pk_cols = [c.strip() for c in pk_str.split("+")]
                tables[tbl_name] = {
                    "ko_name": ko_name,
                    "pk_cols": pk_cols,
                    "domain": current_domain,
                    "is_star": is_star,
                }
    return tables


# ══════════════════════════════════════════════════════════════
# PG 스키마 추출 (docker exec)
# ══════════════════════════════════════════════════════════════

def _get_pg_schema() -> dict[str, list[dict]]:
    """PG ADWOWN 스키마의 테이블/컬럼 정보를 docker exec로 추출."""
    query = (
        "SELECT t.table_name, c.column_name, c.data_type, "
        "c.character_maximum_length, c.is_nullable, "
        "CASE WHEN pk.column_name IS NOT NULL THEN 'Y' ELSE 'N' END AS is_pk "
        "FROM information_schema.tables t "
        "JOIN information_schema.columns c "
        "ON t.table_name = c.table_name AND t.table_schema = c.table_schema "
        "LEFT JOIN ("
        "  SELECT kcu.table_name, kcu.column_name "
        "  FROM information_schema.table_constraints tc "
        "  JOIN information_schema.key_column_usage kcu "
        "  ON tc.constraint_name = kcu.constraint_name "
        "  WHERE tc.constraint_type = 'PRIMARY KEY'"
        "  AND tc.table_schema = 'adwown'"
        ") pk ON c.table_name = pk.table_name"
        " AND c.column_name = pk.column_name "
        "WHERE t.table_schema = 'adwown'"
        " AND t.table_type = 'BASE TABLE' "
        "ORDER BY t.table_name, c.ordinal_position"
    )
    cmd = [
        "docker", "exec", "dc-postgres", "psql",
        "-U", "postgres", "-d", "test_db",
        "-t", "-A", "-F|", "-c", query,
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8"
    )
    if result.returncode != 0:
        print(f"PG 스키마 추출 실패: {result.stderr}")
        sys.exit(1)

    schema: dict[str, list[dict]] = {}
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) < 6:
            continue
        tbl = parts[0].strip().upper()
        col_info = {
            "name": parts[1].strip().upper(),
            "data_type": parts[2].strip(),
            "max_length": parts[3].strip() or None,
            "nullable": parts[4].strip(),
            "is_pk": parts[5].strip() == "Y",
        }
        schema.setdefault(tbl, []).append(col_info)
    return schema


# ══════════════════════════════════════════════════════════════
# TYPE-3: 메타 설명 품질 생성
# ══════════════════════════════════════════════════════════════

# PII 컬럼 패턴
PII_COLS = {"PHONE_NO", "EMAIL", "CUS_ADR", "ADR_CNTS", "RRN", "CRD_NO", "ACN"}

# 필수 POOR/MISSING 케이스 (요구사항 명시)
FORCED_POOR_MISSING = {
    ("TB_ADW_CSC101M", "CUS_DCD"): "타입코드",
    ("TB_ADW_DEP201P", "BAL_AMT"): "잔액",
    ("TB_ADW_DEP202S", "BASE_DT"): "기준일자",
    ("TB_ADW_LNB302M", "LN_PUSE_CD"): None,
    ("TB_ADW_LNB302M", "CLTR_DCD"): None,
    ("TB_ADW_CRD401M", "FLG_YN"): "Y/N FLAG",
    ("TB_ADW_FXD501L", "DL_DCD"): "딜유형",
    ("TB_ADW_AML1116M", "ALERT_LVL_CD"): None,
    ("TB_ADW_PNB903L", "CNTR_DCD"): "유형",
    ("TB_ADW_RSK1101M", "IND_CD"): "지표",
    ("TB_ADW_MKT1201M", "CAMP_TGT_DCD"): None,
    ("TB_ADW_MKT1202M", "RESP_YN"): "Y/N",
    ("TB_ADW_FIN1306S", "PL_ITEM_CD"): "항목",
    ("TB_ADW_WMB1401M", "WM_GRD_CD"): None,
    ("TB_ADW_INS803M", "INS_DCD"): "보험유형",
    ("TB_ADW_DEA203M", "ACT_STCD"): None,
    ("TB_ADW_LNB341P", "LN_BAL_AMT"): "잔액",
}

FORCED_TABLE_POOR_MISSING = {
    "TB_ADW_TRX701L": "거래 이력",
    "TB_ADW_RSK1101M": "리스크",
    "TB_ADW_DEA203M": "계좌 마스터",
    "TB_ADW_LNB341P": "여신 일별",
}

# 한글 컬럼명 추론 사전
COL_KO_MAP = {
    "EDPS_CSN": "전산고객번호", "CSM": "고객명", "CUS_DCD": "고객구분코드",
    "CUS_GRD_CD": "고객등급코드", "MKT_GRD_CD": "마케팅등급코드",
    "BLNG_BRCD": "소속부점코드", "BR_NM": "부점명", "BR_DCD": "부점유형코드",
    "JOIN_DT": "가입일자", "RGST_DT": "등록일자",
    "STD_DT": "기준일자", "BASE_DT": "기준일자", "BASE_YM": "기준년월",
    "ACN": "계좌번호", "ACT_DCD": "계좌구분코드", "ACT_STCD": "계좌상태코드",
    "BAL_AMT": "잔액", "TOT_BAL_AMT": "총잔액",
    "LN_NO": "여신번호", "LN_EXC_AMT": "여신실행금액", "LN_BAL_AMT": "여신잔액",
    "LN_APR_AMT": "여신승인금액", "LN_DCD": "여신구분코드", "LN_STCD": "여신상태코드",
    "LN_PUSE_CD": "대출용도코드", "CLTR_DCD": "담보구분코드",
    "OVDU_GRD_CD": "연체등급코드", "OVDU_AMT": "연체금액", "OVDU_DY_CN": "연체일수",
    "OVDU_YN": "연체여부",
    "CRD_NO": "카드번호", "CRD_DCD": "카드구분코드",
    "TR_ID": "거래ID", "TR_DT": "거래일자", "TR_AMT": "거래금액", "TR_DCD": "거래구분코드",
    "CHN_CD": "채널코드", "DL_NO": "딜번호", "DL_DCD": "딜구분코드", "DL_RT": "딜환율",
    "CCY_CD": "통화코드", "BASE_RT": "기준환율",
    "FND_ACN": "펀드계좌번호", "FUND_CD": "펀드코드", "ERNS_RT": "수익률",
    "INS_NO": "보험번호", "INS_DCD": "보험구분코드", "INS_PD_CD": "보험상품코드",
    "PLAN_NO": "제도번호", "PN_DCD": "연금구분코드",
    "IND_CD": "지표코드", "IND_VAL": "지표값",
    "CAMP_CD": "캠페인코드", "CAMP_STCD": "캠페인상태코드",
    "PL_ITEM_CD": "손익항목코드", "GL_ACCT_CD": "계정과목코드",
    "WM_GRD_CD": "WM등급코드", "INVEST_PRFL_CD": "투자성향코드",
    "PD_CD": "상품코드", "PD_NM": "상품명", "INT_RT": "이자율",
    "FLG_YN": "플래그여부", "GRD_CD": "등급코드", "GRD_NM": "등급명",
    "APLY_RT": "적용율", "LTV_RTO": "LTV비율",
    "FEE_DCD": "수수료구분코드", "CHG_DT": "변경일자", "CALC_DT": "산출일자",
    "EVAL_DT": "평가일자", "EFF_DT": "적용시작일자", "EXEC_DT": "실행일자",
    "RPAY_DT": "상환일자", "RPAY_AMT": "상환금액", "STAT_DT": "상태변경일자",
    "ALERT_LVL_CD": "경보등급코드", "CNTR_DCD": "납입구분코드",
    "CRSC_GRD_CD": "신용등급코드", "RSK_STAGE_CD": "리스크단계코드",
    "CAMP_TGT_DCD": "캠페인대상구분코드", "RESP_YN": "응답여부",
    "NM": "명칭", "AMT": "금액", "RMRK": "비고",
}


def _col_ko_name(col_name: str) -> str:
    """컬럼명에서 한글명 추론."""
    if col_name in COL_KO_MAP:
        return COL_KO_MAP[col_name]
    # 접미사 기반 추론
    if col_name.endswith("_CD"):
        return col_name.replace("_CD", "").replace("_", " ").title() + " 코드"
    if col_name.endswith("_DT"):
        return col_name.replace("_DT", "").replace("_", " ").title() + " 일자"
    if col_name.endswith("_AMT"):
        return col_name.replace("_AMT", "").replace("_", " ").title() + " 금액"
    if col_name.endswith("_NM"):
        return col_name.replace("_NM", "").replace("_", " ").title() + " 명"
    if col_name.endswith("_NO"):
        return col_name.replace("_NO", "").replace("_", " ").title() + " 번호"
    if col_name.endswith("_YN"):
        return col_name.replace("_YN", "").replace("_", " ").title() + " 여부"
    if col_name.endswith("_SEQ"):
        return col_name.replace("_SEQ", "").replace("_", " ").title() + " 순번"
    if col_name.endswith("_ID"):
        return col_name.replace("_ID", "").replace("_", " ").title() + " ID"
    if col_name.endswith("_CNT"):
        return col_name.replace("_CNT", "").replace("_", " ").title() + " 건수"
    if col_name.endswith("_RT") or col_name.endswith("_RATE"):
        base = col_name.replace("_RT", "").replace("_RATE", "")
        return base.replace("_", " ").title() + " 비율"
    return col_name


def _table_desc_quality(
    tbl_name: str, ko_name: str, domain: str
) -> str | None:
    """TYPE-3: 테이블 설명 품질을 확률적으로 결정."""
    # 강제 POOR/MISSING
    if tbl_name in FORCED_TABLE_POOR_MISSING:
        return FORCED_TABLE_POOR_MISSING[tbl_name]

    # 결정론적 해시 기반 (재실행 시 동일 결과)
    h = int(hashlib.md5(tbl_name.encode()).hexdigest(), 16) % 100

    if h < 15:  # BEST 15%
        cycle = random.choice(["일배치", "실시간", "월배치"])
        return (
            f"{ko_name} 테이블. 당행 {domain} 업무 영역에서 관리하는 데이터로, "
            f"관련 업무 프로세스 수행 시 데이터가 적재된다. "
            f"갱신 주기는 {cycle}이며, 기준일자 조건을 포함하여 조회해야 한다."
        )
    elif h < 40:  # GOOD 25%
        return f"{ko_name}. 당행 업무 데이터."
    elif h < 80:  # POOR 40%
        return ko_name if ko_name else tbl_name
    else:  # MISSING 20%
        return random.choice([None, "TEMP", "DATA TABLE", ""])


def _col_desc_quality(tbl_name: str, col_name: str) -> str | None:
    """TYPE-3: 컬럼 설명 품질을 확률적으로 결정."""
    # 강제 POOR/MISSING
    key = (tbl_name, col_name)
    if key in FORCED_POOR_MISSING:
        return FORCED_POOR_MISSING[key]

    ko = _col_ko_name(col_name)
    digest = hashlib.md5(f"{tbl_name}.{col_name}".encode()).hexdigest()
    h = int(digest, 16) % 100

    if h < 15:  # BEST
        return f"{ko} — 업무 처리 시 사용되는 필수 항목"
    elif h < 40:  # GOOD
        return ko
    elif h < 80:  # POOR
        # 2~3 단어로 축약
        words = ko.split()
        return " ".join(words[:2]) if len(words) > 2 else ko
    else:  # MISSING
        return random.choice([None, "FLAG", "CODE", "VALUE", ""])


def _pg_type_to_es(data_type: str, max_len: str | None) -> str:
    """PG 데이터타입 → ES용 타입 문자열."""
    if data_type == "date":
        return "DATE"
    if data_type in (
        "timestamp without time zone", "timestamp with time zone"
    ):
        return "TIMESTAMP"
    if data_type == "integer":
        return "INTEGER"
    if data_type == "numeric":
        return "NUMERIC"
    if data_type == "character varying":
        return f"VARCHAR({max_len})" if max_len else "VARCHAR"
    if data_type == "character":
        return f"CHAR({max_len})" if max_len else "CHAR"
    return data_type.upper()


# ══════════════════════════════════════════════════════════════
# code_meta (TYPE-2: 공식 코드만)
# ══════════════════════════════════════════════════════════════

CODE_META_DOCS = [
    {"code_field": "CUS_DCD", "code_field_desc": "고객구분코드",  # noqa: E501
     "table_name": "TB_ADW_CSC101M",
     "codes": {"01": "개인", "02": "법인", "03": "개인사업자"}},
    {"code_field": "CUS_GRD_CD", "code_field_desc": "고객등급코드",  # noqa: E501
     "table_name": "TB_ADW_CSC101M",
     "codes": {"01": "VIP", "02": "우수", "03": "일반", "04": "잠재", "05": "관리"}},  # noqa: E501
    {"code_field": "MKT_GRD_CD", "code_field_desc": "마케팅등급코드",  # noqa: E501
     "table_name": "TB_ADW_CSP103M",
     "codes": {"A": "최우수", "B": "우수", "C": "일반", "D": "관심", "E": "휴면"}},  # noqa: E501
    {"code_field": "ACT_DCD", "code_field_desc": "계좌구분코드",  # noqa: E501
     "table_name": "TB_ADW_DEP201P",
     "codes": {"01": "보통예금", "02": "정기예금", "03": "적금", "04": "MMF"}},  # noqa: E501
    {"code_field": "ACT_STCD", "code_field_desc": "계좌상태코드",  # noqa: E501
     "table_name": "TB_ADW_DEP201P",
     "codes": {"01": "정상", "02": "해지", "03": "휴면"}},
    {"code_field": "LN_DCD", "code_field_desc": "여신구분코드",  # noqa: E501
     "table_name": "TB_ADW_LNB301M",
     "codes": {"01": "신용대출", "02": "담보대출", "03": "보증대출"}},
    {"code_field": "LN_STCD", "code_field_desc": "여신상태코드",  # noqa: E501
     "table_name": "TB_ADW_LNB301M",
     "codes": {"01": "정상", "02": "기한이익상실", "03": "연체", "04": "대위변제", "05": "상각"}},  # noqa: E501
    {"code_field": "OVDU_GRD_CD", "code_field_desc": "연체등급코드",  # noqa: E501
     "table_name": "TB_ADW_LNB301M",
     "codes": {"A": "정상", "B": "요주의", "C": "고정", "D": "회수의문", "E": "추정손실"}},  # noqa: E501
    {"code_field": "LN_PUSE_CD", "code_field_desc": "대출용도코드",  # noqa: E501
     "table_name": "TB_ADW_LNB302M",
     "codes": {"01": "주택구입", "02": "전세자금", "03": "사업자금", "04": "생활자금", "05": "기타"}},  # noqa: E501
    {"code_field": "CLTR_DCD", "code_field_desc": "담보구분코드",  # noqa: E501
     "table_name": "TB_ADW_LNB302M",
     "codes": {"01": "부동산", "02": "유가증권", "03": "예적금", "04": "무담보"}},  # noqa: E501
    {"code_field": "CRD_DCD", "code_field_desc": "카드구분코드",  # noqa: E501
     "table_name": "TB_ADW_CRD401M",
     "codes": {"01": "신용카드", "02": "체크카드", "03": "선불카드"}},
    {"code_field": "TR_DCD", "code_field_desc": "거래구분코드",
     "table_name": "TB_ADW_TRX701L",
     "codes": {str(i): f"거래유형{i}" for i in range(100, 200)}},
    {"code_field": "CHN_CD", "code_field_desc": "채널코드",
     "table_name": "TB_ADW_TRX701L",
     "codes": {"01": "영업점", "02": "인터넷뱅킹", "03": "모바일뱅킹", "04": "ATM"}},  # noqa: E501
    {"code_field": "FX_DL_DCD", "code_field_desc": "외환딜구분코드",  # noqa: E501
     "table_name": "TB_ADW_FXD501L",
     "codes": {"01": "현물매입", "02": "현물매도", "03": "선물환", "04": "스왑", "05": "옵션"}},  # noqa: E501
    {"code_field": "CCY_CD", "code_field_desc": "통화코드",
     "table_name": "TB_ADW_COM012M",
     "codes": {"KRW": "한국원", "USD": "미국달러", "EUR": "유로", "JPY": "일본엔", "GBP": "영국파운드", "CNY": "중국위안"}},  # noqa: E501
    {"code_field": "FND_DCD", "code_field_desc": "펀드구분코드",
     "table_name": "TB_ADW_FND603M",
     "codes": {"01": "주식형", "02": "채권형", "03": "혼합형", "04": "MMF"}},
    {"code_field": "RSK_GRD_CD", "code_field_desc": "펀드위험등급",  # noqa: E501
     "table_name": "TB_ADW_FND611M",
     "codes": {"1": "매우높은위험", "2": "높은위험", "3": "다소높은위험", "4": "보통위험", "5": "낮은위험"}},  # noqa: E501
    {"code_field": "INS_DCD", "code_field_desc": "보험구분코드",
     "table_name": "TB_ADW_INS803M",
     "codes": {"L": "생명보험", "N": "손해보험", "H": "건강보험"}},
    {"code_field": "PAY_STCD", "code_field_desc": "납입상태코드",  # noqa: E501
     "table_name": "TB_ADW_INS805L",
     "codes": {"01": "정상납입", "02": "미납", "03": "완납"}},
    {"code_field": "PN_DCD", "code_field_desc": "연금구분코드",
     "table_name": "TB_ADW_PNB901M",
     "codes": {"DB": "확정급여형", "DC": "확정기여형", "IRP": "개인형퇴직연금"}},  # noqa: E501
    {"code_field": "CRSC_GRD_CD", "code_field_desc": "신용등급코드",  # noqa: E501
     "table_name": "TB_ADW_LNA322M",
     "codes": {
         "AAA": "최우량", "AA": "우량", "A": "양호", "BBB": "보통",
         "BB": "주의", "B": "취약", "CCC": "위험",
         "CC": "매우위험", "C": "부실", "D": "채무불이행",
     }},
    {"code_field": "BR_DCD", "code_field_desc": "부점유형코드",
     "table_name": "TB_ADW_COM001M",
     "codes": {"01": "본점", "02": "지점", "03": "출장소"}},
    {"code_field": "CAMP_STCD", "code_field_desc": "캠페인상태코드",  # noqa: E501
     "table_name": "TB_ADW_MKT1201M",
     "codes": {"01": "계획", "02": "실행", "03": "종료"}},
    {"code_field": "RSK_STAGE_CD", "code_field_desc": "IFRS9단계코드",  # noqa: E501
     "table_name": "TB_ADW_RSK1111M",
     "codes": {"1": "Stage1-정상", "2": "Stage2-유의적증가", "3": "Stage3-신용손상"}},  # noqa: E501
    # ── 추가 코드 (기존 _EXTRA_CODES 에서 승격) ──────────────
    {"code_field": "GNDR_DCD", "code_field_desc": "성별구분코드",
     "table_name": "TB_ADW_CSC101M",
     "codes": {"M": "남성", "F": "여성"}},
    {"code_field": "AGE_GRP_CD", "code_field_desc": "연령대코드",
     "table_name": "TB_ADW_CSC101M",
     "codes": {"20": "20대", "30": "30대", "40": "40대", "50": "50대", "60": "60대이상"}},  # noqa: E501
    {"code_field": "STS_DCD", "code_field_desc": "상태구분코드",
     "table_name": "TB_ADW_CSC101M",
     "codes": {"01": "활성", "02": "비활성", "03": "정지"}},
    {"code_field": "WM_GRD_CD", "code_field_desc": "자산관리등급코드",  # noqa: E501
     "table_name": "TB_ADW_WMB1401M",
     "codes": {"WM_VIP": "WM VIP", "WM_PREMIUM": "WM프리미엄", "WM_GOLD": "WM골드", "WM_STANDARD": "WM일반"}},  # noqa: E501
    {"code_field": "INVEST_PRFL_CD", "code_field_desc": "투자성향코드",  # noqa: E501
     "table_name": "TB_ADW_WMB1401M",
     "codes": {"1": "안정형", "2": "안정추구형", "3": "위험중립형", "4": "적극투자형", "5": "공격투자형"}},  # noqa: E501
    {"code_field": "ALERT_LVL_CD", "code_field_desc": "경보수준코드",
     "table_name": "TB_ADW_AML1121M",
     "codes": {"H": "높음", "M": "중간", "L": "낮음"}},
    {"code_field": "CHG_RSN_DCD", "code_field_desc": "변경사유코드",
     "table_name": "TB_ADW_CSC102H",
     "codes": {"01": "정보변경", "02": "등급변경", "03": "상태변경", "04": "기타"}},  # noqa: E501
    {"code_field": "ACTN_DCD", "code_field_desc": "행위구분코드",
     "table_name": "TB_ADW_COM017L",
     "codes": {"01": "조회", "02": "등록", "03": "수정", "04": "삭제", "05": "승인"}},  # noqa: E501
    {"code_field": "INS_STCD", "code_field_desc": "보험상태코드",
     "table_name": "TB_ADW_INS803M",
     "codes": {"01": "유지", "02": "실효", "03": "해지", "04": "만기"}},
    {"code_field": "CAMP_TGT_DCD", "code_field_desc": "캠페인대상구분코드",  # noqa: E501
     "table_name": "TB_ADW_MKT1201M",
     "codes": {"01": "전체", "02": "세그먼트", "03": "개인"}},
    {"code_field": "RESP_YN", "code_field_desc": "응답여부",
     "table_name": "TB_ADW_MKT1202M",
     "codes": {"Y": "응답", "N": "미응답"}},
    {"code_field": "FLG_YN", "code_field_desc": "해외사용가능여부",
     "table_name": "TB_ADW_CRD401M",
     "codes": {"Y": "해당", "N": "미해당"}},
    {"code_field": "USE_YN", "code_field_desc": "사용여부",
     "table_name": "TB_ADW_COM001M",
     "codes": {"Y": "사용", "N": "미사용"}},
    {"code_field": "RGN_CD", "code_field_desc": "지역코드",
     "table_name": "TB_ADW_COM001M",
     "codes": {
         "01": "서울", "02": "경기", "03": "인천", "04": "대전",
         "05": "대구", "06": "부산", "07": "광주", "08": "울산",
         "09": "제주", "10": "충북", "11": "전북", "12": "경남",
     }},
    {"code_field": "PREF_CHN_DCD", "code_field_desc": "선호채널구분코드",  # noqa: E501
     "table_name": "TB_ADW_CSP103M",
     "codes": {"영업점": "영업점", "인터넷뱅킹": "인터넷뱅킹", "모바일뱅킹": "모바일뱅킹", "ATM": "ATM", "콜센터": "콜센터"}},  # noqa: E501
    {"code_field": "CONTACT_CHN_CD", "code_field_desc": "접촉채널코드",  # noqa: E501
     "table_name": "TB_ADW_MKT1202M",
     "codes": {"01": "영업점", "02": "인터넷뱅킹", "03": "모바일뱅킹", "04": "ATM"}},  # noqa: E501
    {"code_field": "PL_ITEM_CD", "code_field_desc": "손익항목코드",
     "table_name": "TB_ADW_FIN1306S",
     "codes": {
         "NII": "순이자이익", "NFI": "비이자이익", "OPEX": "판매관리비",
         "PROV": "충당금전입", "PRETAX": "세전이익", "NET": "당기순이익",
         "INT_INC": "이자수익", "FEE_INC": "수수료수익",
         "FX_INC": "외환수익", "FUND_INC": "펀드수익",
     }},
    {"code_field": "IND_CD", "code_field_desc": "리스크지표코드",
     "table_name": "TB_ADW_RSK1101M",
     "codes": {
         "BIS_RATIO": "BIS자기자본비율", "LCR": "유동성커버리지비율",
         "NSFR": "순안정자금조달비율", "NIM": "순이자마진",
         "ROA": "총자산순이익률", "ROE": "자기자본순이익률",
         "NPL_RATIO": "부실채권비율", "CVA": "신용가치조정",
         "LTV_AVG": "평균담보인정비율", "DSR_AVG": "평균총부채원리금상환비율",
     }},
]


# ══════════════════════════════════════════════════════════════
# report_sql
# ══════════════════════════════════════════════════════════════

REPORT_SQL_DOCS = [
    {"report_nm": "월간 신규 고객 현황",  # noqa: E501
     "report_desc": "월별 신규 등록 고객 수를 고객구분별로 집계",
     "domain_cd": "CUS", "tables_used": ["TB_ADW_CSC101M"],
     "sql_text": (  # noqa: E501
         "SELECT DATE_TRUNC('month', JOIN_DT) AS base_month,"
         " CUS_DCD, COUNT(*) AS cnt"
         " FROM ADWOWN.TB_ADW_CSC101M"
         " WHERE STD_DT = CURRENT_DATE GROUP BY 1, 2 ORDER BY 1"
     )},
    {"report_nm": "부점별 여신 잔액 TOP 10",  # noqa: E501
     "report_desc": "부점별 대출 잔액 합계 상위 10개",
     "domain_cd": "LON",
     "tables_used": ["TB_ADW_LNB301M", "TB_ADW_COM001M"],
     "sql_text": (  # noqa: E501
         "SELECT b.BR_NM, SUM(l.LN_BAL_AMT) AS total_bal"
         " FROM ADWOWN.TB_ADW_LNB301M l"
         " JOIN ADWOWN.TB_ADW_COM001M b"
         " ON l.BLNG_BRCD = b.BLNG_BRCD"
         " WHERE l.STD_DT = CURRENT_DATE"
         " GROUP BY 1 ORDER BY 2 DESC LIMIT 10"
     )},
    {"report_nm": "연체율 추이",
     "report_desc": "월별 연체 비율 추이 (연체금액/총여신잔액)",
     "domain_cd": "LON", "tables_used": ["TB_ADW_LNB301M"],
     "sql_text": (  # noqa: E501
         "SELECT STD_DT,"
         " ROUND(SUM(OVDU_AMT)::NUMERIC / NULLIF(SUM(LN_BAL_AMT), 0)"
         " * 100, 2) AS ovdu_rate"
         " FROM ADWOWN.TB_ADW_LNB301M GROUP BY 1 ORDER BY 1"
     )},
    {"report_nm": "VIP 고객 자산 현황",
     "report_desc": "VIP 등급 고객의 수신+여신 종합 현황",
     "domain_cd": "CUS",
     "tables_used": ["TB_ADW_CSC101M", "TB_ADW_DEP201P", "TB_ADW_LNB301M"],
     "sql_text": (  # noqa: E501
         "SELECT ci.EDPS_CSN, ci.CSM,"
         " COALESCE(SUM(ab.BAL_AMT),0) AS dep,"
         " COALESCE(SUM(li.LN_BAL_AMT),0) AS loan"
         " FROM ADWOWN.TB_ADW_CSC101M ci"
         " LEFT JOIN ADWOWN.TB_ADW_DEP201P ab"
         " ON ci.EDPS_CSN=ab.EDPS_CSN"
         " LEFT JOIN ADWOWN.TB_ADW_LNB301M li"
         " ON ci.EDPS_CSN=li.EDPS_CSN"
         " WHERE ci.CUS_GRD_CD='01'"
         " GROUP BY 1,2 ORDER BY dep DESC LIMIT 50"
     )},
    {"report_nm": "계좌구분별 잔액 집계",
     "report_desc": "계좌 구분별 계좌수, 총잔액",
     "domain_cd": "DEP", "tables_used": ["TB_ADW_DEP201P"],
     "sql_text": (  # noqa: E501
         "SELECT ACT_DCD, COUNT(*) AS cnt, SUM(BAL_AMT) AS total"
         " FROM ADWOWN.TB_ADW_DEP201P"
         " WHERE STD_DT = CURRENT_DATE GROUP BY 1 ORDER BY 3 DESC"
     )},
    {"report_nm": "카드 이용 현황",
     "report_desc": "월별 카드 이용 금액 추이",
     "domain_cd": "CRD", "tables_used": ["TB_ADW_CRD401M"],
     "sql_text": (
         "SELECT STD_DT, SUM(MON_USE_AMT) AS total_use"
         " FROM ADWOWN.TB_ADW_CRD401M GROUP BY 1 ORDER BY 1"
     )},
    {"report_nm": "거래 채널별 통계",
     "report_desc": "채널별 거래 건수 및 금액",
     "domain_cd": "TRX", "tables_used": ["TB_ADW_TRX701L"],
     "sql_text": (  # noqa: E501
         "SELECT CHN_CD, COUNT(*) AS cnt, SUM(TR_AMT) AS total"
         " FROM ADWOWN.TB_ADW_TRX701L"
         " WHERE TR_DT >= CURRENT_DATE - INTERVAL '30 days'"
         " GROUP BY 1 ORDER BY 2 DESC"
     )},
    {"report_nm": "고객구분별 분포",
     "report_desc": "개인/법인 고객의 구분별 인원수",
     "domain_cd": "CUS", "tables_used": ["TB_ADW_CSC101M"],
     "sql_text": (  # noqa: E501
         "SELECT CUS_DCD, COUNT(*)"
         " FROM ADWOWN.TB_ADW_CSC101M"
         " WHERE STD_DT=CURRENT_DATE GROUP BY 1 ORDER BY 1"
     )},
    {"report_nm": "담보대출 평균 금리",
     "report_desc": "담보대출 평균 적용 금리",
     "domain_cd": "LON", "tables_used": ["TB_ADW_LNB301M"],
     "sql_text": (  # noqa: E501
         "SELECT ROUND(AVG(INT_RT),2)"
         " FROM ADWOWN.TB_ADW_LNB301M"
         " WHERE STD_DT=CURRENT_DATE AND LN_DCD='02'"
     )},
    {"report_nm": "휴면 계좌 현황",
     "report_desc": "휴면 상태 계좌 수 및 잔액",
     "domain_cd": "DEP", "tables_used": ["TB_ADW_DEP201P"],
     "sql_text": (  # noqa: E501
         "SELECT COUNT(*), SUM(BAL_AMT)"
         " FROM ADWOWN.TB_ADW_DEP201P"
         " WHERE STD_DT=CURRENT_DATE AND ACT_STCD='03'"
     )},
]


# ══════════════════════════════════════════════════════════════
# term_dict
# ══════════════════════════════════════════════════════════════

TERM_DICT_DOCS = [
    {
        "term_ko": "고객",
        "col_pattern": "EDPS_CSN, CUS_*",
        "table_hint": "TB_ADW_CSC101M, TB_ADW_CSC102H",
        "definition": "은행과 거래 관계가 있는 개인 또는 법인",
        "synonym": "거래처, 손님",
        "caution": "EDPS_CSN으로 식별. 주민번호 직접 조회 금지",
    },
    {
        "term_ko": "잔액",
        "col_pattern": "BAL_AMT, TOT_BAL_AMT, ACT_BAL",
        "table_hint": "TB_ADW_DEP201P, TB_ADW_DEP202S",
        "definition": "특정 시점의 계좌 잔액",
        "synonym": "잔고, 예금액",
        "caution": (
            "T+0(당일) vs T+1(전일) 기준 차이 주의. "
            "TB_ADW_DEP201P는 당일, TB_ADW_DEP202S는 전일 기준"
        ),
    },
    {
        "term_ko": "여신",
        "col_pattern": "LN_*",
        "table_hint": "TB_ADW_LNB301M, TB_ADW_LNB302M",
        "definition": "은행이 고객에게 자금을 대출하는 행위 또는 그 금액",
        "synonym": "대출, 융자",
        "caution": "LNB301M(잔액 기준) vs LNB302M(부가정보 기준) 구분 필요",
    },
    {
        "term_ko": "수신",
        "col_pattern": "ACT_*, BAL_AMT",
        "table_hint": "TB_ADW_DEP201P, TB_ADW_DEA203M",
        "definition": "은행이 고객으로부터 자금을 예치받는 행위",
        "synonym": "예금, 적금",
        "caution": "계좌구분코드(ACT_DCD) 확인 필수",
    },
    {
        "term_ko": "연체",
        "col_pattern": "OVDU_*, OVDU_YN",
        "table_hint": "TB_ADW_LNB301M, TB_ADW_LNA322M",
        "definition": "약정한 기일까지 원리금을 상환하지 않은 상태",
        "synonym": "미납, 지연",
        "caution": "연체등급(A~E)과 미정의코드(F,Z) 존재 가능",
    },
    {
        "term_ko": "VIP",
        "col_pattern": "CUS_GRD_CD, MKT_GRD_CD",
        "table_hint": "TB_ADW_CSC101M, TB_ADW_CSP103M",
        "definition": "연간 거래액 또는 자산 기준 최상위 등급 고객",
        "synonym": "우수고객, 프리미엄",
        "caution": "영업등급(CUS_GRD_CD)과 마케팅등급(MKT_GRD_CD)이 다를 수 있음",
    },
    {
        "term_ko": "금리",
        "col_pattern": "INT_RT, APLY_RT",
        "table_hint": "TB_ADW_LNB301M, TB_ADW_DEP201P",
        "definition": "자금 대차에 대한 이자 비율",
        "synonym": "이자율, 이율",
        "caution": "연이율(%) 기준. 고정/변동 구분 필요",
    },
    {
        "term_ko": "부점",
        "col_pattern": "BLNG_BRCD, BR_NM",
        "table_hint": "TB_ADW_COM001M",
        "definition": "은행의 영업점 단위",
        "synonym": "영업점, 지점, 점포",
        "caution": "부점코드 기준. 001=본점영업부",
    },
    {
        "term_ko": "연체율",
        "col_pattern": "OVDU_AMT, LN_BAL_AMT",
        "table_hint": "TB_ADW_LNB301M",
        "definition": "총 대출금액 대비 연체금액의 비율",
        "synonym": "부실비율",
        "caution": "연체율 = 연체금액 / 총대출금액 × 100",
    },
    {
        "term_ko": "BIS비율",
        "col_pattern": "BIS_RATIO",
        "table_hint": "TB_CAPITAL_ADEQUACY",
        "definition": "자기자본 대비 위험가중자산 비율 (국제결제은행 기준)",
        "synonym": "자본적정성비율, CAR",
        "caution": "BIS비율 = 자기자본 / 위험가중자산 × 100. 최소 8% 이상",
    },
    {
        "term_ko": "NIM",
        "col_pattern": "NIM_*",
        "table_hint": "TB_NIM_CALC",
        "definition": "순이자마진. 이자수익과 이자비용의 차이를 운용자산으로 나눈 비율",
        "synonym": "순이자마진, Net Interest Margin",
        "caution": "NIM = (이자수익 - 이자비용) / 평균운용자산 × 100",
    },
    {
        "term_ko": "LCR",
        "col_pattern": "LCR_*",
        "table_hint": "TB_LIQUIDITY_RATIO",
        "definition": "유동성커버리지비율. 고유동성자산 대비 순현금유출액 비율",
        "synonym": "유동성비율",
        "caution": (
            "LCR = 고유동성자산 / 향후30일순현금유출액 × 100. 최소 100% 이상"
        ),
    },
    {
        "term_ko": "환율",
        "col_pattern": "BASE_RT, DL_RT, CCY_CD",
        "table_hint": "TB_ADW_FXB502M, TB_ADW_FXD501L",
        "definition": "두 통화 간 교환 비율",
        "synonym": "외환시세",
        "caution": "고시환율(기준)과 체결환율(실거래)은 다름",
    },
    {
        "term_ko": "펀드",
        "col_pattern": "FUND_CD, FND_ACN, ERNS_RT",
        "table_hint": "TB_ADW_FND601P, TB_ADW_FND602P",
        "definition": "투자자의 자금을 모아 전문가가 운용하는 간접투자상품",
        "synonym": "투자신탁, 수익증권",
        "caution": "잔고(원금)와 평가액(시가)은 다른 개념",
    },
    {
        "term_ko": "DSR",
        "col_pattern": "DSR_*",
        "table_hint": "TB_LOAN_DSR_INFO",
        "definition": "총부채원리금상환비율. 연간 원리금 상환액이 연소득에서 차지하는 비율",
        "synonym": "총부채상환비율",
        "caution": "DSR = 연간총부채원리금상환액 / 연소득 × 100",
    },
    {
        "term_ko": "LTV",
        "col_pattern": "LTV_RTO",
        "table_hint": "TB_ADW_LNB302M",
        "definition": "담보인정비율. 주택담보대출 시 담보가치 대비 대출금액 비율",
        "synonym": "주택담보비율",
        "caution": "LTV = 대출금액 / 담보가치 × 100",
    },
    {
        "term_ko": "신용등급",
        "col_pattern": "CRSC_GRD_CD, RSK_STAGE_CD",
        "table_hint": "TB_ADW_LNA322M, TB_ADW_RSK1111M",
        "definition": "차주의 채무상환능력을 등급으로 표시한 것",
        "synonym": "신용평점, 크레딧등급",
        "caution": "AAA~D 등급. NR(미평가)은 메타에 없을 수 있음",
    },
    {
        "term_ko": "AML",
        "col_pattern": "AML_*, ALERT_LVL_CD",
        "table_hint": "TB_ADW_AML1116M",
        "definition": "자금세탁방지. 불법자금의 세탁을 탐지·방지하는 업무",
        "synonym": "자금세탁방지, Anti-Money Laundering",
        "caution": "의심거래보고(SAR), 고액현금거래보고(CTR) 구분",
    },
    {
        "term_ko": "퇴직연금",
        "col_pattern": "PN_DCD, CNTR_DCD",
        "table_hint": "TB_ADW_PNB901M, TB_ADW_PNB903L",
        "definition": "근로자의 퇴직급여를 사외 금융기관에 적립·운용하는 제도",
        "synonym": "기업연금",
        "caution": (
            "DB형(확정급여)/DC형(확정기여)/IRP(개인형) 구분. HYB(혼합형)은 미정의"
        ),
    },
    {
        "term_ko": "FDS",
        "col_pattern": "FDS_*, FRAUD_*",
        "table_hint": "TB_FDS_ALERT, TB_CARD_FRAUD_ALERT",
        "definition": "이상거래탐지시스템. 비정상적 금융거래를 실시간 탐지",
        "synonym": "이상거래탐지",
        "caution": "카드 FDS와 계좌 FDS 구분 필요",
    },
]


# ══════════════════════════════════════════════════════════════
# 주의: ES 시딩 진입점은 제거되었다 (MongoDB + Qdrant로 대체).
# 본 파일은 상수/헬퍼 공유 모듈로만 유지되며, 직접 실행 대상이 아니다.
# ══════════════════════════════════════════════════════════════

