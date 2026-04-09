# 리뷰 이슈 오탐 검증 보고서

- 일시: 2026-04-06
- 목적: 기존 리뷰에서 제기된 8건의 이슈에 대한 실제 코드 기반 검증 (오탐 식별)

---

## 검증 결과 요약

| ID | 이슈 요약 | 판정 | 비고 |
|----|-----------|------|------|
| PRM-01 | analyzer_viz_svg_system.txt ━━━ 패턴 6개소 | **오탐** | 의도적 구분선, 실제 12개소 |
| PRM-02 | query_normalizer_phase1_system.txt □/■/━━━ 6개소 | **부분 확인** | ■ 4개소 + ━ 2개소. □ 없음 |
| PRM-03 | analyzer_viz_judgment_system.txt □ 체크박스 3개소 | **확인** | 라인 70-72 |
| PRM-04 | query_normalizer_phase2_system.txt ■ 블릿 2개소 | **확인** | 라인 80, 93 |
| SEC-09 | sanitizeHTML에서 style 태그 미제거 | **확인** | 보안 이슈 유효 |
| SEC-10 | sanitizeSVG에서 use href 외부 리소스 로드 가능 | **확인** | 보안 이슈 유효 |
| W-SEC-05 | UNION SELECT 전면 차단이 정상 쿼리도 차단 | **확인 (경미)** | 의도적 보안 정책 |
| W-PRM-01 | intent_classifier Few-shot ambiguities 필드 불일치 | **확인** | 스키마 vs 예시 불일치 |

---

## 상세 검증

### PRM-01: analyzer_viz_svg_system.txt ━━━ 패턴 -- 오탐

**판정: 오탐 (False Positive)**

이슈: "━━━[...]━━━ 패턴 6개소 미변환"

실제 확인 결과:
- ━━━ 구분선이 **12개소** 존재 (라인 4, 6, 16, 18, 29, 31, 46, 48, 65, 67, 98, 100)
- `[절대 규칙]`, `[레이아웃 시스템]`, `[색상 시스템]`, `[스케일링 규칙]`, `[지원하는 시각화 유형]`, `[스타일 세부 규칙]` 등 섹션 구분자로 의도적으로 사용됨
- "미변환"이라고 할 대상이 아님. 프롬프트의 구조적 구분 요소임
- 폐쇄망 LLM 배포 시 유니코드 인식 이슈로 ASCII 문자 통일은 별도 개선사항으로 관리 가능

**근거**: `resources/prompts/present/analyzer_viz_svg_system.txt` 라인 4, 6, 16, 18, 29, 31, 46, 48, 65, 67, 98, 100

---

### PRM-02: query_normalizer_phase1_system.txt □/■/━━━ -- 부분 확인

**판정: 부분 확인 (Partially Confirmed)**

이슈: "□, ■, ━━━ 6개소 잔존"

실제 확인 결과:
- **□ (체크박스): 0개소** -- 해당 파일에 □ 문자 없음. 오탐
- **■ (블릿): 4개소** -- Few-shot 예제 제목 구분자
  - 라인 270: `■ 예제 1: 단순 조회 (EXTRACT)`
  - 라인 320: `■ 예제 2: 집계 + 순위 (AGGREGATE + RANK)`
  - 라인 373: `■ 예제 3: 비교 + 시계열 (COMPARE)`
  - 라인 428: `■ 예제 4: 용어 모호`
- **━━━ (구분선): 2개소** -- 라인 485, 487 (`[필수 준수사항]` 섹션)

총 6개소라는 개수는 맞으나, □는 없고 ■ 4개소 + ━━━ 2개소로 내역이 다름.

**근거**: `resources/prompts/interpret/query_normalizer_phase1_system.txt`

---

### PRM-03: analyzer_viz_judgment_system.txt □ 체크박스 3개소 -- 확인

**판정: 확인 (Confirmed)**

라인 70-72에 체크박스 □ 문자 3개소:

```
70:    □ 각 행에 고유 식별자가 있음 (이름, ID, 번호 등)
71:    □ 컬럼이 3개 이상이며 서로 다른 속성을 나열함
72:    □ 행 간에 크기 비교 추세 비율 관계가 성립하지 않음
```

N4 규칙의 판별 체크리스트에서 사용됨. `-` 블릿으로 대체 가능.

**근거**: `resources/prompts/present/analyzer_viz_judgment_system.txt` 라인 70-72

---

### PRM-04: query_normalizer_phase2_system.txt ■ 블릿 2개소 -- 확인

**판정: 확인 (Confirmed)**

라인 80, 93에 ■ 블릿 2개소:

```
80: ■ R1 위반: GROUP이 있는데 agg_function이 NONE
93: ■ R2 위반: COMPARE인데 compare_period 누락
```

교차 검증 수정 예시의 제목 구분자. `###` 또는 `---` 등 ASCII 문법으로 대체 권장.

**근거**: `resources/prompts/interpret/query_normalizer_phase2_system.txt` 라인 80, 93

---

### SEC-09: sanitizeHTML에서 style 태그 미제거 -- 확인

**판정: 확인 (Confirmed) -- 보안 이슈**

`static/embedded.html` 라인 2040-2048:

```javascript
function sanitizeHTML(html){
  var doc=new DOMParser().parseFromString(html,'text/html');
  doc.querySelectorAll('script,iframe,embed,object,link').forEach(function(e){e.remove();});
  ...
  return doc.body.innerHTML;
}
```

제거 대상: `script, iframe, embed, object, link` -- **`style` 태그 누락**

공격 벡터:
- CSS `background-image: url()` 통한 데이터 유출
- CSS `@import` 통한 외부 리소스 로드
- CSS 기반 키로깅 (`input[value^="a"] { background: url(...) }`)

**권장**: 제거 목록에 `style` 추가

---

### SEC-10: sanitizeSVG에서 use href 외부 리소스 로드 -- 확인

**판정: 확인 (Confirmed) -- 보안 이슈**

`static/embedded.html` 라인 2028-2038:

```javascript
['href','xlink:href'].forEach(function(attr){
  var v=el.getAttribute(attr);
  if(v&&v.trim().toLowerCase().startsWith('javascript:'))el.removeAttribute(attr);
});
```

`javascript:` 프로토콜만 차단. `<use href="http://...">` 또는 `<image href="http://...">`로 외부 리소스 로드 가능.

공격 벡터:
- 외부 서버로 네트워크 요청 발생 (정보 유출)
- 외부 SVG 내 악성 콘텐츠 로드

현대 브라우저의 cross-origin 제한으로 실제 익스플로잇 가능성은 중간 수준.

**권장**: href/xlink:href에서 `#`으로 시작하는 내부 참조만 허용, `http://`, `https://`, `data:` 등 외부 참조 제거

---

### W-SEC-05: UNION SELECT 전면 차단 -- 확인 (경미)

**판정: 확인 (Confirmed) -- 영향도 낮음, 의도적 보안 정책**

`src/utils/security.py` 라인 154-157:

```python
(
    r"\bUNION\s+(?:ALL\s+)?SELECT\b",
    "UNION SELECT는 허용되지 않습니다",
),
```

UNION SELECT 및 UNION ALL SELECT를 전면 차단.

이슈 자체는 유효하나 (정상 UNION 쿼리도 차단됨), 이 프로젝트 맥락에서:
- NL-to-SQL에서 UNION은 SQL 인젝션의 대표적 공격 벡터
- LLM이 생성하는 SQL에서 UNION 대신 CTE/서브쿼리로 대체 가능
- sql_generator 프롬프트에서 UNION 미사용을 유도하면 충돌 없음

**결론**: 의도적 보안 정책으로 현재 단계에서는 적절. 향후 필요시 화이트리스트 도입 검토.

---

### W-PRM-01: intent_classifier Few-shot ambiguities 필드 불일치 -- 확인

**판정: 확인 (Confirmed)**

`resources/prompts/interpret/intent_classifier_system.txt`

**스키마 정의** (라인 98):
> "ambiguities": "UNSURE 또는 AMBIGUOUS일 때만 작성. 그 외에는 빈 배열 []."

**작성 규칙** (라인 109):
> 그 외에는 빈 배열 []을 출력하세요.

**Few-shot 예시의 불일치**:
- CONTINUE/NEW 예시 (라인 143-428): `ambiguities` 키 자체가 JSON에 포함되지 않음
- AMBIGUOUS/UNSURE 예시 (라인 386-490): `ambiguities` 필드 정상 포함

LLM은 Few-shot 예시를 강하게 모방하므로, CONTINUE/NEW일 때 ambiguities 필드가 누락될 가능성 높음.

**영향도**: 하류 코드에서 `result["ambiguities"]`로 직접 접근 시 KeyError 발생 가능. `result.get("ambiguities", [])` 패턴이면 안전하나, 스키마와 예시의 일관성 확보 필요.

**권장**: Few-shot 예시의 CONTINUE/NEW 케이스에 `"ambiguities": []` 명시 추가

---

## 종합

| 판정 | 건수 | 이슈 ID |
|------|------|---------|
| 확인 (Confirmed) | 5건 | PRM-03, PRM-04, SEC-09, SEC-10, W-PRM-01 |
| 부분 확인 | 1건 | PRM-02 (개수 일치, 내역 상이: □ 없음) |
| 확인 (경미) | 1건 | W-SEC-05 (의도적 보안 정책) |
| 오탐 (False Positive) | 1건 | PRM-01 (의도적 섹션 구분선) |

**오탐률: 1/8 (12.5%)**

우선 수정 대상:
1. SEC-09, SEC-10 (보안) -- 즉시 수정 권장
2. W-PRM-01 (런타임 안정성) -- Few-shot 예시 보정
3. PRM-02~04 (프롬프트 품질) -- 폐쇄망 배포 시 ASCII 통일 작업에서 일괄 처리
