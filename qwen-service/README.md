# Qwen 3.5 Chat Service (Docker / Ubuntu)

Serves **Qwen 3.5** behind an **OpenAI-compatible** endpoint using vLLM, on an
Ubuntu 24.04 base image. This is the LLM backend used by the WhatsApp chatbot
(`LLM_BASE_URL=http://<host>:8001/v1`).

## Endpoints exposed

| Endpoint | Purpose |
|---|---|
| `GET /v1/models` | list loaded model(s) |
| `POST /v1/chat/completions` | OpenAI-compatible chat (Qwen) |
| `POST /v1/completions` | raw completion |
| `POST /v1/embeddings` | embeddings (only if the model supports them) |
| `GET /health` | readiness probe |

Host port: **8001** (container port 8000). Map a different host port by editing
`docker-compose.yml` -> `ports`.

## Quick start (server)

GPU server:

```bash
cd qwen-service
cp .env.example .env          # set HF_TOKEN and MODEL_NAME
docker compose up -d --build
curl http://localhost:8001/health
```

CPU-only server (no NVIDIA GPU):

```bash
cd qwen-service
cp .env.example .env
# in .env set a small model, e.g. MODEL_NAME=Qwen/Qwen3.5-4B
docker compose -f docker-compose.yml -f docker-compose.cpu.yml up -d --build
curl http://localhost:8001/health
```

The model downloads on first start (cached in `./models`). Check `docker
compose logs -f qwen` for progress.

## GPU vs CPU

| | GPU (default) | CPU (`docker-compose.cpu.yml`) |
|---|---|---|
| Engine | `vllm` (CUDA) | `vllm-cpu` |
| Requires | NVIDIA GPU + nvidia-container-toolkit | any x86 server, more RAM |
| Speed | fast | slow (few tokens/s) |
| Model sizes | up to 397B | use ≤ 9B |

The image type is fixed at build time (`VLLM_DEVICE=gpu|cpu` build arg) and
baked into `/opt/vllm-device`; `entrypoint.sh` then starts the right engine and
fails with a clear message if a GPU build finds no GPU.

## Changing the model / settings (no rebuilds needed)

Everything below is an **environment variable** — edit `.env`, then
`docker compose up -d` again (no image rebuild):

| Variable | Default | Purpose |
|---|---|---|
| `HF_TOKEN` | – | Hugging Face access token (required, gated model) |
| `MODEL_NAME` | `Qwen/Qwen3.5-35B-A3B` | HF repo id to serve |
| `MAX_MODEL_LEN` | `32768` | context window in tokens |
| `GPU_MEMORY_UTILIZATION` | `0.90` | max GPU memory fraction (GPU only) |
| `QUANTIZATION` | – | e.g. `fp8`, `awq`, `gptq` (GPU only) |
| `SERVED_MODEL_NAME` | – | alias exposed by `/v1/models` |
| `VLLM_EXTRA_ARGS` | – | extra vLLM flags |
| `VLLM_CPU_KVCACHE_SPACE` | – | KV cache size in GB (CPU only) |

Example — switch to the smaller 9B dense model:

```bash
MODEL_NAME=Qwen/Qwen3.5-9B
MAX_MODEL_LEN=16384
docker compose up -d
```

Example — CPU with a small model:

```bash
MODEL_NAME=Qwen/Qwen3.5-4B MAX_MODEL_LEN=8192 \
  docker compose -f docker-compose.yml -f docker-compose.cpu.yml up -d --build
```

## Changing the exposed endpoint / port

Edit `qwen-service/docker-compose.yml`:

```yaml
ports:
  - "8001:8000"    # change "8001" to any free host port
```

Then update the chatbot's `LLM_BASE_URL` in the project root `.env`:

```
LLM_BASE_URL=http://<server-ip>:<new-port>/v1
LLM_API_KEY=EMPTY
LLM_MODEL=<the MODEL_NAME you set>
```

## Deploying behind HTTPS (production)

Expose the service to the Internet behind a reverse proxy (Caddy / nginx / Nginx
Proxy Manager):

```
https://llm.yourdomain.com -> http://127.0.0.1:8001
```

Then point the chatbot at `LLM_BASE_URL=https://llm.yourdomain.com/v1`.

Example nginx snippet:

```nginx
server {
    listen 443 ssl;
    server_name llm.yourdomain.com;
    ssl_certificate     /etc/letsencrypt/live/llm.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/llm.yourdomain.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

## GPU notes

- Requires `nvidia-container-toolkit` on the host (see `runDockerFile.txt`).
- VRAM planning (Qwen3.5-35B-A3B): ~24 GB with FP8 weights, ~20 GB model cache.
- No GPU? Use the CPU override (section above). Never set `VLLM_DEVICE` as a
  runtime env var — the image type is baked at build time.

## Operations

```bash
docker compose logs -f qwen      # watch model load / inference logs
docker compose restart qwen      # restart the service
docker compose down              # stop (keeps ./models cache)
docker compose down -v           # stop and drop model cache
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Triton is installed but 0 active driver(s) found` / `RuntimeError` at startup on a GPU build | no GPU visible to the container: reinstall nvidia-container-toolkit and verify with `docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi`, or rebuild for CPU with the cpu override |
| `401` / `gated model` during download | HF token missing or no access → check `HF_TOKEN` in `.env` |
| CUDA out of memory | lower `GPU_MEMORY_UTILIZATION` / `MAX_MODEL_LEN`, or use an FP8/Int4 variant |
| Slow generation | run on GPU; on CPU use a smaller `MODEL_NAME` (≤ 9B) |
| Model downloads every restart | ensure `./models` volume is mounted and on persistent disk |
