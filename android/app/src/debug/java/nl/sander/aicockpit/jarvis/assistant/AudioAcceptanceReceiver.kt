package nl.sander.aicockpit.jarvis.assistant

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import nl.sander.aicockpit.jarvis.live.AcceptanceAudioInput
import nl.sander.aicockpit.jarvis.live.AcceptanceAudioStream
import nl.sander.aicockpit.jarvis.live.AcceptancePlaybackRecording
import nl.sander.aicockpit.jarvis.audio.JarvisAudioService
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder

/** APK debug source set only. PCM is private, bounded and never supplies a transcript or pixels. */
class AudioAcceptanceReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        runCatching {
            intent.getStringExtra("recording_id")?.let {
                AcceptancePlaybackRecording.start(context, it)
                return
            }
            if (intent.getBooleanExtra("finish_recording", false)) {
                AcceptancePlaybackRecording.finish()
                return
            }
            if (intent.getBooleanExtra("activity", false)) {
                File(context.filesDir, "audio-acceptance/playback-activity.json")
                    .writeText(AcceptancePlaybackRecording.activity().toString())
                return
            }
            if (intent.getBooleanExtra("snapshot", false)) {
                check(JarvisVoiceInteractionSession.snapshotAcceptanceHealth()) { "No active Live session for health observation" }
                return
            }
            if (intent.getBooleanExtra("clear", false)) {
                AcceptanceAudioInput.clear()
                AcceptanceAudioStream.stop()
                return
            }
            if (intent.hasExtra("stream_port")) {
                // Live relay for a simulated user; the wake service starts on the relayed audio.
                AcceptanceAudioStream.start(context, intent.getIntExtra("stream_port", 0))
                context.startForegroundService(Intent(context, JarvisAudioService::class.java)
                    .setAction(nl.sander.aicockpit.jarvis.audio.AudioAction.WAKE.wire))
                return
            }
            val id = requireNotNull(intent.getStringExtra("sample_id"))
            require(id.matches(Regex("[a-zA-Z0-9_-]{1,80}")))
            val root = File(context.filesDir, "audio-acceptance").canonicalFile
            val file = File(root, "$id.pcm").canonicalFile
            require(file.parentFile == root && file.length() in 2..1_920_000 && file.length() % 2L == 0L)
            val bytes = file.readBytes()
            val view = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN).asShortBuffer()
            val pcm = ShortArray(view.remaining())
            view.get(pcm)
            AcceptanceAudioInput.install(context, id, pcm, intent.getIntExtra("command_end_sample", 0),
                intent.getIntExtra("command_start_sample", 0), intent.getBooleanExtra("ready_path", false))
            pcm.fill(0); bytes.fill(0)
            if (intent.getBooleanExtra("continuation", false)) {
                check(JarvisVoiceInteractionSession.continueAcceptanceAudio()) { "No ready Live activation for continuation" }
                return
            }
            // The foreground eligibility and microphone permission checks remain production checks.
            context.startForegroundService(Intent(context, JarvisAudioService::class.java)
                .setAction(nl.sander.aicockpit.jarvis.audio.AudioAction.WAKE.wire))
        }.onFailure { Log.e("JarvisAudioAcceptance", "Audio sample installation failed", it) }
    }
}
