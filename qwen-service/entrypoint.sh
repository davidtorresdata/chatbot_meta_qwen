#!/usr/bin/env bash
# =====================================================================
# Qwen 3.5 OpenAI-compatible endpoint launcher (vLLM).
#
# The image is built for either GPU (vllm) or CPU (vllm-cpu); the baked
# marker file /opt/vllm-device tells this script which one to use.
#
# Env knobs (no rebuild needed to change these):
#   MODEL_NAME             Hugging Face repo id (default Qwen/Qwen3.5-35B-A3B)
#   HOST / PORT            bind address / port (default 0.0.0.0 / 8000)
#   MAX_MODEL_LEN          context window in tokens (default 32768)
#   GPU_MEMORY_UTILIZATION max GPU memory to use 0.0-1.0 (GPU only)
#   QUANTIZATION           optional fp8/awq/gptq (GPU only)
#   SERVED_MODEL_NAME      optional alias exposed via /v1/models
#   VLLM_EXTRA_ARGS        extra vLLM flags, space separated
#   VLLM_CPU_KVCACHE_SPACE KV cache in GB (CPU only)
#   HUGGING_FACE_HUB_TOKEN HF access token (required, gated model)
# =====================================================================
set -euo pipefail

MODEL_NAME="${MODEL_NAME:-Qwen/Qwen3.5-35B-A3B}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-32768}"
GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.90}"
QUANTIZATION="${QUANTIZATION:-}"
SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-}"
# The image build type is authoritative (baked at build time).
VLLM_DEVICE_FILE="${VLLM_DEVICE_FILE:-/opt/vllm-device}"
VLLM_DEVICE="$(cat "$VLLM_DEVICE_FILE" 2>/dev/null || echo gpu)"

if [ -z "${HUGGING_FACE_HUB_TOKEN:-}" ]; then
  echo "WARNING: HUGGING_FACE_HUB_TOKEN is not set." >&2
  echo "Qwen3.5 is a gated model; the download will fail without it." >&2
fi

# ------------------------------------------------------------- device check
if [ "$VLLM_DEVICE" = "cpu" ]; then
  echo "Image built for CPU (vllm-cpu)."
  # tcmalloc gives a big speed-up on CPU; use it if present.
  LD_TCMALLOC=$(find /usr/lib /usr/lib/x86_64-linux-gnu \
    -name "libtcmalloc_minimal.so*" 2>/dev/null | head -n1 || true)
  if [ -n "$LD_TCMALLOC" ]; then
    export LD_PRELOAD="$LD_TCMALLOC${LD_PRELOAD:+:$LD_PRELOAD}"
    echo "LD_PRELOAD set to $LD_TCMALLOC"
  fi
else
  if python -c "import torch; raise SystemExit(0 if torch.cuda.is_available() else 1)" 2>/dev/null; then
    echo "CUDA GPU detected."
  else
    echo "ERROR: This image is the GPU build of vLLM, but no CUDA-capable" >&2
    echo "GPU/driver was found inside the container." >&2
    echo "" >&2
    echo "Fix ONE of the following:" >&2
    echo "" >&2
    echo "  A) The server HAS an NVIDIA GPU but it is not visible to Docker:" >&2
    echo "     sudo apt-get install -y nvidia-container-toolkit" >&2
    echo "     sudo nvidia-ctk runtime configure --runtime=docker" >&2
    echo "     sudo systemctl restart docker" >&2
    echo "     docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi" >&2
    echo "" >&2
    echo "  B) The server has NO NVIDIA GPU - rebuild for CPU (use a small model):" >&2
    echo "     docker compose -f docker-compose.yml -f docker-compose.cpu.yml up -d --build" >&2
    echo "     (see qwen-service/runDockerFile.txt, section 'CPU only')" >&2
    exit 1
  fi
fi

# --------------------------------------------------------------- vLLM args
args=(--model "$MODEL_NAME" \
      --host "$HOST" \
      --port "$PORT" \
      --max-model-len "$MAX_MODEL_LEN")

if [ "$VLLM_DEVICE" = "cpu" ]; then
  # The vllm-cpu build auto-selects the CPU backend (no --device flag).
  # On the CPU backend --gpu-memory-utilization controls the fraction of
  # system RAM reserved for the engine (its default 0.92 can OOM a host).
  args+=(--dtype bfloat16 --enforce-eager --gpu-memory-utilization "$GPU_MEMORY_UTILIZATION")
else
  args+=(--gpu-memory-utilization "$GPU_MEMORY_UTILIZATION")
  if [ -n "$QUANTIZATION" ]; then
    args+=(--quantization "$QUANTIZATION")
  fi
fi
if [ -n "$SERVED_MODEL_NAME" ]; then
  args+=(--served-model-name "$SERVED_MODEL_NAME")
fi
if [ -n "${VLLM_EXTRA_ARGS:-}" ]; then
  # shellcheck disable=SC2206
  read -r -a extra_args <<< "$VLLM_EXTRA_ARGS"
  args+=("${extra_args[@]}")
fi

echo "Starting Qwen service (device=$VLLM_DEVICE): ${args[*]}"
exec python -m vllm.entrypoints.openai.api_server "${args[@]}"
