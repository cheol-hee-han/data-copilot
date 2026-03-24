---
name: impyla + CDP 7.1.9 + Sybase IQ ODBC 의존성 리서치
description: Cloudera CDP 7.1.9 impyla Kerberos/LDAP 연결 패키지 확정, Impala/Hive 버전, Sybase IQ ODBC 드라이버 이름까지 포함한 종합 버전 호환성 정보
type: project
---

impyla로 CDP 7.1.9에 연결 시 필요한 패키지 목록 및 시스템 컴포넌트 버전 확정 (2026-03-23 리서치).

## CDP 7.1.9 컴포넌트 버전 (공식 확인)
- Impala: **4.0.0** (빌드 태그: 4.0.0.7.1.9.0-387)
- Hive: **3.1.3000** (Apache 3.1.3 기반, Cloudera 패치 포함)

## impyla 버전 호환성
- 권장 최신 버전: **0.22.0** (2025-07-31 릴리스, Python 3.12/3.13 지원)
- 0.18.0+: thrift 0.16.0으로 업그레이드됨 (Python 3.10 호환성 확보)
- 0.17.0+: thrift_sasl 0.4.3으로 업그레이드됨
- 0.18–0.19 범위도 동작하나, 0.22.0 사용 권장
- 고정 의존성: `thrift==0.16.0`, `thrift_sasl==0.4.3`

## Kerberos/LDAP 의존성 (이전 확정 내용 유지)
- `pip install impyla[kerberos]` = impyla + thrift==0.16.0 + thrift_sasl==0.4.3 + pure-sasl + kerberos>=1.3.0
- `sasl` (C 확장) 패키지는 불필요. pure-sasl 기반으로 전환 완료
- LDAP 인증은 기본 설치만으로 동작

## Hive HiveServer2 연결 시 주의사항
- binary transport 모드: impyla가 정상 지원
- HTTP transport + Kerberos/SSL 조합: impyla에서 지원 불완전 (GitHub issue #294, #365 존재)
- CDP 7.1.9 Hive 3.1.3000에서 특별한 호환성 이슈 보고 없음

## OS 레벨 필수 (RHEL/CentOS)
- cyrus-sasl-gssapi, cyrus-sasl-devel, krb5-libs, krb5-workstation

## Sybase IQ 16.1 ODBC 드라이버 이름
- 드라이버 등록명: **"SQL Anywhere 16"** (Windows ODBC 관리자에서 확인 필요)
- pyodbc 연결 문자열: `DRIVER={SQL Anywhere 16};Host=...;Port=...;UID=...;PWD=...`
- pyodbc 5.x는 Python 3 전용이나 ODBC 3.0+ 호환 드라이버와 동작 (Sybase IQ 네이티브 드라이버 포함)
- 대안: "Sybase IQ" 드라이버 이름도 일부 환경에서 사용됨 (실제 설치 환경에서 `odbcinst -q -d`로 확인 필수)

보고서 위치: docs/research/20260323-impyla-kerberos-dependencies.md

**Why:** 폐쇄망 반입 시 필요한 wheel 파일 목록 확정 및 불필요한 패키지 배제 목적. Sybase IQ ODBC 드라이버명은 연결 코드 작성 시 직접 필요.
**How to apply:** impyla Impala/Hive 연결 코드 작성, pyproject.toml 의존성 추가, Sybase IQ pyodbc 연결 코드 작성 시 이 목록 참조.
