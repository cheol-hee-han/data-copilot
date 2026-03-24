# 폐쇄망 DB 커넥터 의존성 및 설치 가이드

**작성일:** 2026-03-23
**대상 환경:** 은행 폐쇄망 (Cloudera CDP 7.1.9 + SAP Sybase IQ 16.1)
**목적:** Impala / Hive / Sybase IQ 커넥터의 Python 의존성, OS 패키지, 설치 절차, 주의사항 정리

---

## 목차

1. [대상 시스템 버전 정보](#1-대상-시스템-버전-정보)
2. [Python 패키지 의존성](#2-python-패키지-의존성)
3. [OS 레벨 패키지 (RPM)](#3-os-레벨-패키지-rpm)
4. [Sybase IQ ODBC 드라이버 설정](#4-sybase-iq-odbc-드라이버-설정)
5. [Sybase IQ — ODBC 없이 연결하는 대안](#5-sybase-iq--odbc-없이-연결하는-대안)
6. [인증 방식별 설정](#6-인증-방식별-설정)
7. [환경 변수 (.env)](#7-환경-변수-env)
8. [폐쇄망 반입 체크리스트](#8-폐쇄망-반입-체크리스트)
9. [알려진 이슈 및 주의사항](#9-알려진-이슈-및-주의사항)

---

## 1. 대상 시스템 버전 정보

| 시스템 | 플랫폼 | 서버 버전 | 비고 |
|--------|--------|----------|------|
| Impala | Cloudera CDP 7.1.9 | **4.0.0** | JDBC 2.6.35.1067은 Java 드라이버 버전 (Python은 Thrift 사용) |
| Hive | Cloudera CDP 7.1.9 | **3.1.3000** (Apache 3.1.3 기반) | HiveServer2 Thrift 프로토콜 |
| Sybase IQ | SAP | **16.1** | SQL Anywhere 16 기반, 기본 포트 2638 |

---

## 2. Python 패키지 의존성

### Impala / Hive 공통

| 패키지 | 버전 | 역할 | 비고 |
|--------|------|------|------|
| `impyla` | `>=0.20.0` | HiveServer2 Thrift 클라이언트 | Impala·Hive 모두 지원. 0.18~0.19는 Python 3.12 HTTPS 버그 포함 |
| `thrift` | `==0.16.0` | Thrift RPC 프로토콜 | **impyla가 핀 고정**, 0.17+ 설치 시 의존성 충돌 |
| `thrift-sasl` | `==0.4.3` | Thrift-SASL 어댑터 | **impyla가 핀 고정** |
| `pure-sasl` | `>=0.6.2` | SASL 메커니즘 (LDAP/PLAIN) | 순수 Python |
| `kerberos` | `>=1.3.0` | GSSAPI(Kerberos) 인증 | MIT libkrb5 C 래퍼. **LDAP만 쓸 경우 불필요** |

> **주의:** `thrift`와 `thrift-sasl`은 반드시 명시된 버전으로 고정해야 한다.
> impyla의 `setup.cfg`가 `==` 핀을 걸고 있어 상위 버전 설치 시 pip 의존성 충돌이 발생한다.

### Sybase IQ

| 패키지 | 버전 | 역할 | 비고 |
|--------|------|------|------|
| `pyodbc` | `>=5.0.0` | ODBC 클라이언트 | pyodbc는 패스스루, 실제 호환성은 ODBC 드라이버가 결정 |

또는 ODBC 없이 사용할 수 있는 대안 (→ [5장 참조](#5-sybase-iq--odbc-없이-연결하는-대안)):

| 패키지 | 버전 | 역할 | 비고 |
|--------|------|------|------|
| `sqlanydb` | `>=1.0.13` | SAP 공식 Python DB-API 2.0 드라이버 | 순수 Python + ctypes, ODBC 불필요 |

### 폐쇄망 반입용 wheel 목록 (요약)

```text
impyla-0.20.0-py3-none-any.whl        (또는 0.22.0)
thrift-0.16.0-cp312-cp312-linux_x86_64.whl
thrift_sasl-0.4.3-py3-none-any.whl
pure_sasl-0.6.2-py3-none-any.whl
kerberos-1.3.1-cp312-cp312-linux_x86_64.whl  (Kerberos 사용 시)
pyodbc-5.x.x-cp312-cp312-linux_x86_64.whl    (ODBC 방식)
sqlanydb-1.0.13-py3-none-any.whl              (sqlanydb 방식)
```

---

## 3. OS 레벨 패키지 (RPM)

### RHEL/CentOS 7 (폐쇄망 서버 기준)

#### Kerberos 인증 사용 시 (필수)

```bash
yum install krb5-libs krb5-devel krb5-workstation
yum install cyrus-sasl cyrus-sasl-gssapi cyrus-sasl-plain cyrus-sasl-md5 cyrus-sasl-devel
```

| RPM 패키지 | 용도 |
|-----------|------|
| `krb5-libs` | MIT Kerberos 런타임 라이브러리 |
| `krb5-devel` | `kerberos` Python 패키지 빌드 시 필요한 헤더 |
| `krb5-workstation` | `kinit`, `klist` 등 Kerberos 도구 |
| `cyrus-sasl-gssapi` | SASL GSSAPI 메커니즘 플러그인 |
| `cyrus-sasl-plain` | SASL PLAIN/LDAP 메커니즘 플러그인 |

#### LDAP 인증만 사용 시 (최소)

```bash
yum install cyrus-sasl cyrus-sasl-plain cyrus-sasl-md5
```

#### Sybase IQ ODBC 사용 시

```bash
yum install unixODBC unixODBC-devel
```

> SAP Sybase IQ Client 패키지를 별도 반입·설치하여 ODBC 드라이버를 등록해야 한다.

---

## 4. Sybase IQ ODBC 드라이버 설정

### 드라이버 설치 확인

```bash
# 등록된 ODBC 드라이버 목록 확인
odbcinst -q -d
```

### `/etc/odbcinst.ini` 등록 예시

```ini
[SQL Anywhere 16]
Description = SAP Sybase IQ 16.1 ODBC Driver
Driver      = /opt/sap/iq_client/lib64/libdbodbc16_r.so
Setup       = /opt/sap/iq_client/lib64/libdbodbc16_r.so
```

### 연결 확인

```bash
# isql로 연결 테스트
isql -v "SQL Anywhere 16" <user> <password>
```

> **드라이버명 주의:** 환경마다 등록명이 다를 수 있다 (`"SQL Anywhere 16"`, `"Sybase IQ"`, `"iAnywhere Solutions 16"` 등).
> 실제 등록명을 `odbcinst -q -d`로 확인 후 `.env`의 `SYBASE_ODBC_DRIVER`에 지정해야 한다.

---

## 5. Sybase IQ — ODBC 없이 연결하는 대안

### 1순위: sqlanydb (SAP 공식 드라이버) — 권장

SAP가 직접 관리하는 Python DB-API 2.0 드라이버. **순수 Python + ctypes** 구조로,
ODBC 설정 없이 SAP IQ Client의 네이티브 라이브러리(`dbcapi`)만 있으면 동작한다.

**장점:**
- ODBC 드라이버 등록(`odbcinst.ini`) 불필요
- unixODBC RPM 불필요
- 순수 Python이라 wheel 반입이 간단 (플랫폼 무관 `py3-none-any.whl`)

**필요 파일:**

| OS | 네이티브 라이브러리 | 위치 (IQ Client 설치 기준) |
|----|-------------------|--------------------------|
| Linux | `libdbcapi_r.so` | `$IQDIR16/lib64/` |
| Windows | `dbcapi.dll` | `%IQDIR16%\Bin64\` |

**환경 설정:**

```bash
# Linux — 방법 1: sa_config.sh 소싱 (LD_LIBRARY_PATH 자동 설정)
source /opt/sap/iq_client/sa_config.sh

# Linux — 방법 2: 환경 변수 직접 지정
export SQLANY_API_DLL=/opt/sap/iq_client/lib64/libdbcapi_r.so

# Windows
set SQLANY_API_DLL=C:\SAP\IQ-16_1\Bin64\dbcapi.dll
```

**연결 코드 예시:**

```python
import sqlanydb

conn = sqlanydb.connect(
    host="10.0.1.100",
    port="2638",
    userid="readonly_user",
    password="****",
    dbn="info_db",
    charset="UTF-8",
)
cursor = conn.cursor()
cursor.execute("SELECT * FROM loan_master LIMIT 10")
rows = cursor.fetchall()
```

**주의:** sqlanydb는 Python 3.12 공식 테스트가 없다 (순수 ctypes 구조상 동작 가능성 높으나, 폐쇄망 반입 전 사전 테스트 필수).

### 2순위: JayDeBeApi + sajdbc4.jar (JVM이 이미 존재하는 환경)

Java JDBC 드라이버를 JPype1 경유로 Python에서 호출하는 방식.
폐쇄망에 이미 JRE가 설치된 환경이라면 차선으로 고려 가능하다.

**필요 파일:**
- `sajdbc4.jar` (IQ Client에서 추출, **jconn4.jar는 ASE 전용이므로 사용 불가**)
- `dbjdbc16.dll` / `libdbjdbc16.so` (네이티브 JNI 라이브러리)
- JRE 8+ 설치
- `JayDeBeApi` + `JPype1` Python 패키지

**비권장 사유:** JRE 반입 부담, JPype1 C 확장 빌드 필요, 디버깅 복잡.

### 비교표

| 항목 | pyodbc (ODBC) | sqlanydb (네이티브) | JayDeBeApi (JDBC) |
|------|--------------|--------------------|--------------------|
| ODBC 설정 | 필요 | **불필요** | 불필요 |
| OS 패키지 | unixODBC | 없음 | JRE |
| 네이티브 라이브러리 | libdbodbc16_r.so | **libdbcapi_r.so** | dbjdbc16.so + JVM |
| Python wheel | C 확장 (플랫폼별) | **순수 Python** | C 확장 (JPype1) |
| 폐쇄망 반입 난이도 | 중간 | **쉬움** | 어려움 |
| Python 3.12 호환 | 검증됨 | 테스트 필요 | 검증됨 |

---

## 6. 인증 방식별 설정

### LDAP 인증 (Impala / Hive)

```env
IMPALA_AUTH_MECHANISM=LDAP
IMPALA_USER=svc_readonly
IMPALA_PASSWORD=****

HIVE_AUTH_MECHANISM=LDAP
HIVE_USER=svc_readonly
HIVE_PASSWORD=****
```

추가 패키지 불필요 (`pure-sasl`로 충분).

### Kerberos (GSSAPI) 인증 (Impala / Hive)

```env
IMPALA_AUTH_MECHANISM=GSSAPI
HIVE_AUTH_MECHANISM=GSSAPI
```

사전 조건:
1. OS에 `krb5-workstation` 설치
2. `/etc/krb5.conf` 설정 (KDC 주소, realm 등)
3. `kinit` 으로 TGT 발급 후 실행

```bash
kinit svc_readonly@BANK.LOCAL
python -m src.main
```

> Kerberos 사용 시 user/password 환경 변수는 무시된다 (TGT에서 인증 정보를 가져옴).

---

## 7. 환경 변수 (.env)

```env
# ── Impala (CDP 7.1.9, Impala 4.0.0) ──
IMPALA_HOST=cdp-impala.bank.local
IMPALA_PORT=21050
IMPALA_AUTH_MECHANISM=LDAP          # LDAP | GSSAPI | NOSASL
IMPALA_USER=svc_readonly
IMPALA_PASSWORD=****
IMPALA_USE_SSL=false
IMPALA_DATABASE=info_db
IMPALA_QUERY_TIMEOUT=60

# ── Hive (CDP 7.1.9, Hive 3.1.3) ──
HIVE_HOST=cdp-hive.bank.local
HIVE_PORT=10000
HIVE_AUTH_MECHANISM=LDAP            # LDAP | GSSAPI | NOSASL
HIVE_USER=svc_readonly
HIVE_PASSWORD=****
HIVE_USE_SSL=false
HIVE_DATABASE=info_db
HIVE_QUERY_TIMEOUT=120

# ── Sybase IQ (16.1) ──
SYBASE_DRIVER=native                # native (sqlanydb) | odbc (pyodbc)
SYBASE_HOST=sybase-iq.bank.local
SYBASE_PORT=2638
SYBASE_DATABASE=info_db
SYBASE_USER=readonly_user
SYBASE_PASSWORD=****
SYBASE_ODBC_DRIVER=SQL Anywhere 16  # odbc 방식 전용 (odbcinst -q -d 로 확인)
SYBASE_CHARSET=UTF-8
SYBASE_QUERY_TIMEOUT=60
```

---

## 8. 폐쇄망 반입 체크리스트

### Python wheel 파일

- [ ] `impyla-0.20.0+` wheel
- [ ] `thrift-0.16.0` wheel (플랫폼별 C 확장)
- [ ] `thrift_sasl-0.4.3` wheel
- [ ] `pure_sasl-0.6.2+` wheel
- [ ] `kerberos-1.3.0+` wheel (Kerberos 사용 시, 플랫폼별 C 확장)
- [ ] `pyodbc-5.x` wheel (ODBC 방식, 플랫폼별 C 확장) 또는 `sqlanydb-1.0.13+` wheel (네이티브 방식)

### OS RPM 패키지

- [ ] `cyrus-sasl`, `cyrus-sasl-plain`, `cyrus-sasl-md5`
- [ ] `krb5-libs`, `krb5-devel`, `krb5-workstation`, `cyrus-sasl-gssapi` (Kerberos 사용 시)
- [ ] `unixODBC`, `unixODBC-devel` (ODBC 방식 사용 시)

### 네이티브 드라이버

- [ ] SAP Sybase IQ 16.1 Client 패키지 (ODBC 방식: `libdbodbc16_r.so` / 네이티브 방식: `libdbcapi_r.so`)

### 인프라 설정

- [ ] Impala 데몬 접근 가능 (`cdp-impala:21050`)
- [ ] HiveServer2 접근 가능 (`cdp-hive:10000`)
- [ ] Sybase IQ 접근 가능 (`sybase-iq:2638`)
- [ ] Kerberos KDC 접근 가능 + `/etc/krb5.conf` 설정 (Kerberos 사용 시)
- [ ] ODBC 드라이버 등록 (`odbcinst -q -d`) (ODBC 방식 사용 시)

---

## 9. 알려진 이슈 및 주의사항

### Impala / Hive 공통

| 이슈 | 영향 | 대응 |
|------|------|------|
| `thrift` 0.17+ 설치 시 impyla 충돌 | import 오류 | `thrift==0.16.0` 핀 고정 필수 |
| impyla 0.18~0.19 Python 3.12 HTTPS 버그 | SSL 연결 실패 | `impyla>=0.20.0` 사용 |
| Hive HTTP transport + Kerberos | `AttributeError: module 'http.client' has no attribute 'HTTP'` | binary transport(기본값) 사용 권장 ([impyla#294](https://github.com/cloudera/impyla/issues/294)) |
| impyla는 동기 드라이버 | 이벤트 루프 블로킹 | `asyncio.to_thread()` 래핑 적용 완료 |

### Sybase IQ

| 이슈 | 영향 | 대응 |
|------|------|------|
| ODBC 드라이버명 환경마다 상이 | 연결 실패 | `odbcinst -q -d`로 확인 후 `.env`에 지정 |
| `jconn4.jar`는 ASE 전용 | 데이터 타입 불일치 | JDBC 사용 시 반드시 `sajdbc4.jar` 사용 |
| sqlanydb Python 3.12 미공식 | 잠재적 비호환 | 반입 전 사전 테스트 필수 |
| Sybase IQ 문자셋 | 한글 깨짐 | `CHARSET=UTF-8` 명시 |
