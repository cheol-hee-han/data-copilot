---
name: project_sybase_iq_driver
description: SAP Sybase IQ 16.1 non-ODBC Python 드라이버 조사 결과 및 권고 (2026-03-23)
type: project
---

sqlanydb가 SAP IQ 16.1 공식 Python 드라이버로 확정됨 (ODBC 불필요, dbcapi 네이티브 라이브러리만 필요).

**Why:** pyodbc 없이 폐쇄망에서 Sybase IQ 16.1에 접속할 방법이 필요했으며, SAP 공식 문서 및 KBA가 sqlanydb를 IQ용 공식 인터페이스로 명시함.

**How to apply:**
- sqlanydb 사용 시 SAP IQ Client에서 dbcapi.dll (Windows) 또는 libdbcapi_r.so (Linux) 추출하여 반입 필요
- sqlalchemy-sqlany는 SQLAlchemy 2.x 미호환이므로 async 환경에서는 sqlanydb 직접 + run_in_executor 패턴 권장
- JayDeBeApi + sajdbc4.jar는 JVM이 이미 있는 경우 차선 (sajdbc4.jar는 IQ 공식, jconn4.jar는 ASE 전용이므로 혼동 금지)
- python-sybase, FreeTDS/pymssql은 IQ 미지원으로 기각
- 보고서: docs/research/20260323-sybase-iq-python-drivers.md
