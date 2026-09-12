package nl.sander.aicockpit.jarvis.realtime

/** Pure lifecycle rules shared by the Android Live transport and deterministic tests. */
class LiveSessionPolicy(private val idleLimitMs: Long = 60_000L) {
    var listening: Boolean = false; private set
    var playing: Boolean = false; private set
    var pendingExchanges: Int = 0; private set
    private var lastActivityMs: Long = 0

    fun listeningChanged(value: Boolean, nowMs: Long) { listening = value; lastActivityMs = nowMs }
    fun playbackChanged(value: Boolean, nowMs: Long) { playing = value; lastActivityMs = nowMs }
    fun exchangeStarted(nowMs: Long) { pendingExchanges++; lastActivityMs = nowMs }
    fun exchangeFinished(nowMs: Long) { pendingExchanges = (pendingExchanges - 1).coerceAtLeast(0); lastActivityMs = nowMs }
    fun conversationalActivity(nowMs: Long) { lastActivityMs = nowMs }
    fun shouldClose(nowMs: Long): Boolean = nowMs - lastActivityMs >= idleLimitMs && pendingExchanges == 0 && !playing
}

data class ActivationAudioHandoff(val activationId: String, val samples: ShortArray, val replayed: Boolean)

/** WebRTC's microphone track cannot replay arbitrary PCM pre-roll; fail explicitly. */
fun boundedActivationHandoff(activationId: String, samples: ShortArray, canReplay: Boolean): Result<ActivationAudioHandoff> =
    if (!canReplay && samples.isNotEmpty()) Result.failure(UnsupportedOperationException("WebRTC capture cannot replay activation audio"))
    else Result.success(ActivationAudioHandoff(activationId, samples.copyOf(), samples.isNotEmpty()))
