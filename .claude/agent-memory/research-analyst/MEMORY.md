# Research Analyst Agent Memory Index

## Project Context

- [project_reranker_optimization.md](project_reranker_optimization.md) — BGE-Reranker-v2-m3 CPU 최적화 리서치 (2026-03-21): FlagEmbedding ONNX 미지원 확인, ONNX O3+INT8이 최적 전략, 보고서 위치 포함
- [project_impyla_kerberos.md](project_impyla_kerberos.md) — impyla + CDP 7.1.9 Kerberos GSSAPI 의존성 확정 (2026-03-23): kerberos>=1.3.0 필수, sasl C 확장 불필요, OS libkrb5 필요, 보고서 위치 포함
- [project_sybase_iq_driver.md](project_sybase_iq_driver.md) — Sybase IQ 16.1 non-ODBC Python 드라이버 확정 (2026-03-23): sqlanydb 공식 권장, dbcapi 네이티브 라이브러리 필요, jconn4는 ASE 전용, 보고서 위치 포함
- [project_sqlglot_parsing.md](project_sqlglot_parsing.md) — SQLGlot 파싱 정확도 리서치 (2026-03-24): find_all(exp.Table) 금지(CTE 오인), error_level=RAISE 필수, Impala→hive 매핑, Sybase IQ→None+regex fallback, 보고서 위치 포함
