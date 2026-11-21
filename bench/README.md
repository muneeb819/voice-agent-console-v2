# Benchmarks

`speech_bench.py` measures the speech path **through the Gateway**, the way a call uses it:

| Measure | How |
| --- | --- |
| STT word error rate (WER) | Audio streamed in real time (20 ms frames) as 8 kHz G.711 u-law, transcripts compared with reference text after lower-casing and removing punctuation |
| STT end-of-speech → final | Time from the last speech frame sent to the first final transcript, using the engine's own endpointing (no client flush) |
| TTS first audio | Time from `speak` to the first audio byte |
| TTS real-time factor | Synthesis time ÷ audio duration (below 1.0 = faster than real time) |
| Concurrency | `--concurrency N` runs N parallel streams over the same items |

Every run saves `bench/results/<run_id>-<kind>.json` with the git commit, host, EC2 instance type, Gateway model list, arguments, every item and the summary. **Quote a number only with its run ID.** Results from fake engines (local stack, CI) prove the tool works and mean nothing else.

## Test sets

1. **Synthetic round trip** — `python bench/make_tts_testset.py` synthesises `sentences/call_center_en.txt` with our TTS. Good for latency and a TTS intelligibility check. It overstates accuracy: clean synthetic speech is far easier than a phone line.
2. **Real calls (the accuracy number we report)** — 50–200 short caller utterances from real calls, with written consent for this use and personal data removed. Put the files in a private location (not this repo), write a `manifest.jsonl` with one `{"audio": "file.wav", "text": "exact words"}` per line, and transcribe the references by hand. Mix accents, noisy lines and number-heavy answers (phone numbers, ages, dates).

Never commit caller audio to this repository.
