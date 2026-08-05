# =====================================================================
# Qwen 3.5 chat service - Ubuntu base image
#
# Build option:
#   VLLM_DEVICE   "gpu" (default) -> pip install vllm        (CUDA/GPU)
#                 "cpu"           -> pip install vllm-cpu     (CPU only)
#                 example: docker compose build --build-arg VLLM_DEVICE=cpu
#
# The model is downloaded from Hugging Face on FIRST RUN (not at build
# time), so your HF token is never baked into the image layers.
# Provide it at runtime via the HUGGING_FACE_HUB_TOKEN environment
# variable (see .env.example / runDockerFile.txt).
# =====================================================================
ARG VLLM_DEVICE=gpu

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System packages: python3.12 (Ubuntu 24.04), curl for the healthcheck,
# libgomp1 (OpenMP, required by vLLM CPU), libnuma1 (NUMA, vLLM CPU),
# tcmalloc (CPU perf boost).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-venv \
        curl \
        git \
        build-essential \
        libgomp1 \
        libnuma1 \
    && (apt-get install -y --no-install-recommends libtcmalloc-minimal4t64 \
        || apt-get install -y --no-install-recommends libtcmalloc-minimal4 \
        || true) \
    && rm -rf /var/lib/apt/lists/*

# Isolated virtual environment so system python stays clean.
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --upgrade pip setuptools wheel

ARG VLLM_DEVICE
# vllm-cpu pins a torch build tagged "+cpu" (e.g. torch==2.11.0+cpu) that is
# only published on PyTorch's CPU index, so that index must be enabled too.
RUN if [ "$VLLM_DEVICE" = "cpu" ]; then \
        pip install --extra-index-url https://download.pytorch.org/whl/cpu vllm-cpu; \
    else \
        pip install vllm; \
    fi \
    # remember which build this image is, so entrypoint.sh can react
    && echo "$VLLM_DEVICE" > /opt/vllm-device

WORKDIR /app
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Hugging Face cache lives on a mounted volume so the model survives
# container restarts without re-downloading.
ENV HF_HOME=/models \
    HOST=0.0.0.0 \
    PORT=8000

EXPOSE 8000

# vLLM is slow to load a large model -> long start period.
HEALTHCHECK --interval=30s --timeout=5s --start-period=900s --retries=6 \
    CMD curl -fsS http://localhost:8000/health || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
