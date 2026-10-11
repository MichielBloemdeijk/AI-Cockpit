# M8 clearer-audio diagnostic — 2026-10-07

Current milestone status: **M8 completed with user-accepted caveats**, per the
[subsequent decision](m8-accepted-with-caveats-2026-10-07.md). The diagnostic's
test results and eligibility remain unchanged.

Both targeted admissions passed: screen recipe in **3.052 seconds**, notes plan
in **3.500 seconds**. M8 remained open at the time of this pilot. This diagnostic does not replace
the required complete acceptance cohorts or establish an audio reliability fix.
It follows the user's explicit request to generate clearer speech and retry.

Executable source remained frozen at
`8c066a1c7deb94d921056387ff8f3f4429a049b2e7202509ce8924f66b0a78a2`
(696 files). Only diagnostic artifacts and report documentation changed. The
[preceding report](m8-final-2026-10-07.md) retains the startup and missing-speech
failures. No failed samples were removed or converted into passes.

## Audio preparation and controls

All **37 offline audio-harness/model-diagnostic tests passed** in 27.44 seconds
before provider use. FFmpeg's soxr converter and prepared corpus validation also
passed locally. Three new recordings use `gpt-4o-mini-tts-2025-12-15`, Cedar,
speed 0.9, and instructions to enunciate the exact words in clear English.
OpenAI's [speech guide](https://developers.openai.com/api/docs/guides/text-to-speech)
recommends Cedar and Marin for quality. The original corpus already used OpenAI
speech generation; this is a different voice and preparation, not a switch from
non-AI recordings.

Original 24 kHz WAVs are retained. Band-limited soxr conversion produces mono
16 kHz signed 16-bit input with peaks normalized to -3 dBFS and no trimming or
clipped samples. The existing generator's simple averaging conversion was not
changed. Recipe RMS increased from -22.16 to -18.35 dBFS; notes RMS increased
from -19.72 to -18.03 dBFS. Actual command durations remain similar: 3.826 and
4.540 seconds. Do not attribute an improvement to substantially slower speech.

Three independent file-transcription checks used `gpt-4o-transcribe` without a
transcript prompt. All recognized every expected word: the original recipe,
the new recipe and the new notes request. Thus the original source recording
is intelligible to that transcriber. This does not prove that Android sends
intact processed speech, or that Live's recognition behaves identically.

The generated directory contains new wake/recipe/notes recordings and copied,
unchanged recordings for other fixture cases. Only ready02 and ready07 were
selected. It is explicitly a mixed diagnostic corpus, not a fully regenerated
acceptance corpus.

## Full admission observations

Intervals below use Android's single monotonic clock, from source speech end
to client receipt of committed admission; no server/client clock subtraction.

- Recipe: speech to delegation **1121.341 ms**; delegation to complete tool
  arguments **1557.821 ms**; tool arguments to receipt **373.188 ms**;
  total **3052.349 ms**.
- Notes: speech to delegation **2158.828 ms**; delegation to complete tool
  arguments **1079.262 ms**; tool arguments to receipt **261.789 ms**;
  total **3499.879 ms**.

Recipe Live transcript contains the complete request. The selector streamed
`start_workflow` with `recipe_from_screen@1`, activation screen context and the
correct screen-ingredients goal. Notes streamed `dispatch_task`, a new
`document.v1` result, normal importance and `follow_progress`, with the correct
notes-plan goal. Both produced durable admissions. Subsequent selector messages
confirmed the intended work; all four managed responses ended `completed`
without provider errors. Workers were not executed by this admission pilot.

The notes run still said “I'm listening.” during startup and its user transcript
began “Create a ta” followed by “- Draft a plan for a simple notes” and
“application”. The selector nevertheless selected the correct task. This
retains evidence of startup greeting/imperfect first-word recognition with the
new audio. Two successes do not resolve the earlier intermittent missing speech.

Provider-output recording completed with zero dropped events in both sessions
(102 and 142 events). Streamed `response.output_item.done` records contain the
tool calls/messages; terminal response envelopes have empty output arrays and
must not be mistaken for absent tool calls. Reasoning token counts are visible,
but no reasoning summaries were requested and private reasoning is unavailable.
Recorded traces are sanitized provider-exposed outputs, not proof of the exact
managed selector input. Diagnostics explicitly mark this pilot ineligible for
full acceptance; its incomplete-corpus exit is intentional.

## Usage and next step

This retry used **three TTS calls, three file-transcription calls and two Live
admission turns**. The latter reported four managed responses: 15,332 input
tokens (8,769 cached), 257 output tokens (62 reasoning), and 30 Live seconds.
File transcription reported 121 input and 41 output tokens. TTS usage and
monetary cost were not reported and remain unknown. No worker-model calls or
broad acceptance rerun occurred. Owned backend/emulator processes were stopped;
the follow-up process inventory found none remaining.

Keep these recordings for the next diagnostic; first inspect actual speech
after native wake buffering/audio processing, preferably through a local
decoded receiver. The strongest unresolved question is whether the request's
beginning reaches Live intact while startup playback occurs. Compare source and
processed recordings before changing turn sequencing or adding waits. Promote
the better conversion into the generator only after this control distinguishes
preparation from runtime effects, then freeze and validate the final corpus.
Complete current-source functional/capture acceptance remains required to close
M8; the existing gate review stays at three of eight passing assertions.

No desktop crash was observed during these two admission tests. The phone being
disconnected for this attempt is a useful observation, not evidence that remote
phone access caused the earlier crashes.

Artifacts under `data/m8-final-2026-10-07/`: `hq-offline.xml`,
`hq-audio-quality.json`, `hq-prepared-offline/`,
`hq-admission-pilot/audio/acceptance.json`,
`hq-admission-pilot/trace-summary.json`, `hq-admission-pilot/model-outputs/`,
and `all-attempt-usage.json`. Audio is under
`data/audio-corpus-hq-pilot-2026-10-07/`, including retained `original-24k/` WAVs.
