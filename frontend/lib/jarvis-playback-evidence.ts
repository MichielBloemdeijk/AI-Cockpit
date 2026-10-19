import { submitJarvisPlayback } from "./api";

/** Compact client playback evidence. No audio samples leave the player here. */
export class JarvisPlaybackEvidence {
  private timer: ReturnType<typeof setTimeout> | null = null;
  private active = false;
  private sequence = 0;
  private lower: number | null = null;
  private upper: number | null = null;
  private previous: { at: number; position: number; track: string; usable: boolean } | null = null;
  private lost = false;
  private sending: Promise<void> = Promise.resolve();
  private received: { samples: number; concealed: number; track: string } | null = null;
  private sampling = false;
  private input: { id: string; energy: number; duration: number } | null = null;

  constructor(private session: { id: string; generation: number }, private controller: string,
    private audio: () => HTMLAudioElement | null, requestStart: number,
    private send = submitJarvisPlayback, private stats?: () => Promise<RTCStatsReport>) {
    this.lower = requestStart;
  }

  started(at: number) {
    if (this.active) return;
    this.active = true;
    // The provider session began between request transmission and receipt of
    // session.started. Keep the entire range, not a guessed clock offset.
    this.upper = at;
    this.schedule();
  }

  private schedule() {
    // Schedule after sampling finishes. setInterval can issue a short catch-up
    // poll after a stall, reading the same cached RTC counters twice and creating
    // an artificial reception gap despite uninterrupted buffered playback.
    this.timer = setTimeout(async () => {
      this.timer = null;
      try { await this.sample(); }
      finally { if (this.active) this.schedule(); }
    }, 250);
  }

  async sample() {
    if (this.sampling) { this.lost = true; return; }
    this.sampling = true;
    try {
    const element = this.audio();
    if (!element) { this.previous = null; return; }
    const stream = element.srcObject as MediaStream | null;
    const tracks = stream?.getAudioTracks?.() ?? [];
    const at = performance.now();
    const position = element.currentTime * 1000;
    const track = tracks.map(t => t.id).join(",");
    let receivedAudio = false;
    let concealedFraction = 1;
    let inputEnergy: number | null = null;
    let inputDuration: number | null = null;
    let inputSource: string | null = null;
    if (this.stats) {
      try {
        const report = await this.stats();
        const sources: { id: string; energy: number; duration: number }[] = [];
        let samples = 0; let concealed = 0; let supported = false;
        report.forEach(value => {
          if (value.type === "media-source" && value.kind === "audio"
            && Number.isFinite(value.totalAudioEnergy) && Number.isFinite(value.totalSamplesDuration)) {
            sources.push({ id: value.id, energy: value.totalAudioEnergy, duration: value.totalSamplesDuration });
          }
          if (value.type === "inbound-rtp" && (value.kind === "audio" || value.mediaType === "audio")
            && typeof value.totalSamplesReceived === "number" && typeof value.concealedSamples === "number") {
            samples += value.totalSamplesReceived; concealed += value.concealedSamples; supported = true;
          }
        });
        const source = sources.length === 1 ? sources[0] : null;
        const priorInput = this.input;
        if (source && priorInput && source.id === priorInput.id && source.energy >= priorInput.energy
          && source.duration > priorInput.duration) {
          inputSource = source.id;
          inputEnergy = source.energy - priorInput.energy;
          inputDuration = (source.duration - priorInput.duration) * 1000;
        }
        this.input = source;
        const old = this.received;
        receivedAudio = supported && old !== null && old.track === track && samples > old.samples && concealed >= old.concealed;
        if (receivedAudio && old) concealedFraction = Math.min(1, (concealed - old.concealed) / (samples - old.samples));
        this.received = supported ? { samples, concealed, track } : null;
      } catch { this.received = null; this.input = null; }
    }
    const usable = !element.paused && !element.muted && element.volume > 0 && element.readyState >= 2
      && tracks.some(t => t.readyState === "live" && !t.muted);
    const prior = this.previous;
    this.previous = { at, position, track, usable };
    if (!prior || !track) return;
    const gap = this.lost || at - prior.at > 600 || prior.track !== track;
    this.lost = false;
    const payload = { generation: this.session.generation, controller_id: this.controller,
      sequence: ++this.sequence, reports: [{ track_id: track, client_start_ms: prior.at, client_end_ms: at,
        playback_start_ms: prior.position, playback_end_ms: position,
        origin_lower_ms: this.lower, origin_upper_ms: this.upper,
        mapping_supported: this.lower !== null && this.upper !== null && this.upper >= this.lower,
        paused: element.paused || !prior.usable, muted: element.muted, volume: element.volume,
        track_live: usable, received_audio: receivedAudio, concealed_fraction: concealedFraction, gap,
        input_source_id: inputSource, input_audio_energy: inputEnergy, input_audio_duration_ms: inputDuration }] };
    this.sending = this.sending.then(async () => {
      try { await this.send(this.session.id, payload); }
      catch { this.lost = true; }
    });
    } finally { this.sampling = false; }
  }

  stop() {
    this.active = false;
    void this.sample();
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
  }
}
