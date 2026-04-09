"""Gunicorn 프로덕션 설정.

사용법::

    # gunicorn (프로덕션 권장)
    gunicorn src.main:app -c gunicorn.conf.py

    # uvicorn 단독 (gunicorn 미설치 시)
    uvicorn src.main:app --host 0.0.0.0 --port 8000

환경변수::

    GUNICORN_BIND       바인드 주소:포트       (기본: 0.0.0.0:8000)
    GUNICORN_WORKERS    워커 프로세스 수       (기본: 1)
    GUNICORN_TIMEOUT    워커 응답 타임아웃(초) (기본: 120)

[주의] workers > 1 사용 시 아래 사항을 반드시 확인:

  1. 임베딩 모델 메모리:
     BGE-M3(~570MB) + ONNX Reranker가 워커마다 중복 로드된다.
     workers=4 이면 추가 ~2.3GB 필요. 서버 가용 메모리를 확인할 것.
     해결: 임베딩 모델을 별도 서빙 프로세스로 분리 후 워커 수 확장.

  2. 로그 파일 충돌:
     TimedRotatingFileHandler의 lock은 프로세스 간 보호 불가.
     멀티워커 시 로그 파일이 깨질 수 있다.
     해결: LOG_FORMAT=json + stdout 출력 후 외부 logrotate 사용.

  3. DB 커넥션 풀:
     워커마다 풀이 생성된다. workers × db_pool_size ≤ DB max_connections 확인.
     기본: 1 × 5 = 5 커넥션. 워커 4개 시 20 커넥션.
"""

import os

# ── 환경변수에서 읽기 (폐쇄망 배포 시 .env로 튜닝) ──
bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8000")

# 임베딩 모델 메모리 이슈로 기본 1워커.
# asyncio 기반 동시 처리가 단일 워커에서도 충분한 처리량을 제공한다.
workers = int(os.environ.get("GUNICORN_WORKERS", "1"))

worker_class = "uvicorn.workers.UvicornWorker"
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "120"))
graceful_timeout = 30    # SIGTERM 후 진행 중 요청 완료 대기 시간
keepalive = 5            # Keep-Alive 유지 시간 (초)

accesslog = "-"          # stdout으로 접근 로그
errorlog = "-"           # stdout으로 에러 로그
