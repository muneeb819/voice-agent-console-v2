# Runbook — Phase 2: deploy the Speech Engine on the dev GPU host and record the first baseline

**Who runs it:** Muneeb (AWS access) with engineering on a call, or engineering once given SSM access to the host.
**Prerequisites:** the dev environment and checklist from [infra-handoff.md](../infra-handoff.md) §1 and §8.
**Time:** about 2 hours, most of it model download and benchmark runs.
**Changes nothing in V1** and nothing outside the dev VPC.

## 1. Check the GPU host

```bash
nvidia-smi                                   # driver >= 550, CUDA 12.x, 1x L40S (or L4)
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
df -h /                                       # >= 100 GB free for images + models
```

## 2. Get the code and build the Speech Engine image

```bash
sudo mkdir -p /opt/mmavie && sudo chown "$USER" /opt/mmavie && cd /opt/mmavie
git clone https://github.com/muneeb819/voice-agent-console-v2.git && cd voice-agent-console-v2
git checkout <release tag or commit agreed for this run>
docker build -t mmavie/speech:$(git rev-parse --short HEAD) --target api services/speech
```

## 3. Pin and download the models (the only time the host talks to Hugging Face)

```bash
python3 -m venv /opt/mmavie/venv && . /opt/mmavie/venv/bin/activate
pip install httpx huggingface_hub
export HF_HOME=/opt/mmavie/models            # mounted into the container as /models
export REGISTRY_URL=http://<cpu-host-private-ip>:8003 REGISTRY_TOKEN=<from mmavie/dev/registry/inbound-tokens>
python scripts/register_models.py --only stt,tts --download
```

Record the two revision hashes it prints in the run notes. **Before production:** read each model card and confirm the licence; the catalog marks them unverified until a person does. In particular, faster-whisper's `large-v3-turbo` is a third-party CTranslate2 conversion (`mobiuslabsgmbh/faster-whisper-large-v3-turbo`) of OpenAI's MIT-licensed model.

## 4. Start the Speech Engine offline

```bash
docker run -d --name mmavie-speech --gpus all --restart unless-stopped \
  -p <gpu-private-ip>:8100:8000 \
  -v /opt/mmavie/models:/models -v mmavie-speech-data:/data \
  -e HF_HUB_OFFLINE=1 -e SPEECH_BACKEND=real \
  -e SPEECH_API_KEYS="gateway:<from mmavie/dev/speech/api-keys>" \
  -e SPEECH_STT_MODEL=large-v3-turbo -e SPEECH_STT_DEVICE=cuda -e SPEECH_STT_COMPUTE_TYPE=float16 \
  -e SPEECH_TTS_DEVICE=cuda \
  mmavie/speech:<sha>
curl -fsS http://<gpu-private-ip>:8100/readyz   # 503 while warming, then 200
```

`HF_HUB_OFFLINE=1` guarantees the engine loads only the pinned snapshot and never calls out. If `/readyz` stays 503, `docker logs mmavie-speech` shows which model failed to load.

## 5. Point the Gateway at it

On the CPU host, set the route file's `whisper-large-v3-turbo` and `kokoro-82m` upstream to `http://<gpu-private-ip>:8100` (see infra-handoff §6), restart the Gateway, and confirm:

```bash
curl -fsS -H "Authorization: Bearer <console→gateway token>" http://<cpu-private-ip>:8000/v1/models
# both speech aliases must show healthy_upstreams: 1
```

## 6. Record the baseline (from the CPU host, so the Gateway hop is included)

```bash
export BENCH_GATEWAY_URL=http://127.0.0.1:8000 BENCH_TOKEN=<console→gateway token>
python bench/make_tts_testset.py                                   # synthetic set, 25 sentences
python bench/speech_bench.py tts --sentences bench/sentences/call_center_en.txt --concurrency 1 --repeat 3 --label "g6e.xlarge kokoro"
python bench/speech_bench.py stt --manifest bench/testsets/tts-roundtrip/manifest.jsonl --concurrency 1 --repeat 2 --label "g6e.xlarge whisper turbo fp16"
for n in 4 10; do
  python bench/speech_bench.py stt --manifest bench/testsets/tts-roundtrip/manifest.jsonl --concurrency $n --label "g6e.xlarge c=$n"
  python bench/speech_bench.py tts --sentences bench/sentences/call_center_en.txt --concurrency $n --label "g6e.xlarge c=$n"
done
nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv   # capture during the c=10 runs
git add -f bench/results/*.json && git commit -m "bench: Phase 2 speech baseline" && git push
```

Then, with consented real call audio (see [bench/README.md](../../bench/README.md)), repeat the STT run on that manifest. That run is the accuracy number we quote.

## 7. Acceptance (from the approved roadmap)

- [ ] STT word error rate recorded on real consented call audio, with run ID
- [ ] STT end-of-speech → final latency p50/p95 recorded at concurrency 1, 4, 10
- [ ] TTS first-audio p95 ≤ 300 ms at concurrency 1
- [ ] Speech Engine running with `HF_HUB_OFFLINE=1`; no outbound calls during the runs
- [ ] Model revisions and licences recorded in the registry

## Rollback

`docker rm -f mmavie-speech`, restore the previous route file on the Gateway, restart it. Models and results stay on disk. Nothing else depends on this host in Phase 2.
