# embedded.html UI 변경사항 테스트 리포트

**날짜**: 2026-03-30
**대상**: `static/embedded.html` — Insight 버튼, content-copy 통합, CSV 복사, 시각화 복사
**리뷰어**: code-reviewer agent

---

## A. 기능 이슈 (Functional Issues)

### A-1. Insight 버튼 show/hide

| ID | 등급 | 설명 |
|----|------|------|
| F-01 | WARNING | **`msg.insight`가 null/undefined일 때 버튼이 영구 hidden으로 남는 문제**. Line 737에서 `style="display:none"`으로 시작하고, Line 791의 `if(msg.insight)` 분기에서만 `ib.style.display=''`을 설정한다. insight가 나중에 별도 이벤트(`stream:end`의 `data.insight`)로 도착하면 정상 작동하지만, **legacy response 경로**(Line 1215)에서 `data.insight`가 없으면 버튼이 계속 숨겨진 채로 남아 문제는 없다. 다만 insight가 비어있는 객체 `{}`로 올 경우에도 truthy이므로 버튼이 보이지만 패널 내용은 비게 된다. |
| F-02 | WARNING | **빈 `query_interpretation` 객체 시 빈 섹션 렌더링**. Line 966-972에서 `qi`가 truthy(`{}`)이지만 `period`, `target`, `metric`이 모두 없으면 `<div class="insight-section">` 안에 제목만 있고 내용이 없는 빈 섹션이 생성된다. |
| F-03 | INFO | **insight 버튼 토글 상태가 다른 메시지와 독립적**. 각 message-row 내부의 `insight-slot`에 대해 개별 토글하므로 정상. 단, 동일 메시지에서 insight 패널을 열고 닫을 때 `open` 클래스만 토글하며 `max-height:3000px` 트랜지션이 작동한다 -- 내용이 매우 길 경우 3000px를 초과할 가능성은 낮지만 이론적으로 존재한다. |

**코드 경로 추적**:
```
ensureDOM() → line 737: display:none
render() → line 791: if(msg.insight) → ib.style.display=''
                                     → renderInsight(row, msg)
handleStream() end → line 1245: m2.insight=data.insight → RD.render(m2)
handleLegacy() → line 1215: msg.insight=data.insight → RD.render(msg)
```

---

### A-2. Markdown 테이블 CSV 복사

| ID | 등급 | 설명 |
|----|------|------|
| F-04 | WARNING | **merged cell(colspan/rowspan) 미처리**. `_tableToCSV()` (Line 1124-1135)는 `tr > th,td`만 순회하므로 `colspan`이 있는 셀은 하나의 값으로만 추출되어 CSV 열 수가 행마다 달라진다. Markdown 렌더링에서는 colspan이 거의 발생하지 않으나, `viz.html`로 전달되는 rich HTML 테이블에서는 발생 가능. |
| F-05 | INFO | **빈 테이블 처리**. `<table></table>` (행 없음)의 경우 `rows` 배열이 비어 빈 문자열이 클립보드에 복사된다. 기능적으로는 에러가 아니지만 사용자에게 "복사됨"이라고 표시되면서 빈 내용이 복사되는 UX 문제가 있다. |
| F-06 | INFO | **특수문자 처리 확인**. Line 1129에서 `replace(/"/g,'""')`로 큰따옴표 이스케이프 처리하고 전체를 `""`로 감싼다. 쉼표, 줄바꿈, 큰따옴표 모두 RFC 4180 기준 올바르게 처리된다. 다만 셀 내부에 `\n`이 포함된 경우 CSV 행이 깨질 수 있다 -- `textContent.trim()`이 줄바꿈을 유지하기 때문이다. |
| F-07 | CRITICAL | **`navigator.clipboard.writeText` 실패 시 무시**. Line 1117에서 `.catch`로 toast를 표시하지만, Line 1102-1106의 **코드 복사** 버튼에는 `.catch` 핸들러가 없다. HTTP(비-HTTPS) 환경이나 iframe sandbox에서 clipboard API가 실패하면 unhandled promise rejection이 발생한다. |

**_tableToCSV 코드 (Line 1124-1135)**:
```javascript
function _tableToCSV(tbl){
    var rows=[];
    tbl.querySelectorAll('tr').forEach(function(tr){
      var cells=[];
      tr.querySelectorAll('th,td').forEach(function(c){
        var t=c.textContent.trim().replace(/"/g,'""');
        cells.push('"'+t+'"');
      });
      if(cells.length)rows.push(cells.join(','));
    });
    return rows.join('\n');
}
```

---

### A-3. 시각화 복사 버튼 (SVG/HTML)

| ID | 등급 | 설명 |
|----|------|------|
| F-08 | CRITICAL | **SVG 내부에 외부 폰트/스타일 미포함 시 `_svgToPNG` 결과 깨짐**. Line 1562-1576의 `_svgToPNG`는 `XMLSerializer`로 SVG를 직렬화한 후 `btoa`로 인코딩한다. SVG가 CSS 변수(`var(--txt)` 등)를 참조하는 경우 Image 렌더링에서 해당 변수를 해석할 수 없어 텍스트가 보이지 않거나 색상이 잘못될 수 있다. 서버에서 생성하는 SVG가 인라인 스타일만 사용한다면 문제없으나, 테마 변수를 사용하는 경우 **빈 이미지가 복사**된다. |
| F-09 | WARNING | **HTML viz 복사에서 `html2canvas` 로딩 실패 시 무한 대기 없음 (정상)**. Line 1577-1583에서 `html2canvas`가 undefined이면 즉시 toast + `cb(null)`을 호출하므로 안전하다. 다만 `html2canvas` 실행 중 예외 발생 시 `.catch(function(){cb(null);})`으로 처리되므로 정상. |
| F-10 | WARNING | **`_htmlCapture`의 `backgroundColor:'#ffffff'` 하드코딩**. Line 1580에서 배경을 항상 흰색으로 설정하므로 dim/dark 테마에서 캡처 시 흰색 배경 위에 밝은 색 텍스트가 올라가 거의 보이지 않는 결과물이 생성된다. |
| F-11 | WARNING | **viz 복사 버튼이 SVG와 HTML을 동시에 가지는 viz에서의 동작**. Line 906-922에서 `svgEl=vc.querySelector('svg')`를 먼저 확인하고, 없으면 `_htmlCapture`로 폴백한다. `viz.code`(SVG)와 `viz.html`이 동시에 존재하면 SVG가 우선 복사되므로 의도에 맞는지 확인 필요. |

---

### A-4. 경쟁 조건 / 타이밍 이슈

| ID | 등급 | 설명 |
|----|------|------|
| F-12 | WARNING | **`renderInsight` 중복 호출 방지 불완전**. Line 962에서 `if(slot.querySelector('.insight-panel'))return;`으로 이미 렌더링된 경우 건너뛴다. 그러나 `stream:end`와 `legacy response`가 빠르게 연속 호출되면 첫 번째 호출에서 `innerHTML` 할당(Line 1075) 전에 두 번째 호출이 `.insight-panel`을 찾지 못해 중복 렌더링이 이론적으로 가능하다. 실제로는 JS 싱글 스레드 특성상 발생 확률 매우 낮음. |
| F-13 | INFO | **복사 버튼의 "복사됨" 상태 복원 타이머가 겹칠 수 있음**. Line 911, 919에서 `setTimeout(function(){cap.innerHTML=ICOPY+' 복사';},2000)`를 사용하는데, 2초 내에 다시 클릭하면 첫 번째 타이머가 "복사됨" 상태를 덮어쓴다. 기능적 문제는 아니지만 UX 깜빡임이 발생할 수 있다. |

---

### A-5. null/undefined 접근 위험

| ID | 등급 | 설명 |
|----|------|------|
| F-14 | CRITICAL | **`ins.total_elapsed.toFixed(1)` — 타입 미검증**. Line 1038-1039에서 `ins.total_elapsed!=null` 체크 후 바로 `.toFixed(1)`을 호출한다. 서버에서 `total_elapsed`를 문자열(`"3.5"`)로 보내면 `"3.5".toFixed`는 `undefined`이므로 `TypeError`가 발생한다. 또한 `NaN`이 올 경우 `NaN.toFixed(1)`은 `"NaN"`을 반환하여 UI에 `"NaN초"`가 표시된다. |
| F-15 | CRITICAL | **`s.elapsed.toFixed(1)` — 동일 문제**. Line 1045에서 `ins.step_timings` 배열의 각 요소 `s.elapsed`에도 같은 `.toFixed(1)` 호출이 있다. `elapsed`가 문자열이면 동일하게 크래시. |
| F-16 | WARNING | **`viz.table_data.rows` 접근 시 null 체크 누락**. Line 932에서 `viz.table_data&&viz.table_data.columns` 체크 후 Line 943에서 `viz.table_data.rows.map`을 호출한다. `columns`는 있으나 `rows`가 null/undefined이면 크래시. |
| F-17 | INFO | **`esc()` 함수에 non-string 입력 시**. Line 1521의 `esc(s)`는 `div.textContent=s`를 사용하므로 숫자, null 등이 들어와도 자동 toString 처리되어 크래시하지 않는다. 안전. |

---

## B. 디자인 및 UX 이슈 (Design & UX Issues)

### B-1. `.content-copy` 버튼 일관성

| ID | 등급 | 설명 |
|----|------|------|
| D-01 | WARNING | **코드 블록의 content-copy와 테이블/viz의 content-copy 내용 불일치**. 코드 블록 복사 버튼(Line 1101)은 텍스트만 `'복사'`이고 SVG 아이콘이 없다. 테이블 복사 버튼(Line 1114)은 `ICOPY+' CSV 복사'`로 아이콘+텍스트. viz 복사 버튼(Line 904)은 `ICOPY+' 복사'`로 아이콘+텍스트. 세 가지 모두 `.content-copy` 클래스를 공유하지만 **아이콘 유무**가 다르다. |
| D-02 | INFO | **코드 블록의 content-copy에 "복사됨" 상태 아이콘도 없음**. 코드 블록은 `b.textContent='복사됨'` (Line 1104), 테이블/viz는 `IC+' 복사됨'`으로 체크 아이콘을 포함. |

### B-2. `color-mix()` 브라우저 호환성

| ID | 등급 | 설명 |
|----|------|------|
| D-03 | WARNING | **`color-mix(in srgb, var(--bg) 85%, transparent)` 미지원 브라우저 대응 없음**. Chrome 111+, Firefox 113+, Safari 16.2+ 필요. 미지원 브라우저에서 `background` 속성 전체가 무시되어 **버튼 배경이 투명**해진다. `backdrop-filter:blur(6px)`가 여전히 적용되므로 완전히 보이지 않는 것은 아니지만, 가독성이 크게 저하된다. CLAUDE.md에 명시된 폐쇄망 배포 환경의 브라우저 버전을 확인해야 한다. |
| D-04 | INFO | **폴백 전략 제안**: `background` 속성 앞에 `background:rgba(255,255,255,0.85);`(light) 또는 테마별 폴백을 추가하면 구형 브라우저에서도 동작한다. 단, CSS 변수 기반이므로 단순 폴백이 어려울 수 있다. |

### B-3. `backdrop-filter` 테마별 동작

| ID | 등급 | 설명 |
|----|------|------|
| D-05 | INFO | **3개 테마 모두 정상 작동 예상**. `backdrop-filter:blur(6px)`는 배경색과 독립적으로 동작한다. light 테마에서는 밝은 배경 위 blur, dim/dark에서는 어두운 배경 위 blur로 자연스럽다. `color-mix`가 `var(--bg)`를 사용하므로 테마 전환 시 자동으로 배경색이 변경된다. |

### B-4. 버튼 위치 (`top:6px, right:8px`)

| ID | 등급 | 설명 |
|----|------|------|
| D-06 | WARNING | **코드 블록에서 언어 레이블과 겹침 가능성**. highlight.js가 `<code class="language-xxx">`에 대해 언어 레이블을 표시하는 CSS 테마가 있는 경우, `top:6px;right:8px` 위치의 복사 버튼과 겹칠 수 있다. 현재 사용 중인 `github.min.css` / `github-dark.min.css`에서는 언어 레이블을 CSS로 표시하지 않으므로 **현 설정에서는 안전**하나, hljs 테마 변경 시 주의 필요. |
| D-07 | INFO | **viz-body 내 차트가 상단까지 꽉 차는 경우**. `viz-body`는 `padding:20px`이 있으므로(Line 294) 버튼이 차트 위에 겹치지 않고 padding 영역에 위치한다. 정상. |
| D-08 | INFO | **table-wrap은 position:relative가 CSS에 명시**(Line 210). `code-wrap`도 동일. 그러나 **viz-body**는 Line 294에서 이미 `position:relative`가 설정되어 있으므로 content-copy 버튼의 absolute 위치 기준이 올바르다. |

### B-5. Insight 패널 빈 상태

| ID | 등급 | 설명 |
|----|------|------|
| D-09 | WARNING | **`query_interpretation`가 빈 객체이고 `is_success=true`이며 다른 필드도 없는 경우**, 패널은 기본 신뢰도 "보통"(`conf=ins.confidence||'보통'`, Line 1028)만 표시되며 거의 비어 보인다. 최소한 "분석 정보가 충분하지 않습니다" 같은 폴백 메시지가 있으면 좋겠다. |

---

## C. 접근성 이슈 (Accessibility)

| ID | 등급 | 설명 |
|----|------|------|
| A-01 | WARNING | **content-copy 버튼에 aria-label/title 불일치**. viz 복사 버튼(Line 904)에는 `cap.title='이미지를 클립보드에 복사'`가 있으나, 코드 블록 복사 버튼(Line 1101)과 테이블 CSV 복사 버튼(Line 1113)에는 `title` 속성이 없다. 스크린 리더에서 버튼 목적을 알 수 없다. |
| A-02 | WARNING | **content-copy 버튼에 키보드 접근 불가**. 모든 `.content-copy` 버튼은 `<button>` 요소이므로 Tab으로 포커스 가능하지만, `opacity:0` 상태에서 hover 시에만 보인다. 키보드 포커스 시에도 보이도록 `:focus-visible` 스타일이 필요하다. 현재 CSS(Line 219-221)에는 hover 트리거만 있고 focus 트리거가 없다. |
| A-03 | INFO | **insight 버튼에 이모지 텍스트 사용**. Line 737의 insight 버튼이 `title="분석 과정 보기"`를 가지고 있어 스크린 리더 지원은 양호하다. |
| A-04 | INFO | **차트 모달의 접근성은 양호**. Line 1646에서 `role="dialog"`, `aria-label="차트 확대 보기"`가 설정되어 있고, Tab키 트래핑(Line 1655)과 Esc 닫기가 구현되어 있다. |

---

## D. 종합 이슈 요약

### CRITICAL (즉시 수정 필요)

| ID | 위치 | 설명 | 수정 방안 |
|----|------|------|-----------|
| F-07 | Line 1102-1106 | 코드 복사 버튼 clipboard API `.catch` 누락 | `.catch(function(){toast('복사 실패');})` 추가 |
| F-14 | Line 1039 | `total_elapsed.toFixed(1)` 타입 미검증 | `Number(ins.total_elapsed).toFixed(1)` 또는 `parseFloat` 사용 + NaN 체크 |
| F-15 | Line 1045 | `s.elapsed.toFixed(1)` 동일 타입 미검증 | `Number(s.elapsed).toFixed(1)` 또는 `parseFloat` 사용 + NaN 체크 |
| F-08 | Line 1562-1576 | SVG에 CSS 변수 참조 시 `_svgToPNG` 결과 깨짐 | SVG 직렬화 전 computed style을 인라인으로 주입하는 전처리 추가, 또는 서버 측에서 인라인 스타일 보장 |

### WARNING (조기 수정 권장)

| ID | 위치 | 설명 |
|----|------|------|
| F-01 | Line 791 | 빈 insight 객체 `{}` 시 버튼은 보이나 패널 내용 없음 |
| F-02 | Line 966-972 | 빈 `query_interpretation` 객체 시 빈 섹션 생성 |
| F-04 | Line 1124-1135 | colspan/rowspan 미처리로 CSV 열 수 불일치 |
| F-10 | Line 1580 | dark 테마 캡처 시 흰 배경 하드코딩 |
| F-16 | Line 943 | `viz.table_data.rows` null 체크 누락 |
| D-01 | Line 1101 vs 1114 | 코드/테이블/viz 복사 버튼의 아이콘 유무 불일치 |
| D-03 | Line 212 | `color-mix()` 폐쇄망 브라우저 호환성 미확인 |
| D-09 | Line 964-1076 | insight 패널 거의 빈 상태 시 폴백 메시지 없음 |
| A-01 | Line 1101, 1113 | 코드/테이블 복사 버튼 title 속성 누락 |
| A-02 | Line 219-221 | content-copy에 `:focus-visible` 스타일 누락 |

### INFO (개선 권장)

| ID | 위치 | 설명 |
|----|------|------|
| F-05 | Line 1124-1135 | 빈 테이블 복사 시 빈 문자열이 복사됨 |
| F-06 | Line 1129 | 셀 내부 줄바꿈 시 CSV 행 깨짐 가능 |
| F-13 | Line 911 | 빠른 재클릭 시 "복사됨" 타이머 중복 |
| D-02 | Line 1104 | 코드 복사 "복사됨" 상태에 아이콘 없음 |
| D-07 | Line 294 | viz-body padding으로 버튼 위치 안전 |

---

## E. 수정 제안 코드

### E-1. F-07 수정 (코드 복사 `.catch` 추가)

```javascript
// Line 1102-1106 수정
b.onclick=function(){
    var code=pre.querySelector('code')?pre.querySelector('code').innerText:pre.innerText;
    navigator.clipboard.writeText(code).then(function(){
        b.innerHTML=IC+' 복사됨';
        setTimeout(function(){b.innerHTML=ICOPY+' 복사';},2000);
    }).catch(function(){toast('복사 실패');});
};
```
이 수정은 D-01, D-02도 동시에 해결한다 (아이콘 일관성).

### E-2. F-14, F-15 수정 (타입 안전성)

```javascript
// Line 1039 수정
var elapsed=parseFloat(ins.total_elapsed);
if(!isNaN(elapsed)){
    h+='<div class="insight-section"><div class="insight-title">...'+elapsed.toFixed(1)+'초';
    // ...
}

// Line 1045 수정
var stepElapsed=parseFloat(s.elapsed);
h+='<span>'+(isNaN(stepElapsed)?'-':stepElapsed.toFixed(1))+'초</span>';
```

### E-3. F-10 수정 (테마 인식 캡처)

```javascript
function _htmlCapture(el,cb){
    if(!el){cb(null);return;}
    if(typeof html2canvas==='undefined'){toast('html2canvas 라이브러리가 필요합니다');cb(null);return;}
    var theme=document.documentElement.getAttribute('data-theme')||'light';
    var bgColor=theme==='dark'?'#0e0e10':theme==='dim'?'#2b2a27':'#ffffff';
    html2canvas(el,{backgroundColor:bgColor,scale:2,useCORS:true}).then(function(canvas){
        canvas.toBlob(function(blob){cb(blob);},'image/png');
    }).catch(function(){cb(null);});
}
```

### E-4. A-02 수정 (키보드 접근성)

```css
.code-wrap:hover .content-copy,
.table-wrap:hover .content-copy,
.viz-body:hover>.content-copy,
.content-copy:focus-visible{opacity:1}
```

---

## F. 테스트 매트릭스

| 테스트 시나리오 | 예상 결과 | 현재 상태 |
|----------------|-----------|-----------|
| 일반 assistant 메시지 (insight 없음) | 💡 버튼 숨김 | PASS |
| insight 포함 응답 수신 | 💡 버튼 표시, 클릭 시 패널 토글 | PASS |
| insight가 빈 객체 `{}` | 💡 버튼 표시, 패널 거의 비어있음 | WARN |
| Markdown 테이블 → CSV 복사 | 정상 CSV 생성 | PASS |
| 빈 테이블 → CSV 복사 | 빈 문자열 복사 + "복사됨" 표시 | WARN |
| 특수문자(쉼표, 따옴표) 포함 테이블 | RFC 4180 준수 CSV | PASS |
| SVG 차트 → 이미지 복사 | PNG 클립보드 복사 | PASS (인라인 스타일 SVG 전제) |
| SVG에 CSS 변수 사용 | 빈/깨진 이미지 복사 | FAIL |
| HTML viz → 이미지 복사 (light) | 정상 캡처 | PASS |
| HTML viz → 이미지 복사 (dark) | 흰 배경에 밝은 텍스트 | FAIL |
| html2canvas 미로드 | toast + graceful 실패 | PASS |
| 코드 블록 복사 (비-HTTPS) | unhandled rejection | FAIL |
| `total_elapsed`가 문자열 | TypeError 크래시 | FAIL |
| `total_elapsed`가 NaN | UI에 "NaN초" 표시 | FAIL |
| 키보드만으로 복사 버튼 접근 | 버튼 보이지 않음 | FAIL |
| 3개 테마 전환 후 content-copy 표시 | 배경 적절히 변경 | PASS (modern browser) |
| 폐쇄망 구형 브라우저 | color-mix 미지원 시 투명 배경 | WARN |
