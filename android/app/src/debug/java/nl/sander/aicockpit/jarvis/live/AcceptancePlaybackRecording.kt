package nl.sander.aicockpit.jarvis.live

import android.content.Context
import android.content.pm.ApplicationInfo
import android.media.AudioTimestamp
import android.media.AudioTrack
import android.os.SystemClock
import org.json.JSONObject
import java.io.File
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.TimeUnit

/** Explicit debug recording only. The audio callback never writes to disk. */
object AcceptancePlaybackRecording {
    private data class Frame(val bytes: ByteArray, val at: Long, val rate: Int, val channels: Int,
        val format: Int, val head: Long?, val trackNs: Long?, val trackFrame: Long?, val stream: String = "output")
    private class Capture(val root: File, val id: String) {
        val queue = ArrayBlockingQueue<Frame>(2048)
        @Volatile var stopping = false
        @Volatile var overflow = false
        @Volatile var error: String? = null
        val started = System.currentTimeMillis()
        var count = 0L
        var inputs = 0L
        @Volatile var lastVoiced = 0L
        lateinit var thread: Thread
    }
    @Volatile private var capture: Capture? = null

    @Synchronized fun start(context: Context, id: String) {
        check(context.applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0)
        require(id.matches(Regex("[a-zA-Z0-9_-]{1,80}")))
        check(capture == null) { "Recording already active" }
        val root = File(context.filesDir, "audio-acceptance").canonicalFile.apply { mkdirs() }
        val value = Capture(root, id)
        value.thread = Thread({
            runCatching {
                File(root, "$id-output.pcm").outputStream().buffered().use { pcm ->
                    File(root, "$id-output.jsonl").bufferedWriter().use { events ->
                      File(root, "$id-input.pcm").outputStream().buffered().use { input ->
                       File(root, "$id-input.jsonl").bufferedWriter().use { inputEvents ->
                        var offset = 0L; var inputOffset = 0L
                        while (!value.stopping || value.queue.isNotEmpty()) {
                            val frame = value.queue.poll(100, TimeUnit.MILLISECONDS) ?: continue
                            val isInput = frame.stream == "input"
                            (if (isInput) input else pcm).write(frame.bytes)
                            (if (isInput) inputEvents else events).appendLine(JSONObject()
                                .put("offset", if (isInput) inputOffset else offset).put("bytes", frame.bytes.size)
                                .put("epoch_ms", frame.at).put("rate", frame.rate).put("channels", frame.channels)
                                .put("format", frame.format).put("playback_head", frame.head)
                                .put("track_timestamp_ns", frame.trackNs).put("track_frame_position", frame.trackFrame).toString())
                            if (isInput) { inputOffset += frame.bytes.size; value.inputs++ }
                            else { offset += frame.bytes.size; value.count++ }
                            frame.bytes.fill(0)
                        }
                       }
                      }
                    }
                }
            }.onFailure { value.error = it.javaClass.simpleName }
        }, "jarvis-eval-recording").apply { isDaemon = true; start() }
        capture = value
    }

    fun input(buffer: java.nio.ByteBuffer, count: Int, rate: Int, channels: Int, format: Int) {
        val value = capture ?: return
        if (value.stopping) return
        if (System.currentTimeMillis() - value.started > 180_000) { value.overflow = true; return }
        val data = ByteArray(count)
        buffer.duplicate().apply { position(0); get(data) }
        if (!value.queue.offer(Frame(data, System.currentTimeMillis(), rate, channels, format,
                null, null, null, "input"))) value.overflow = true
    }

    fun append(bytes: ByteArray, rate: Int, channels: Int, format: Int, module: Any) {
        val value = capture ?: return
        if (value.stopping) return
        if (System.currentTimeMillis() - value.started > 180_000) { value.overflow = true; return }
        if (format == android.media.AudioFormat.ENCODING_PCM_16BIT) {
            val pcm = java.nio.ByteBuffer.wrap(bytes).order(java.nio.ByteOrder.LITTLE_ENDIAN).asShortBuffer()
            while (pcm.hasRemaining()) {
                if (kotlin.math.abs(pcm.get().toInt()) > 200) { value.lastVoiced = System.currentTimeMillis(); break }
            }
        }
        var head: Long? = null; var stampNs: Long? = null; var stampFrame: Long? = null
        runCatching {
            val output = module.javaClass.getField("audioOutput").get(module)
            val field = output.javaClass.getDeclaredField("audioTrack").apply { isAccessible = true }
            val track = field.get(output) as AudioTrack
            head = track.playbackHeadPosition.toLong() and 0xffffffffL
            val timestamp = AudioTimestamp()
            if (track.getTimestamp(timestamp)) { stampNs = timestamp.nanoTime; stampFrame = timestamp.framePosition }
        }
        if (!value.queue.offer(Frame(bytes.copyOf(), System.currentTimeMillis(), rate, channels, format,
                head, stampNs, stampFrame))) value.overflow = true
    }

    @Synchronized fun finish() {
        val value = capture ?: return
        value.stopping = true
        value.thread.join(5000)
        val complete = !value.thread.isAlive && !value.overflow && value.error == null
        File(value.root, "${value.id}-recording.json").writeText(JSONObject()
            .put("started_epoch_ms", value.started).put("finished_epoch_ms", System.currentTimeMillis())
            .put("monotonic_ns", SystemClock.elapsedRealtimeNanos()).put("frames", value.count)
            .put("input_frames", value.inputs)
            .put("complete", complete).put("overflow", value.overflow).put("error", value.error).toString())
        capture = null
    }

    fun activity(): JSONObject = JSONObject().put("last_voiced_epoch_ms", capture?.lastVoiced ?: 0)
        .put("recording", capture != null).put("epoch_ms", System.currentTimeMillis())
}
