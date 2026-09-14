package nl.sander.aicockpit.jarvis.live

import android.media.AudioFormat
import kotlinx.coroutines.delay
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.ArrayDeque

internal class LiveActivationAudioBuffer(
    private val maxSamples: Int = SAMPLE_RATE * MAX_SECONDS,
    private val targetBacklogSamples: Int = SAMPLE_RATE * TARGET_BACKLOG_MS / 1_000,
    private val retainedSilenceSamples: Int = SAMPLE_RATE * RETAINED_SILENCE_MS / 1_000,
) {
    private val queued = ArrayDeque<Short>()
    private var activationId: String? = null
    private var releaseRequested = false
    private var recorderReleased = false
    private var claimed = false
    private var retainedSilenceInRun = 0
    var overflowed: Boolean = false
        private set

    @Synchronized
    fun begin(id: String, initial: ShortArray): Boolean {
        clearLocked()
        activationId = id
        return appendLocked(id, initial, initial.size)
    }

    @Synchronized
    fun append(id: String, samples: ShortArray, count: Int): Boolean =
        appendLocked(id, samples, count.coerceIn(0, samples.size))

    @Synchronized
    fun requestRecorderRelease(id: String): Boolean {
        if (activationId != id || overflowed) return false
        releaseRequested = true
        return true
    }

    @Synchronized fun shouldReleaseRecorder(id: String): Boolean =
        activationId == id && releaseRequested

    @Synchronized fun markRecorderReleased(id: String) {
        if (activationId == id) recorderReleased = true
    }

    @Synchronized fun isRecorderReleased(id: String): Boolean =
        activationId == id && recorderReleased

    @Synchronized
    fun claim(id: String): Boolean {
        if (activationId != id || !recorderReleased || overflowed) return false
        claimed = true
        return true
    }

    /**
     * While WebRTC is negotiating, retain its newly captured microphone audio
     * behind the wake buffer and replace the outbound frame with silence. This
     * prevents speech during setup from overtaking the activation audio.
     */
    @Synchronized
    fun holdPendingWebRtc(current: ShortArray): Boolean {
        if (activationId == null || !recorderReleased || claimed || overflowed || current.isEmpty()) return false
        if (queued.size + current.size > maxSamples) {
            overflowed = true
            queued.clear()
        } else {
            current.forEach(queued::addLast)
        }
        current.fill(0)
        return true
    }

    /**
     * Replaces the newly captured WebRTC frame with the oldest queued frame,
     * appending the new capture first. The queue therefore stays continuous
     * at one bounded offset without losing speech at cutover.
     */
    @Synchronized
    fun replaceWithQueued(current: ShortArray): Boolean {
        if (!claimed || overflowed || current.isEmpty()) return false
        current.forEach(queued::addLast)
        while (queued.size - current.size > targetBacklogSamples) {
            val inspectCount = minOf(current.size, queued.size - current.size)
            if (inspectCount <= 0 || !frontIsConfidentSilence(inspectCount)) {
                retainedSilenceInRun = 0
                break
            }
            if (retainedSilenceInRun < retainedSilenceSamples) break
            repeat(inspectCount) { queued.removeFirst() }
        }
        current.indices.forEach { current[it] = queued.removeFirst() }
        if (isConfidentSilence(current)) retainedSilenceInRun += current.size
        else retainedSilenceInRun = 0
        return true
    }

    @Synchronized fun clear(id: String? = null) {
        if (id == null || activationId == id) clearLocked()
    }

    @Synchronized fun queuedSamples(): Int = queued.size

    private fun appendLocked(id: String, samples: ShortArray, count: Int): Boolean {
        if (activationId != id || overflowed || claimed) return false
        if (queued.size + count > maxSamples) {
            overflowed = true
            queued.clear()
            return false
        }
        repeat(count) { queued.addLast(samples[it]) }
        return true
    }

    private fun clearLocked() {
        queued.clear()
        activationId = null
        releaseRequested = false
        recorderReleased = false
        claimed = false
        retainedSilenceInRun = 0
        overflowed = false
    }

    private fun frontIsConfidentSilence(count: Int): Boolean {
        var peak = 0
        var squareSum = 0.0
        val iterator = queued.iterator()
        repeat(count) {
            val value = iterator.next().toInt()
            peak = maxOf(peak, kotlin.math.abs(value))
            squareSum += value.toDouble() * value
        }
        return peak <= CONFIDENT_SILENCE_PEAK &&
            kotlin.math.sqrt(squareSum / count) <= CONFIDENT_SILENCE_RMS
    }

    private fun isConfidentSilence(samples: ShortArray): Boolean {
        var peak = 0
        var squareSum = 0.0
        samples.forEach { sample ->
            val value = sample.toInt()
            peak = maxOf(peak, kotlin.math.abs(value))
            squareSum += value.toDouble() * value
        }
        return peak <= CONFIDENT_SILENCE_PEAK &&
            kotlin.math.sqrt(squareSum / samples.size) <= CONFIDENT_SILENCE_RMS
    }

    companion object {
        const val SAMPLE_RATE = 16_000
        const val MAX_SECONDS = 30
        const val TARGET_BACKLOG_MS = 250
        const val RETAINED_SILENCE_MS = 500
        const val CONFIDENT_SILENCE_PEAK = 48
        const val CONFIDENT_SILENCE_RMS = 20.0
    }
}

object LiveActivationAudioHandoff {
    private val buffer = LiveActivationAudioBuffer()

    fun begin(id: String, initial: ShortArray): Boolean = buffer.begin(id, initial)
    fun append(id: String, samples: ShortArray, count: Int): Boolean = buffer.append(id, samples, count)
    fun shouldReleaseRecorder(id: String): Boolean = buffer.shouldReleaseRecorder(id)
    fun markRecorderReleased(id: String) = buffer.markRecorderReleased(id)

    suspend fun releaseRecorder(id: String, timeoutMs: Long = 1_500): Result<Unit> =
        runCatching {
            check(buffer.requestRecorderRelease(id)) { "Android Live activation audio is unavailable; please retry" }
            val deadline = android.os.SystemClock.elapsedRealtime() + timeoutMs
            while (!buffer.isRecorderReleased(id) && android.os.SystemClock.elapsedRealtime() < deadline) {
                delay(10)
            }
            check(buffer.isRecorderReleased(id)) { "Android Live activation audio handoff timed out; please retry" }
        }.onFailure {
            buffer.clear(id)
        }

    fun claim(id: String): Result<Unit> = runCatching {
        check(buffer.claim(id)) { "Android Live activation audio is unavailable; please retry" }
    }.onFailure { buffer.clear(id) }

    suspend fun releaseRecorderAndClaim(id: String, timeoutMs: Long = 1_500): Result<Unit> =
        releaseRecorder(id, timeoutMs).mapCatching { claim(id).getOrThrow() }

    fun processWebRtcBuffer(
        bytes: ByteBuffer,
        audioFormat: Int,
        channels: Int,
        sampleRate: Int,
        byteCount: Int,
    ) {
        if (
            audioFormat != AudioFormat.ENCODING_PCM_16BIT || channels != 1 ||
            sampleRate != LiveActivationAudioBuffer.SAMPLE_RATE || byteCount <= 0
        ) return
        val view = bytes.duplicate().order(ByteOrder.LITTLE_ENDIAN)
        view.position(0)
        val sampleCount = minOf(byteCount, view.capacity()) / 2
        val current = ShortArray(sampleCount) { view.getShort(it * 2) }
        if (buffer.holdPendingWebRtc(current)) {
            current.indices.forEach { view.putShort(it * 2, current[it]) }
            return
        }
        if (!buffer.replaceWithQueued(current)) return
        current.indices.forEach { view.putShort(it * 2, current[it]) }
        current.fill(0)
    }

    fun clear(id: String? = null) = buffer.clear(id)
}
