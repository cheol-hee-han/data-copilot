#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# install.sh — 폐쇄망 타겟 호스트에서 Data Copilot 설치
#
# 전제:
#   - 번들(data-copilot-bundle-YYYYMMDD.tar.gz)이 이미 해제되어
#     ./dist/ 디렉토리가 존재함
#   - 본 스크립트는 dist/의 상위 또는 내부 어디서 실행해도 무방
#   - root 권한 필요 (RPM 설치, /opt 배치, systemd 등록 대비)
#
# 수행 작업:
#   1) Python 3.12 확인 (없으면 번들 tarball로 설치 안내)
#   2) OS RPM 설치
#   3) 앱을 $INSTALL_ROOT 로 배치 (기본 /opt/bdp/data-copilot)
#   4) uv 로 .venv 오프라인 재현
#   5) 모델 가중치 복사
#   6) 서비스 계정/권한 설정
#   7) systemd 유닛 배치 ($INSTALL_ROOT 치환 후 /etc/systemd/system/)
#
# 환경변수 override:
#   INSTALL_ROOT                 앱 설치 경로 (기본: /opt/bdp/data-copilot)
#   SERVICE_USER                 서비스 계정 (기본: datacopilot)
#   EMBEDDING_MODEL_CACHE_PATH   모델 배치 경로 (기본: $INSTALL_ROOT/models)
#   SKIP_SYSTEMD                 1 로 설정 시 [7/7] 단계 건너뜀
#
# 단계별 수동 실행: docs/guides/closed-network-runbook.md §6-3 참조
# ──────────────────────────────────────────────────────────────
set -euo pipefail

# ── 실행 위치 자동 탐색 ────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# dist 디렉토리 후보: 현재 디렉토리, 스크립트의 상위 등
if [[ -d "./dist" ]]; then
    DIST_DIR="$(cd ./dist && pwd)"
elif [[ -d "$SCRIPT_DIR/dist" ]]; then
    DIST_DIR="$SCRIPT_DIR/dist"
elif [[ -d "$SCRIPT_DIR/../../dist" ]]; then
    DIST_DIR="$(cd "$SCRIPT_DIR/../../dist" && pwd)"
else
    echo "!! dist/ 디렉토리를 찾지 못했습니다. 번들 해제 위치에서 실행하세요." >&2
    exit 1
fi

INSTALL_ROOT="${INSTALL_ROOT:-/opt/bdp/data-copilot}"
SERVICE_USER="${SERVICE_USER:-datacopilot}"
MODEL_CACHE_PATH="${EMBEDDING_MODEL_CACHE_PATH:-$INSTALL_ROOT/models}"

echo "==> 설치 시작"
echo "   DIST_DIR       = $DIST_DIR"
echo "   INSTALL_ROOT   = $INSTALL_ROOT"
echo "   SERVICE_USER   = $SERVICE_USER"
echo "   MODEL_CACHE    = $MODEL_CACHE_PATH"

# ── 0. root 권한 체크 ─────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    echo "!! 본 스크립트는 root 권한으로 실행해야 합니다 (sudo bash $0)" >&2
    exit 1
fi

# ── 1. Python 3.12 확인 ──────────────────────────────────────
echo "==> [1/7] Python 3.12 확인"
if ! command -v python3.12 >/dev/null 2>&1; then
    echo "!! python3.12 미설치."
    echo "   번들의 $DIST_DIR/python/ 하위 tarball을 수동으로 설치 후 다시 실행하세요."
    echo "   (배포 정책상 자동 컴파일·설치는 수행하지 않습니다)"
    exit 1
fi
python3.12 --version

# ── 2. OS RPM 설치 ───────────────────────────────────────────
echo "==> [2/7] OS RPM 패키지 설치"
if [[ -d "$DIST_DIR/os-packages" ]] && ls "$DIST_DIR"/os-packages/*.rpm >/dev/null 2>&1; then
    # 로컬 RPM + 의존성 자동 해결 (로컬 저장소만 사용)
    dnf install -y --disablerepo='*' --nogpgcheck "$DIST_DIR"/os-packages/*.rpm || {
        echo "!! dnf 실패 — rpm -ivh 로 재시도"
        rpm -ivh --force --nodeps "$DIST_DIR"/os-packages/*.rpm
    }
else
    echo "   (RPM 없음 — 건너뜀)"
fi

# ── 3. 앱 배치 ────────────────────────────────────────────────
echo "==> [3/7] 앱을 $INSTALL_ROOT 로 배치"
mkdir -p "$INSTALL_ROOT"
rsync -a --delete \
    --exclude='.venv/' \
    "$DIST_DIR/app/" "$INSTALL_ROOT/"

# ── 4. uv 로 .venv 오프라인 재현 ─────────────────────────────
echo "==> [4/7] .venv 생성 + 오프라인 의존성 설치"
# uv 가 타겟에 설치돼 있지 않으면 wheels 중에서 uv wheel 을 우선 설치
if ! command -v uv >/dev/null 2>&1; then
    echo "   uv 미설치 — wheels에서 설치 시도"
    python3.12 -m venv /tmp/uv-bootstrap
    /tmp/uv-bootstrap/bin/pip install --no-index --find-links "$DIST_DIR/wheels" uv
    UV_BIN="/tmp/uv-bootstrap/bin/uv"
else
    UV_BIN="$(command -v uv)"
fi

cd "$INSTALL_ROOT"
"$UV_BIN" sync \
    --frozen \
    --offline \
    --python python3.12 \
    --find-links "$DIST_DIR/wheels"

# ── 5. 모델 가중치 복사 ───────────────────────────────────────
echo "==> [5/7] 임베딩/리랭커 모델 복사 → $MODEL_CACHE_PATH"
mkdir -p "$MODEL_CACHE_PATH"
rsync -a "$DIST_DIR/models/" "$MODEL_CACHE_PATH/"

# ── 6. 서비스 계정/권한 ──────────────────────────────────────
echo "==> [6/7] 서비스 계정 생성 및 권한 설정"
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --shell /sbin/nologin --home-dir "$INSTALL_ROOT" "$SERVICE_USER"
fi
chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_ROOT"
chown -R "$SERVICE_USER":"$SERVICE_USER" "$MODEL_CACHE_PATH"

# ── 7. systemd 유닛 배치 (INSTALL_ROOT 치환) ─────────────────
if [[ "${SKIP_SYSTEMD:-0}" == "1" ]]; then
    echo "==> [7/7] systemd 유닛 배치 — SKIP_SYSTEMD=1 로 건너뜀"
else
    echo "==> [7/7] systemd 유닛 배치 (/etc/systemd/system/data-copilot.service)"
    SRC_UNIT="$INSTALL_ROOT/deploy/systemd/data-copilot.service"
    DST_UNIT="/etc/systemd/system/data-copilot.service"
    if [[ ! -f "$SRC_UNIT" ]]; then
        echo "!! 원본 유닛 파일 부재: $SRC_UNIT — 수동 배치가 필요합니다." >&2
    else
        # /opt/bdp/data-copilot 하드코딩을 INSTALL_ROOT 로 치환하여 /etc 에 설치.
        # systemd unit 은 datacopilot 계정이 아닌 root 소유로 두는 것이 표준.
        sed "s|/opt/bdp/data-copilot|$INSTALL_ROOT|g" "$SRC_UNIT" \
            > "$DST_UNIT"
        chmod 644 "$DST_UNIT"
        chown root:root "$DST_UNIT"
        systemctl daemon-reload
        echo "  → 배치 완료. 활성화: sudo systemctl enable --now data-copilot"
    fi
fi

echo
echo "✓ 설치 완료"
echo "  INSTALL_ROOT = $INSTALL_ROOT"
echo "  다음 단계:"
echo "   1) $INSTALL_ROOT/.env 작성 (docs/guides/env-configuration-guide.md 참조)"
echo "   2) DB 초기화: $INSTALL_ROOT/deploy/db-init/{postgres,mongo,qdrant}/init.sh"
echo "                 $INSTALL_ROOT/resources/connectors/neo4j/init.sh"
echo "   3) 서비스 기동: sudo systemctl enable --now data-copilot"
