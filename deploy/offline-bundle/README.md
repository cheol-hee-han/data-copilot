# offline-bundle — 폐쇄망 설치 번들

폐쇄망에 반입할 **단일 tar.gz 번들**을 생성하고, 타겟 호스트에서 설치하는
스크립트 모음입니다.

## 구성

| 파일 | 용도 |
|---|---|
| `build.sh` | 빌드 머신(Linux)에서 번들 생성 |
| `install.sh` | 폐쇄망 타겟에서 번들 설치 |
| `download_models.sh` | 임베딩/리랭커 모델 가중치 다운로드 (`build.sh` 내부 호출) |
| `os-packages.txt` | 폐쇄망 타겟에 필요한 RPM 목록 (주석 포함) |

## 산출물 구조 (`dist/`)

```
dist/
├── python/                 # Python 3.12.x tarball (사전 다운로드)
├── wheels/                 # uv pip download 결과 (torch CPU wheel 포함)
├── models/                 # BGE-M3, BGE-Reranker-v2-m3 가중치
│   ├── bge-m3/
│   └── bge-reranker-v2-m3/
├── os-packages/            # dnf download --resolve 결과 (*.rpm)
├── app/                    # 프로젝트 소스
│   ├── src/
│   ├── static/
│   ├── resources/
│   ├── pyproject.toml
│   ├── uv.lock
│   ├── gunicorn.conf.py
│   └── langgraph.json
└── MANIFEST.txt            # 산출물별 크기/해시
```

## 빌드 머신 사전 조건

- Linux (폐쇄망 OS와 동일 계열, 예: Rocky 9 / RHEL 9)
- `uv` 설치 (https://docs.astral.sh/uv/)
- `huggingface-cli` 설치 (`pip install -U "huggingface_hub[cli]"`)
- `dnf`, `curl`, `tar`, `sha256sum` 사용 가능
- HuggingFace 및 PyPI, download.pytorch.org 접속 가능 (온라인)
- 디스크 여유: 최소 20GB (모델 가중치 수 GB + wheels)

## 실행 순서 (빌드 머신)

```bash
cd <project-root>
bash deploy/offline-bundle/build.sh
# 산출물: deploy/offline-bundle/dist/
# 최종 번들: deploy/offline-bundle/data-copilot-bundle-YYYYMMDD.tar.gz
```

## 실행 순서 (폐쇄망 타겟)

```bash
# 1. 번들 반입 후 해제
tar -xzf data-copilot-bundle-YYYYMMDD.tar.gz
cd dist

# 2. 설치 (root 권한 필요)
sudo bash ../deploy/offline-bundle/install.sh
```

`install.sh` 가 수행하는 작업:
1. Python 3.12 설치 여부 확인 (없으면 번들 tarball로 설치 — 선택)
2. OS RPM 설치 (`dnf install -y ./os-packages/*.rpm`)
3. 앱 소스를 `/opt/data-copilot/` 로 배치
4. `uv sync --frozen --offline --find-links ./wheels/` 로 `.venv` 재현
5. 모델 가중치를 `EMBEDDING_MODEL_CACHE_PATH` 경로로 복사
6. 서비스 계정·권한 설정
