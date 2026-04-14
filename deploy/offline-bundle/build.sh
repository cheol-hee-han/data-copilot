#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# build.sh — 폐쇄망 반입용 오프라인 번들 생성 스크립트
#
# 빌드 머신(온라인 Linux, 폐쇄망과 동일 OS 계열)에서 실행한다.
# 산출물: deploy/offline-bundle/dist/ + data-copilot-bundle-YYYYMMDD.tar.gz
#
# 사전 조건:
#   - uv, huggingface-cli, dnf, curl, tar, sha256sum 사용 가능
#   - 인터넷 접속 (PyPI, HuggingFace, pytorch.org)
# ──────────────────────────────────────────────────────────────
set -euo pipefail

# ── 경로 설정 ──────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DIST_DIR="$SCRIPT_DIR/dist"
BUNDLE_NAME="data-copilot-bundle-$(date +%Y%m%d)"
BUNDLE_TAR="$SCRIPT_DIR/${BUNDLE_NAME}.tar.gz"

# ── Python 버전 (폐쇄망 타겟과 반드시 일치시킬 것) ─────────────
PYTHON_VERSION="3.12.7"
PYTHON_TARBALL_URL="https://www.python.org/ftp/python/${PYTHON_VERSION}/Python-${PYTHON_VERSION}.tgz"

# ── 0. 초기화 ─────────────────────────────────────────────────
echo "==> [0/7] 산출물 디렉토리 초기화: $DIST_DIR"
rm -rf "$DIST_DIR"
mkdir -p "$DIST_DIR"/{python,wheels,models,os-packages,app}

# ── 1. Python tarball 다운로드 ───────────────────────────────
echo "==> [1/7] Python ${PYTHON_VERSION} tarball 다운로드"
curl -fL -o "$DIST_DIR/python/Python-${PYTHON_VERSION}.tgz" "$PYTHON_TARBALL_URL"

# ── 2. Python wheels 다운로드 ────────────────────────────────
#    torch CPU wheel은 별도 인덱스에서 받아야 한다.
#    나머지는 uv.lock 기반으로 --find-links 가능한 wheel 전량 수집.
echo "==> [2/7] torch CPU wheel 다운로드"
uv pip download \
    --python-version "$PYTHON_VERSION" \
    --dest "$DIST_DIR/wheels" \
    --index-url "https://download.pytorch.org/whl/cpu" \
    torch torchvision

echo "==> [2/7] 프로젝트 의존성 wheel 다운로드 (uv.lock 기반)"
# uv.lock → requirements.txt 형태로 내보내 pip download 로 수집
uv export \
    --project "$PROJECT_ROOT" \
    --format requirements-txt \
    --no-hashes \
    --no-emit-project \
    > "$DIST_DIR/requirements.txt"

uv pip download \
    --python-version "$PYTHON_VERSION" \
    --dest "$DIST_DIR/wheels" \
    --find-links "$DIST_DIR/wheels" \
    -r "$DIST_DIR/requirements.txt"

# ── 3. HuggingFace 모델 다운로드 ─────────────────────────────
echo "==> [3/7] 임베딩/리랭커 모델 다운로드"
bash "$SCRIPT_DIR/download_models.sh" "$DIST_DIR/models"

# ── 4. OS 패키지(RPM) 다운로드 ────────────────────────────────
echo "==> [4/7] OS RPM 패키지 다운로드"
# os-packages.txt 에서 주석(#)·빈 줄 제외하고 패키지명만 추출
PACKAGES=$(grep -vE '^\s*(#|$)' "$SCRIPT_DIR/os-packages.txt" | tr '\n' ' ')
# --resolve 는 의존성까지 포함, --alldeps 는 배포판에 따라 옵션명 다름
dnf download \
    --resolve \
    --alldeps \
    --destdir "$DIST_DIR/os-packages" \
    $PACKAGES || {
        echo "!! dnf download 실패 — yum/dnf 권한 또는 저장소 확인 필요" >&2
        exit 1
    }

# ── 5. 앱 소스 복사 ───────────────────────────────────────────
echo "==> [5/7] 앱 소스 복사"
# 필수 파일/디렉토리만 선별 복사 (pptx, logs, tests 등 제외)
rsync -a \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    --exclude='.venv/' \
    --exclude='logs/' \
    --exclude='models/' \
    --exclude='*.pptx' \
    "$PROJECT_ROOT/src/" "$DIST_DIR/app/src/"

rsync -a --exclude='__pycache__/' \
    "$PROJECT_ROOT/static/" "$DIST_DIR/app/static/"

rsync -a --exclude='__pycache__/' \
    "$PROJECT_ROOT/resources/" "$DIST_DIR/app/resources/"

cp "$PROJECT_ROOT/pyproject.toml" "$DIST_DIR/app/"
cp "$PROJECT_ROOT/uv.lock"        "$DIST_DIR/app/"
cp "$PROJECT_ROOT/gunicorn.conf.py" "$DIST_DIR/app/"
cp "$PROJECT_ROOT/langgraph.json" "$DIST_DIR/app/" 2>/dev/null || true

# deploy/ 자체도 타겟에서 참조하므로 포함
rsync -a --exclude='dist/' --exclude='*.tar.gz' \
    "$PROJECT_ROOT/deploy/" "$DIST_DIR/app/deploy/"

# ── 6. MANIFEST 생성 ─────────────────────────────────────────
echo "==> [6/7] MANIFEST.txt 생성 (크기/해시)"
(
    cd "$DIST_DIR"
    echo "# Data Copilot Offline Bundle — $(date -u +'%Y-%m-%dT%H:%M:%SZ')"
    echo "# Python: ${PYTHON_VERSION}"
    echo
    echo "## Sizes (du -sh)"
    du -sh python wheels models os-packages app
    echo
    echo "## SHA256 (주요 파일)"
    find python models -type f \( -name '*.tgz' -o -name '*.bin' -o -name '*.safetensors' \) \
        -exec sha256sum {} \; || true
) > "$DIST_DIR/MANIFEST.txt"

# ── 7. 최종 tar.gz 묶기 ──────────────────────────────────────
echo "==> [7/7] 최종 번들 압축: $BUNDLE_TAR"
tar -czf "$BUNDLE_TAR" -C "$SCRIPT_DIR" dist

echo
echo "✓ 번들 생성 완료: $BUNDLE_TAR"
echo "  크기: $(du -sh "$BUNDLE_TAR" | cut -f1)"
echo "  SHA256: $(sha256sum "$BUNDLE_TAR" | cut -d' ' -f1)"
