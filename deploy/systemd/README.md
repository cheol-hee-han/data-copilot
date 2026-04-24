# systemd — Data Copilot 서비스 유닛

폐쇄망 Linux 호스트에서 Data Copilot(FastAPI + gunicorn)을
systemd 서비스로 기동하기 위한 유닛 파일입니다.

## 사전 조건

- `deploy/offline-bundle/install.sh` 실행 완료
  - `/opt/bdp/data-copilot/` 에 앱 배치
  - `/opt/bdp/data-copilot/.venv/` 에 의존성 설치
  - `datacopilot` 사용자/그룹 생성
- `/opt/bdp/data-copilot/.env` 작성 완료
  (참고: `docs/guides/env-configuration-guide.md`)
- DB 초기화 완료 (`deploy/db-init/`)

## 설치 및 기동

```bash
# 1) 유닛 파일 설치
sudo cp /opt/bdp/data-copilot/deploy/systemd/data-copilot.service \
        /etc/systemd/system/data-copilot.service

# 2) systemd 재로드
sudo systemctl daemon-reload

# 3) 부팅 시 자동 시작 + 즉시 기동
sudo systemctl enable --now data-copilot

# 4) 상태 확인
sudo systemctl status data-copilot
```

## 로그 확인

```bash
# 실시간 로그
sudo journalctl -u data-copilot -f

# 최근 500줄
sudo journalctl -u data-copilot -n 500 --no-pager

# 특정 시간 이후
sudo journalctl -u data-copilot --since "2026-04-14 09:00:00"
```

## 운영 명령어

| 작업 | 명령 |
|---|---|
| 재시작 | `sudo systemctl restart data-copilot` |
| 정지   | `sudo systemctl stop data-copilot` |
| 재로드(SIGHUP) | `sudo systemctl reload data-copilot` *(gunicorn 설정 지원 시)* |
| 자동기동 해제 | `sudo systemctl disable data-copilot` |

## 트러블슈팅

- **기동 실패 (status exit-code=203/EXEC)**: `.venv/bin/gunicorn` 경로·권한 확인
- **환경변수 누락**: `EnvironmentFile=/opt/bdp/data-copilot/.env` 경로·소유자 확인
- **권한 오류**: `chown -R datacopilot:datacopilot /opt/bdp/data-copilot` 재실행
- **모델 로드 실패**: `EMBEDDING_MODEL_CACHE_PATH` 환경변수와 실제 복사 경로 일치 여부 확인

## 유닛 파일 수정 시

본 디렉토리(`/opt/bdp/data-copilot/deploy/systemd/`)의 원본을 수정한 뒤,
아래 순서로 반영합니다:

```bash
sudo cp /opt/bdp/data-copilot/deploy/systemd/data-copilot.service \
        /etc/systemd/system/data-copilot.service
sudo systemctl daemon-reload
sudo systemctl restart data-copilot
```
