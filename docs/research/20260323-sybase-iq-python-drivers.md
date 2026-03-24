# SAP Sybase IQ 16.1 Python Driver 대안 분석 (non-ODBC)

**작성일**: 2026-03-23
**리서치 목적**: pyodbc 없이 Sybase IQ 16.1에 연결 가능한 Python 드라이버 식별 및 폐쇄망 배포 가능성 평가

---

## 요약 (Executive Summary)

| 옵션 | IQ 16.1 지원 | 네이티브 C 필요 | JVM 필요 | 폐쇄망 실용성 | 종합 평가 |
|------|-------------|----------------|----------|--------------|----------|
| **sqlanydb** | 공식 지원 | dbcapi.dll / libdbcapi_r.so | 없음 | 높음 (권장) | A |
| **sqlalchemy-sqlany** | sqlanydb 위에 동작 | 동좌 | 없음 | 높음 (권장) | A |
| **JayDeBeApi + sajdbc4.jar** | 지원 확인됨 | dbjdbc16.dll / libdbjdbc16.so | JRE 필수 | 중간 (JVM 반입 필요) | B |
| **JayDeBeApi + jconn4.jar** | IQ 미공식 (ASE용) | 없음 | JRE 필수 | 낮음 | C |
| **python-sybase** | 미지원 (ASE 전용) | Sybase CT-Lib | 없음 | 없음 | F |
| **FreeTDS + pymssql** | 미검증 (불안정) | FreeTDS .so | 없음 | 낮음 | D |

**권고**: sqlanydb + sqlalchemy-sqlany 조합이 유일한 실용적 ODBC-free 경로. JVM 허용 시 JayDeBeApi + sajdbc4.jar가 차선.

---

## 1. sqlanydb — SAP 공식 Python 드라이버

### 개요

SAP가 직접 관리하는 오픈소스 Python DB-API 2.0 드라이버. SQL Anywhere C API(`dbcapi`)를 ctypes로 래핑한 **순수 Python 코드**이며 C 컴파일 확장 없음.

- GitHub: [sqlanywhere/sqlanydb](https://github.com/sqlanywhere/sqlanydb)
- PyPI: [sqlanydb](https://pypi.org/project/sqlanydb/)
- 마지막 커밋: 2024년 3월 11일 (활성 유지)

### IQ 16.1 호환성

SAP 공식 문서(Infocenter)가 **SAP Sybase IQ 16.0 SP2부터 sqlanydb를 공식 Python 인터페이스로 명시**. IQ 16.1은 해당 범위에 포함됨.

SAP KBA 2475541 ("How to install and verify sqlanydb for IQ connection with python")이 존재하며 IQ용 공식 설치 절차 제공.

### 설치 요건

```
pip install sqlanydb
```

**핵심 의존성: dbcapi 네이티브 라이브러리**

| 플랫폼 | 라이브러리 파일 | 위치 (SAP IQ 설치 기준) |
|--------|---------------|------------------------|
| Windows 64bit | `dbcapi.dll` | `%IQDIR16%\Bin64\` |
| Linux 64bit | `libdbcapi_r.so` | `$IQDIR16/lib64/` |

라이브러리 탐색 순서: 환경변수 `SQLANY_API_DLL` → 플랫폼 기본 경로 순. Linux에서는 `sa_config.sh` 소싱 또는 `LD_LIBRARY_PATH` 설정 필요.

**중요**: `dbcapi.dll` / `libdbcapi_r.so`는 **SAP IQ 서버 설치본 또는 SAP IQ Client 설치본**에 포함되어 있음. 별도 무료 다운로드 가능 (SCN DOC-35857). 폐쇄망에 SAP IQ Client 패키지를 함께 반입하면 해결.

### Python 버전 지원

공식 classifiers는 Python 3.7까지 명시되어 있으나, 내부적으로 순수 ctypes 사용이므로 **Python 3.12에서도 동작 가능** (커뮤니티 보고). 테스트 필요.

### 연결 예시

```python
import sqlanydb

conn = sqlanydb.connect(
    uid="username",
    pwd="password",
    host="iq-server-host:2638",
    dbn="database_name"
)
```

### 폐쇄망 반입 목록

1. `sqlanydb` Python 패키지 (순수 Python wheel)
2. SAP IQ 16.1 Client 설치본 → `dbcapi.dll` / `libdbcapi_r.so` 추출

---

## 2. sqlalchemy-sqlany — SQLAlchemy 다이얼렉트

### 개요

- GitHub: [sqlanywhere/sqlalchemy-sqlany](https://github.com/sqlanywhere/sqlalchemy-sqlany)
- PyPI: [sqlalchemy-sqlany](https://pypi.org/project/sqlalchemy-sqlany/)
- sqlanydb를 하위 드라이버로 사용하는 SQLAlchemy 외부 다이얼렉트

### IQ 16.1 호환성

sqlanydb가 동작하면 동작함. 독립적인 C 라이브러리 없음.

### 연결 예시

```python
from sqlalchemy import create_engine

engine = create_engine(
    "sqlanywhere://username:password@/?host=iq-server-host;dbn=database_name"
)
```

### 유지보수 상태

마지막 PyPI 릴리스가 2018년으로 오래됨. SQLAlchemy 2.x와의 호환성이 보장되지 않음. async SQLAlchemy(asyncio) 지원 없음 — Data Copilot의 `async/await` 패턴에 맞지 않으므로 **직접 sqlanydb 연결 위에 비동기 래퍼를 구현하는 방식이 현실적**.

---

## 3. python-sybase (Sybase 모듈)

### 결론: IQ에 사용 불가

python-sybase (SourceForge: python-sybase.sourceforge.net)는 **Sybase CT-Library를 사용하는 Sybase ASE (Adaptive Server Enterprise) 전용** 드라이버. SAP IQ는 전혀 다른 엔진(SQL Anywhere 기반)이므로 호환되지 않음.

- SAP Community 공식 답변에서도 IQ는 sqlanydb, ASE는 별도 드라이버를 사용하도록 명확히 구분
- 유지보수 사실상 중단 (마지막 릴리스 2010년대 초)
- **기각 사유**: IQ 미지원, 유지보수 중단

---

## 4. JayDeBeApi + JDBC

### 4a. sajdbc4.jar (iAnywhere/SQL Anywhere JDBC 드라이버) — 권장

SAP IQ 16.1 설치본에는 두 종류의 JDBC 드라이버가 포함됨:
- **sajdbc4.jar**: SQL Anywhere JDBC 4.0 드라이버 (IQ 공식 지원)
- **jconn4.jar**: jConnect (ASE 전용, IQ 비공식)

| 항목 | 내용 |
|------|------|
| Driver class | `sap.jdbc4.sqlanywhere.IDriver` |
| JDBC URL | `jdbc:sqlanywhere:host=HOST;dbn=DBNAME;uid=USER;pwd=PASS` |
| 네이티브 의존 | Windows: `dbjdbc16.dll`, Linux: `libdbjdbc16.so` (IQ 설치 폴더 `bin64/` 또는 `lib64/`) |
| JAR 위치 | IQ 설치폴더 `java/` 하위 |

### JayDeBeApi 연결 예시

```python
import jaydebeapi

conn = jaydebeapi.connect(
    "sap.jdbc4.sqlanywhere.IDriver",
    "jdbc:sqlanywhere:host=iq-server:2638;dbn=mydb;uid=user;pwd=pass",
    [],
    "/opt/iq16/java/sajdbc4.jar"
)
```

### 설치 요건

- JRE (또는 JDK) — 폐쇄망 반입 필수
- JPype1 (JayDeBeApi 의존성) — C 확장 포함, 빌드 또는 wheel 반입 필요
- `sajdbc4.jar` + `libdbjdbc16.so` (IQ 설치본에서 추출)

### 실용성 평가

JVM 반입 부담이 있으나, 일부 금융권 폐쇄망에서 Java가 이미 설치된 경우(Hadoop/Spark 환경 등) JRE를 별도 반입하지 않아도 됨. Impala 연결에 이미 Java 스택을 쓰는 경우라면 **sajdbc4.jar + JayDeBeApi** 조합이 현실적 대안.

JayDeBeApi 자체는 PyPI에서 순수 Python wheel이지만 **JPype1이 C 확장**을 포함하므로 플랫폼별 빌드 또는 미리 빌드된 wheel 반입이 필요.

### 4b. jconn4.jar (jConnect) — IQ 미권장

jConnect는 SAP ASE 용도. IQ에 대해 TDS 프로토콜로 일부 접속 사례가 존재하지만 공식 지원이 아니며 데이터 타입 처리 불일치 보고 있음. SAP 공식 문서도 IQ 연결에는 sajdbc4.jar 사용을 명시.

- **기각 사유**: IQ 비공식 지원, 데이터 타입 불일치 리스크

---

## 5. FreeTDS + pymssql / ctds

### 결론: IQ에 실용적 사용 불가

FreeTDS는 Sybase와 Microsoft SQL Server의 TDS(Tabular Data Stream) 프로토콜 구현체. TDS 5.0으로 설정 시 Sybase ASE에 연결 가능하나:

- SAP IQ 16.x는 TDS 프로토콜 지원 여부가 버전에 따라 다름 — IQ의 주 연결 프로토콜은 TDS가 아닌 **SQL Anywhere TCP/IP 프로토콜(포트 2638)**
- pymssql 문서에서 Sybase ASE와의 연결 사례는 있으나 IQ 16.x에 대한 검증 사례 없음
- ctds(C TDS 라이브러리) 역시 ASE 중심
- FreeTDS 자체가 C 라이브러리이므로 폐쇄망에서 컴파일 또는 패키지 설치 필요

**기각 사유**: IQ 연결 프로토콜과 TDS 불일치, 검증 사례 없음, 불필요한 C 라이브러리 의존

---

## 6. 기타 옵션

### 6a. CData Python Connector

상용 솔루션 (CData Software). 라이선스 비용 및 폐쇄망 반입 문제로 **은행 내부망 도입 가능성 낮음**. 제외.

### 6b. @sap/iq-client (Node.js)

Node.js 전용 패키지. Python과 무관. 제외.

### 6c. SQLAlchemy-Sybase (gordthompson fork)

Sybase ASE 전용 SQLAlchemy 다이얼렉트 (pyodbc 기반). IQ 미지원, ODBC 필요. 제외.

---

## 최종 권고

### 1순위: sqlanydb + 커스텀 비동기 래퍼

```
pip install sqlanydb
# + SAP IQ 16.1 Client에서 dbcapi 네이티브 라이브러리 반입
```

- ODBC 완전 제거
- JVM 불필요
- SAP 공식 지원 IQ 16.x
- 폐쇄망 반입 목록 최소화 (Python wheel + 네이티브 .dll/.so 1개)
- `async/await` 패턴 적용 시: `asyncio.get_event_loop().run_in_executor()` 로 블로킹 sqlanydb 호출을 스레드풀에서 실행

### 2순위: JayDeBeApi + sajdbc4.jar (JVM 이미 존재하는 환경)

```
pip install JayDeBeApi JPype1
# + sajdbc4.jar, libdbjdbc16.so (IQ 설치본 추출)
# + JRE 8 이상
```

- JVM이 이미 환경에 있는 경우(Impala/Hadoop 스택) 추가 반입 최소화
- Impala 연결에도 JayDeBeApi를 쓴다면 드라이버 레이어 통일 가능

### 기각 옵션 요약

| 옵션 | 기각 사유 |
|------|----------|
| python-sybase | ASE 전용, IQ 미지원 |
| jconn4 + JayDeBeApi | jConnect는 ASE용, IQ 비공식 |
| FreeTDS + pymssql | IQ TDS 프로토콜 미매칭, 검증 사례 없음 |
| CData Connector | 상용 라이선스, 폐쇄망 도입 어려움 |

---

## 폐쇄망 반입 체크리스트 (1순위 기준)

```
Python packages (wheels):
  - sqlanydb-x.x.x-py3-none-any.whl

Native libraries (SAP IQ 16.1 Client에서 추출):
  - [Windows] dbcapi.dll (bin64/)
  - [Linux]   libdbcapi_r.so (lib64/)

환경 설정:
  - [Windows] setx SQLANY_API_DLL "C:\iq_client\bin64\dbcapi.dll"
  - [Linux]   export LD_LIBRARY_PATH=/opt/iq_client/lib64:$LD_LIBRARY_PATH
              또는 source /opt/iq_client/sa_config.sh
```

---

## 출처

- [sqlanywhere/sqlanydb GitHub](https://github.com/sqlanywhere/sqlanydb)
- [sqlanywhere/sqlalchemy-sqlany GitHub](https://github.com/sqlanywhere/sqlalchemy-sqlany/)
- [SAP Infocenter - Python Support (IQ 16.0)](https://infocenter.sybase.com/help/topic/com.sybase.infocenter.dc01776.1600/doc/html/san1357754966211.html)
- [SAP Infocenter - Python Support (IQ 16.02)](https://infocenter.sybase.com/help/topic/com.sybase.infocenter.dc01776.1602/doc/html/san1357754966211.html)
- [SAP Infocenter - sqlanydb (IQ 16.02)](https://infocenter.sybase.com/help/topic/com.sybase.infocenter.dc01776.1602/doc/html/san1357754966617.html)
- [SAP KBA 3250076 - Could not load dbcapi error](https://userapps.support.sap.com/sap/support/knowledge/en/3250076)
- [SAP Community - Python ASE & IQ drivers](https://community.sap.com/t5/technology-q-a/python-ase-iq-drivers/qaq-p/11934158)
- [Sybase IQ JDBC Drivers - Aqua Data Studio](https://wiki.idera.com/display/ADS/Sybase+IQ+JDBC+Drivers)
- [SAP Support - SAP IQ TDS JDBC vs iAnywhere JDBC](https://support.semarchy.com/support/solutions/articles/43000617823-sap-iq-tds-jdbc-driver-and-ianywhere-sqlanywhere-driver)
- [baztian/jaydebeapi GitHub](https://github.com/baztian/jaydebeapi)
- [FreeTDS - Choosing TDS Protocol Version](https://www.freetds.org/userguide/ChoosingTdsProtocol.html)
- [python-sybase SourceForge](https://python-sybase.sourceforge.net/)
- [Python Wiki - Sybase](https://wiki.python.org/moin/Sybase)
- [Hynek Schlawack - A Short Summary on Sybase SQL Anywhere and Python](https://hynek.me/articles/a-short-summary-on-sybase-sql-anywhere-python/)
- [sqlanydb PyPI](https://pypi.org/project/sqlanydb/)
- [Snyk - sqlanydb Package Health](https://snyk.io/advisor/python/sqlanydb)
