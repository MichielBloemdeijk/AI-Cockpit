import { HeyBuddyOnnxInference } from "./jarvis-hey-buddy-inference";
import { JarvisWakeEngine } from "./jarvis-wake-engine";
import {
  JARVIS_WAKE_SAMPLE_RATE,
  type JarvisWakeWorkerCommand,
  type JarvisWakeWorkerEvent,
} from "./jarvis-wake-protocol";

interface WakeWorkerScope {
  onmessage: ((event: MessageEvent<JarvisWakeWorkerCommand>) => void) | null;
  postMessage: (event: JarvisWakeWorkerEvent, transfer?: Transferable[]) => void;
  close: () => void;
}

const scope = globalThis as unknown as WakeWorkerScope;
let engine: JarvisWakeEngine | null = null;
let operation = 0;

function emit(event: JarvisWakeWorkerEvent, transfer: Transferable[] = []): void {
  scope.postMessage(event, transfer);
}

scope.onmessage = (event) => {
  const command = event.data;
  if (command.type === "audio") {
    engine?.push(command.samples);
    return;
  }
  if (command.type === "pause") {
    engine?.pause();
    return;
  }
  if (command.type === "resume") {
    engine?.resume();
    return;
  }
  if (command.type === "destroy") {
    operation += 1;
    void engine?.destroy().finally(() => scope.close());
    engine = null;
    return;
  }

  const currentOperation = ++operation;
  void (async () => {
    await engine?.destroy();
    const inference = await HeyBuddyOnnxInference.create(command.configuration);
    if (currentOperation !== operation) {
      await inference.destroy();
      return;
    }
    engine = new JarvisWakeEngine({
      inference,
      wakeThreshold: command.configuration.wakeThreshold,
      candidateWakeThreshold: command.configuration.candidateWakeThreshold,
      candidateWakeHits: command.configuration.candidateWakeHits,
      speechLookbackMs: command.configuration.speechLookbackMs,
      candidateLookbackMs: command.configuration.candidateLookbackMs,
      positiveVadThreshold: command.configuration.positiveVadThreshold,
      negativeVadThreshold: command.configuration.negativeVadThreshold,
      negativeVadCount: command.configuration.negativeVadCount,
      onSpeechStart: (probability) => emit({
        type: "vad_boundary",
        state: "speech_started",
        probability,
      }),
      onSpeechEnd: (probability) => emit({
        type: "vad_boundary",
        state: "speech_stopped",
        probability,
      }),
      onDetected: (probability) => emit({ type: "detected", probability }),
      onUtterance: (samples) => emit(
        { type: "utterance", samples, sampleRate: JARVIS_WAKE_SAMPLE_RATE },
        [samples.buffer],
      ),
      onError: (cause) => emit({
        type: "error",
        message: cause instanceof Error ? cause.message : "Wake inference failed",
      }),
    });
    emit({ type: "ready" });
  })().catch((cause) => {
    if (currentOperation !== operation) return;
    emit({
      type: "error",
      message: cause instanceof Error ? cause.message : "Wake model failed to load",
    });
  });
};
