"""Recorded autonomous announcement scenarios on the owned API37 emulator."""
from hashlib import sha256
import importlib.util
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import time
import wave

import httpx

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "nl.sander.aicockpit.jarvis"


def load_helper():
    spec = importlib.util.spec_from_file_location("screen_workflow", Path(__file__).with_name("verify-screen-workflow.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def render_recording(raw, frames, metadata, output, allowed_gaps=()):
    """Place callback PCM on the device clock; reject gaps or unknown playback."""
    if not frames:
        raise ValueError("No Android playback recording frames")
    rate, channels, form = frames[0]["rate"], frames[0]["channels"], frames[0]["format"]
    if channels not in (1, 2) or form != 2 or rate not in (16000, 24000, 44100, 48000):
        raise ValueError(f"Unsupported recorded Android PCM format: {rate} Hz, {channels} ch, format {form}")
    if channels == 2:
        # Playback may be stereo on some images; review only needs a mono timeline.
        import audioop
        raw = audioop.tomono(bytes(raw), 2, .5, .5)
        frames = [{**f, "bytes": f["bytes"] // 2, "offset": f["offset"] // 2, "channels": 1} for f in frames]
        channels = 1
    base = frames[0]["epoch_ms"] - frames[0]["bytes"] / 2 / rate * 1000
    total = round((frames[-1]["epoch_ms"] - base) / 1000 * rate)
    samples = bytearray(max(total * 2, 0))
    cursor = 0
    valid = metadata.get("complete") is True and metadata.get("frames") == len(frames)
    prior_time = frames[0]["epoch_ms"]
    heads = []
    for frame in frames:
        count = frame["bytes"]
        valid &= frame["offset"] == cursor and cursor + count <= len(raw)
        valid &= (frame["rate"], frame["channels"], frame["format"]) == (rate, channels, form)
        gap_known = any(start - 250 <= prior_time and frame["epoch_ms"] <= end + 250 for start, end in allowed_gaps)
        valid &= frame["epoch_ms"] >= prior_time and (frame["epoch_ms"] - prior_time < 250 or gap_known)
        end = round((frame["epoch_ms"] - base) / 1000 * rate) * 2
        start = max(0, end - count)
        samples[start:end] = raw[cursor:cursor + count][-max(0, end - start):]
        cursor += count; prior_time = frame["epoch_ms"]
        if frame.get("playback_head") is not None:
            heads.append(frame["playback_head"])
    valid &= cursor == len(raw)
    with wave.open(str(output), "wb") as audio:
        audio.setparams((1, 2, rate, 0, "NONE", "not compressed")); audio.writeframes(samples)
    return {"assistant_audio_epoch_ms": base, "recording_complete": bool(valid),
        "playback_verified": len(heads) > 1 and max(heads) > min(heads),
        "playback_frames": frames, "device_recording": metadata}


def run_adaptive(spec, root, helper, serial, broadcast, state, action, poll, evidence, seen_sessions):
    """Simulated user over a live audio relay; task completions follow the scenario schedule."""
    import random
    sys.path.insert(0, str(Path(__file__).parent))
    from android_caller_relay import Relay
    adaptive = spec["adaptive"]
    port = random.randint(47000, 48999)
    helper.adb(serial, "forward", f"tcp:{port}", f"tcp:{port}")
    broadcast("--ei", "stream_port", str(port))
    time.sleep(1.5)
    caller = subprocess.Popen([spec["python"], "-m", "app.selftest.caller"], cwd=spec["backend_directory"],
        env={**os.environ, "JARVIS_CALLER_AGENDA": json.dumps(adaptive)}, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=(root / "caller.log").open("w", encoding="utf-8"), text=True, encoding="utf-8")
    relay = Relay(port, caller, root)
    relay.start()
    try:
        with wave.open(spec["wake_audio"], "rb") as stream:
            relay.send_mic(stream.readframes(stream.getnframes()) + bytes(16000))
        ready = poll(lambda s: any(x["lifecycle_state"] == "ready" and x["id"] not in seen_sessions for x in s["sessions"]), 40)
        seen_sessions.update(x["id"] for x in ready["sessions"])
        evidence["markers"].append({"type": "adaptive.ready", "epoch_ms": time.time() * 1000})
        schedule = adaptive.get("tasks", [{"finish_after_seconds": 12}])
        admitted, done = {}, set()
        started = time.monotonic()
        while time.monotonic() - started < adaptive.get("max_seconds", 90):
            if relay.error:
                raise RuntimeError(relay.error)
            snapshot = state()
            for task in snapshot["tasks"][:len(schedule)]:
                if task["id"] not in admitted:
                    admitted[task["id"]] = time.monotonic(); evidence["target_task_ids"].append(task["id"])
                    evidence["markers"].append({"type": "adaptive.admission", "epoch_ms": time.time() * 1000})
            for index, task_id in enumerate(evidence["target_task_ids"]):
                plan = schedule[index]
                if task_id in done or time.monotonic() - admitted[task_id] < plan.get("finish_after_seconds", 12):
                    continue
                action(action="finish", task_id=task_id, status=plan.get("status", "completed"),
                    summary=plan.get("summary", "The Hello World page is ready."))
                evidence["markers"].append({"type": "completion", "epoch_ms": time.time() * 1000, "task_id": task_id})
                done.add(task_id)
            time.sleep(.2)
        if schedule and not done:
            raise RuntimeError("Simulated user did not get its intended task admitted")
    finally:
        relay.close()
        evidence["caller_finalized"] = relay.caller_closed.is_set()
        helper.adb(serial, "forward", "--remove", f"tcp:{port}")


def main():
    spec = json.loads(Path(os.environ["JARVIS_EVAL_SPEC"]).read_text(encoding="utf-8"))
    root = Path(os.environ["JARVIS_EVAL_ROOT"])
    helper = load_helper()
    serial = booted = None
    evidence = {"schema": "jarvis-announcement-evidence.v1", "transport": "android-webrtc",
        "case_id": spec["case"]["id"], "expectation": spec["case"].get("expectation"),
        "max_announcements": spec["case"].get("max_announcements"), "target_task_ids": [], "eligible_ms": {},
        "sessions": [], "markers": [], "input_understood": False, "clock_valid": False,
        "recording_complete": False, "playback_verified": False}
    spoken = []
    tail_remaining_ms = spec["case"].get("observation_tail_ms",0)
    gaps = []
    seen_sessions = set()
    headers = {"x-eval-token": os.environ["JARVIS_EVAL_TOKEN"]}
    with httpx.Client(base_url=spec["backend_url"], headers=headers, timeout=10, trust_env=False) as client:
        def state():
            result = client.get("/__eval/state"); result.raise_for_status(); return result.json()
        def action(**value):
            result = client.post("/__eval/action", json=value); result.raise_for_status(); return result.json()
        def poll(predicate, seconds=25):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                snapshot = state()
                if predicate(snapshot): return snapshot
                time.sleep(.2)
            raise TimeoutError("Android scenario condition was not reached")
        def broadcast(*args):
            return helper.adb(serial, "shell", "am", "broadcast", "-n", f"{PACKAGE}/.assistant.AudioAcceptanceReceiver", *args)
        def observe_reply_tail():
            nonlocal tail_remaining_ms
            if tail_remaining_ms<=0:return
            started=time.monotonic();exhausted=False
            while True:
                broadcast("--ez","activity","true")
                activity=json.loads(helper.adb(serial,"shell","run-as",PACKAGE,"cat","files/audio-acceptance/playback-activity.json"))
                if not activity["recording"]:raise ValueError("Playback recording stopped during observation")
                if time.time()*1000-activity["last_voiced_epoch_ms"]>=2000:break
                if (time.monotonic()-started)*1000>=tail_remaining_ms:
                    exhausted=True;break
                time.sleep(.1)
            elapsed=(time.monotonic()-started)*1000
            tail_remaining_ms=max(0,tail_remaining_ms-elapsed)
            if elapsed>100 or exhausted:evidence["markers"].append({"type":"observation.playback_tail","epoch_ms":time.time()*1000,"extended_ms":elapsed,"exhausted":exhausted})
            if exhausted:evidence["observation_tail_exhausted"]=True
        def audio(text, initial=False):
            filename = sha256(text.encode()).hexdigest()[:16] + "-command.wav"
            command = b""
            if text:
                with wave.open(str(Path(spec["corpus_directory"]) / filename), "rb") as stream:
                    command = stream.readframes(stream.getnframes())
            pcm = bytes(16000) + command + bytes(16000)
            start = 8000
            if initial:
                with wave.open(spec["wake_audio"], "rb") as stream:
                    wake = stream.readframes(stream.getnframes())
                pcm = wake + bytes(24000) + command + bytes(16000)
                start = len(wake) // 2 + 12000
            sample_id = f"notification-{len(spoken)}"
            path = root / f"{sample_id}.pcm"; path.write_bytes(pcm)
            staging = f"/data/local/tmp/{sample_id}.pcm"
            remote = f"files/audio-acceptance/{sample_id}.pcm"
            try:
                helper.adb(serial, "push", str(path), staging)
                helper.adb(serial, "shell", "run-as", PACKAGE, "cp", staging, remote)
            finally:
                helper.adb(serial, "shell", "rm", "-f", staging)
            if helper.adb(serial, "shell", "run-as", PACKAGE, "sha256sum", remote).split()[0] != sha256(pcm).hexdigest():
                raise ValueError("Android speech checksum mismatch")
            before = time.time() * 1000
            broadcast("--es", "sample_id", sample_id, "--ei", "command_start_sample", str(start),
                "--ei", "command_end_sample", str(start + len(command) // 2), "--ez", "ready_path", "true",
                "--ez", "continuation", str(not initial).lower())
            spoken.append({"text": text, "sample_id": sample_id, "epoch_ms": before,
                "duration_ms": len(pcm) / 32, "command_duration_ms": len(command) / 32})
            if initial:
                ready = poll(lambda s: any(session["lifecycle_state"] == "ready" and session["id"] not in seen_sessions for session in s["sessions"]), 40)
                seen_sessions.update(session["id"] for session in ready["sessions"])
                if gaps and gaps[-1][1] is None: gaps[-1][1] = time.time() * 1000
            return before + len(pcm) / 32
        try:
            serial, booted = helper.ensure_emulator()
            helper.adb(serial, "install", "-r", str(ROOT / "android/app/build/outputs/apk/debug/app-debug.apk"))
            for permission in ("android.permission.RECORD_AUDIO", "android.permission.POST_NOTIFICATIONS", "android.permission.ACCESS_LOCAL_NETWORK"):
                helper.adb(serial, "shell", "pm", "grant", PACKAGE, permission)
            helper.adb(serial, "shell", "cmd", "role", "add-role-holder", "android.app.role.ASSISTANT", PACKAGE)
            helper.adb(serial, "shell", "run-as", PACKAGE, "mkdir", "-p", "files/audio-acceptance")
            # Pair through the real owner-issued code and existing debug fixture;
            # it does not create a fake voice session or bypass task admission.
            client.post("/api/auth/login", json={"password": ""}).raise_for_status()  # owner session (personal mode)
            result = client.post("/api/jarvis/devices/pairing-code"); result.raise_for_status()
            helper.adb(serial, "shell", "am", "start", "-W", "-n", f"{PACKAGE}/.web.ResultFixtureActivity",
                "--es", "server_url", spec["backend_url"].replace("127.0.0.1", "10.0.2.2"),
                "--es", "pairing_code", result.json()["code"], "--ez", "audio_evaluation", "true",
                "--es", "path", "/results/00000000-0000-0000-0000-000000000000")
            time.sleep(2)
            broadcast("--es", "recording_id", "announcements")
            device_ms = float(helper.adb(serial, "shell", "date", "+%s%3N"))
            evidence["clock_valid"] = abs(device_ms - time.time() * 1000) < 250
            first, pending = True, None
            if spec.get("adaptive"):
                run_adaptive(spec, root, helper, serial, broadcast, state, action, poll, evidence, seen_sessions)
            for step in ([] if spec.get("adaptive") else spec["case"]["steps"]):
                kind = step["type"]
                if kind == "settle":
                    until=time.monotonic()+20
                    while time.monotonic()<until:
                        s=state(); active=set(); last_backend=0
                        for row in s["provider_events"]:
                            event=row["event"].get("event",{}) if row["event"].get("type")=="response.event" else {}
                            if event: last_backend=max(last_backend,row["epoch_ms"])
                            if event.get("type")=="response.created": active.add(event["response"]["id"])
                            if event.get("type") in {"response.completed","response.failed","response.cancelled"}: active.discard(event["response"]["id"])
                        broadcast("--ez","activity","true")
                        activity=json.loads(helper.adb(serial,"shell","run-as",PACKAGE,"cat","files/audio-acceptance/playback-activity.json"))
                        if activity["recording"] and not active and time.time()*1000-max(last_backend,activity["last_voiced_epoch_ms"])>=step.get("quiet_ms",3000): break
                        time.sleep(.2)
                    else: raise TimeoutError("Android acoustic admission setup did not settle")
                elif kind == "wait_input":
                    after=spoken[-1]["epoch_ms"]
                    poll(lambda s: re.search(step["pattern"],"".join(row["event"].get("delta","") for row in s["provider_events"]
                        if row["epoch_ms"]>=after and row["event"].get("type")=="session.input_transcript.delta"),re.I),12)
                elif kind == "at_caller_offset":
                    due=spoken[-1]["epoch_ms"]+step["seconds"]*1000
                    if time.time()*1000>due+250: raise ValueError("Android floor instruction missed frozen completion point")
                    time.sleep(max(0,(due-time.time()*1000)/1000))
                elif kind == "observation_end":
                    evidence["markers"].append({"type":"observation.nominal_end","epoch_ms":time.time()*1000})
                    observe_reply_tail()
                    evidence["observation_end_ms"]=time.time()*1000
                elif kind == "comparison": evidence["markers"].append({"type":"no_completion.comparison","epoch_ms":time.time()*1000})
                elif kind == "sync": action(action="sync")
                elif kind == "eligible": evidence["eligible_ms"].update({id:time.time()*1000 for id in evidence["target_task_ids"]})
                elif kind == "status_query":
                    evidence["status_query_start_ms"]=time.time()*1000
                    evidence["status_query_end_ms"]=audio(step["text"],initial=first)
                    first=False
                    time.sleep(max(0,(evidence["status_query_end_ms"]-time.time()*1000)/1000))
                elif kind in {"opening", "speak", "speak_async"}:
                    end = audio(spec["opening"] if kind == "opening" else step["text"], initial=first)
                    first = False
                    if kind == "speak_async": pending = end
                    else: time.sleep(max(0, (end - time.time() * 1000) / 1000))
                elif kind == "admit":
                    snapshot = poll(lambda s: len(s["tasks"]) >= step["count"])
                    evidence["target_task_ids"] = [t["id"] for t in snapshot["tasks"][:step["count"]]]
                elif kind == "wait": time.sleep(step["seconds"])
                elif kind == "join":
                    time.sleep(max(0, (pending - time.time() * 1000) / 1000))
                    evidence["eligible_ms"].update({id: pending for id in evidence["target_task_ids"]})
                    evidence["eligible_input_sample_id"] = spoken[-1]["sample_id"]
                elif kind == "finish":
                    id = evidence["target_task_ids"][step.get("index", 0)]
                    if spec["real_worker"]:
                        snapshot = poll(lambda s: any(t["id"] == id and t["finished_at"] is not None for t in s["tasks"]), 90)
                    else:
                        action(action="finish", task_id=id, status=step.get("status", "completed"), summary=step.get("summary", "The Hello World page is ready."))
                        snapshot = state()
                    evidence["eligible_ms"][id] = next(t["finished_at"] for t in snapshot["tasks"] if t["id"] == id)
                elif kind == "sync": action(action="sync")
                elif kind == "eligible":
                    evidence["eligible_ms"].update({id: time.time() * 1000 for id in evidence["target_task_ids"]})
                    evidence["eligible_input_sample_id"] = spoken[-1]["sample_id"]
                elif kind == "wait_output":
                    at = time.time() * 1000
                    poll(lambda s: any(e["epoch_ms"] >= at and e["event"]["type"] == "session.output_transcript.delta" for e in s["provider_events"]), 12)
                elif kind == "question": action(action="question", task_id=evidence["target_task_ids"][0])
                elif kind in {"observer_stop", "observer_restart"}: action(action=kind)
                elif kind == "disconnect":
                    gaps.append([time.time() * 1000, None])
                    helper.adb(serial, "shell", "am", "broadcast", "-n", f"{PACKAGE}/.assistant.CaptureProbeReceiver", "--es", "command", "hide")
                    poll(lambda s: all(x["lifecycle_state"] not in {"connecting","ready"} for x in s["sessions"]),10)
                    evidence["markers"].append({"type": "session.disconnected", "epoch_ms": time.time() * 1000})
                elif kind == "resume":
                    audio("", initial=True); first = False
                    evidence["markers"].append({"type": "session.resumed", "epoch_ms": time.time() * 1000})
                elif kind == "reconnect":
                    gaps.append([time.time() * 1000, None])
                    helper.adb(serial, "shell", "am", "broadcast", "-n", f"{PACKAGE}/.assistant.CaptureProbeReceiver", "--es", "command", "hide")
                    poll(lambda s: all(x["lifecycle_state"] not in {"connecting","ready"} for x in s["sessions"]),10)
                    time.sleep(1)
                    audio("",initial=True); first=False
                else: raise ValueError("Unsupported Android scenario step " + kind)
            observe_reply_tail()
            evidence["state"] = state()
            recognized = " ".join(e["event"].get("delta", "") for e in evidence["state"]["provider_events"] if e["event"]["type"] == "session.input_transcript.delta").lower()
            evidence["recognized_request"] = recognized
            evidence["input_understood"] = bool(spec.get("adaptive")) or ("hello" in recognized and "world" in recognized and bool(evidence["target_task_ids"]))
            if evidence["expectation"] == "forbidden": evidence["input_understood"] &= any(w in recognized for w in ("quiet", "silent", "silence"))
        except Exception as error:
            evidence["error"] = str(error)
        finally:
            if serial:
                try:
                    broadcast("--ez", "finish_recording", "true")
                    def pull(name):
                        return subprocess.run([str(helper.ADB), "-s", serial, "exec-out", "run-as", PACKAGE,
                            "cat", "files/audio-acceptance/" + name], capture_output=True, check=True, timeout=15).stdout
                    raw = pull("announcements-output.pcm")
                    frames = [json.loads(line) for line in pull("announcements-output.jsonl").decode().splitlines()]
                    metadata = json.loads(pull("announcements-recording.json"))
                    known_gaps = [pair for pair in gaps if pair[1] is not None]
                    evidence["controlled_reconnect_gaps"] = known_gaps
                    evidence.update(render_recording(raw, frames, metadata, root / "assistant.wav", known_gaps))
                    input_frames = [json.loads(line) for line in pull("announcements-input.jsonl").decode().splitlines()]
                    caller_recording = render_recording(pull("announcements-input.pcm"), input_frames,
                        {**metadata, "frames": metadata["input_frames"]}, root / "caller.wav", known_gaps)
                    evidence["caller_recording"] = caller_recording
                    evidence["recording_complete"] &= caller_recording["recording_complete"]
                    log = helper.adb(serial, "logcat", "-d", "-v", "brief", "JarvisAudioAcceptance:I", "*:S")
                    (root / "android-logcat.txt").write_text(log, encoding="utf-8")
                    observations = []
                    for line in log.splitlines():
                        if "{" in line:
                            try: observations.append(json.loads(line[line.index("{"):]))
                            except ValueError: pass
                    offset = metadata["finished_epoch_ms"] - metadata["monotonic_ns"] / 1e6
                    for phrase in spoken:
                        end = next((o.get("command_last_sample_ns") for o in observations
                            if o.get("event") == "command_last_input_sample" and o.get("sample_id") == phrase["sample_id"]), None)
                        if end is not None: phrase["verified_end_epoch_ms"] = offset + end / 1e6
                    evidence["caller_audio_windows"] = [(p["verified_end_epoch_ms"] - p["command_duration_ms"], p["verified_end_epoch_ms"])
                        for p in spoken if p.get("verified_end_epoch_ms") is not None]
                    eligible_sample = evidence.get("eligible_input_sample_id")
                    if eligible_sample:
                        end = next((p.get("verified_end_epoch_ms") for p in spoken if p["sample_id"] == eligible_sample), None)
                        if end is None: evidence["clock_valid"] = False
                        else:
                            for id in evidence["target_task_ids"]:
                                task = next(t for t in evidence["state"]["tasks"] if t["id"] == id)
                                evidence["eligible_ms"][id] = max(end, task["finished_at"] or 0)
                    broadcast("--ez", "clear", "true")
                    helper.adb(serial, "shell", "am", "broadcast", "-n", f"{PACKAGE}/.assistant.CaptureProbeReceiver", "--es", "command", "hide")
                except Exception as error: evidence["capture_error"] = str(error)
            try: evidence["state"] = state()
            except httpx.HTTPError: pass
            evidence["spoken_inputs"] = spoken
            (root / "evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
            helper.stop_tree(booted)
    return 2 if evidence.get("error") or evidence.get("capture_error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
