# UI/UX Design Quality Review Report

- **Date**: 2026-04-06
- **Target**: static/embedded.html (2,415 lines, single-file chatbot application)
- **Reviewer Role**: UI/UX Design Quality Reviewer
- **Context**: Data Copilot chatbot for bank employees (non-IT users), vanilla JS + CSS

---

## Part 1: User-Reported Issue Analysis

### Issue #1: Action icons layout (two rows instead of one)

- **file**: static/embedded.html
- **line**: 882-890 (HTML in ensureDOM), 234-239 (CSS .msg-actions), 512-516 (CSS .msg-like-actions)
- **severity**: warning
- **category**: structure
- **problem**: .msg-actions (copy, regen, insight) and .msg-like-actions (like, dislike) are two separate div containers with display:flex, creating two rows. This wastes vertical space and fragments a unified action bar.
- **recommendation**: Merge all five buttons into a single div.msg-actions. Remove .msg-like-actions div and its CSS (lines 512-516). Move .liked/.disliked styles into .msg-actions scope. Add a small visual separator (e.g. margin-left:8px) before the like/dislike pair. Update event binding at line 896-898 to query within .msg-actions.

---

### Issue #2: Download bar removal

- **file**: static/embedded.html
- **line**: 453-461 (CSS .download-bar), 537-539, 1374-1386 (JS renderDownload), 1621-1628 (JS handleDownloadReady), 881, 1079
- **severity**: warning
- **category**: structure
- **problem**: The orange download bar is redundant. The markdown table already has a CSV copy button via attachCodeCopy (lines 1408-1422). Two CSV actions confuse users.
- **recommendation**: Remove: CSS .download-bar/.download-bar.downloaded, JS renderDownload, JS handleDownloadReady, the download-slot div from ensureDOM, the renderDownload call at line 1079, and App.downloadCSV unless needed for large datasets. If the server sends download_ready events, silently ignore them.

---

### Issue #3: Trace file download button

- **file**: static/embedded.html
- **line**: 887-889 (like/dislike button area)
- **severity**: warning
- **category**: structure
- **problem**: No button exists for downloading trace/reasoning/report files generated at LOG_PATH/traces/filename.md.
- **recommendation**: Add a new act-btn in the merged action bar with data-act=trace and style=display:none. Show when insight data is available. On click, call App.downloadTrace(turnId) fetching /api/turns/{turnId}/trace. Ensure the server endpoint exists.

---

### Issue #4: Clarification UI redesign

- **file**: static/embedded.html
- **line**: 1004-1041 (JS _showFeedbackPopup), 541-553 (CSS feedback), 1054-1057
- **severity**: critical
- **category**: structure
- **problem**: No live clarification UI exists. Only _renderClarificationRestored handles history. No handler for server clarification events during live conversation. The user wants inline chat-bubble UI with option selection + free text, no skip button.
- **recommendation**: (1) Add case clarification in ED.handle(). Create assistant message with turnType:clarification. (2) Render a div.clarification-options inline after bot-bubble: option buttons, free-text input, submit button, no skip. (3) On submit, send selected/typed text via CN.send() and disable UI. (4) CSS: background:var(--bg2); border:1px solid var(--border); border-radius:var(--r-sm); padding:12px; margin-top:8px.

---

### Issue #5: Data extraction result ordering

- **file**: static/embedded.html
- **line**: N/A (server-side)
- **severity**: warning
- **category**: structure
- **problem**: Reference sections appear before data table. User wants: (1) data/table, (2) reference info, (3) process summary.
- **recommendation**: Primarily a server-side prompt change. Modify the present-layer template to output the table first. Client-side DOM reordering is fragile and not recommended as primary solution.

---

### Issue #6: Collapsible reference blocks

- **file**: static/embedded.html
- **line**: 1043-1053 (JS render), 194-218 (CSS .bot-bubble)
- **severity**: warning
- **category**: structure
- **problem**: Reference sections are plain markdown with no collapse. Should be collapsible (default=open) in a gray reference block.
- **recommendation**: Post-process mdRender output to wrap matching heading sections in HTML details-open elements with a styled summary. CSS: .ref-block { background:var(--bg2); border:1px solid var(--border); border-radius:var(--r-sm); padding:10px 14px; margin:8px 0; }.

---

### Issue #7: Conversation history data verification

- **file**: static/embedded.html
- **line**: 1823-1858 (JS loadSession)
- **severity**: warning
- **category**: structure
- **problem**: (a) Insight button shows when has_metadata is true from server -- logic is correct. (b) Progress steps are NOT restored from history (transient streaming state only).
- **recommendation**: (a) Verify server returns has_metadata:true for turns with insight. (b) Progress absence is acceptable. Optionally add static summary for completed turns.

---

### Issue #8: Insight panel query_interpretation

- **file**: static/embedded.html
- **line**: 1252-1259
- **severity**: warning
- **category**: structure
- **problem**: query_interpretation rendering depends on server data. Client checks if(qi) correctly. Whether populated is a server concern.
- **recommendation**: Verify server populates query_interpretation with period, target, metric. No client bug.

---

### Issue #9: SQL viewer syntax highlighting

- **file**: static/embedded.html
- **line**: 1305-1306, 1313-1314
- **severity**: warning
- **category**: consistency
- **problem**: SQL in insight panel has class=language-sql but hljs.highlight() is never called. Insight panel produces unstyled plain text unlike mdRender which uses marked highlight callback.
- **recommendation**: After slot.innerHTML=h at line 1372, add: slot.querySelectorAll(pre code.language-sql).forEach(function(block){ if(typeof hljs !== undefined) hljs.highlightElement(block); }); -- a one-line fix.

---

### Issue #10: Settings modal additional options

- **file**: static/embedded.html
- **line**: 666-694
- **severity**: suggestion
- **category**: structure
- **problem**: Only theme and font size. Banking chatbot users would benefit from more options.
- **recommendation**: Add: (1) Default row limit dropdown, (2) Auto-scroll toggle, (3) Completion notification sound toggle, (4) High contrast accessibility toggle, (5) Keyboard shortcuts guide button.

---

### Issue #11: Regenerate functionality

- **file**: static/embedded.html
- **line**: 2155-2168 (JS App.regen)
- **severity**: critical
- **category**: structure
- **problem**: App.regen() calls CN.send(lastUser.text) as raw text with no turn_id. Server treats it as a new query, creating duplicate turns and losing regeneration tracking.
- **recommendation**: Send structured payload: CN.send(JSON.stringify({ type:regen, text:lastUser.text, turn_id:lastUser.turnId })). Server must recognize type:regen. Requires coordinated server changes.

---

### Issue #12: Markdown cell height / line-height

- **file**: static/embedded.html
- **line**: 196, 197, 217
- **severity**: warning
- **category**: consistency
- **problem**: line-height:1.78 is excessive for data tables. padding:10px 12px on cells and margin-bottom:12px on paragraphs make results sparse.
- **recommendation**: Reduce line-height to 1.6, paragraph margin to 8px, cell padding to 7px 10px. Optionally add .bot-bubble.data-dense modifier for extraction results.

---

### Issue #13: Sidebar conversation ordering

- **file**: static/embedded.html
- **line**: 1945-2026 (JS renderList), 1780-1786 (JS SB.init)
- **severity**: warning
- **category**: structure
- **problem**: No explicit sort on _sessions after loading. If server returns unsorted data, sidebar order is arbitrary within date groups.
- **recommendation**: After .map() in SB.init() and after loadMore, add: _sessions.sort(function(a,b){ return b.ts - a.ts; });

---

### Issue #14: Streaming output verification

- **file**: static/embedded.html
- **line**: 1459-1483 (JS SE), 1548-1589 (JS handleStream)
- **severity**: suggestion
- **category**: structure
- **problem**: Server-driven text IS streamed incrementally (correct). Legacy path simulates typing (acceptable). SVG/HTML arrive as single payloads (acceptable). Markdown tables may render partially during streaming.
- **recommendation**: Consider requestAnimationFrame batching for chunk renders. Consider buffering partial markdown tables. All optional optimizations.

---

### Issue #15: Large markdown table scrolling

- **file**: static/embedded.html
- **line**: 216-218 (CSS .bot-bubble table), 221 (CSS .table-wrap)
- **severity**: warning
- **category**: consistency
- **problem**: .table-wrap has no overflow handling. Wide tables overflow container. Inconsistent with .viz-body which has bordered overflow.
- **recommendation**: Update .table-wrap: overflow-x:auto; overflow-y:auto; max-height:400px; border:1px solid var(--border); border-radius:var(--r-sm); margin:12px 0;. Add sticky header for .table-wrap th. Add matching scrollbar styles.

---

## Part 2: General UX Findings

### 2.1 Component Structure Review

#### Single Responsibility -- NEEDS IMPROVEMENT

- **file**: static/embedded.html
- **line**: 1-2415
- **severity**: critical
- **category**: structure
- **problem**: Entire application (2,415 lines) in one file. RD (renderer) contains business logic spanning ~600 lines. ED manages both event routing and UI state.
- **recommendation**: If single-file is intentional for deployment, at minimum extract renderInsight (~120 lines) and renderViz (~70 lines) into clearer helper boundaries.

#### Conditional Rendering Complexity -- NEEDS IMPROVEMENT

- **file**: static/embedded.html
- **line**: 844-921 (JS ensureDOM)
- **severity**: warning
- **category**: structure
- **problem**: 4 levels of conditional branching in ensureDOM.
- **recommendation**: Extract into named functions: _createErrorRow, _createGapRow, _createAssistantRow, _createUserRow. Use early returns.

#### Duplicated Patterns -- NEEDS IMPROVEMENT

- **file**: static/embedded.html
- **line**: 1536-1542/1576-1583, 1544-1546/1585-1587
- **severity**: suggestion
- **category**: structure
- **problem**: Turn_id capture and title sync logic duplicated between handleLegacy and handleStream.end.
- **recommendation**: Extract into shared _finalizeTurn(msg, data).

---

### 2.2 Accessibility Audit

#### Keyboard access for interactive divs -- CRITICAL

- **file**: static/embedded.html
- **line**: 1974 (.chat-item), 256 (.phase-header), 1145 (.progress-collapsed-summary)
- **severity**: critical
- **category**: accessibility
- **problem**: .chat-item has tabindex=0 and role=button but no onKeyDown for Enter/Space. .phase-header and .progress-collapsed-summary have click handlers with no keyboard support. WCAG 2.1.1.
- **recommendation**: Add keydown handlers for Enter/Space on all interactive div/span elements. Add tabindex=0 and role=button to .phase-header.

#### Missing input labels -- WARNING

- **file**: static/embedded.html
- **line**: 592, 651, 996
- **severity**: warning
- **category**: accessibility
- **problem**: #sbSearch, #messageInput, and dynamically created edit inputs lack aria-label. WCAG 1.3.1, 4.1.2.
- **recommendation**: Add aria-label attributes to all input elements.

#### Action buttons invisible to keyboard users -- WARNING

- **file**: static/embedded.html
- **line**: 236
- **severity**: warning
- **category**: accessibility
- **problem**: Action buttons opacity:0 only visible on :hover. Keyboard users cannot see them. WCAG 2.1.1, 2.4.7.
- **recommendation**: Add .message-row:focus-within .msg-actions { opacity:1; } and .act-btn:focus-visible outline.

#### Missing focus traps on modals -- WARNING

- **file**: static/embedded.html
- **line**: 367-398, 2184-2196
- **severity**: warning
- **category**: accessibility
- **problem**: Settings and confirm modals have no focus trap. WCAG 2.4.3.
- **recommendation**: Implement generic trapFocus(modalElement) utility for all modals.

#### Color-only information -- WARNING

- **file**: static/embedded.html
- **line**: 119, 121-123, 513-514
- **severity**: warning
- **category**: accessibility
- **problem**: Like/dislike uses color-only states (green/red). WCAG 1.4.1.
- **recommendation**: Add visual change beyond color (filled vs outline icon).

#### SVG icons lack accessible names -- SUGGESTION

- **file**: static/embedded.html
- **line**: 633-634, 583
- **severity**: suggestion
- **category**: accessibility
- **problem**: Inline SVGs lack role=img or aria-hidden. WCAG 1.1.1.
- **recommendation**: Add aria-hidden=true to decorative SVGs, role=img with aria-label to meaningful ones.

**Accessibility Grade: SEVERE**

---

### 2.3 Visual Consistency Check

#### Spacing inconsistency -- WARNING

- **file**: static/embedded.html
- **line**: various
- **severity**: warning
- **category**: consistency
- **problem**: Many one-off spacing values (4/7/9/10/12/13/14/16/18/24px) without a scale.
- **recommendation**: Define spacing tokens --sp-1:4px through --sp-6:24px.

#### Hardcoded semantic colors -- WARNING

- **file**: static/embedded.html
- **line**: 321, 346-347, 429-434, 513-514, 559-562
- **severity**: warning
- **category**: consistency
- **problem**: Error/success/warning colors hardcoded in multiple places per theme.
- **recommendation**: Add semantic color tokens: --clr-success, --clr-error, --clr-warn with -bg/-txt variants.

#### Typography scale -- SUGGESTION

- **file**: static/embedded.html
- **line**: various
- **severity**: suggestion
- **category**: consistency
- **problem**: 12+ distinct font sizes without clear scale.
- **recommendation**: Consolidate: --fs-xs:11px; --fs-sm:12.5px; --fs-md:14px; --fs-lg:16px; --fs-xl:19px.

#### Single breakpoint -- SUGGESTION

- **file**: static/embedded.html
- **line**: 476, 509, 565
- **severity**: suggestion
- **category**: consistency
- **problem**: Only 640px breakpoint. No tablet breakpoint.
- **recommendation**: Add ~1024px breakpoint for tablets/small laptops.

#### Design token coverage -- SUGGESTION

- **file**: static/embedded.html
- **line**: 23-60
- **severity**: suggestion
- **category**: consistency
- **problem**: Good base tokens but missing semantic colors, spacing, type scale.
- **recommendation**: Extend token system as detailed above.

**Consistency Grade: MODERATE**

---

### 2.4 Mobile Responsiveness

- **file**: static/embedded.html
- **line**: 476-485
- **severity**: warning
- **category**: responsiveness
- **problem**: Minimal mobile styles. 28x28px action buttons too small for touch. No tablet breakpoint.
- **recommendation**: Increase .act-btn to 36x36px on mobile. Add touch-action:manipulation. Add tablet breakpoint.

---

### 2.5 Error Handling and Recovery UX

- **file**: static/embedded.html
- **line**: 851-869, 1491, 1668
- **severity**: warning
- **category**: structure
- **problem**: After 5 WebSocket reconnect attempts, only static error banner with no reconnect option. Offline banner has no action.
- **recommendation**: Add reconnect button to error/offline banners. Add reconnect from topbar status.

---

## Part 3: Suggested New Features

### User Convenience

1. **In-conversation search** (Ctrl+F): Find specific data in long chats
2. **Export as report**: Download conversation as formatted HTML/PDF
3. **Click-to-copy table cells**: Copy individual values from tables
4. **Bookmark responses**: Pin important answers for quick reference
5. **Scroll-to-bottom button**: Floating button when scrolled up during streaming
6. **Input history**: Up/Down arrow to cycle previous messages
7. **Regen comparison**: Side-by-side old vs new response

### Admin Features

1. **SQL review mode**: Always-visible SQL for validation
2. **Feedback analytics**: Aggregate like/dislike data
3. **Session statistics**: Query patterns and error rates

---

## Summary

| Category | Critical | Warning | Suggestion | Total |
|----------|----------|---------|------------|-------|
| Structure | 3 | 8 | 2 | 13 |
| Accessibility | 1 | 4 | 1 | 6 |
| Consistency | 0 | 3 | 3 | 6 |
| Responsiveness | 0 | 1 | 0 | 1 |
| **Total** | **4** | **16** | **6** | **26** |

### Top Priority Actions (Critical)

1. **Issue #4** -- Implement inline clarification UI (no live handler exists)
2. **Issue #11** -- Fix regen to send turn_id for server-side tracking
3. **Accessibility** -- Keyboard handlers, input labels, modal focus traps
4. **Issue #9** -- Add hljs.highlightElement() for insight SQL (one-line fix)
