"""seed_all.py — 은행 AI 에이전트 테스트 데이터 통합 시딩 스크립트.

[설계 원칙]
- PostgreSQL : 랜덤 생성 기반 통계적 대규모 데이터 + 불완전성 케이스 비율 삽입
- ElasticSearch: nested 컬럼 구조(table_meta) + report_sql + code_meta + term_dict
- Qdrant       : fastembed(multilingual-e5-small) + biz_manual + sql_history
- 연결 정보     : .env 기반 (하드코딩 없음)
- 재실행 안전성 : TRUNCATE / 인덱스 재생성 / 컬렉션 재생성 방식

사용법:
    cp .env.example .env   # 연결 정보 입력
    pip install -r requirements-seed.txt
    python seed_all.py
"""

from __future__ import annotations

import os
import random
import sys
from datetime import date, timedelta

from dotenv import load_dotenv

load_dotenv()

# ══════════════════════════════════════════════════════════════
# 0. 설정 — .env 기반
# ══════════════════════════════════════════════════════════════

PG_INFO_DSN = (
    f"host={os.getenv('PG_HOST','localhost')} "
    f"port={os.getenv('PG_PORT','5432')} "
    f"dbname={os.getenv('PG_INFO_DB','info_db')} "
    f"user={os.getenv('PG_USER','postgres')} "
    f"password={os.getenv('PG_PASSWORD','postgres')}"
)
PG_HISTORY_DSN = (
    f"host={os.getenv('PG_HOST','localhost')} "
    f"port={os.getenv('PG_PORT','5432')} "
    f"dbname={os.getenv('PG_HISTORY_DB','history_db')} "
    f"user={os.getenv('PG_USER','postgres')} "
    f"password={os.getenv('PG_PASSWORD','postgres')}"
)

ES_URL      = os.getenv("ES_URL", "http://localhost:9200")
ES_USER     = os.getenv("ES_USER", "elastic")
ES_PASSWORD = os.getenv("ES_PASSWORD", "elastic_pass")

QDRANT_URL  = os.getenv("QDRANT_URL", "http://localhost:6333")

# fastembed 모델 — 워크플로우와 반드시 일치
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "intfloat/multilingual-e5-small")
EMBEDDING_DIM   = 384  # multilingual-e5-small 출력 차원

# ══════════════════════════════════════════════════════════════
# 1. 공통 상수 — 기존 seed_postgres.py 그대로 유지
# ══════════════════════════════════════════════════════════════

BRANCHES = [
    ("001", "본점영업부", "01", "서울"), ("002", "강남지점",  "01", "서울"),
    ("003", "여의도지점", "01", "서울"), ("004", "서초지점",  "01", "서울"),
    ("005", "종로지점",  "01", "서울"), ("006", "영등포지점", "01", "서울"),
    ("007", "마포지점",  "01", "서울"), ("008", "송파지점",  "01", "서울"),
    ("009", "분당지점",  "02", "경기"), ("010", "수원지점",  "02", "경기"),
    ("011", "인천지점",  "03", "인천"), ("012", "대전지점",  "04", "대전"),
    ("013", "대구지점",  "05", "대구"), ("014", "부산지점",  "06", "부산"),
    ("015", "광주지점",  "07", "광주"), ("016", "울산지점",  "08", "울산"),
    ("017", "제주지점",  "09", "제주"), ("018", "청주지점",  "10", "충북"),
    ("019", "전주지점",  "11", "전북"), ("020", "창원지점",  "12", "경남"),
]

CUST_GRADES    = ["01", "02", "03", "04", "05"]  # 01:VIP, 02:골드, 03:실버, 04:일반, 05:신규
CUS_TYPES     = ["01", "02", "03"]       # 개인, 기업, 개인사업자
GENDERS        = ["M", "F"]
AGE_GROUPS     = ["20", "30", "40", "50", "60"]
LN_TYPES       = ["01", "02", "03"]       # 신용, 담보, 보증

DEPOSIT_PRODUCTS = [
    ("P001", "자유입출금통장"), ("P002", "정기예금 1년"),
    ("P003", "정기예금 2년"),  ("P004", "정기적금 12개월"),
    ("P005", "정기적금 24개월"),("P006", "MMF 통장"),
    ("P007", "청년희망적금"),  ("P008", "주택청약저축"),
]

TR_TYPES = ["01", "02", "03"]

SURNAMES    = ["김","이","박","최","정","강","조","윤","장","임","한","오","서","신","권"]
GIVEN_NAMES = [
    "민준","서윤","하준","지우","서준","서연","도윤","하은",
    "지호","수빈","예준","지민","시우","유진","주원","채원",
    "지훈","수현","건우","소율","현우","다은","선우","예은",
    "영호","미영","정수","은정","상혁","혜진","태현","지영",
]
COMPANY_NAMES = [
    "(주)한국전자","(주)서울물산","(주)대한건설","(주)미래기술",
    "(주)동양식품","(주)코리아소프트","(주)글로벌트레이딩","(주)신세계유통",
]

# ── 불완전성 삽입 비율 (TYPE-2) ──────────────────────────────
# 랜덤 생성 데이터 중 이 비율만큼 미정의 코드값 삽입
IMPERFECTION_RATE = 0.03   # 전체의 약 3%


def _rnd_date(start: date, end: date) -> date:
    delta = (end - start).days
    return start + timedelta(days=random.randint(0, max(delta, 0)))


def _generate_name(cust_type: str) -> str:
    if cust_type == "02":
        return random.choice(COMPANY_NAMES)
    return random.choice(SURNAMES) + random.choice(GIVEN_NAMES)


# ══════════════════════════════════════════════════════════════
# 2. PostgreSQL 시딩
# ══════════════════════════════════════════════════════════════

def seed_postgres() -> None:
    import psycopg2

    # ── 정보계 DB ──────────────────────────────────────────
    conn = psycopg2.connect(PG_INFO_DSN)
    conn.autocommit = False
    cur = conn.cursor()
    try:
        # 재실행 안전: TRUNCATE (FK 순서 역순)
        for tbl in [
            "TB_ADW_TRX701L", "TB_ADW_LNR301S",
            "TB_ADW_DEP201M", "TB_ADW_LNB301M",
            "TB_ADW_CSC101M",   "TB_ADW_COM001M",
        ]:
            cur.execute(f"TRUNCATE TABLE {tbl} CASCADE")

        today = date.today()

        # 1. 지점 ─────────────────────────────────────────
        for b in BRANCHES:
            cur.execute(
                "INSERT INTO TB_ADW_COM001M (BRCD,BR_NM,RGN_CD,RGN_NM) "
                "VALUES (%s,%s,%s,%s)", b)
        print(f"  TB_ADW_COM001M : {len(BRANCHES):>5}건")

        # 2. 고객 500명 (랜덤 생성) ────────────────────────
        # [보완] TYPE-2: ~3%는 CUS_GRD_CD에 미정의 코드('99') 삽입
        N_CUST = 500
        cust_ids: list[str] = []
        for i in range(1, N_CUST + 1):
            cid       = f"C{i:08d}"
            ctype     = random.choices(CUS_TYPES, weights=[75, 15, 10])[0]
            reg_dt    = _rnd_date(today - timedelta(days=365*5), today)
            brch_cd   = random.choice(BRANCHES)[0]
            gender    = random.choice(GENDERS) if ctype != "02" else None
            age_grp   = random.choice(AGE_GROUPS) if ctype != "02" else None

            # [보완 TYPE-2] 약 3%에 미정의 등급 코드 삽입
            if random.random() < IMPERFECTION_RATE:
                grade = random.choice(["99", None])   # 메타에 없는 코드
            else:
                grade = random.choices(CUST_GRADES, weights=[5,15,30,50])[0]

            cur.execute(
                "INSERT INTO TB_ADW_CSC101M "
                "(EDPS_CSN,CSM,RGST_DT,CUS_DCD,BRCD,GNDR_CD,AGE_GR_CD,CUS_GRD_CD) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (cid, _generate_name(ctype), reg_dt, ctype, brch_cd, gender, age_grp, grade),
            )
            cust_ids.append(cid)
        print(f"  TB_ADW_CSC101M   : {N_CUST:>5}건  "
              f"(불완전 등급코드 약 {int(N_CUST*IMPERFECTION_RATE)}건 포함)")

        # 3. 대출 800건 (랜덤 생성) ────────────────────────
        # [보완] TYPE-2: ~3%는 LN_DCD에 미정의 코드('09') 삽입
        N_LOAN = 800
        for i in range(1, N_LOAN + 1):
            loan_no  = f"L{i:08d}"
            cust_no  = random.choice(cust_ids)
            loan_dt  = _rnd_date(today - timedelta(days=365*3), today)
            mtrty_dt = loan_dt + timedelta(days=random.choice([365,730,1095,1825]))

            # [보완 TYPE-2] 약 3%에 미정의 대출유형 삽입
            if random.random() < IMPERFECTION_RATE:
                ltype    = "09"           # 메타 미정의
                loan_amt = random.randint(5, 500) * 1000000
                int_rate = round(random.uniform(2.0, 15.0), 2)
            else:
                ltype = random.choices(LN_TYPES, weights=[40,45,15])[0]
                if ltype == "01":
                    loan_amt = random.randint(5, 100) * 1000000
                    int_rate = round(random.uniform(4.0, 12.0), 2)
                elif ltype == "02":
                    loan_amt = random.randint(50, 1000) * 1000000
                    int_rate = round(random.uniform(2.5, 6.0), 2)
                else:
                    loan_amt = random.randint(10, 300) * 1000000
                    int_rate = round(random.uniform(3.0, 8.0), 2)

            loan_bal    = int(loan_amt * (1 - random.uniform(0.0, 0.7)))
            is_overdue  = random.random() < 0.08
            overdue_yn  = "Y" if is_overdue else "N"
            overdue_days= random.randint(1, 180) if is_overdue else 0
            overdue_amt = int(loan_bal * random.uniform(0.01, 0.3)) if is_overdue else 0

            cur.execute(
                "INSERT INTO TB_ADW_LNB301M "
                "(LN_NO,EDPS_CSN,LN_EXC_AMT,LN_BAL_AMT,LN_DT,MTRTY_DT,"
                "APLY_RT,LN_DCD,OVDU_YN,OVDU_DY_CN,OVDU_AMT) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (loan_no,cust_no,loan_amt,loan_bal,loan_dt,mtrty_dt,
                 int_rate,ltype,overdue_yn,overdue_days,overdue_amt),
            )
        print(f"  TB_ADW_LNB301M   : {N_LOAN:>5}건  "
              f"(미정의 유형코드 약 {int(N_LOAN*IMPERFECTION_RATE)}건 포함)")

        # 4. 예금 600건 (랜덤 생성) ────────────────────────
        N_DEP = 600
        acct_ids: list[str] = []
        for i in range(1, N_DEP + 1):
            acct_no = f"A{i:010d}"
            cust_no = random.choice(cust_ids)
            prod    = random.choice(DEPOSIT_PRODUCTS)
            open_dt = _rnd_date(today - timedelta(days=365*3), today)
            acct_bal= random.randint(10, 50000) * 10000
            int_rate= round(random.uniform(0.1, 4.5), 4)
            # [보완 TYPE-2] ~3%에 미정의 계좌상태 삽입
            if random.random() < IMPERFECTION_RATE:
                status = "99"
            else:
                status = random.choices(["01","02","03"], weights=[80,12,8])[0]
            cur.execute(
                "INSERT INTO TB_ADW_DEP201M "
                "(ACN,EDPS_CSN,ACT_BAL_AMT,OPNG_DT,PD_CD,PD_NM,APLY_RT,ACT_STCD) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (acct_no,cust_no,acct_bal,open_dt,prod[0],prod[1],int_rate,status),
            )
            acct_ids.append(acct_no)
        print(f"  TB_ADW_DEP201M: {N_DEP:>5}건  "
              f"(미정의 상태코드 약 {int(N_DEP*IMPERFECTION_RATE)}건 포함)")

        # 5. 거래내역 3000건, 최근 6개월 (랜덤 생성) ───────
        # [보완] TYPE-2: ~2%에 미정의 거래유형('09') 삽입
        N_TXN = 3000
        for i in range(1, N_TXN + 1):
            txn_no  = f"T{i:012d}"
            acct_no = random.choice(acct_ids)
            txn_dt  = _rnd_date(today - timedelta(days=180), today)
            txn_tm  = f"{random.randint(9,17):02d}{random.randint(0,59):02d}{random.randint(0,59):02d}"
            txn_amt = random.randint(1, 5000) * 10000
            if random.random() < 0.02:
                txn_type = "09"   # [TYPE-2] 미정의 거래유형
            else:
                txn_type = random.choice(TR_TYPES)
            brch_cd = random.choice(BRANCHES)[0]
            cur.execute(
                "INSERT INTO TB_ADW_TRX701L "
                "(TR_NO,ACN,TR_DT,TR_TM,TR_AMT,TR_DCD,BRCD) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (txn_no,acct_no,txn_dt,txn_tm,txn_amt,txn_type,brch_cd),
            )
        print(f"  TB_ADW_TRX701L : {N_TXN:>5}건  (미정의 거래유형 약 {int(N_TXN*0.02)}건 포함)")

        # 6. 연체 통계 12개월 × 20지점 × 3유형 (랜덤 생성) ─
        n_stat = 0
        for mo in range(12, 0, -1):
            base_dt = today.replace(day=1) - timedelta(days=30*mo)
            base_ym = base_dt.strftime("%Y%m")
            for brch_cd, *_ in BRANCHES:
                for lt in LN_TYPES:
                    tot_cnt  = random.randint(20, 200)
                    tot_amt  = tot_cnt * random.randint(30, 300) * 1_000_000
                    ovd_cnt  = int(tot_cnt * random.uniform(0.01, 0.08))
                    ovd_amt  = int(tot_amt * random.uniform(0.005, 0.04))
                    ovd_rate = round(ovd_amt / tot_amt * 100 if tot_amt else 0, 2)
                    cur.execute(
                        "INSERT INTO TB_ADW_LNR301S "
                        "(BASE_YM,BRCD,LN_DCD,TOT_LN_CN,"
                        "TOT_LN_AMT,OVDU_CN,OVDU_AMT,OVDU_RT) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                        (base_ym,brch_cd,lt,tot_cnt,tot_amt,ovd_cnt,ovd_amt,ovd_rate),
                    )
                    n_stat += 1
        print(f"  TB_ADW_LNR301S: {n_stat:>5}건")

        conn.commit()
        print("  → 정보계 DB 완료\n")
    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"정보계 DB 시딩 실패: {e}") from e
    finally:
        cur.close(); conn.close()

    # ── 이력 DB ────────────────────────────────────────────
    conn = psycopg2.connect(PG_HISTORY_DSN)
    conn.autocommit = False
    cur = conn.cursor()

    # [보완] 기존 15건 유지 + 재질문 케이스 2건 추가 = 17건
    HISTORY = [
        ("이번 달 신규 고객 수",
         "SELECT COUNT(*) AS new_cust_cnt FROM TB_ADW_CSC101M "
         "WHERE RGST_DT >= DATE_TRUNC('month', CURRENT_DATE)",
         True, "user01", 45),
        ("대출 유형별 실행 건수",
         "SELECT LN_DCD, COUNT(*) AS loan_cnt, SUM(LN_EXC_AMT) AS total_amt "
         "FROM TB_ADW_LNB301M WHERE LN_DT >= DATE_TRUNC('month', CURRENT_DATE) "
         "GROUP BY LN_DCD",
         True, "user01", 120),
        ("지점별 고객 수",
         "SELECT b.BR_NM, COUNT(c.EDPS_CSN) AS cust_cnt "
         "FROM TB_ADW_CSC101M c JOIN TB_ADW_COM001M b ON c.BRCD = b.BRCD "
         "GROUP BY b.BR_NM ORDER BY cust_cnt DESC LIMIT 10",
         True, "user02", 85),
        ("연체율 추이",
         "SELECT BASE_YM, "
         "ROUND(SUM(OVDU_AMT)::NUMERIC / NULLIF(SUM(TOT_LN_AMT),0)*100,2) AS overdue_rate "
         "FROM TB_ADW_LNR301S "
         "WHERE BASE_YM >= TO_CHAR(CURRENT_DATE-INTERVAL '12 months','YYYYMM') "
         "GROUP BY BASE_YM ORDER BY BASE_YM",
         True, "user03", 200),
        ("예금 잔액 현황",
         "SELECT PD_CD, COUNT(*) AS acct_cnt, SUM(ACT_BAL_AMT) AS total_bal "
         "FROM TB_ADW_DEP201M WHERE ACT_STCD = '01' "
         "GROUP BY PD_CD ORDER BY total_bal DESC",
         True, "user02", 90),
        ("VIP 고객 대출 현황",
         "SELECT c.CSM, c.CUS_GRD_CD, COUNT(l.LN_NO) AS loan_cnt, "
         "SUM(l.LN_BAL_AMT) AS total_bal "
         "FROM TB_ADW_CSC101M c JOIN TB_ADW_LNB301M l ON c.EDPS_CSN = l.EDPS_CSN "
         "WHERE c.CUS_GRD_CD = '01' "
         "GROUP BY c.CSM, c.CUS_GRD_CD ORDER BY total_bal DESC LIMIT 20",
         True, "user01", 150),
        ("이번 달 입금 총액",
         "SELECT SUM(TR_AMT) AS total_deposit FROM TB_ADW_TRX701L "
         "WHERE TR_DCD = '01' AND TR_DT >= DATE_TRUNC('month', CURRENT_DATE)",
         True, "user04", 60),
        ("지점별 연체율 상위 10개",
         "SELECT b.BR_NM, s.OVDU_RT, s.OVDU_AMT "
         "FROM TB_ADW_LNR301S s JOIN TB_ADW_COM001M b ON s.BRCD = b.BRCD "
         "WHERE s.BASE_YM = TO_CHAR(CURRENT_DATE-INTERVAL '1 month','YYYYMM') "
         "ORDER BY s.OVDU_RT DESC LIMIT 10",
         True, "user03", 110),
        ("연령대별 고객 분포",
         "SELECT AGE_GR_CD, COUNT(*) AS cust_cnt "
         "FROM TB_ADW_CSC101M WHERE CUS_DCD = '01' "
         "GROUP BY AGE_GR_CD ORDER BY AGE_GR_CD",
         True, "user05", 55),
        ("담보대출 평균 금리",
         "SELECT ROUND(AVG(APLY_RT),2) AS avg_rate "
         "FROM TB_ADW_LNB301M WHERE LN_DCD = '02'",
         True, "user01", 40),
        ("월별 신규 대출 추이",
         "SELECT DATE_TRUNC('month', LN_DT) AS base_month, "
         "COUNT(*) AS loan_cnt, SUM(LN_EXC_AMT) AS total_amt "
         "FROM TB_ADW_LNB301M WHERE LN_DT >= CURRENT_DATE - INTERVAL '12 months' "
         "GROUP BY DATE_TRUNC('month', LN_DT) ORDER BY base_month",
         True, "user02", 180),
        ("휴면 계좌 현황",
         "SELECT COUNT(*) AS dormant_cnt, SUM(ACT_BAL_AMT) AS dormant_bal "
         "FROM TB_ADW_DEP201M WHERE ACT_STCD = '03'",
         True, "user04", 35),
        ("고객별 총 자산",
         "SELECT c.EDPS_CSN, c.CSM, "
         "COALESCE(SUM(d.ACT_BAL_AMT),0) AS deposit_total, "
         "COALESCE(SUM(l.LN_BAL_AMT),0) AS loan_total "
         "FROM TB_ADW_CSC101M c "
         "LEFT JOIN TB_ADW_DEP201M d ON c.EDPS_CSN=d.EDPS_CSN AND d.ACT_STCD='01' "
         "LEFT JOIN TB_ADW_LNB301M l ON c.EDPS_CSN=l.EDPS_CSN "
         "GROUP BY c.EDPS_CSN, c.CSM ORDER BY deposit_total DESC LIMIT 50",
         True, "user01", 350),
        ("상품별 가중평균금리",
         "SELECT PD_CD, PD_NM, "
         "ROUND(SUM(APLY_RT*ACT_BAL_AMT)/NULLIF(SUM(ACT_BAL_AMT),0),4) AS weighted_avg_rate, "
         "SUM(ACT_BAL_AMT) AS total_bal "
         "FROM TB_ADW_DEP201M WHERE ACT_STCD='01' "
         "GROUP BY PD_CD, PD_NM ORDER BY total_bal DESC",
         True, "user03", 140),
        ("기업 고객 대출 비중",
         "SELECT c.CUS_DCD, COUNT(*) AS loan_cnt, SUM(l.LN_EXC_AMT) AS total_amt "
         "FROM TB_ADW_LNB301M l JOIN TB_ADW_CSC101M c ON l.EDPS_CSN=c.EDPS_CSN "
         "GROUP BY c.CUS_DCD",
         True, "user05", 95),
        # [보완] 실패 케이스 — 에이전트 오류 패턴 학습용
        ("최근 거래 내역 보여줘",
         "SELECT * FROM TB_ADW_TRX701L",          # 기간 조건 누락 → 타임아웃
         False, "user06", None),
        ("이번 달 연체 고객",
         "SELECT * FROM TB_ADW_LNB301M WHERE OVDU_YN='Y' "
         "AND LN_DCD IN ('01','02','03','09')",  # 미정의 코드 포함 처리 예시
         True, "user07", 210),
    ]

    try:
        cur.execute("TRUNCATE TABLE sql_exec_log RESTART IDENTITY")
        for row in HISTORY:
            cur.execute(
                "INSERT INTO sql_exec_log "
                "(query_text,sql_text,success_yn,user_id,exec_ms) "
                "VALUES (%s,%s,%s,%s,%s)", row)
        conn.commit()
        print(f"  sql_exec_log: {len(HISTORY):>4}건  (실패 케이스 1건 포함)")
        print("  → 이력 DB 완료\n")
    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"이력 DB 시딩 실패: {e}") from e
    finally:
        cur.close(); conn.close()


# ══════════════════════════════════════════════════════════════
# 3. ElasticSearch 시딩
# ══════════════════════════════════════════════════════════════

def seed_elasticsearch() -> None:
    from elasticsearch import Elasticsearch

    es = Elasticsearch(ES_URL, basic_auth=(ES_USER, ES_PASSWORD), request_timeout=30)
    if not es.ping():
        raise ConnectionError(f"ES 연결 실패: {ES_URL}")
    print(f"  ES {es.info()['version']['number']} 연결 확인")

    def _recreate(name: str, body: dict) -> None:
        if es.indices.exists(index=name):
            es.indices.delete(index=name)
        es.indices.create(index=name, body=body)

    def _bulk(name: str, docs: list[dict]) -> None:
        actions = []
        for i, doc in enumerate(docs):
            actions += [{"index": {"_index": name, "_id": str(i)}}, doc]
        es.bulk(body=actions, refresh="wait_for")
        print(f"  {name:<15}: {len(docs):>3}건")

    _SHARD = {"settings": {"number_of_shards": 1, "number_of_replicas": 0}}

    # ── (A) table_meta — nested 컬럼 구조 유지 ─────────────
    # [강점 유지] 컬럼이 테이블 문서 안에 내장 → 한 번의 조회로 컬럼 확인
    # [보완 TYPE-3] 설명 품질 GOOD/POOR/MISSING 혼재
    _recreate("table_meta", {**_SHARD, "mappings": {"properties": {
        "table_name":        {"type": "keyword"},
        "table_description": {"type": "text", "analyzer": "standard"},
        "schema":            {"type": "keyword"},
        "update_cycle":      {"type": "keyword"},
        "columns": {"type": "nested", "properties": {
            "name": {"type": "keyword"},
            "type": {"type": "keyword"},
            "desc": {"type": "text", "analyzer": "standard"},
            "pk":   {"type": "boolean"},
            "pii":  {"type": "boolean"},
            "fk":   {"type": "keyword"},
            # [보완] 컬럼별 코드 참조 추가 — 에이전트 코드 해석 지원
            "code_ref": {"type": "keyword"},
        }},
    }}})

    _bulk("table_meta", [
        # GOOD — 상세 설명
        {"table_name": "TB_ADW_CSC101M",
         "table_description": "고객 기본 정보 테이블. EDPS_CSN 단일 PK. 일배치 갱신. "
                              "CUS_GRD_CD 조회 시 '99'/NULL 등 미정의 코드 존재 가능.",
         "schema": "DW", "update_cycle": "일배치",
         "columns": [
             {"name":"EDPS_CSN",      "type":"VARCHAR(20)",  "desc":"고객번호 (전행 고유 식별자)", "pk":True},
             {"name":"CSM",      "type":"VARCHAR(100)", "desc":"고객명",                    "pii":True},
             {"name":"RGST_DT",       "type":"DATE",         "desc":"등록(가입)일자"},
             {"name":"CUS_DCD", "type":"VARCHAR(2)",   "desc":"고객유형코드 (01:개인, 02:기업, 03:개인사업자)",
              "code_ref":"CUS_DCD"},
             {"name":"BRCD",      "type":"VARCHAR(10)",  "desc":"관리지점코드", "fk":"TB_ADW_COM001M.BRCD"},
             {"name":"GNDR_CD",    "type":"CHAR(1)",      "desc":"성별코드 (M:남성, F:여성)"},
             {"name":"AGE_GR_CD",   "type":"VARCHAR(2)",   "desc":"연령대코드 (20:20대, 30:30대, 40:40대, 50:50대, 60:60대이상)"},
             {"name":"CUS_GRD_CD","type":"VARCHAR(10)",  "desc":"고객등급코드 (01:VIP, 02:골드, 03:실버, 04:일반, 05:신규)",
              "code_ref":"CUS_GRD_CD"},
         ]},
        # GOOD
        {"table_name": "TB_ADW_LNB301M",
         "table_description": "여신(대출) 정보 테이블. 개인/기업 대출 계약 건별 정보. "
                              "LN_DCD '09' 등 레거시 미정의 코드 존재 가능.",
         "schema": "DW", "update_cycle": "일배치",
         "columns": [
             {"name":"LN_NO",      "type":"VARCHAR(20)",   "desc":"대출번호 (대출 계약 고유 식별자)", "pk":True},
             {"name":"EDPS_CSN",      "type":"VARCHAR(20)",   "desc":"고객번호", "fk":"TB_ADW_CSC101M.EDPS_CSN"},
             {"name":"LN_EXC_AMT",     "type":"NUMERIC(18,0)", "desc":"대출금액(원) - 최초 실행 금액"},
             {"name":"LN_BAL_AMT",     "type":"NUMERIC(18,0)", "desc":"대출잔액(원) - 현재 남은 원금"},
             {"name":"LN_DT",      "type":"DATE",          "desc":"대출실행일자"},
             {"name":"MTRTY_DT",     "type":"DATE",          "desc":"만기일자"},
             {"name":"APLY_RT",     "type":"NUMERIC(5,2)",  "desc":"적용금리(%)"},
             {"name":"LN_DCD", "type":"VARCHAR(2)",    "desc":"대출유형코드 (01:신용, 02:담보, 03:보증)",
              "code_ref":"LN_DCD"},
             {"name":"OVDU_YN",   "type":"CHAR(1)",       "desc":"연체여부 (Y:연체, N:정상)"},
             {"name":"OVDU_DY_CN", "type":"INTEGER",       "desc":"연체일수"},
             {"name":"OVDU_AMT",  "type":"NUMERIC(18,0)", "desc":"연체금액(원)"},
         ]},
        # GOOD
        {"table_name": "TB_ADW_DEP201M",
         "table_description": "수신(예금) 정보 테이블. 예적금 계좌별 잔액·상품 정보. "
                              "ACT_STCD '99' 등 미정의 코드 존재 가능.",
         "schema": "DW", "update_cycle": "일배치",
         "columns": [
             {"name":"ACN",        "type":"VARCHAR(20)",   "desc":"계좌번호", "pk":True, "pii":True},
             {"name":"EDPS_CSN",        "type":"VARCHAR(20)",   "desc":"고객번호", "fk":"TB_ADW_CSC101M.EDPS_CSN"},
             {"name":"ACT_BAL_AMT",       "type":"NUMERIC(18,0)", "desc":"계좌잔액(원)"},
             {"name":"OPNG_DT",        "type":"DATE",          "desc":"개설일자"},
             {"name":"PD_CD",        "type":"VARCHAR(10)",   "desc":"상품코드"},
             {"name":"PD_NM",        "type":"VARCHAR(100)",  "desc":"상품명"},
             {"name":"APLY_RT",       "type":"NUMERIC(5,4)",  "desc":"적용금리"},
             {"name":"ACT_STCD", "type":"VARCHAR(2)",    "desc":"계좌상태코드 (01:정상, 02:해지, 03:휴면)",
              "code_ref":"ACT_STCD"},
         ]},
        # GOOD — 대용량 주의사항 명시
        {"table_name": "TB_ADW_TRX701L",
         "table_description": "거래 내역 테이블 (대용량). 입출금·이체 등 모든 거래 기록. "
                              "조회 시 반드시 TR_DT 날짜 조건 포함 필수 (미포함 시 타임아웃). "
                              "TR_DCD '09' 등 미정의 코드 존재 가능.",
         "schema": "DW", "update_cycle": "실시간",
         "columns": [
             {"name":"TR_NO",      "type":"VARCHAR(30)",   "desc":"거래번호 (고유 식별자)", "pk":True},
             {"name":"ACN",     "type":"VARCHAR(20)",   "desc":"계좌번호", "pii":True},
             {"name":"TR_DT",      "type":"DATE",          "desc":"거래일자 (필수 조건 — 미입력 시 전체 스캔)"},
             {"name":"TR_TM",      "type":"VARCHAR(6)",    "desc":"거래시각 (HHMMSS)"},
             {"name":"TR_AMT",     "type":"NUMERIC(18,0)", "desc":"거래금액(원)"},
             {"name":"TR_DCD", "type":"VARCHAR(2)",    "desc":"거래유형코드 (01:입금, 02:출금, 03:이체)",
              "code_ref":"TR_DCD"},
             {"name":"BRCD",     "type":"VARCHAR(10)",   "desc":"거래지점코드", "fk":"TB_ADW_COM001M.BRCD"},
         ]},
        # GOOD
        {"table_name": "TB_ADW_COM001M",
         "table_description": "지점 정보 테이블. 영업점 코드·명칭·지역 정보.",
         "schema": "DW", "update_cycle": "월배치",
         "columns": [
             {"name":"BRCD",   "type":"VARCHAR(10)",  "desc":"지점코드", "pk":True},
             {"name":"BR_NM",   "type":"VARCHAR(100)", "desc":"지점명"},
             {"name":"RGN_CD", "type":"VARCHAR(4)",   "desc":"지역코드", "code_ref":"RGN_CD"},
             {"name":"RGN_NM", "type":"VARCHAR(50)",  "desc":"지역명"},
         ]},
        # POOR — [보완 TYPE-3] 설명이 짧아 집계 테이블임을 알기 어려움
        {"table_name": "TB_ADW_LNR301S",
         "table_description": "여신 연체 통계",   # 의도적 POOR
         "schema": "DW", "update_cycle": "월배치",
         "columns": [
             {"name":"BASE_YM",        "type":"VARCHAR(6)",    "desc":"기준년월 (YYYYMM)"},
             {"name":"BRCD",        "type":"VARCHAR(10)",   "desc":"지점코드", "fk":"TB_ADW_COM001M.BRCD"},
             {"name":"LN_DCD",   "type":"VARCHAR(2)",    "desc":"대출유형"},  # POOR
             {"name":"TOT_LN_CN", "type":"INTEGER",       "desc":"총건수"},    # POOR
             {"name":"TOT_LN_AMT", "type":"NUMERIC(18,0)", "desc":"총금액"},    # POOR
             {"name":"OVDU_CN",    "type":"INTEGER",       "desc":"연체건수"},
             {"name":"OVDU_AMT",    "type":"NUMERIC(18,0)", "desc":"연체금액(원)"},
             {"name":"OVDU_RT",   "type":"NUMERIC(5,2)",  "desc":"연체율(%)"},
         ]},
    ])

    # ── (B) report_sql — 활용사례 검색 인덱스 ──────────────
    # [강점 유지] 기존 10건 → 15건으로 확장, ES 키워드 검색 지원
    _recreate("report_sql", {**_SHARD, "mappings": {"properties": {
        "report_name":  {"type": "text", "analyzer": "standard"},
        "description":  {"type": "text", "analyzer": "standard"},
        "sql":          {"type": "text", "index": False},
        "category":     {"type": "keyword"},
        "tables_used":  {"type": "keyword"},
        # [보완] SQL 패턴 태그 추가 — 패턴별 검색 지원
        "sql_patterns": {"type": "keyword"},
    }}})

    _bulk("report_sql", [
        {"report_name":"월간 신규 고객 현황",
         "description":"월별 신규 등록 고객 수를 고객유형별로 집계한다.",
         "category":"고객","tables_used":["TB_ADW_CSC101M"],"sql_patterns":["GROUP BY","DATE_TRUNC"],
         "sql":"SELECT DATE_TRUNC('month',RGST_DT) AS base_month, CUS_DCD, COUNT(*) AS new_cust_cnt "
               "FROM TB_ADW_CSC101M "
               "WHERE RGST_DT >= DATE_TRUNC('month',CURRENT_DATE) - INTERVAL '12 months' "
               "GROUP BY DATE_TRUNC('month',RGST_DT), CUS_DCD ORDER BY base_month"},
        {"report_name":"대출 실행 현황",
         "description":"기간별 대출 유형별 실행 건수, 금액, 평균 금리를 산출한다.",
         "category":"여신","tables_used":["TB_ADW_LNB301M"],"sql_patterns":["GROUP BY","AVG"],
         "sql":"SELECT LN_DCD, COUNT(*) AS loan_cnt, SUM(LN_EXC_AMT) AS total_amt, AVG(APLY_RT) AS avg_rate "
               "FROM TB_ADW_LNB301M WHERE LN_DT >= DATE_TRUNC('month',CURRENT_DATE) "
               "GROUP BY LN_DCD"},
        {"report_name":"연체율 추이",
         "description":"월별 연체율 추이를 최근 12개월 기준으로 산출한다. 연체율 = 연체금액/총대출금액×100",
         "category":"여신","tables_used":["TB_ADW_LNR301S"],"sql_patterns":["GROUP BY","NULLIF","ROUND"],
         "sql":"SELECT BASE_YM, SUM(OVDU_CN) AS total_overdue, SUM(TOT_LN_CN) AS total_loan, "
               "ROUND(SUM(OVDU_AMT)::NUMERIC / NULLIF(SUM(TOT_LN_AMT),0)*100,2) AS overdue_rate "
               "FROM TB_ADW_LNR301S GROUP BY BASE_YM ORDER BY BASE_YM"},
        {"report_name":"지점별 여신 실적",
         "description":"지점별 대출 건수, 총 대출금액, 평균 금리를 산출한다.",
         "category":"여신","tables_used":["TB_ADW_LNB301M","TB_ADW_CSC101M","TB_ADW_COM001M"],
         "sql_patterns":["JOIN","GROUP BY","ORDER BY DESC"],
         "sql":"SELECT b.BR_NM, COUNT(l.LN_NO) AS loan_cnt, SUM(l.LN_EXC_AMT) AS total_amt, "
               "ROUND(AVG(l.APLY_RT),2) AS avg_rate "
               "FROM TB_ADW_LNB301M l "
               "JOIN TB_ADW_CSC101M c ON l.EDPS_CSN=c.EDPS_CSN "
               "JOIN TB_ADW_COM001M b ON c.BRCD=b.BRCD "
               "GROUP BY b.BR_NM ORDER BY total_amt DESC"},
        {"report_name":"수신 상품별 잔액 현황",
         "description":"예금 상품별 계좌 수, 총 잔액, 가중평균금리를 산출한다.",
         "category":"수신","tables_used":["TB_ADW_DEP201M"],"sql_patterns":["GROUP BY","가중평균"],
         "sql":"SELECT PD_CD, PD_NM, COUNT(*) AS acct_cnt, SUM(ACT_BAL_AMT) AS total_bal, "
               "ROUND(SUM(APLY_RT*ACT_BAL_AMT)/NULLIF(SUM(ACT_BAL_AMT),0),4) AS weighted_avg_rate "
               "FROM TB_ADW_DEP201M WHERE ACT_STCD='01' "
               "GROUP BY PD_CD, PD_NM ORDER BY total_bal DESC"},
        {"report_name":"VIP 고객 여수신 종합",
         "description":"VIP 등급 고객의 대출 잔액과 예금 잔액을 종합 조회한다.",
         "category":"고객","tables_used":["TB_ADW_CSC101M","TB_ADW_LNB301M","TB_ADW_DEP201M"],
         "sql_patterns":["LEFT JOIN","COALESCE","다중조인"],
         "sql":"SELECT c.EDPS_CSN, c.CSM, "
               "COALESCE(SUM(l.LN_BAL_AMT),0) AS loan_total, "
               "COALESCE(SUM(d.ACT_BAL_AMT),0) AS deposit_total "
               "FROM TB_ADW_CSC101M c "
               "LEFT JOIN TB_ADW_LNB301M l ON c.EDPS_CSN=l.EDPS_CSN "
               "LEFT JOIN TB_ADW_DEP201M d ON c.EDPS_CSN=d.EDPS_CSN AND d.ACT_STCD='01' "
               "WHERE c.CUS_GRD_CD='VIP' "
               "GROUP BY c.EDPS_CSN, c.CSM ORDER BY deposit_total DESC LIMIT 50"},
        {"report_name":"연령대별 고객 분포",
         "description":"개인 고객의 연령대별 인원수를 집계한다.",
         "category":"고객","tables_used":["TB_ADW_CSC101M"],"sql_patterns":["GROUP BY","필터"],
         "sql":"SELECT AGE_GR_CD, COUNT(*) AS cust_cnt "
               "FROM TB_ADW_CSC101M WHERE CUS_DCD='01' "
               "GROUP BY AGE_GR_CD ORDER BY AGE_GR_CD"},
        {"report_name":"지점별 연체율 순위",
         "description":"직전월 기준 지점별 연체율을 내림차순으로 조회한다.",
         "category":"여신","tables_used":["TB_ADW_LNR301S","TB_ADW_COM001M"],
         "sql_patterns":["JOIN","RANK","ORDER BY DESC"],
         "sql":"SELECT b.BR_NM, "
               "ROUND(SUM(s.OVDU_AMT)::NUMERIC/NULLIF(SUM(s.TOT_LN_AMT),0)*100,2) AS overdue_rate "
               "FROM TB_ADW_LNR301S s JOIN TB_ADW_COM001M b ON s.BRCD=b.BRCD "
               "WHERE s.BASE_YM=TO_CHAR(CURRENT_DATE-INTERVAL '1 month','YYYYMM') "
               "GROUP BY b.BR_NM ORDER BY overdue_rate DESC"},
        {"report_name":"일별 거래 추이",
         "description":"최근 30일간 일별 거래 건수와 총 거래금액 추이를 산출한다.",
         "category":"거래","tables_used":["TB_ADW_TRX701L"],"sql_patterns":["날짜범위","GROUP BY"],
         "sql":"SELECT TR_DT, COUNT(*) AS txn_cnt, SUM(TR_AMT) AS total_amt "
               "FROM TB_ADW_TRX701L "
               "WHERE TR_DT >= CURRENT_DATE - INTERVAL '30 days' "
               "GROUP BY TR_DT ORDER BY TR_DT"},
        {"report_name":"담보대출 만기 도래 현황",
         "description":"향후 3개월 이내 만기 도래하는 담보대출 건수와 잔액을 지점별로 집계한다.",
         "category":"여신","tables_used":["TB_ADW_LNB301M","TB_ADW_CSC101M","TB_ADW_COM001M"],
         "sql_patterns":["BETWEEN","JOIN","GROUP BY"],
         "sql":"SELECT b.BR_NM, COUNT(*) AS cnt, SUM(l.LN_BAL_AMT) AS total_bal "
               "FROM TB_ADW_LNB301M l "
               "JOIN TB_ADW_CSC101M c ON l.EDPS_CSN=c.EDPS_CSN "
               "JOIN TB_ADW_COM001M b ON c.BRCD=b.BRCD "
               "WHERE l.LN_DCD='02' "
               "AND l.MTRTY_DT BETWEEN CURRENT_DATE AND CURRENT_DATE+INTERVAL '3 months' "
               "GROUP BY b.BR_NM ORDER BY total_bal DESC"},
        # [보완] 추가 5건
        {"report_name":"전월 대비 거래금액 증감",
         "description":"당월과 전월의 거래금액 합계를 비교하여 증감률을 산출한다.",
         "category":"거래","tables_used":["TB_ADW_TRX701L"],"sql_patterns":["CTE","LAG","WINDOW"],
         "sql":"WITH monthly AS ("
               "  SELECT DATE_TRUNC('month',TR_DT) AS mon, SUM(TR_AMT) AS total "
               "  FROM TB_ADW_TRX701L "
               "  WHERE TR_DT >= DATE_TRUNC('month',CURRENT_DATE) - INTERVAL '1 month' "
               "  GROUP BY 1"
               ") "
               "SELECT mon, total, LAG(total) OVER (ORDER BY mon) AS prev_total, "
               "ROUND((total - LAG(total) OVER (ORDER BY mon)) "
               "/ NULLIF(LAG(total) OVER (ORDER BY mon),0)*100, 2) AS pct_change "
               "FROM monthly ORDER BY mon"},
        {"report_name":"잔액 상위 고객 Top 20",
         "description":"예금 잔액 기준 상위 20명 고객 목록.",
         "category":"고객","tables_used":["TB_ADW_CSC101M","TB_ADW_DEP201M"],
         "sql_patterns":["서브쿼리","ORDER BY DESC","LIMIT"],
         "sql":"SELECT c.EDPS_CSN, c.CSM, c.CUS_GRD_CD, SUM(d.ACT_BAL_AMT) AS total_bal "
               "FROM TB_ADW_CSC101M c "
               "JOIN TB_ADW_DEP201M d ON c.EDPS_CSN=d.EDPS_CSN AND d.ACT_STCD='01' "
               "GROUP BY c.EDPS_CSN, c.CSM, c.CUS_GRD_CD "
               "ORDER BY total_bal DESC LIMIT 20"},
        {"report_name":"고객 등급별 평균 대출금액",
         "description":"고객 등급(VIP/Gold/Silver/General)별 평균 대출금액 비교.",
         "category":"여신","tables_used":["TB_ADW_CSC101M","TB_ADW_LNB301M"],
         "sql_patterns":["GROUP BY","AVG","JOIN"],
         "sql":"SELECT c.CUS_GRD_CD, COUNT(l.LN_NO) AS loan_cnt, "
               "ROUND(AVG(l.LN_EXC_AMT)) AS avg_loan_amt "
               "FROM TB_ADW_CSC101M c JOIN TB_ADW_LNB301M l ON c.EDPS_CSN=l.EDPS_CSN "
               "GROUP BY c.CUS_GRD_CD ORDER BY avg_loan_amt DESC"},
        {"report_name":"신규 수신 계좌 월별 추이",
         "description":"최근 12개월 월별 신규 개설 계좌 수와 총 초기 잔액.",
         "category":"수신","tables_used":["TB_ADW_DEP201M"],"sql_patterns":["DATE_TRUNC","GROUP BY"],
         "sql":"SELECT DATE_TRUNC('month',OPNG_DT) AS base_month, "
               "COUNT(*) AS new_acct_cnt, SUM(ACT_BAL_AMT) AS total_bal "
               "FROM TB_ADW_DEP201M "
               "WHERE OPNG_DT >= CURRENT_DATE - INTERVAL '12 months' "
               "GROUP BY DATE_TRUNC('month',OPNG_DT) ORDER BY base_month"},
        # [보완] 미정의 코드 처리 패턴 예시
        {"report_name":"계좌 상태별 잔액 현황 (미정의 코드 포함)",
         "description":"계좌 상태코드별 잔액 집계. 미정의 코드('99' 등)는 '기타'로 처리한다.",
         "category":"수신","tables_used":["TB_ADW_DEP201M"],
         "sql_patterns":["CASE WHEN","GROUP BY","미정의코드처리"],
         "sql":"SELECT "
               "CASE ACT_STCD "
               "  WHEN '01' THEN '정상' "
               "  WHEN '02' THEN '해지' "
               "  WHEN '03' THEN '휴면' "
               "  ELSE '기타(' || ACT_STCD || ')' "
               "END AS status_nm, "
               "COUNT(*) AS acct_cnt, SUM(ACT_BAL_AMT) AS total_bal "
               "FROM TB_ADW_DEP201M "
               "GROUP BY ACT_STCD ORDER BY total_bal DESC"},
    ])

    # ── (C) code_meta ───────────────────────────────────────
    # [보완 TYPE-2] 공식 코드만 정의 — 실데이터와 불일치 의도적 유지
    _recreate("code_meta", {**_SHARD, "mappings": {"properties": {
        "code_field":      {"type": "keyword"},
        "code_field_desc": {"type": "text", "analyzer": "standard"},
        "table_name":      {"type": "keyword"},
        "codes":           {"type": "object", "enabled": False},
    }}})

    _bulk("code_meta", [
        {"code_field":"CUS_DCD","code_field_desc":"고객유형코드","table_name":"TB_ADW_CSC101M",
         "codes":{"01":"개인","02":"기업","03":"개인사업자"}},
        # [TYPE-2] '99'/NULL 누락 — 실데이터에는 존재
        {"code_field":"CUS_GRD_CD","code_field_desc":"고객등급코드","table_name":"TB_ADW_CSC101M",
         "codes":{"01":"VIP등급","02":"골드등급","03":"실버등급","04":"일반등급","05":"신규등급"}},
        {"code_field":"GNDR_CD","code_field_desc":"성별코드","table_name":"TB_ADW_CSC101M",
         "codes":{"M":"남성","F":"여성"}},
        {"code_field":"AGE_GR_CD","code_field_desc":"연령대코드","table_name":"TB_ADW_CSC101M",
         "codes":{"20":"20대","30":"30대","40":"40대","50":"50대","60":"60대이상"}},
        # [TYPE-2] '09' 누락
        {"code_field":"LN_DCD","code_field_desc":"대출유형코드","table_name":"TB_ADW_LNB301M",
         "codes":{"01":"신용대출","02":"담보대출","03":"보증대출"}},
        {"code_field":"OVDU_YN","code_field_desc":"연체여부","table_name":"TB_ADW_LNB301M",
         "codes":{"Y":"연체","N":"정상"}},
        # [TYPE-2] '09' 누락
        {"code_field":"TR_DCD","code_field_desc":"거래유형코드","table_name":"TB_ADW_TRX701L",
         "codes":{"01":"입금","02":"출금","03":"이체"}},
        # [TYPE-2] '99' 누락
        {"code_field":"ACT_STCD","code_field_desc":"계좌상태코드","table_name":"TB_ADW_DEP201M",
         "codes":{"01":"정상","02":"해지","03":"휴면"}},
        {"code_field":"RGN_CD","code_field_desc":"지역코드","table_name":"TB_ADW_COM001M",
         "codes":{"01":"서울","02":"경기","03":"인천","04":"대전","05":"대구",
                  "06":"부산","07":"광주","08":"울산","09":"제주",
                  "10":"충북","11":"전북","12":"경남"}},
    ])

    # ── (D) term_dict — 용어사전 ────────────────────────────
    # [보완 +a] 에이전트가 자연어 → 컬럼명 매핑 시 참조
    _recreate("term_dict", {**_SHARD, "mappings": {"properties": {
        "term_ko":     {"type": "text", "analyzer": "standard",
                        "fields": {"keyword": {"type": "keyword"}}},
        "col_pattern": {"type": "keyword"},
        "table_hint":  {"type": "keyword"},
        "definition":  {"type": "text"},
        "synonyms":    {"type": "text"},
        "caution":     {"type": "text"},
    }}})

    _bulk("term_dict", [
        {"term_ko":"고객번호","col_pattern":"EDPS_CSN","table_hint":"TB_ADW_CSC101M",
         "definition":"전행 고객 고유 식별번호. 모든 고객 관련 조인의 기준 키.",
         "synonyms":"고객ID, 고객식별번호","caution":None},
        {"term_ko":"고객등급","col_pattern":"CUS_GRD_CD","table_hint":"TB_ADW_CSC101M",
         "definition":"고객 분류 등급. 01:VIP/02:골드/03:실버/04:일반/05:신규.",
         "synonyms":"VIP, 우수고객, 등급",
         "caution":"'99'/NULL 등 미정의 코드 실데이터 존재 가능. 집계 시 COALESCE 처리 권장."},
        {"term_ko":"신규고객","col_pattern":"RGST_DT","table_hint":"TB_ADW_CSC101M",
         "definition":"최초 등록일(RGST_DT) 기준 신규 가입 고객.",
         "synonyms":"가입일, 등록일","caution":None},
        {"term_ko":"잔액","col_pattern":"ACT_BAL_AMT,LN_BAL_AMT","table_hint":"TB_ADW_DEP201M,TB_ADW_LNB301M",
         "definition":"계좌 또는 대출의 현재 잔액(원).",
         "synonyms":"현재잔액, 예금잔액, 대출잔액","caution":None},
        {"term_ko":"연체","col_pattern":"OVDU_YN,OVDU_DY_CN,OVDU_AMT",
         "table_hint":"TB_ADW_LNB301M",
         "definition":"대출 원리금 미상환 상태.",
         "synonyms":"연체율, 연체건수, 부실",
         "caution":"OVDU_YN='Y' 조건으로 조회. 연체율은 TB_ADW_LNR301S 집계 테이블 사용 권장."},
        {"term_ko":"거래내역","col_pattern":"TR_DT,TR_AMT","table_hint":"TB_ADW_TRX701L",
         "definition":"입출금·이체 등 모든 거래 기록.",
         "synonyms":"거래이력, 입출금내역",
         "caution":"대용량 테이블. TR_DT 날짜 조건 필수. 미포함 시 전체 스캔 타임아웃."},
        {"term_ko":"지점","col_pattern":"BRCD,BR_NM","table_hint":"TB_ADW_COM001M",
         "definition":"영업점 단위 지점.",
         "synonyms":"지점명, 영업점, 지점코드","caution":None},
        {"term_ko":"이번달","col_pattern":"RGST_DT,LN_DT,TR_DT,OPNG_DT",
         "table_hint":None,
         "definition":"현재 년월 기준 1일~말일.",
         "synonyms":"당월, 이달, 금월",
         "caution":"DATE_TRUNC('month', CURRENT_DATE) 사용 권장."},
    ])

    print("  → ES 완료\n")


# ══════════════════════════════════════════════════════════════
# 4. Qdrant 시딩
# ══════════════════════════════════════════════════════════════

def seed_qdrant() -> None:
    from fastembed import TextEmbedding
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, PointStruct, VectorParams

    print(f"  임베딩 모델 로딩: {EMBEDDING_MODEL} (최초 실행 시 ~130MB 다운로드)")
    embedder = TextEmbedding(model_name=EMBEDDING_MODEL)
    client   = QdrantClient(url=QDRANT_URL)

    def _recreate_col(name: str) -> None:
        if client.collection_exists(name):
            client.delete_collection(name)
        client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
        )

    def _upsert(col: str, docs: list[dict], text_key: str) -> None:
        texts  = [d[text_key] for d in docs]
        embeds = list(embedder.embed(texts))
        points = [
            PointStruct(id=i, vector=e.tolist(), payload=d)
            for i, (d, e) in enumerate(zip(docs, embeds))
        ]
        client.upsert(collection_name=col, points=points)
        print(f"  {col:<20}: {len(points):>3}건")

    # ── (A) biz_manual — 기존 15건 유지 ────────────────────
    _recreate_col("biz_manual")
    BIZ_MANUAL = [
        {"title":"여신 심사 절차","category":"여신",
         "content":"여신 심사 절차: 1.대출 신청 접수 2.신용 평가(CSS 점수) 3.담보 평가 "
                   "4.심사역 심사 5.승인/반려 결정 6.대출 실행. "
                   "신용대출은 CSS 600점 이상, 담보대출은 LTV 70% 이하가 기본 조건."},
        {"title":"연체 관리 기준","category":"여신",
         "content":"연체 분류: 1~29일 단기연체, 30~89일 장기연체, 90일 이상 부실채권. "
                   "연체율 = 연체금액 / 총 대출금액 × 100. "
                   "연체 발생 시 SMS→전화→내용증명→법적 조치 순서로 관리."},
        {"title":"수신 상품 안내","category":"수신",
         "content":"주요 수신 상품: 보통예금(입출금 자유), 정기예금(만기 고정금리), "
                   "정기적금(매월 적립), MMF(단기 금융상품). "
                   "예금자보호법상 1인당 5,000만원까지 보호."},
        {"title":"BIS 비율 산출","category":"경영지표",
         "content":"BIS비율 = 자기자본 / 위험가중자산 × 100. "
                   "자기자본 = 기본자본(Tier1) + 보완자본(Tier2). "
                   "은행업감독규정 최소 BIS비율 8% 이상. 바젤III 보통주자본비율 4.5% 이상."},
        {"title":"고객 등급 분류 체계","category":"고객관리",
         "content":"VIP: 총자산 10억 이상 또는 월거래 1억 이상. "
                   "Gold: 총자산 3억 이상 또는 월거래 3천만 이상. "
                   "Silver: 총자산 1억 이상. General: 기타. "
                   "등급별 우대금리·수수료 면제 혜택 제공."},
        {"title":"대출 금리 산정 기준","category":"여신",
         "content":"적용금리 = 기준금리 + 가산금리 - 우대금리. "
                   "기준금리: COFIX 또는 금융채 6개월물. "
                   "우대금리: 급여이체·카드실적·적금 가입 조건 충족 시. "
                   "변동금리: 3/6개월 단위 변동. 고정금리: 만기까지 고정."},
        {"title":"NIM(순이자마진) 산출","category":"경영지표",
         "content":"NIM = (이자수익 - 이자비용) / 운용자산 평균잔액 × 100. "
                   "이자수익: 대출이자+유가증권이자+예치금이자. "
                   "국내 은행 평균 NIM 약 1.5~2.0%."},
        {"title":"LCR(유동성커버리지비율) 산출","category":"경영지표",
         "content":"LCR = 고유동성자산 / 향후 30일간 순현금유출액 × 100. "
                   "바젤III 최소 LCR 100% 이상. 고유동성자산: 현금·국채 등."},
        {"title":"여신 한도 관리","category":"여신",
         "content":"동일인 여신한도: 자기자본의 20% 이내. "
                   "동일차주 여신한도: 자기자본의 25% 이내. "
                   "CSS 점수 기반 개인 신용한도 자동 산출."},
        {"title":"외환 거래 절차","category":"외환",
         "content":"전신환 매매·여행자수표·외화현찰 매매 가능. "
                   "건당 USD 1만 초과 시 한국은행 보고. "
                   "연간 USD 5만 초과 시 지정거래외국환은행 확인."},
        {"title":"자금세탁방지(AML) 업무","category":"준법감시",
         "content":"CDD(고객확인): 신규 거래 시 신원 확인. "
                   "STR(의심거래보고): 금융정보분석원 보고. "
                   "CTR(고액현금거래): 1일 1천만원 이상 현금거래 보고."},
        {"title":"퇴직연금 업무","category":"수신",
         "content":"DB형: 회사 운용, 확정 급여. DC형: 근로자 운용, 성과 연동. "
                   "IRP: 이직·퇴직 시 퇴직금 수령 계좌. "
                   "IRP 납입액 연 900만원까지 세액공제(13.2~16.5%)."},
        {"title":"전자금융 사고 대응","category":"전자금융",
         "content":"피싱·파밍·스미싱·메모리해킹 대응. "
                   "사고 인지 즉시 피해 계좌 지급정지 → 경찰 신고 → 금감원 보고."},
        {"title":"신용등급 체계","category":"여신",
         "content":"NICE/KCB 1~1000점(10등급). 1~2등급: 최우량. 9~10등급: 위험(대출 제한). "
                   "CSS: 은행 자체 내부 신용평가. 연소득·재직기간·기존대출건수·연체이력 반영."},
        {"title":"지점 성과 평가 기준","category":"경영관리",
         "content":"KPI: 수신 실적(예금 순증액·신규계좌), 여신 실적(대출 실행액), "
                   "수익성(NIM), 건전성(연체율), 고객관리(신규고객·VIP 유지율). "
                   "평가: 월별 점검, 분기별 종합. 등급: S/A/B/C/D."},
    ]
    _upsert("biz_manual", BIZ_MANUAL, "content")

    # ── (B) sql_history — [보완 +a] 신규 컬렉션 추가 ────────
    # 자연어 요건 ↔ SQL 매핑 + 재질문/실패 케이스 포함
    _recreate_col("sql_history")
    SQL_HISTORY = [
        {"nl_query":"이번 달 신규 고객 수","intent":"신규 고객 집계","exec_status":"SUCCESS",
         "sql":"SELECT COUNT(*) AS new_cust_cnt FROM TB_ADW_CSC101M "
               "WHERE RGST_DT >= DATE_TRUNC('month', CURRENT_DATE)",
         "pattern":"COUNT + DATE_TRUNC","caution":None},
        {"nl_query":"지점별 여신 잔액 TOP 10","intent":"지점별 여신집계","exec_status":"SUCCESS",
         "sql":"SELECT b.BR_NM, SUM(l.LN_BAL_AMT) AS tot FROM TB_ADW_LNB301M l "
               "JOIN TB_ADW_CSC101M c ON l.EDPS_CSN=c.EDPS_CSN "
               "JOIN TB_ADW_COM001M b ON c.BRCD=b.BRCD "
               "GROUP BY b.BR_NM ORDER BY tot DESC LIMIT 10",
         "pattern":"GROUP BY + JOIN + LIMIT","caution":None},
        {"nl_query":"연체 중인 고객 목록","intent":"연체 고객 조회","exec_status":"SUCCESS",
         "sql":"SELECT c.EDPS_CSN, c.CSM, l.OVDU_DY_CN, l.OVDU_AMT "
               "FROM TB_ADW_LNB301M l JOIN TB_ADW_CSC101M c ON l.EDPS_CSN=c.EDPS_CSN "
               "WHERE l.OVDU_YN='Y' ORDER BY l.OVDU_DY_CN DESC",
         "pattern":"JOIN + WHERE 필터","caution":None},
        {"nl_query":"VIP 고객 보유 상품 현황","intent":"VIP 포트폴리오","exec_status":"SUCCESS",
         "sql":"SELECT c.EDPS_CSN, c.CSM, "
               "COUNT(DISTINCT d.ACN) AS acct_cnt, SUM(d.ACT_BAL_AMT) AS deposit_tot, "
               "COUNT(DISTINCT l.LN_NO) AS loan_cnt, SUM(l.LN_BAL_AMT) AS loan_tot "
               "FROM TB_ADW_CSC101M c "
               "LEFT JOIN TB_ADW_DEP201M d ON c.EDPS_CSN=d.EDPS_CSN AND d.ACT_STCD='01' "
               "LEFT JOIN TB_ADW_LNB301M l ON c.EDPS_CSN=l.EDPS_CSN "
               "WHERE c.CUS_GRD_CD='VIP' "
               "GROUP BY c.EDPS_CSN, c.CSM",
         "pattern":"다중 LEFT JOIN + GROUP BY","caution":"CUS_GRD_CD '99'/NULL 등 미정의 코드 존재 가능"},
        {"nl_query":"전월 대비 거래금액 증감","intent":"거래 전월비","exec_status":"SUCCESS",
         "sql":"WITH m AS (SELECT DATE_TRUNC('month',TR_DT) AS mon, SUM(TR_AMT) AS v "
               "FROM TB_ADW_TRX701L "
               "WHERE TR_DT >= DATE_TRUNC('month',CURRENT_DATE) - INTERVAL '1 month' "
               "GROUP BY 1) "
               "SELECT mon, v, LAG(v) OVER (ORDER BY mon) AS prev, "
               "v - LAG(v) OVER (ORDER BY mon) AS diff FROM m",
         "pattern":"CTE + LAG()","caution":"TR_DT 조건 반드시 포함"},
        {"nl_query":"평균 대출잔액보다 높은 고객","intent":"서브쿼리 집계","exec_status":"SUCCESS",
         "sql":"SELECT c.EDPS_CSN, c.CSM, SUM(l.LN_BAL_AMT) AS tot "
               "FROM TB_ADW_CSC101M c JOIN TB_ADW_LNB301M l ON c.EDPS_CSN=l.EDPS_CSN "
               "GROUP BY c.EDPS_CSN, c.CSM "
               "HAVING SUM(l.LN_BAL_AMT) > (SELECT AVG(LN_BAL_AMT) FROM TB_ADW_LNB301M)",
         "pattern":"HAVING + 스칼라 서브쿼리","caution":None},
        {"nl_query":"거래 10건 이상 활성 계좌","intent":"HAVING 필터","exec_status":"SUCCESS",
         "sql":"SELECT ACN, COUNT(*) AS cnt FROM TB_ADW_TRX701L "
               "WHERE TR_DT >= DATE_TRUNC('month', CURRENT_DATE) "
               "GROUP BY ACN HAVING COUNT(*) >= 10 ORDER BY cnt DESC",
         "pattern":"GROUP BY + HAVING","caution":"TR_DT 조건 반드시 포함"},
        {"nl_query":"계좌 상태별 잔액 집계","intent":"미정의 코드 포함 집계","exec_status":"SUCCESS",
         "sql":"SELECT CASE ACT_STCD WHEN '01' THEN '정상' WHEN '02' THEN '해지' "
               "WHEN '03' THEN '휴면' ELSE '기타('||ACT_STCD||')' END AS status_nm, "
               "COUNT(*) AS cnt, SUM(ACT_BAL_AMT) AS total "
               "FROM TB_ADW_DEP201M GROUP BY ACT_STCD ORDER BY total DESC",
         "pattern":"CASE WHEN (미정의 코드 처리)","caution":"ACT_STCD '99' 등 미정의 코드 존재"},
        # 재질문 케이스 — [보완 +a]
        {"nl_query":"최근 거래 내역 보여줘","intent":"CLARIFICATION_NEEDED","exec_status":"CLARIFY",
         "sql":None,
         "pattern":"CLARIFICATION_NEEDED",
         "caution":"기간 미명시. TB_ADW_TRX701L은 대용량 — TR_DT 조건 없이 조회 불가."},
        {"nl_query":"고객 잔액 알려줘","intent":"CLARIFICATION_NEEDED","exec_status":"CLARIFY",
         "sql":None,
         "pattern":"CLARIFICATION_NEEDED",
         "caution":"특정 고객 미명시. '어떤 고객의 잔액인지' 확인 필요."},
    ]
    _upsert("sql_history", SQL_HISTORY, "nl_query")

    print("  → Qdrant 완료\n")


# ══════════════════════════════════════════════════════════════
# 5. 메인
# ══════════════════════════════════════════════════════════════

def main() -> None:
    print("=" * 60)
    print(" 은행 AI 에이전트 — 테스트 데이터 통합 시딩")
    print(f" 임베딩 모델 : {EMBEDDING_MODEL}")
    print(f" 불완전성 비율: {IMPERFECTION_RATE*100:.0f}%")
    print("=" * 60)

    results: dict[str, str] = {}

    print("\n[1/3] PostgreSQL")
    try:
        seed_postgres()
        results["PostgreSQL"] = "✅ 완료"
    except Exception as e:
        print(f"  ❌ {e}")
        results["PostgreSQL"] = f"❌ {e}"

    print("[2/3] ElasticSearch")
    try:
        seed_elasticsearch()
        results["ElasticSearch"] = "✅ 완료"
    except Exception as e:
        print(f"  ❌ {e}")
        results["ElasticSearch"] = f"❌ {e}"

    print("[3/3] Qdrant")
    try:
        seed_qdrant()
        results["Qdrant"] = "✅ 완료"
    except Exception as e:
        print(f"  ❌ {e}")
        results["Qdrant"] = f"❌ {e}"

    print("=" * 60)
    print(" 결과 요약")
    for k, v in results.items():
        print(f"  {k:<15} {v}")
    print("=" * 60)

    if any("❌" in v for v in results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
