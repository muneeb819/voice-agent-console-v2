# AWS infrastructure handoff spec

**Owner of AWS:** Muneeb (see [ADR 0006](adr/0006-aws-owned-by-operator.md)). This file is the contract between the infrastructure you build and the services in this repo. If something here does not fit your account, change it in a pull request so both sides stay in sync.

Prices quoted in the Phase 0 report were us-east-1 on-demand on 2026-11-22; confirm before buying. Pick the region **closest to the ViciBox/carrier** to keep RTP latency low.

---

## 1. What to build first (Phases 1–4: one dev environment)

| # | Resource | Spec | Why |
| --- | --- | --- | --- |
| 1 | GPU vCPU quota | Request **"Running On-Demand G and VT instances"** ≥ 8 vCPU (dev), ≥ 48 vCPU (50-call prod later) | New accounts are often at 0; this is the longest lead time |
| 2 | VPC `mmavie-dev` | e.g. `10.40.0.0/16`, 2 AZs, private subnets for all services, public subnet only for ALB/NAT | No service has a public IP |
| 3 | GPU host `mmavie-dev-gpu-1` | **g6e.xlarge** (1× L40S 48 GB, 4 vCPU, 32 GiB), 200 GB gp3 root, private subnet | Runs Speech Engine + vLLM together for 1–10 test calls |
| 4 | CPU host `mmavie-dev-app-1` | c7i.xlarge (or t3.large to start), 50 GB gp3, private subnet | Gateway, Agent Runtime, Audio Engine, Model Registry |
| 5 | RDS PostgreSQL 16 `mmavie-dev` | db.t4g.medium, single-AZ for dev, encrypted, 7-day backups | Config, sessions, audit |
| 6 | S3 buckets | `mmavie-dev-models` (weights), `mmavie-dev-recordings` | SSE-KMS, versioning on, Block Public Access on, no public policies |
| 7 | Secrets Manager | Secrets listed in §5 | No secrets in images or files |
| 8 | SSM Session Manager | On both hosts | No inbound SSH needed |

Dev GPU cost control: stop `mmavie-dev-gpu-1` outside working hours (instance scheduler or a simple EventBridge rule).

**GPU host software:** Ubuntu 22.04 or AWS Deep Learning Base AMI (GPU, Ubuntu 22.04), NVIDIA driver ≥ 550 with CUDA 12.x, Docker Engine + NVIDIA Container Toolkit. Verify with `nvidia-smi` and `docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi`.

---

## 2. Ports and security groups

All rules are **inbound**. Nothing below is open to `0.0.0.0/0` except the ALB on 443.

| Security group | Attached to | Port / proto | Source | Purpose |
| --- | --- | --- | --- | --- |
| `sg-mmavie-alb` | ALB | 443/tcp | Operator IPs (or 0.0.0.0/0 with WAF) | Console V2 (Phase 6) |
| `sg-mmavie-app` | CPU host | 8000–8003/tcp | `sg-mmavie-app`, `sg-mmavie-alb` | Gateway 8000, Runtime 8001, Audio 8002, Registry 8003 (host ports in prod layout) |
| `sg-mmavie-app` | CPU host | 5062/udp (SIP), 9092/tcp (AudioSocket) | **ViciBox private/public IP /32 only** | Telephony edge (Phase 7) |
| `sg-mmavie-app` | CPU host | 20000–20999/udp | ViciBox IP /32 only | RTP media (2 ports per call, 25 calls per host) |
| `sg-mmavie-gpu` | GPU host | 8100/tcp (speech), 8200/tcp (vLLM) | `sg-mmavie-app` only | Inference, reached only through the Gateway |
| `sg-mmavie-db` | RDS | 5432/tcp | `sg-mmavie-app` only | PostgreSQL |
| `sg-mmavie-obs` | Prometheus/Grafana (later) | 9090, 3000/tcp | Admin IPs via SSM port-forward | Monitoring |

Outbound: allow 443 to package registries, Hugging Face (first model download only) and AWS endpoints via NAT or VPC endpoints. After models are mirrored to S3, Hugging Face access can be removed. **No outbound access to AI provider APIs is required.**

---

## 3. IAM

| Role | Attached to | Permissions |
| --- | --- | --- |
| `mmavie-dev-gpu` | GPU host profile | `s3:GetObject`/`ListBucket` on `mmavie-dev-models`; `secretsmanager:GetSecretValue` on `mmavie/dev/speech/*`, `mmavie/dev/llm/*`; SSM core; CloudWatch agent |
| `mmavie-dev-app` | CPU host profile | `secretsmanager:GetSecretValue` on `mmavie/dev/*`; `s3:PutObject`/`GetObject` on `mmavie-dev-recordings`; `s3:GetObject` on `mmavie-dev-models`; SSM core; CloudWatch agent |
| `mmavie-deployer` (optional) | Your CI/automation | ECR push, SSM SendCommand to the two hosts |

No role gets `*` on any service. KMS key policy allows only these roles to decrypt.

---

## 4. Container images and how each service runs

Images are built from this repo (`docker/service.Dockerfile`, `services/speech/Dockerfile`). Push to ECR repo names `mmavie/<service>`. Every service listens on container port 8000 and exposes `/healthz` (liveness) and `/readyz` (readiness; 503 until dependencies are up).

| Service | Host | Image / command | Host port | GPU |
| --- | --- | --- | --- | --- |
| Speech Engine | GPU | `services/speech` target `api` | 8100 | yes (`--gpus all`) |
| LLM Engine | GPU | `vllm/vllm-openai` pinned tag, see §6 | 8200 | yes |
| Gateway | CPU | `SERVICE_DIR=services/gateway MODULE=mmavie_gateway` | 8000 | no |
| Agent Runtime | CPU | `services/agent-runtime`, `mmavie_runtime` | 8001 | no |
| Audio Engine | CPU | `services/audio-engine`, `mmavie_audio` (+ UDP 5062, 20000–20999; TCP 9092 from Phase 7) | 8002 | no |
| Model Registry | CPU | `services/model-registry`, `mmavie_registry`, `EXTRA_PIP="psycopg[binary]==3.2.3"` | 8003 | no |
| Migrations | CPU (one-shot) | registry image, command `python db/migrate.py` | — | no |

`services/llm-fake` is **dev/CI only** and refuses to start when `MMAVIE_ENVIRONMENT` is staging or prod.

---

## 5. Secrets (AWS Secrets Manager)

Generate each as ≥ 40 random characters. Services refuse tokens shorter than 32 characters or starting with `dev-` in staging/prod.

| Secret name | Used by | Becomes env var |
| --- | --- | --- |
| `mmavie/dev/db/app` (username, password, host, dbname) | Registry, migrations, later Runtime/Console | `MMAVIE_DATABASE_URL` |
| `mmavie/dev/gateway/inbound-tokens` | Gateway | `MMAVIE_SERVICE_TOKENS` = `console:<t1>,runtime:<t2>` |
| `mmavie/dev/llm/api-key` | vLLM (`--api-key`) and Gateway | Gateway `LLM_UPSTREAM_TOKEN` |
| `mmavie/dev/speech/api-keys` | Speech Engine and Gateway | Speech `SPEECH_API_KEYS=gateway:<t>`; Gateway `SPEECH_UPSTREAM_TOKEN` |
| `mmavie/dev/runtime/inbound-tokens` | Agent Runtime | `MMAVIE_SERVICE_TOKENS` |
| `mmavie/dev/audio/inbound-tokens` | Audio Engine | `MMAVIE_SERVICE_TOKENS` |
| `mmavie/dev/registry/inbound-tokens` | Model Registry | `MMAVIE_SERVICE_TOKENS` |
| `mmavie/dev/speech/clone-token` | Speech + clone worker (Phase 5) | `SPEECH_CLONE_TOKEN` |

A small entrypoint (or your ECS task definition `secrets:` block) reads these at start; values never go into images, user data or logs.

---

## 6. Environment variables per service

Common to every MMA-VIE service: `MMAVIE_ENVIRONMENT` (`dev` | `staging` | `prod`), `MMAVIE_SERVICE_NAME`, `MMAVIE_LOG_LEVEL` (default `INFO`), `MMAVIE_SERVICE_TOKENS`.

| Service | Variables |
| --- | --- |
| Gateway | `MMAVIE_GATEWAY_ROUTES_FILE` (JSON route table; internal upstream URLs only), `MMAVIE_GATEWAY_PROBE_INTERVAL_S` (5), `MMAVIE_GATEWAY_UPSTREAM_TIMEOUT_S` (60), `LLM_UPSTREAM_TOKEN`, `SPEECH_UPSTREAM_TOKEN` |
| Agent Runtime | `MMAVIE_GATEWAY_URL` (e.g. `http://10.40.x.x:8000`) |
| Audio Engine | `MMAVIE_SIP_PORT` (5062), `MMAVIE_AUDIOSOCKET_PORT` (9092), `MMAVIE_RTP_PORT_MIN`/`MAX` (20000/20999), `MMAVIE_PBX_ALLOWED_CIDRS` (ViciBox /32), `MMAVIE_MAX_CALLS` (25) |
| Model Registry | `MMAVIE_DATABASE_URL` |
| Speech Engine | `SPEECH_BACKEND=real`, `SPEECH_API_KEYS`, `SPEECH_STT_MODEL=large-v3-turbo`, `SPEECH_STT_DEVICE=cuda`, `SPEECH_TTS_DEVICE=cuda`, `SPEECH_DATA_DIR=/data` (see `services/speech/.env.example`) |
| vLLM | Model and flags are set in Phase 3. Starting point: `--model <registry-pinned model> --revision <hash> --api-key <secret> --enable-auto-tool-choice --tool-call-parser <per model> --gpu-memory-utilization 0.85 --max-model-len 16384 --port 8000` |

Gateway route file for the dev environment (replace IPs with your private addresses):

```json
{"routes": [
  {"alias": "qwen3-voice", "kind": "llm", "upstream_model": "<served model name>",
   "upstreams": [{"url": "http://10.40.1.10:8200", "token_env": "LLM_UPSTREAM_TOKEN"}]},
  {"alias": "whisper-large-v3-turbo", "kind": "stt", "upstream_model": "large-v3-turbo",
   "upstreams": [{"url": "http://10.40.1.10:8100", "token_env": "SPEECH_UPSTREAM_TOKEN"}]},
  {"alias": "kokoro-82m", "kind": "tts", "upstream_model": "kokoro",
   "upstreams": [{"url": "http://10.40.1.10:8100", "token_env": "SPEECH_UPSTREAM_TOKEN"}]}
]}
```

---

## 7. Production shape (Phase 8, not now)

Per the approved plan: 2–3× g6e.xlarge (LLM), 2–3× g6.xlarge (speech), 2–3× c7i.xlarge (media/runtime/gateway) across 2 AZs, RDS Multi-AZ, ALB + WAF for the console, separate prod account or VPC, cross-region snapshot copies. Sizing is confirmed only by the 10/25/50-call load tests.

---

## 8. Handoff checklist — send these back when the dev environment is up

- [ ] Region and AZs used
- [ ] Private IPs (or internal DNS names) of the GPU and CPU hosts
- [ ] RDS endpoint, and that `mmavie/dev/db/app` exists
- [ ] All secret names from §5 created (names only, never values)
- [ ] `nvidia-smi` output from the GPU host (driver + CUDA version)
- [ ] ECR repositories created, or tell me to hand you images another way
- [ ] Confirmation that SIP/RTP ports are **not** open yet (they open in Phase 7)

With that, Phase 2 can deploy the real Speech Engine to the GPU host and record the first measured STT/TTS latencies.
