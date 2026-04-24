#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# download_models.sh — HuggingFace 임베딩/리랭커 모델 다운로드
#
# 사용법:
#   bash download_models.sh <DEST_DIR>
#
# 사전 조건:
#   - huggingface-cli 설치 (pip install -U "huggingface_hub[cli]")
#   - HuggingFace 접속 가능 (온라인)
#
# 산출물:
#   <DEST_DIR>/bge-m3/
#   <DEST_DIR>/bge-reranker-v2-m3/
# ──────────────────────────────────────────────────────────────
set -euo pipefail

DEST_DIR="${1:-./models}"
mkdir -p "$DEST_DIR"

# huggingface_hub >= 0.28 부터 `huggingface-cli` 가 deprecated 되고 `hf` 로 교체됨.
# 둘 중 사용 가능한 것을 자동 선택.
if command -v hf >/dev/null 2>&1; then
    HF_BIN="hf"
elif command -v huggingface-cli >/dev/null 2>&1; then
    HF_BIN="huggingface-cli"
else
    echo "!! hf / huggingface-cli 둘 다 미설치" >&2
    echo "   설치: pip install -U \"huggingface_hub[cli]\"" >&2
    exit 1
fi

# BAAI/bge-m3, BAAI/bge-reranker-v2-m3 는 public 모델이라 토큰 불필요.
# 레이트 리밋(403/429) 대비 타임아웃 여유.
export HF_HUB_DOWNLOAD_TIMEOUT=60

# 신구 CLI 인자 차이:
#   huggingface-cli download <repo> --local-dir <dir>
#   hf download <repo> --local-dir <dir>   (동일, --local-dir-use-symlinks 옵션 제거됨)
hf_download() {
    local repo="$1" dir="$2"
    echo "==> $repo → $dir"
    if [[ "$HF_BIN" == "hf" ]]; then
        "$HF_BIN" download "$repo" --local-dir "$dir"
    else
        "$HF_BIN" download "$repo" --local-dir "$dir" --local-dir-use-symlinks False
    fi
}

hf_download "BAAI/bge-m3"              "$DEST_DIR/bge-m3"
hf_download "BAAI/bge-reranker-v2-m3"  "$DEST_DIR/bge-reranker-v2-m3"

# ── Reranker ONNX 변환 (INT8 동적 양자화) ────────────────────
# 폐쇄망 타겟에서 첫 기동 시 PyTorch → ONNX 변환이 불필요하도록
# 빌드머신에서 미리 변환하여 번들에 포함한다.
ONNX_DIR="$DEST_DIR/bge-reranker-v2-m3/onnx"
ONNX_PATH="$ONNX_DIR/model.onnx"

if [[ -f "$ONNX_PATH" ]]; then
    echo "==> ONNX 모델 캐시됨 — 스킵 ($(du -h "$ONNX_PATH" | cut -f1))"
else
    echo "==> Reranker ONNX 변환 (INT8 양자화)"
    python3 -c "
import sys, os
from pathlib import Path

model_dir = sys.argv[1]
onnx_dir  = Path(sys.argv[2])
onnx_dir.mkdir(parents=True, exist_ok=True)

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

tokenizer = AutoTokenizer.from_pretrained(model_dir)
model = AutoModelForSequenceClassification.from_pretrained(model_dir)
model.eval()

dummy = tokenizer(
    [['query', 'document']],
    padding=True, truncation=True, max_length=512, return_tensors='pt',
)

input_names = ['input_ids', 'attention_mask']
dynamic_axes = {
    'input_ids': {0: 'batch', 1: 'seq'},
    'attention_mask': {0: 'batch', 1: 'seq'},
    'logits': {0: 'batch'},
}
inputs = (dummy['input_ids'], dummy['attention_mask'])

if 'token_type_ids' in dummy:
    input_names.append('token_type_ids')
    dynamic_axes['token_type_ids'] = {0: 'batch', 1: 'seq'}
    inputs = inputs + (dummy['token_type_ids'],)

raw_path = onnx_dir / 'model_raw.onnx'
final_path = onnx_dir / 'model.onnx'

os.environ['PYTHONIOENCODING'] = 'utf-8'

with torch.no_grad():
    torch.onnx.export(
        model, inputs, str(raw_path),
        input_names=input_names,
        output_names=['logits'],
        dynamic_axes=dynamic_axes,
        opset_version=18,
        do_constant_folding=True,
        dynamo=False,
    )

from onnxruntime.quantization import QuantType, quantize_dynamic
quantize_dynamic(str(raw_path), str(final_path), weight_type=QuantType.QInt8)
raw_path.unlink(missing_ok=True)

size_mb = final_path.stat().st_size / (1024 * 1024)
print(f'ONNX 변환 완료: {final_path} ({size_mb:.1f} MB)')
" "$DEST_DIR/bge-reranker-v2-m3" "$ONNX_DIR"
fi

echo "✓ 모델 다운로드 + ONNX 변환 완료"
du -sh "$DEST_DIR"/*
