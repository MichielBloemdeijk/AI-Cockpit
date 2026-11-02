package nl.sander.aicockpit.jarvis.live

import android.content.Context
import android.view.Window
import org.json.JSONObject
import java.nio.ByteBuffer

/** The debug build's acceptance hooks: test-audio input, playback recording, relay and evidence logs. */
object DebugAcceptanceHooks : AcceptanceHooks {
    override val active: Boolean get() = true

    override fun replaceWakeFrame(frame: ShortArray, count: Int) = AcceptanceAudioInput.replaceWakeFrame(frame, count)

    override fun replaceWebRtcFrame(buffer: ByteBuffer, audioFormat: Int, channels: Int, sampleRate: Int, byteCount: Int) =
        AcceptanceAudioInput.replaceWebRtcFrame(buffer, audioFormat, channels, sampleRate, byteCount)

    override fun webRtcInput(buffer: ByteBuffer, byteCount: Int, sampleRate: Int, channels: Int, audioFormat: Int) =
        AcceptancePlaybackRecording.input(buffer, byteCount, sampleRate, channels, audioFormat)

    override fun playback(data: ByteArray, sampleRate: Int, channels: Int, audioFormat: Int, audioModule: Any) {
        AcceptanceAudioInput.observePlayback(data)
        AcceptancePlaybackRecording.append(data, sampleRate, channels, audioFormat, audioModule)
        AcceptanceAudioStream.output(data, sampleRate, channels)
    }

    override fun ready(sessionId: String, generation: Int) = AcceptanceAudioInput.observeReady(sessionId, generation)
    override fun health(context: Context, audioModule: Any, checkpoint: Boolean) =
        AcceptanceAudioInput.observeHealth(context, audioModule, checkpoint)
    override fun rtp(bytesSent: Long, packetsSent: Long) = AcceptanceAudioInput.observeRtp(bytesSent, packetsSent)
    override fun capture(stage: String, captureId: String, detail: JSONObject) = AcceptanceAudioInput.observeCapture(stage, captureId, detail)
    override fun diagnostic(stage: String, detail: JSONObject) = AcceptanceAudioInput.observeDiagnostic(stage, detail)
    override fun admission(output: JSONObject, event: JSONObject) = AcceptanceAudioInput.observeAdmission(output, event)
    override fun observeWindow(window: Window?) = AcceptanceAudioInput.observeWindow(window)
    override fun stopObservingWindow(window: Window?) = AcceptanceAudioInput.stopObservingWindow(window)
}
