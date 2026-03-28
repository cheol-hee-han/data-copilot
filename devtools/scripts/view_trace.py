"""트레이스 타임라인 뷰어 — 파이프라인 실행 흐름을 순서대로 출력.

사용법:
    python devtools/scripts/view_trace.py                    # 최신 트레이스
    python devtools/scripts/view_trace.py evaluation/traces/trace_xxx.json  # 특정 트레이스
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def view_trace(path: Path) -> None:
    """트레이스 파일을 시간순 타임라인으로 출력한다."""
    d = json.loads(path.read_text(encoding="utf-8"))

    print(f'질의: {d.get("user_input", "")}')
    print(f'최종: {d.get("final_status", "")} | 의도: {d.get("final_intent", "")}')
    print(f'소요: {d.get("total_duration_ms", 0):.0f}ms | LLM: {d.get("total_llm_calls", 0)}회 | 토큰: {d.get("total_llm_tokens", 0)}')
    print()

    events: list[dict] = []

    for n in d.get("nodes", []):
        events.append({"time": n.get("timestamp", ""), "type": "NODE", **n})
    for lc in d.get("llm_calls", []):
        events.append({"time": lc.get("timestamp", ""), "type": "LLM", **lc})
    for dc in d.get("decisions", []):
        events.append({"time": dc.get("timestamp", ""), "type": "DECISION", **dc})
    for cr in d.get("context_retrievals", []):
        events.append({"time": cr.get("timestamp", ""), "type": "CONTEXT", **cr})

    events.sort(key=lambda x: x.get("time", ""))

    print("=" * 100)
    print(f"  파이프라인 실행 타임라인 ({len(events)}개 이벤트)")
    print("=" * 100)
    print()

    for seq, e in enumerate(events, 1):
        t = e["time"][11:19] if len(e["time"]) > 19 else e["time"][:8]
        etype = e["type"]

        if etype == "NODE":
            print(f'[{seq:2}] {t} NODE  {e["node"]:<25} {e.get("duration_ms", 0):>8.0f}ms  status={e.get("status", "")}')

        elif etype == "LLM":
            print(f'[{seq:2}] {t} LLM   {e["node"]:<25} in={e.get("prompt_tokens", 0):>5} out={e.get("response_tokens", 0):>4}  {e.get("latency_ms", 0):>8.0f}ms')
            prompt_first = e.get("prompt_summary", "").split("|")[0].strip()[:150]
            print(f"         prompt: {prompt_first}")
            resp = e.get("response_text", "")
            if resp:
                print("         response:")
                for line in resp.splitlines():
                    print(f"           {line}")
            print()

        elif etype == "DECISION":
            print(f'[{seq:2}] {t} DECIDE {e["node"]:<24} {e.get("decision_type", "")}: {e.get("chosen", "")} (conf={e.get("confidence", 0):.2f})')
            if e.get("reason"):
                print(f'         reason: {e["reason"][:150]}')
            print()

        elif etype == "CONTEXT":
            print(f'[{seq:2}] {t} CTX   {e.get("source", ""):<25} query="{e.get("query", "")[:60]}" -> {e.get("results_count", 0)}건  {e.get("latency_ms", 0):>8.0f}ms')
            summaries = e.get("results_summary", [])
            if summaries and summaries[0]:
                print(f"         summary: {summaries[0][:150]}")
            print()

    sql = d.get("sql", {})
    print("=" * 100)
    print("  SQL 결과")
    print("=" * 100)
    print(f'  generated: {bool(sql.get("generated_sql"))}')
    if sql.get("generated_sql"):
        print(f'  SQL: {sql["generated_sql"][:300]}')
    print(f'  validated: {sql.get("validated", False)}')
    print(f'  executed: {sql.get("execution_success", False)}')
    print(f'  rows: {sql.get("row_count", 0)}')
    print(f'  retry: {sql.get("retry_count", 0)}')
    print()
    print(f'  최종 응답: {d.get("final_response_summary", "")[:300]}')


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="트레이스 타임라인 뷰어")
    parser.add_argument("trace_file", nargs="?", help="트레이스 JSON 파일 경로 (생략 시 최신)")
    parser.add_argument("-o", "--output", help="출력 파일 경로 (생략 시 콘솔 출력)")
    args = parser.parse_args()

    if args.trace_file:
        p = Path(args.trace_file)
    else:
        traces_dir = Path("evaluation/traces")
        files = sorted(traces_dir.glob("trace_*.json"))
        if not files:
            print("트레이스 파일 없음")
            sys.exit(1)
        p = files[-1]

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            import builtins
            _orig_print = builtins.print

            def _file_print(*a, **kw):
                text = " ".join(str(x) for x in a)
                f.write(text + kw.get("end", "\n"))

            builtins.print = _file_print
            try:
                print(f"트레이스: {p.name}")
                print()
                view_trace(p)
            finally:
                builtins.print = _orig_print
        _orig_print(f"출력 완료: {out_path.absolute()}")
    else:
        print(f"트레이스: {p.name}")
        print()
        view_trace(p)
