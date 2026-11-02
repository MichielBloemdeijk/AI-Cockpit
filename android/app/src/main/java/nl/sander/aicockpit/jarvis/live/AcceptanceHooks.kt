package nl.sander.aicockpit.jarvis.live

import android.content.Context
import android.content.pm.ApplicationInfo
import android.view.Window
import org.json.JSONObject
import java.nio.ByteBuffer

/**
 * The narrow seam the acceptance harnesses use on the real audio and capture path.
 *
 * Production code calls these hooks at fixed points (microphone frames, playback,
 * readiness, capture stages); the defaults do nothing. Only a debuggable build can
 * install an implementation (from `src/debug`), so a release build can never
 * replace microphone input, record playback or relay audio. Hooks run on audio
 * callbacks and must stay bounded: no file or network I/O on the caller's thread.
 */
interface AcceptanceHooks {
    /** True while a harness is running; gates acceptance-only work such as transport stats. */
    val active: Boolean get() = false
    /** Replace a wake-microphone frame in place with installed test audio. */
    fun replaceWakeFrame(frame: ShortArray, count: Int) = Unit
    /** Replace a WebRTC capture buffer in place with installed test audio. */
    fun replaceWebRtcFrame(buffer: ByteBuffer, audioFormat: Int, channels: Int, sampleRate: Int, byteCount: Int) = Unit
    /** The capture buffer as WebRTC will send it. */
    fun webRtcInput(buffer: ByteBuffer, byteCount: Int, sampleRate: Int, channels: Int, audioFormat: Int) = Unit
    /** Played assistant audio. */
    fun playback(data: ByteArray, sampleRate: Int, channels: Int, audioFormat: Int, audioModule: Any) = Unit
    fun ready(sessionId: String, generation: Int) = Unit
    /** Runtime health (frames, underruns, ANRs). Unknown counters stay absent, never zero. */
    fun health(context: Context, audioModule: Any, checkpoint: Boolean) = Unit
    fun rtp(bytesSent: Long, packetsSent: Long) = Unit
    fun capture(stage: String, captureId: String, detail: JSONObject = JSONObject()) = Unit
    fun diagnostic(stage: String, detail: JSONObject = JSONObject()) = Unit
    fun admission(output: JSONObject, event: JSONObject = JSONObject()) = Unit
    fun observeWindow(window: Window?) = Unit
    fun stopObservingWindow(window: Window?) = Unit
}

object Acceptance {
    @Volatile
    var hooks: AcceptanceHooks = object : AcceptanceHooks {}
        private set

    /** Debug harnesses only; a release (non-debuggable) app refuses. */
    fun install(context: Context, implementation: AcceptanceHooks) {
        check(context.applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0) { "Acceptance hooks are debug-only" }
        hooks = implementation
    }
}
