# deploy/ — 폐쇄망 반입 자산

Data Copilot을 **폐쇄망 베어메탈 환경**(Linux, Rocky/RHEL 계열)에
배포하기 위한 자산을 모아둔 디렉토리입니다.

폐쇄망 환경 전제:
- Docker 미사용, 베어메탈 Linux
- Python 3.12는 사전 설치되어 있을 수 있음 (번들에도 tarball 포함)
- PostgreSQL / MongoDB / Qdrant 는 **이미 구축되어 있음**
  (DB 서버 설치 없이 **스키마/컬렉션 초기화만** 수행)
- LLM은 폐쇄망 내 OpenAI 호환 엔드포인트 (Solar Pro 2 등)
- 프론트엔드는 `static/embedded.html` 단일 페이지 (npm 빌드 불필요)

## 디렉토리 구조

```
deploy/
├── README.md                 # (본 문서) 전체 개요 + 반입 순서
├── offline-bundle/           # 번들 빌드/설치 스크립트
│   ├── README.md
│   ├── build.sh              # 빌드 머신(Linux)에서 번들 생성
│   ├── install.sh            # 폐쇄망 타겟에서 설치
│   ├── download_models.sh    # HF 모델 가중치 다운로드
│   ├── os-packages.txt       # 필요 RPM 목록
│   └── .gitignore
├── db-init/                  # 기구축 DB 초기화 래퍼
│   ├── README.md
│   ├── postgres/init.sh      # PG 스키마/이력 테이블 생성
│   ├── qdrant/init.sh        # Qdrant 컬렉션 생성
│   └── mongo/init.sh         # Mongo 컬렉션/인덱스 생성
└── systemd/                  # systemd 서비스 유닛
    ├── data-copilot.service
    └── README.md
```

## 반입 단계 (요약)

1. **번들 생성** (외부 빌드 머신, Linux)
   - `deploy/offline-bundle/build.sh` 실행 → `dist/data-copilot-bundle-YYYYMMDD.tar.gz` 산출
2. **반입** (보안 절차에 따라 물리/논리 반입)
   - tar.gz 1개 파일을 폐쇄망 반입 서버로 이관
3. **설치** (폐쇄망 타겟 호스트)
   - tar 해제 후 `deploy/offline-bundle/install.sh` 실행
   - `/opt/bdp/data-copilot/` 에 앱 배치, `.venv` 재현, 모델 가중치 복사
4. **DB 초기화** (기구축 PG/Mongo/Qdrant 에 대해 1회만)
   - `deploy/db-init/postgres/init.sh`
   - `deploy/db-init/mongo/init.sh`
   - `deploy/db-init/qdrant/init.sh`
5. **서비스 등록·기동**
   - `deploy/systemd/data-copilot.service` 설치 → `systemctl enable --now data-copilot`

자세한 절차는 각 하위 디렉토리 README를 참고합니다.

## 관련 문서

- `docs/guides/migration-guide.md` — 폐쇄망 마이그레이션 가이드
- `docs/guides/customization-targets.md` — 환경별 커스터마이징 포인트
- `docs/guides/env-configuration-guide.md` — 환경변수 설정
