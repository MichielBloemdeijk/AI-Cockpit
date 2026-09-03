# Jarvis Wake Model Record

## Candidate

- Wake phrase: `jarvis`
- Candidate ID: `jarvis-en-personal-v1r-20260903`
- Status: installed; automated recall-first evaluation complete; manual browser and Android acceptance pending
- Intended runtime: foreground browser Worker through ONNX Runtime Web/WASM and Android ONNX Runtime
- Model input: one window of 16 speech-embedding frames by 96 features
- Activation threshold: `0.5`
- ONNX size: `1,031,919` bytes
- ONNX SHA-256: `ccf08b70f827ee33a5b55bad160bee796715929ede8390742d562d795575cf23`

## Source and environment

- Hey Buddy source: `https://github.com/painebenjamin/hey-buddy` at commit `6e78d26205da8b4c7c5b05be91dde3304e12b837`
- Trainer package version: `0.1.1`, installed editable from that commit
- Shared pretrained model repository revision: `68a13d58e216e8e4837559ae665ca3e9769b41c2`
- Python: `3.10.11`
- PyTorch: `2.13.0+cu130`
- ONNX: `1.22.0`
- ONNX Runtime GPU: `1.23.2`
- TorchCodec: `0.16.0+cu130`
- FFmpeg: `7.1.1`
- Training device: NVIDIA GeForce RTX 3080 Ti, 12 GiB, under Ubuntu/WSL

The upstream environment file currently resolves newer PyTorch/CUDA packages
than its older CUDA 12.4 declaration. CUDA execution, training, checkpointing,
ONNX conversion, ONNX validation, and inference were therefore smoke-tested
before this candidate pass. Current PyTorch also requires `onnxscript` for the
export step, and current Hugging Face audio decoding requires TorchCodec plus
FFmpeg; both were added only to the isolated training environment.

Hey Buddy's default 25,000-example feature batch is documented upstream as
requiring roughly 20 GiB. WSL had 15 GiB plus 4 GiB swap, so the isolated
source checkout used 12,500-example feature batches. This changes only how the
same requested sample count is prepared, not the model architecture or app.
The Hugging Face and temporary augmentation caches were redirected to a
task-specific directory on D: after the initial cache expanded the Ubuntu VHD
on C:. Both checkpoint sets, their logs, and the selected ONNX remain retained.

## Personal recall tuning

The installed candidate starts from the accepted phase-9 pass-1 checkpoint and
fine-tunes it on the product owner's browser calibration recordings. The two
private exports contain 64 unique recordings: 40 wake-word attempts across
normal, quiet, distant, and noisy conditions, plus 24 hard negatives. Four hard
negatives deliberately use the real word "Jarvis" conversationally; they are
excluded from keyword-model negative scoring because the downstream address
gate is responsible for rejecting them.

Recordings were stratified before training into 44 train, 10 validation, and 10
untouched test clips. Only the training split was augmented, using restrained
speed, pitch, gain, timing-shift, and additive-noise variations. Fine-tuning ran
for 200 CPU steps with AdamW at `5e-6`; checkpoint selection prioritized
normal-volume recall, followed by overall personal recall, while enforcing
false-positive ceilings on personal, phonetic-adversarial, and sampled generic
speech. Step 130 was selected. The existing runtime preprocessing and activation
threshold of `0.5` are unchanged.

At the locked threshold, the personal candidate changed the measurements as
follows:

- Untouched personal test recall: `66.67%` to `100%` (`4/6` to `6/6`).
- Untouched normal-volume recall: `66.67%` to `100%` (`2/3` to `3/3`).
- Untouched hard-negative false positives: unchanged at `0/4`.
- Synthetic Jarvis recall: `71.20%` to `80.68%`.
- Phonetic-adversarial false-positive rate: `0.66%` to `2.94%`.
- Sampled generic-speech false-positive rate: `0.025%` to `0.075%`.

The higher adversarial rate is an intentional recall-first tradeoff. The
personal test set is small, so these results justify installing a manual-test
candidate rather than claiming final device acceptance. The private source audio,
split manifest, and transcripts remain under ignored `.local/` storage; they are
not committed to Git.

## Training and evaluation data

The pass uses synthetic Piper speech plus the datasets listed below. Exact
repository revisions are recorded because the upstream repository cards carry
the authoritative component-level attribution and license details.

- Hey Buddy precalculated medium negative and validation sets:
  `benjamin-paine/hey-buddy` at `33f887ffbb4e2e3bdf80385c6472ff343c582b26`
- Free Music Archive commercial 16 kHz augmentation set:
  `benjamin-paine/free-music-archive-commercial-16khz-full` at `4fad7a587e80adca671184bd017d1d6ae8ea9a1d`
- Freesound/LAION commercial 16 kHz augmentation set:
  `benjamin-paine/freesound-laion-640k-commercial-16khz-full` at `c43a99517b038f1902bd2529e43ee72b3fc33688`
- MIT impulse-response 16 kHz augmentation set:
  `benjamin-paine/mit-impulse-response-survey-16khz` at `d771bf19c73f0009d50cca10fdd356e7c9ce601d`

The Hey Buddy documentation describes its source and pretrained assets as
Apache-2.0 and the aggregate training data as CC-BY 4.0 with component-specific
terms in the individual dataset cards. This record intentionally preserves the
component repositories and revisions instead of assigning a broader license to
the generated model.

## Pass 1 configuration

- 25,000 synthetic positive training utterances
- 25,000 phonetically adversarial training utterances across 250 phrases
- Official medium negative corpus (`training-medium.npy`, 21,741,703,232 bytes)
- Official precalculated validation corpus (`validation.npy`, 411,916,928 bytes)
- 5,000 synthetic positive validation utterances
- 5,000 synthetic positive test utterances
- 5,000 synthetic adversarial test utterances
- Default phrase, background/noise, impulse-response, equalization, distortion,
  pitch, band-stop, colored-noise, gain, and reverberation augmentations
- Perceptron architecture, layer dimension 96, two layers, gated MLP
- 2,000 initial steps and two stages; the second stage expands to 4,000 steps
- Validation every 250 steps, dynamic negative weighting, threshold `0.5`

Trainer invocation:

```text
heybuddy train jarvis --perceptron --training-medium-default-dataset
  --augmentation-dataset-no-streaming --positive-samples 25000
  --adversarial-samples 25000 --adversarial-phrases 250
  --validation-samples 5000 --testing-positive-samples 5000
  --testing-adversarial-samples 5000 --num-batch-threads 4
  --validation-num-batch-threads 1 --testing-num-batch-threads 1
  --steps 2000 --stages 2 --validation-steps 250
  --checkpoint-steps 2000 --logging-steps 10
```

## Pass 2 configuration

The bounded final tuning pass doubled the generated training data to 50,000
positive and 50,000 adversarial utterances, retained 250 adversarial phrases,
and used 3,000 initial steps plus a 6,000-step false-positive-tuned stage. The
validation and testing sets remained 5,000 synthetic positive examples each,
with 5,000 adversarial test examples and a target of 2.0 false activations per
hour. Its exact invocation is:

```text
heybuddy train jarvis --perceptron --training-medium-default-dataset
  --augmentation-dataset-no-streaming --positive-samples 50000
  --adversarial-samples 50000 --adversarial-phrases 250
  --positive-batch-size 100 --adversarial-batch-size 100
  --negative-batch-size 1000 --target-false-positive-rate 2.0
  --validation-samples 5000 --testing-positive-samples 5000
  --testing-adversarial-samples 5000 --num-batch-threads 4
  --validation-num-batch-threads 1 --testing-num-batch-threads 1
  --steps 3000 --stages 2 --validation-steps 250
  --checkpoint-steps 3000 --logging-steps 10
```

Hey Buddy replaced four non-finite speech embeddings in each of pass 2's four
bounded feature batches using its built-in random fallback (16 embeddings out
of 800,000 generated embeddings). Training, checkpoint validation, and final
model outputs remained finite.

## Automated results

Pass 1 finished with:

- Validation false activations: `2.0/hour`
- Validation recall: `98.14%`
- Synthetic test accuracy: `86.87%`
- Synthetic test recall: `75.20%`
- Phonetically adversarial test false-positive rate: `1.46%`

Pass 2's first stage reached `79.26%` synthetic test recall, but at `3.85`
validation false activations per hour. Its final tuned checkpoint finished at
`75.92%` test recall, `1.30%` adversarial false positives, and `2.575`
validation false activations per hour. The small `0.72` percentage-point recall
gain did not justify exceeding the locked `2.0/hour` false-activation bound, so
pass 1 remains the selected candidate.

A separately drawn direct checkpoint sweep produced `71.20%` recall and
`0.66%` adversarial false positives at threshold `0.5`; this expected sampling
spread in generated utterances is why the real-device worksheet below was the
original numeric acceptance gate for Phase 9. On 2026-08-30 the product
owner accepted the foreground POC from normal real-device use without reporting
the scripted counts. This explicit acceptance moves implementation forward while
keeping lower-volume consistency as a future tuning item.

The selected checkpoint was exported as a single-file opset-19 ONNX graph on
Windows to avoid the external tensor sidecar emitted by the current WSL
exporter. ONNX checker passed, CPU ONNX Runtime matched the PyTorch checkpoint
with maximum absolute error `1.3096723705530167e-09`, and the integrity-checking
installer reproduced the hash and byte size recorded above. The earlier smoke
model is no longer installed.

## Manual acceptance and future tuning worksheet

The previous phase-9 candidate was accepted for the foreground POC on
2026-08-30. The recall-first personal candidate installed on 2026-09-03 now
awaits the same browser and Android listening checks. The VAD plus
faster-whisper fallback remains unselected.

The v2 calibration wizard records normal, quiet, distant, noisy, and
hard-negative samples into browser-local IndexedDB. Samples can be reviewed,
exported as a versioned JSON/audio bundle for the isolated GPU training
environment, or deleted; ordinary background audio is never added. The same
installed ONNX artifact is consumed by the browser and Android companion.

The earlier worksheet was intentionally a foreground-PWA test; background phone
listening was not part of Phase 9. For the v2 calibrated run, record the device,
browser or Android build, and date alongside these results:

- Run 20 deliberate invocations, split between `Jarvis` alone and `Jarvis`
  immediately followed by a natural command. At least 18 must activate.
- Confirm the phrase-alone cases receive a natural acknowledgement and that the
  command cases preserve the words after the wake phrase.
- Play mixed speech/media for 30 minutes. No more than one false wake is allowed.
- Confirm activation does not visibly stall scrolling, typing, or navigation.
- Confirm explicit Sleep returns to wake-only listening after the live follow-up
  window, and that a browser-blocked auto-arm exposes a functional Arm button.
- Measure at least 95% normal-volume recall, 90% quiet/distant recall, no more
  than one false wake per evaluation hour, 95% address precision, and 90%
  address recall before replacing the selected candidate or marking v2 release
  acceptance complete.
