# impyla + Cloudera CDP 7.1.9 Kerberos (GSSAPI) 의존성 완전 분석

**작성일**: 2026-03-23
**분석 대상**: impyla 최신 릴리스 (v0.18.x 기준, setup.py master branch) + CDP Private Cloud Base 7.1.9
**목적**: 폐쇄망 반입 시 필요한 Python 패키지 및 OS 패키지 목록 확정

---

## 1. 결론 요약 (TL;DR)

| 인증 방식 | 트랜스포트 | 추가로 필요한 패키지 |
|---|---|---|
| LDAP | binary (TCP) | 없음 (thrift_sasl이 처리) |
| LDAP | HTTP/HTTPS | 없음 (HTTP 헤더로 처리) |
| GSSAPI (Kerberos) | binary (TCP) | `kerberos>=1.3.0` + OS: cyrus-sasl-gssapi |
| GSSAPI (Kerberos) | HTTP/HTTPS | `kerberos>=1.3.0` (필수) + OS: libkrb5 |

**핵심**: `pip install impyla[kerberos]` 한 줄이 Python 레벨을 커버하지만, OS 레벨 라이브러리 없이는 동작하지 않는다.

---

## 2. impyla 공식 의존성 구조

출처: [impyla setup.py (master branch, cloudera/impyla)](https://github.com/cloudera/impyla/blob/master/setup.py)

### 2.1 install_requires (항상 설치됨)

```
six
bitarray
thrift==0.16.0
thrift_sasl==0.4.3
```

### 2.2 extras_require (선택 설치)

```
impyla[kerberos]  →  kerberos>=1.3.0
```

### 2.3 thrift_sasl의 하위 의존성

`thrift_sasl==0.4.3`은 자체적으로 `pure-sasl`을 의존성으로 끌어온다.
따라서 `pip install impyla`만 실행해도 아래 패키지가 모두 설치된다:

```
impyla
├── thrift==0.16.0
├── thrift_sasl==0.4.3
│   └── pure-sasl (thrift_sasl의 의존성)
├── six
└── bitarray
```

---

## 3. 인증 방식별 패키지 역할 분석

### 3.1 두 가지 트랜스포트 경로

impyla는 인증 경로가 트랜스포트 방식에 따라 완전히 다르다. 이것이 패키지 혼란의 핵심 원인이다.

출처: [impyla/impala/_thrift_api.py (cloudera/impyla)](https://github.com/cloudera/impyla/blob/master/impala/_thrift_api.py)

```
[binary TCP transport - 기본, port 21050]
connect(..., use_http_transport=False)  ← 기본값
  → get_transport() 사용
  → thrift_sasl.TSaslClientTransport 사용
  → impala.sasl_compat.PureSASLClient (pure-sasl 래퍼)
  → GSSAPI 처리: pure-sasl의 GSSAPIMechanism
      → 내부적으로 import kerberos 시도
      → Windows에서 실패 시 import winkerberos 폴백

[HTTP transport - 선택, port 28000/21001]
connect(..., use_http_transport=True)
  → get_http_transport() 사용
  → Python http.client 직접 사용
  → GSSAPI: import kerberos (또는 winkerberos) 직접 임포트
  → LDAP/PLAIN: HTTP Basic Auth 헤더로 처리 (SASL 라이브러리 불필요)
```

### 3.2 LDAP 인증에 필요한 패키지

**binary transport**: `thrift_sasl` + `pure-sasl`로 처리 (추가 패키지 없음)
**HTTP transport**: HTTP `Authorization: Basic` 헤더로 처리 (SASL 라이브러리 완전 불필요)

즉, **LDAP는 `pip install impyla` 기본 설치만으로 동작한다**.

### 3.3 GSSAPI (Kerberos) 인증에 필요한 패키지

출처: [pure-sasl/puresasl/mechanisms.py (thobbs/pure-sasl)](https://github.com/thobbs/pure-sasl/blob/master/puresasl/mechanisms.py)

pure-sasl의 GSSAPI 메커니즘 내부 임포트 로직:

```python
# mechanisms.py 핵심 로직 (발췌)
try:
    import kerberos
    have_kerberos = True
except ImportError:
    have_kerberos = False

if platform.system() == 'Windows':
    try:
        import winkerberos as kerberos
        have_kerberos = True
    except ImportError:
        have_kerberos = False
```

`kerberos` 패키지가 없으면 `have_kerberos = False`가 되어 GSSAPIMechanism 클래스가 메커니즘 딕셔너리에 등록되지 않는다. 결과:
- binary transport: "None of the mechanisms listed meet all required properties" 오류
- HTTP transport: ImportError 발생

출처: [pure-sasl issue #20 — kerberos package not installed warning](https://github.com/thobbs/pure-sasl/issues/20)

**결론**: GSSAPI를 사용하려면 반드시 `kerberos` 패키지(또는 Windows에서 `winkerberos`)가 필요하다. `pure-sasl` 자체는 이것을 선택 의존성으로 처리하여 ImportError 없이 넘어가지만, 실제 GSSAPI 인증 시도 시 런타임 오류가 발생한다.

---

## 4. `sasl`, `pure-sasl`, `thrift-sasl` 관계

이 세 패키지는 자주 혼동되지만 역할이 완전히 다르다.

| 패키지 | 유형 | 역할 | 현재 사용 여부 |
|---|---|---|---|
| `sasl` (Cloudera fork) | C 확장 (Cython/SWIG) | Cyrus-SASL C 라이브러리 Python 바인딩 | **더 이상 불필요** |
| `pure-sasl` | Pure Python | SASL 프로토콜 클라이언트 구현 (PLAIN, GSSAPI 등) | `thrift_sasl`의 의존성으로 자동 설치 |
| `thrift-sasl` | Python | Thrift TSaslClientTransport 구현 (pure-sasl 래퍼) | impyla의 직접 의존성 |

### 역사적 맥락

초기 impyla는 `sasl` 패키지(Cyrus-SASL C 바인딩)를 직접 사용했다. 이 패키지는:
- Linux에서 `cyrus-sasl-devel` 헤더 파일 필요
- Windows에서 컴파일 사실상 불가

이로 인해 "no mechanism available: No worthy mechs found" 오류가 광범위하게 발생했다.
출처: [impyla issue #149 — SASL Error: no mechanism available](https://github.com/cloudera/impyla/issues/149)

현재 impyla master는 `thrift_sasl==0.4.3`으로 고정하여 `pure-sasl` 기반으로 전환 완료. `sasl` 패키지(C 확장)는 더 이상 impyla의 의존성에 없다.

출처: [impyla issue #352 — can't install thrift-sasl with Python 3.7](https://github.com/cloudera/impyla/issues/352)

---

## 5. OS 레벨 의존성

출처: [Cloudera CDP 7.1.9 공식 문서 — Configuring Impyla for Impala](https://docs.cloudera.com/cdp-private-cloud-base/7.1.9/impala-start-stop/topics/impala-impyla.html)

### RHEL/CentOS 7 (폐쇄망 타겟 환경)

```bash
# Kerberos GSSAPI 사용 시 필수
sudo yum install \
    gcc-c++ \
    cyrus-sasl-md5 \
    cyrus-sasl-plain \
    cyrus-sasl-gssapi \        # GSSAPI 메커니즘 플러그인
    cyrus-sasl-devel           # 헤더 파일

# Kerberos 클라이언트 도구
sudo yum install \
    krb5-libs \
    krb5-devel \
    krb5-workstation           # kinit, klist 등
```

### Ubuntu/Debian

```bash
sudo apt install \
    g++ \
    libsasl2-dev \
    libsasl2-2 \
    libsasl2-modules-gssapi-mit \   # MIT Kerberos GSSAPI 모듈
    libkrb5-dev \
    krb5-user
```

### OS 패키지가 필요한 이유

`kerberos` Python 패키지(PyPI)는 내부적으로 OS의 MIT Kerberos (`libkrb5`) C 라이브러리를 호출한다. Python 패키지는 래퍼일 뿐이며, 실제 Kerberos TGT 발급 및 서비스 티켓 처리는 OS 레벨 라이브러리가 수행한다.

`kinit`으로 TGT가 발급된 상태여야 GSSAPI 연결이 성공한다.

---

## 6. CDP 7.1.9 연결 코드 예시

### GSSAPI (Kerberos) — binary transport (권장, port 21050)

```python
from impala.dbapi import connect

# 사전 조건: kinit <사용자>@<REALM> 실행 완료
conn = connect(
    host="impala-coordinator.corp.example.com",   # FQDN 필수 (IP 불가)
    port=21050,
    auth_mechanism="GSSAPI",
    kerberos_service_name="impala",
    use_ssl=True,
    ca_cert="/etc/ssl/certs/corp-ca.pem",
)
```

### GSSAPI (Kerberos) — HTTP transport (port 28000)

```python
conn = connect(
    host="impala-coordinator.corp.example.com",
    port=28000,
    auth_mechanism="GSSAPI",
    kerberos_service_name="impala",
    use_http_transport=True,
    http_path="cliservice",
    use_ssl=True,
)
```

### LDAP — HTTP transport

```python
conn = connect(
    host="impala-coordinator.corp.example.com",
    port=28000,
    auth_mechanism="LDAP",
    user="user@CORP.EXAMPLE.COM",
    password="password",
    use_http_transport=True,
    http_path="cliservice",
    use_ssl=True,
)
```

---

## 7. 최종 패키지 목록 (폐쇄망 반입 기준)

### Python 패키지 (pip wheel)

```
# GSSAPI (Kerberos) 사용 시 전체 목록
impyla>=0.18.0
thrift==0.16.0          # impyla가 고정 버전 요구
thrift_sasl==0.4.3      # impyla가 고정 버전 요구
pure-sasl>=0.6.2        # thrift_sasl 의존성
kerberos>=1.3.0         # GSSAPI 필수 (Linux/Mac)
# winkerberos>=0.5.0    # Windows 환경이면 이것으로 대체

# LDAP만 사용 시
impyla>=0.18.0
thrift==0.16.0
thrift_sasl==0.4.3
pure-sasl>=0.6.2
# kerberos 불필요
```

### 기각된 패키지 (설치 불필요)

| 패키지 | 기각 이유 |
|---|---|
| `sasl` (C 확장) | impyla가 pure-sasl로 전환 완료. 폐쇄망에서 C 컴파일 불가 |
| `pykerberos` | `kerberos` 패키지와 동일 프로젝트의 구 명칭. 현재 PyPI의 `kerberos`를 사용 |
| `gssapi` (pythongssapi) | impyla가 직접 사용하지 않음. pure-sasl은 `kerberos` 패키지를 사용 |
| `python-ldap` | LDAP 인증은 HTTP 헤더 또는 thrift_sasl이 처리. 별도 패키지 불필요 |

---

## 8. 자주 발생하는 오류와 원인

| 오류 메시지 | 원인 | 해결 |
|---|---|---|
| `None of the mechanisms listed meet all required properties` | `kerberos` 패키지 미설치 | `pip install kerberos` + OS kerberos 라이브러리 설치 |
| `SASL(-4): no mechanism available: No worthy mechs found` | 구버전 `sasl` C 확장 문제 또는 `cyrus-sasl-gssapi` 미설치 | OS 패키지 설치 또는 `thrift_sasl==0.4.3`으로 업그레이드 |
| `Server not found in Kerberos database` | FQDN 대신 IP 또는 별칭 사용, 또는 `/etc/krb5.conf` 미설정 | FQDN 사용, `rdns = false` 설정 고려 |
| `Could not start SASL` | `thrift` 버전 불일치 (`thrift==0.16.0` 필요) | 버전 고정 |

출처: [impyla issue #262](https://github.com/cloudera/impyla/issues/262), [impyla issue #323](https://github.com/cloudera/impyla/issues/323)

---

## 참고 문헌

- [Cloudera CDP 7.1.9 — Configuring Impyla for Impala (공식)](https://docs.cloudera.com/cdp-private-cloud-base/7.1.9/impala-start-stop/topics/impala-impyla.html)
- [cloudera/impyla setup.py (master)](https://github.com/cloudera/impyla/blob/master/setup.py)
- [cloudera/impyla _thrift_api.py (master)](https://github.com/cloudera/impyla/blob/master/impala/_thrift_api.py)
- [thobbs/pure-sasl mechanisms.py](https://github.com/thobbs/pure-sasl/blob/master/puresasl/mechanisms.py)
- [pure-sasl issue #20 — kerberos package warning](https://github.com/thobbs/pure-sasl/issues/20)
- [impyla issue #149 — No mechanism available](https://github.com/cloudera/impyla/issues/149)
- [impyla issue #262 — Kerberos Server Not Found](https://github.com/cloudera/impyla/issues/262)
- [impyla issue #323 — Kerberos No success](https://github.com/cloudera/impyla/issues/323)
- [ibis issue #2342 — Missing Impala/Kerberos dependencies](https://github.com/ibis-project/ibis/issues/2342)
- [Cloudera CDP 7.1.6 docs archive](https://docs-archive.cloudera.com/cdp-private-cloud-base/7.1.6/impala-start-stop/topics/impala-impyla.html)
