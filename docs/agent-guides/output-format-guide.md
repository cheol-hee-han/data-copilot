# 결과 포맷팅 가이드

## 컬럼명 비즈니스 용어 변환 매핑

```python
COLUMN_NAME_MAPPING = {
    # 고객 관련
    "customer_id": "고객ID", "customer_name": "고객명",
    "customer_status": "고객상태", "phone": "연락처",
    "email": "이메일", "created_at": "가입일",
    "marketing_agree": "마케팅동의",
    # 주문 관련
    "order_id": "주문번호", "total_amount": "주문금액",
    "order_count": "주문횟수", "order_status": "주문상태",
    # 분석 관련
    "conversion_rate": "전환율(%)", "rank": "순위",
    "cumulative_amount": "누적구매금액",
}

ORDER_STATUS_KO = {
    "PENDING": "결제대기", "CONFIRMED": "주문확인",
    "SHIPPED": "배송중", "DELIVERED": "배송완료", "CANCELLED": "취소",
}

CUSTOMER_STATUS_KO = {
    "ACTIVE": "활성", "PROSPECT": "가망고객",
    "INACTIVE": "휴면", "WITHDRAWN": "탈퇴",
}
```

## 데이터 값 포맷 변환 규칙

| 유형 | 컬럼 키워드 | 변환 규칙 | 예시 |
|------|-----------|----------|------|
| 금액 | 금액, amount, price, cost | `f"{int(value):,}원"` | 1000000 → "1,000,000원" |
| 비율 | 율, rate, ratio, percent | `f"{float(value):.1f}%"` | 0.854 → "85.4%" |
| 날짜 | datetime 타입 | `strftime("%Y-%m-%d")` | 2024-03-15T09:00:00 → "2024-03-15" |
| Boolean | 마케팅, agree, consent | "동의" / "미동의" | True → "동의" |
| 상태코드 | status, 상태 | 한글 매핑 테이블 적용 | "ACTIVE" → "활성" |
| None | - | "-" 표시 | None → "-" |

## 자연어 요약 생성

결과 요약 시 핵심 통계만 추출하여 LLM에 전달 (토큰 절약):
- 총 건수
- 날짜 컬럼: 최신/최고 날짜
- 금액 컬럼: 합계/평균

예시 출력:
> "이번 달 마케팅 동의 가망고객은 총 142명입니다.
> 가장 최근 가입자는 홍길동(2024-03-15)이며,
> 서울 지역 고객이 58명(40.8%)으로 가장 많습니다."

## 엑셀(xlsx) 출력 스타일
- 헤더: 진한 파랑 배경(#366092) + 흰색 볼드 글씨
- 홀짝 행: 연한 파랑(#EBF1F5) 교차 배경
- 컬럼 너비: 한글 컬럼명 기준 자동 조정
- UTF-8 BOM CSV: Excel 한글 호환

## 전체 응답 객체 구조

```python
@dataclass
class FormattedResponse:
    summary: str                  # 자연어 요약 (2~3문장)
    sql: str                      # 생성된 SQL (개발자용)
    explanation: str              # SQL 설명 (비기술적)
    columns: list                 # 한글화된 컬럼명
    data: list                    # 포맷된 데이터
    row_count: int
    excel_bytes: Optional[bytes]  # 엑셀 파일
    csv_text: Optional[str]       # CSV 텍스트
    execution_time_ms: int
    confidence: float
```
