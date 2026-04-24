#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# build.sh — 폐쇄망 반입용 오프라인 번들 생성 스크립트
#
# 전제: 빌드머신 == 타겟 환경 (Rocky Linux 9 x86_64, Python 3.12)
#   - WSL Rocky 9 / Rocky 9 서버 / Docker rockylinux:9 어디서든 동일.
#   - 빌드머신이 타겟과 동일하므로 pip 는 현재 인터프리터 기준으로
#     알맞은 manylinux wheel / sdist 를 자동 선택한다.
#     → --platform, --python-version, --abi, --only-binary 전부 불필요.
#
# 사전 조건:
#   - uv, huggingface-cli, dnf, curl, tar, sha256sum, rsync 사용 가능
#   - 기본 python3 가 3.12 (alternatives 로 전환)
#   - 인터넷 접속 (PyPI, HuggingFace, pytorch.org)
#
# 산출물:
#   deploy/offline-bundle/dist/
#   deploy/offline-bundle/data-copilot-bundle-YYYYMMDD.tar.gz
# ──────────────────────────────────────────────────────────────
set -euo pipefail

# ── 경로 ──────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DIST_DIR="$SCRIPT_DIR/dist"
BUNDLE_NAME="data-copilot-bundle-$(date +%Y%m%d)"
BUNDLE_TAR="$SCRIPT_DIR/${BUNDLE_NAME}.tar.gz"

# ── WSL DrvFs 경고 ────────────────────────────────────────────
# /mnt/c 등 Windows FS 는 chmod 가 제한되므로 /etc/wsl.conf 의
# [automount] options="metadata,uid=1000,gid=1000,..." 가 필수.
case "$PROJECT_ROOT" in
    /mnt/*)
        echo "!! [경고] WSL /mnt/ 하위에서 실행 중 — metadata 옵션 미활성 시 chmod 실패" >&2
        echo "          확인: mount | grep /mnt/c → 'metadata' 포함되어야 함" >&2
        ;;
esac

# ── sudo 헬퍼 (root 이면 그대로, 아니면 sudo) ────────────────
if [[ $EUID -eq 0 ]]; then
    SUDO=""
else
    SUDO="sudo"
    if ! command -v sudo >/dev/null 2>&1; then
        echo "!! sudo 미설치 & 비-root 실행 — 패키지 자동 설치 불가" >&2
        exit 1
    fi
fi

# ── 최소 시스템 도구 (dnf 로 자동 설치) ──────────────────────
echo "==> 시스템 도구 확인/설치 (dnf)"
NEED_PKGS=()
command -v curl       >/dev/null 2>&1 || NEED_PKGS+=(curl)
command -v tar        >/dev/null 2>&1 || NEED_PKGS+=(tar)
command -v sha256sum  >/dev/null 2>&1 || NEED_PKGS+=(coreutils)
command -v rsync      >/dev/null 2>&1 || NEED_PKGS+=(rsync)
command -v dnf        >/dev/null 2>&1 || { echo "!! dnf 없음 — Rocky/RHEL 9 환경 아님"; exit 1; }
command -v python3.12 >/dev/null 2>&1 || NEED_PKGS+=(python3.12 python3.12-pip)
rpm -q dnf-plugins-core >/dev/null 2>&1 || NEED_PKGS+=(dnf-plugins-core)

if (( ${#NEED_PKGS[@]} > 0 )); then
    echo "   설치 대상: ${NEED_PKGS[*]}"
    $SUDO dnf install -y "${NEED_PKGS[@]}"
fi

# ── python3 → python3.12 alternatives 전환 ───────────────────
PY_MM=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "0.0")
if [[ "$PY_MM" != "3.12" ]]; then
    echo "==> python3 를 3.12 로 전환 (alternatives)"
    $SUDO alternatives --install /usr/bin/python3 python3 /usr/bin/python3.12 1 2>/dev/null || true
    $SUDO alternatives --set python3 /usr/bin/python3.12
    PY_MM=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    if [[ "$PY_MM" != "3.12" ]]; then
        echo "!! python3 전환 실패 (현재: $PY_MM)" >&2
        exit 1
    fi
fi
python3 -m pip --version >/dev/null 2>&1 || python3 -m ensurepip --upgrade

# ── uv 자동 설치 ──────────────────────────────────────────────
if ! command -v uv >/dev/null 2>&1; then
    echo "==> uv 자동 설치"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    command -v uv >/dev/null 2>&1 || { echo "!! uv 설치 실패"; exit 1; }
fi

# ── huggingface-cli 자동 설치 (빌드머신 전용) ─────────────────
if ! command -v huggingface-cli >/dev/null 2>&1; then
    echo "==> huggingface-cli 자동 설치"
    python3 -m pip install --user -U "huggingface_hub[cli]"
    export PATH="$HOME/.local/bin:$PATH"
    command -v huggingface-cli >/dev/null 2>&1 || { echo "!! huggingface-cli 설치 실패"; exit 1; }
fi

# ── 폐쇄망 타겟용 Python tarball 버전 ─────────────────────────
# dnf 로 python3.12 이미 설치 가능하면 tarball 은 비상용.
PYTHON_VERSION="3.12.7"
PYTHON_TARBALL_URL="https://www.python.org/ftp/python/${PYTHON_VERSION}/Python-${PYTHON_VERSION}.tgz"

# ── 0. 초기화 ─────────────────────────────────────────────────
# 기본: 증분 실행 (이미 받은 wheel/model/RPM 재사용).
# 완전 초기화가 필요하면 CLEAN=1 로 실행: `CLEAN=1 bash build.sh`
#   - app/ 은 최신 소스 반영을 위해 항상 초기화.
if [[ "${CLEAN:-0}" == "1" ]]; then
    echo "==> [0/7] 전체 초기화 (CLEAN=1): $DIST_DIR"
    rm -rf "$DIST_DIR"
else
    echo "==> [0/7] 증분 실행 — 기존 $DIST_DIR 재사용 (전체 재빌드: CLEAN=1)"
    rm -rf "$DIST_DIR/app"
fi
mkdir -p "$DIST_DIR"/{python,wheels,models,os-packages,app}

# ── 1. Python tarball ─────────────────────────────────────────
PY_TGZ="$DIST_DIR/python/Python-${PYTHON_VERSION}.tgz"
if [[ -f "$PY_TGZ" && -s "$PY_TGZ" ]]; then
    echo "==> [1/7] Python tarball 캐시됨 — 스킵 ($(du -h "$PY_TGZ" | cut -f1))"
else
    echo "==> [1/7] Python ${PYTHON_VERSION} tarball 다운로드"
    curl -fL -o "$PY_TGZ" "$PYTHON_TARBALL_URL"
fi

# ── 2. Python wheels / sdists ─────────────────────────────────
# uv.lock 을 단일 진입점(pyproject.toml 에서 파생된 잠금 파일) 으로 사용.
# uv export 로 requirements.txt 생성 후 pip 가 현재 플랫폼 기준으로
# wheel 또는 sdist 를 자동 선택 (cbor/thrift 등 sdist-only 자연 처리).
echo "==> [2/7] 의존성 목록 생성 (uv export)"
uv export \
    --project "$PROJECT_ROOT" \
    --format requirements-txt \
    --no-hashes \
    --no-emit-project \
    > "$DIST_DIR/requirements.txt"

# wheels/ 에 전량 있으면 pip download 자체를 스킵 (PyPI 메타 조회 생략).
# requirements.txt 해시를 기록해두고 변경 없으면 스킵.
REQ_HASH_FILE="$DIST_DIR/wheels/.requirements.sha256"
REQ_HASH=$(sha256sum "$DIST_DIR/requirements.txt" | cut -d' ' -f1)
SKIP_WHEELS=0
if [[ -f "$REQ_HASH_FILE" && "$(cat "$REQ_HASH_FILE")" == "$REQ_HASH" ]]; then
    SKIP_WHEELS=1
fi

if (( SKIP_WHEELS == 1 )); then
    echo "==> [2/7] requirements.txt 변경 없음 — wheels 다운로드 스킵"
else
    echo "==> [2/7] 의존성 + torch CPU 다운로드"
    # --extra-index-url 로 torch CPU 인덱스를 PyPI 와 병행 조회.
    # pip 이 torch 는 pytorch.org, 나머지는 PyPI 에서 해소.
    # sdist 는 기본적으로 허용 → wheel 없을 때 자동 fallback.
    python3 -m pip download \
        --dest "$DIST_DIR/wheels" \
        --extra-index-url "https://download.pytorch.org/whl/cpu" \
        -r "$DIST_DIR/requirements.txt"

    # 타겟에서 sdist 로컬 빌드에 필요한 빌드 메타(wheel, setuptools) 가
    # 이미 requirements.txt 에 포함됐는지 보장 — 누락 시 명시 다운로드.
    for pkg in setuptools wheel pip; do
        ls "$DIST_DIR/wheels/${pkg}"-*.whl >/dev/null 2>&1 || {
            echo "   (보완) $pkg wheel 추가 다운로드"
            python3 -m pip download --dest "$DIST_DIR/wheels" --no-deps "$pkg"
        }
    done

    echo "$REQ_HASH" > "$REQ_HASH_FILE"
fi

# ── [2/7] 버전 정합성 검증 ────────────────────────────────────
# pyproject.toml → uv.lock 기반으로 고정된 버전과 wheels/ 디렉토리
# 내 실제 파일 버전이 일치하는지 확인 (보안 감사된 버전 유지 보증).
echo "==> [2/7] 버전 정합성 검증"
python3 - "$DIST_DIR/requirements.txt" "$DIST_DIR/wheels" <<'PYEOF'
import re
import sys
from pathlib import Path

try:
    from packaging.markers import Marker
    from packaging.version import Version
except ImportError:
    # packaging 은 pip 에 내장돼 있음 — 없으면 fallback
    from pip._vendor.packaging.markers import Marker  # type: ignore
    from pip._vendor.packaging.version import Version  # type: ignore

req_path = Path(sys.argv[1])
wheels_dir = Path(sys.argv[2])

# requirements.txt 파싱 (환경 마커 평가 — 타겟 플랫폼에 해당하지 않으면 제외)
pinned: dict[str, str] = {}
skipped_by_marker = 0
for line in req_path.read_text().splitlines():
    line = line.split("#", 1)[0].strip()
    if not line or line.startswith("-"):
        continue
    # "pkg==1.2.3 ; sys_platform == 'linux'"
    if ";" in line:
        spec, marker_str = line.split(";", 1)
        try:
            if not Marker(marker_str.strip()).evaluate():
                skipped_by_marker += 1
                continue
        except Exception:
            pass
    else:
        spec = line
    m = re.match(r"^([A-Za-z0-9_.\-]+)\s*==\s*([A-Za-z0-9_.\-+]+)", spec.strip())
    if m:
        name = re.sub(r"[-_.]+", "-", m.group(1)).lower()
        pinned[name] = m.group(2)

# wheels 디렉토리 파일명 파싱
actual: dict[str, set[str]] = {}
for f in wheels_dir.iterdir():
    name, ver = None, None
    if f.suffix == ".whl":
        parts = f.stem.split("-")
        if len(parts) >= 2:
            name, ver = parts[0], parts[1]
    elif f.name.endswith(".tar.gz"):
        stem = f.name[:-7]
        parts = stem.rsplit("-", 1)
        if len(parts) == 2:
            name, ver = parts
    if name:
        norm = re.sub(r"[-_.]+", "-", name).lower()
        actual.setdefault(norm, set()).add(ver)

def ver_matches(required: str, actuals: set[str]) -> bool:
    # local version identifier(+cpu, +cu118 등) 는 public version 과 동일로 간주
    req_base = Version(required).public
    for a in actuals:
        try:
            if Version(a).public == req_base:
                return True
        except Exception:
            if a == required:
                return True
    return False

missing, mismatch = [], []
for name, ver in pinned.items():
    if name not in actual:
        missing.append(f"{name}=={ver}")
    elif not ver_matches(ver, actual[name]):
        mismatch.append(f"{name}: 요구={ver}, 실제={sorted(actual[name])}")

print(f"   요구 버전: {len(pinned)}개 (마커로 스킵: {skipped_by_marker}개), wheels 파일: {len(actual)}개")
if missing:
    print(f"   [누락] {len(missing)}개:")
    for m in missing[:20]:
        print(f"     - {m}")
if mismatch:
    print(f"   [버전 불일치] {len(mismatch)}개:")
    for m in mismatch[:20]:
        print(f"     - {m}")

if missing or mismatch:
    print("!! 검증 실패 — pyproject.toml / uv.lock 과 wheels/ 불일치")
    sys.exit(1)
print("   ✓ 모든 고정 버전이 wheels/ 에 정확히 존재")
PYEOF

# ── 3. HuggingFace 모델 ───────────────────────────────────────
# 두 모델 + ONNX 가 모두 완결된 상태(핵심 파일 존재)면 스킵
model_ready() {
    local dir="$1"
    [[ -d "$dir" ]] && ls "$dir"/*.json >/dev/null 2>&1 && \
    { ls "$dir"/*.safetensors >/dev/null 2>&1 || ls "$dir"/*.bin >/dev/null 2>&1; }
}
onnx_ready() {
    [[ -f "$DIST_DIR/models/bge-reranker-v2-m3/onnx/model.onnx" ]]
}
if model_ready "$DIST_DIR/models/bge-m3" && model_ready "$DIST_DIR/models/bge-reranker-v2-m3" && onnx_ready; then
    echo "==> [3/7] 모델 + ONNX 캐시됨 — 스킵"
    du -sh "$DIST_DIR/models"/* 2>/dev/null || true
else
    echo "==> [3/7] 임베딩/리랭커 모델 다운로드 + ONNX 변환"
    bash "$SCRIPT_DIR/download_models.sh" "$DIST_DIR/models"
fi

# ── 4. OS 패키지 (RPM) ────────────────────────────────────────
echo "==> [4/7] OS RPM 패키지 다운로드 (Rocky 9)"

# -devel 패키지 다수(unixODBC-devel, openssl-devel 등)가 CRB 저장소에 있음.
# 기본 비활성화이므로 활성화.
if ! dnf repolist --enabled 2>/dev/null | grep -qiE '^crb\s'; then
    echo "   CRB(CodeReady Builder) 저장소 활성화"
    $SUDO dnf config-manager --set-enabled crb
fi

PACKAGES=$(grep -vE '^\s*(#|$)' "$SCRIPT_DIR/os-packages.txt" | tr '\n' ' ')
# 이미 다수 RPM 받아져 있으면 스킵 (완전 재수집은 CLEAN=1)
# ls 대신 find 사용 — 매칭 없을 때 ls 는 exit 2 → pipefail+set -e 로 스크립트 죽음.
RPM_COUNT=$(find "$DIST_DIR/os-packages" -maxdepth 1 -name '*.rpm' 2>/dev/null | wc -l)
if (( RPM_COUNT >= 10 )); then
    echo "   RPM 캐시됨 ($RPM_COUNT 개) — 스킵"
else
    $SUDO dnf download \
        --resolve \
        --alldeps \
        --destdir "$DIST_DIR/os-packages" \
        $PACKAGES || {
            echo "!! dnf download 실패 — 원인 후보:" >&2
            echo "   - dnf-plugins-core 미설치: sudo dnf install -y dnf-plugins-core" >&2
            echo "   - CRB 저장소 미활성: sudo dnf config-manager --set-enabled crb" >&2
            exit 1
        }
fi

# ── 5. 앱 소스 ────────────────────────────────────────────────
echo "==> [5/7] 앱 소스 복사"
rsync -a \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    --exclude='.venv/' \
    --exclude='logs/' \
    --exclude='models/' \
    --exclude='*.pptx' \
    "$PROJECT_ROOT/src/" "$DIST_DIR/app/src/"

rsync -a --exclude='__pycache__/' "$PROJECT_ROOT/static/"    "$DIST_DIR/app/static/"
rsync -a --exclude='__pycache__/' "$PROJECT_ROOT/resources/" "$DIST_DIR/app/resources/"

cp "$PROJECT_ROOT/pyproject.toml"   "$DIST_DIR/app/"
cp "$PROJECT_ROOT/uv.lock"          "$DIST_DIR/app/"
cp "$PROJECT_ROOT/gunicorn.conf.py" "$DIST_DIR/app/"
cp "$PROJECT_ROOT/langgraph.json"   "$DIST_DIR/app/" 2>/dev/null || true

rsync -a --exclude='dist/' --exclude='*.tar.gz' \
    "$PROJECT_ROOT/deploy/" "$DIST_DIR/app/deploy/"

# ── 6. MANIFEST ───────────────────────────────────────────────
echo "==> [6/7] MANIFEST.txt 생성"
(
    cd "$DIST_DIR"
    echo "# Data Copilot Offline Bundle — $(date -u +'%Y-%m-%dT%H:%M:%SZ')"
    echo "# Python: ${PYTHON_VERSION}"
    echo "# Build host: $(uname -srm)"
    echo
    echo "## Sizes"
    du -sh python wheels models os-packages app
    echo
    echo "## Wheel count"
    echo "wheels: $(ls -1 wheels | wc -l) files"
    echo
    echo "## SHA256 (주요 파일)"
    find python models -type f \( -name '*.tgz' -o -name '*.bin' -o -name '*.safetensors' \) \
        -exec sha256sum {} \; || true
) > "$DIST_DIR/MANIFEST.txt"

# ── 7. 최종 번들 ──────────────────────────────────────────────
echo "==> [7/7] 최종 번들 압축: $BUNDLE_TAR"
tar -czf "$BUNDLE_TAR" -C "$SCRIPT_DIR" dist

echo
echo "✓ 번들 생성 완료: $BUNDLE_TAR"
echo "  크기: $(du -sh "$BUNDLE_TAR" | cut -f1)"
echo "  SHA256: $(sha256sum "$BUNDLE_TAR" | cut -d' ' -f1)"
