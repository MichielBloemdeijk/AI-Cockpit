"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ArrowLeft, Download, Mic, Square, Trash2 } from "lucide-react";

import {
  clearCalibrationSamples,
  deleteCalibrationSample,
  listCalibrationSamples,
  saveCalibrationSample,
  type CalibrationCategory,
  type CalibrationSample,
} from "@/lib/jarvis-calibration-store";

const CATEGORIES: Array<{ id: CalibrationCategory; label: string; prompt: string }> = [
  { id: "normal", label: "Normal", prompt: "Say “Jarvis” in your usual voice." },
  { id: "quiet", label: "Quiet", prompt: "Say it quietly, as you would nearby at night." },
  { id: "distant", label: "Distant", prompt: "Move several metres away, then say “Jarvis”." },
  { id: "noisy", label: "Noisy", prompt: "Use realistic room noise and address Jarvis directly." },
  { id: "hard_negative", label: "Not addressed", prompt: "Say a phrase such as “I was talking about Jarvis”." },
];

async function blobToBase64(blob: Blob): Promise<string> {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  }
  return btoa(binary);
}

function CalibrationSampleRow({ sample, onDelete }: {
  sample: CalibrationSample;
  onDelete: () => void;
}) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  useEffect(() => {
    const next = URL.createObjectURL(sample.audio);
    if (audioRef.current) audioRef.current.src = next;
    return () => URL.revokeObjectURL(next);
  }, [sample.audio]);

  return (
    <div className="flex items-center gap-3 rounded-2xl border border-zinc-800 bg-zinc-900 p-3">
      <audio ref={audioRef} controls className="min-w-0 flex-1" />
      <span className="hidden text-xs capitalize text-zinc-500 sm:inline">{sample.category.replace("_", " ")}</span>
      <button type="button" aria-label="Delete sample" onClick={onDelete} className="flex h-12 w-12 items-center justify-center rounded-xl text-rose-300"><Trash2 size={17} /></button>
    </div>
  );
}

export default function WakeCalibrationPage() {
  const [category, setCategory] = useState<CalibrationCategory>("normal");
  const [samples, setSamples] = useState<CalibrationSample[]>([]);
  const [recording, setRecording] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  const refresh = () => void listCalibrationSamples().then(setSamples).catch((cause) => setError(String(cause)));
  useEffect(refresh, []);
  useEffect(() => () => streamRef.current?.getTracks().forEach((track) => track.stop()), []);

  const start = async () => {
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      streamRef.current = stream;
      recorderRef.current = recorder;
      chunksRef.current = [];
      recorder.ondataavailable = (event) => { if (event.data.size) chunksRef.current.push(event.data); };
      recorder.onstop = () => {
        const audio = new Blob(chunksRef.current, { type: recorder.mimeType || "audio/webm" });
        void saveCalibrationSample(category, audio)
          .then(refresh)
          .catch((cause) => setError(String(cause)))
          .finally(() => {
            chunksRef.current = [];
            recorderRef.current = null;
            streamRef.current = null;
            stream.getTracks().forEach((track) => track.stop());
            setRecording(false);
          });
      };
      recorder.start();
      setRecording(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to start microphone recording");
      setRecording(false);
    }
  };

  const stop = () => recorderRef.current?.stop();
  const exportSamples = async () => {
    const manifest = await Promise.all(samples.map(async (sample) => ({
      id: sample.id,
      category: sample.category,
      created_at: sample.createdAt,
      mime_type: sample.mimeType,
      audio_base64: await blobToBase64(sample.audio),
    })));
    const url = URL.createObjectURL(new Blob([JSON.stringify({ version: 1, samples: manifest }, null, 2)], { type: "application/json" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `jarvis-calibration-${Date.now()}.json`;
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  };

  const current = CATEGORIES.find((item) => item.id === category)!;
  return (
    <div className="flex h-full flex-col bg-zinc-950 text-zinc-100">
      <header className="flex min-h-14 items-center gap-3 border-b border-zinc-800 px-3">
        <Link href="/settings" className="flex h-12 w-12 items-center justify-center rounded-2xl text-zinc-400"><ArrowLeft size={20} /></Link>
        <div><h1 className="text-sm font-semibold">Wake calibration</h1><p className="text-xs text-zinc-500">Recordings stay on this device until you export them.</p></div>
      </header>
      <main className="mx-auto w-full max-w-2xl flex-1 overflow-y-auto p-4">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
          {CATEGORIES.map((item) => (
            <button key={item.id} type="button" onClick={() => setCategory(item.id)} className={`min-h-12 rounded-2xl border px-3 text-xs ${category === item.id ? "border-violet-500 bg-violet-500/10 text-violet-200" : "border-zinc-800 text-zinc-400"}`}>{item.label}</button>
          ))}
        </div>
        <section className="mt-5 rounded-3xl border border-zinc-800 bg-zinc-900 p-5 text-center">
          <p className="text-sm text-zinc-300">{current.prompt}</p>
          <button type="button" onClick={() => void (recording ? stop() : start())} className={`mx-auto mt-5 flex h-20 w-20 items-center justify-center rounded-full ${recording ? "bg-rose-600" : "bg-violet-600"}`}>
            {recording ? <Square size={25} /> : <Mic size={28} />}
          </button>
          <p className="mt-3 text-xs text-zinc-500">{recording ? "Recording — tap to stop" : "Tap to record one sample"}</p>
        </section>
        {error && <p className="mt-3 rounded-xl border border-rose-500/30 p-3 text-sm text-rose-200">{error}</p>}
        <div className="mt-5 flex gap-2">
          <button type="button" disabled={!samples.length} onClick={() => void exportSamples()} className="flex min-h-12 flex-1 items-center justify-center gap-2 rounded-2xl border border-zinc-700 text-sm disabled:opacity-40"><Download size={17} /> Export for retraining</button>
          <button type="button" disabled={!samples.length} onClick={() => void clearCalibrationSamples().then(refresh)} className="flex min-h-12 items-center justify-center rounded-2xl border border-zinc-700 px-4 text-rose-300 disabled:opacity-40"><Trash2 size={17} /></button>
        </div>
        <div className="mt-5 space-y-2">
          {samples.map((sample) => (
            <CalibrationSampleRow
              key={sample.id}
              sample={sample}
              onDelete={() => void deleteCalibrationSample(sample.id).then(refresh)}
            />
          ))}
        </div>
      </main>
    </div>
  );
}
