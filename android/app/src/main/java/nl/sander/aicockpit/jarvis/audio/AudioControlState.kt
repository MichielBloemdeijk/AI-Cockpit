package nl.sander.aicockpit.jarvis.audio

enum class AudioMode { PAUSED, WAKE_STARTING, WAITING, CONNECTING, LIVE, STOPPING, ERROR }
enum class AudioAction(val wire: String, val label: String) {
    LISTEN("listen_now", "Listen now"), STOP("stop_live", "Stop Live"), WAKE("hey_jarvis", "Hey Jarvis")
}

data class AudioControlState(
    val mode: AudioMode = AudioMode.PAUSED,
    val activationId: String? = null,
    val sessionId: String? = null,
    val generation: Int? = null,
    val error: String? = null,
) {
    val text: String get() = when (mode) {
        AudioMode.PAUSED -> "Microphone off"
        AudioMode.WAKE_STARTING -> "Starting wake listening…"
        AudioMode.WAITING -> "Waiting for ‘Hey Jarvis’"
        AudioMode.CONNECTING -> "Starting Live…"
        AudioMode.LIVE -> "Live conversation active"
        AudioMode.STOPPING -> "Stopping…"
        AudioMode.ERROR -> error ?: "Unable to connect"
    }
    val actions: List<AudioAction> get() = when (mode) {
        AudioMode.WAITING, AudioMode.WAKE_STARTING -> listOf(AudioAction.LISTEN)
        AudioMode.CONNECTING, AudioMode.LIVE -> listOf(AudioAction.STOP, AudioAction.WAKE)
        AudioMode.STOPPING -> emptyList()
        else -> listOf(AudioAction.LISTEN, AudioAction.WAKE)
    }
    val liveActive: Boolean get() = mode in setOf(AudioMode.CONNECTING, AudioMode.LIVE)
    fun matches(activation: String?, session: String?, version: Int?): Boolean =
        activationId == activation && sessionId == session && generation == version
}

/** Pure transition policy. The service alone performs audio side effects. */
class AudioControlMachine(var wakePreferred: Boolean) {
    var state = AudioControlState()
        private set
    var epoch = 0L
        private set
    fun listen(activation: String): Boolean {
        if (state.liveActive) return false
        epoch++
        state = AudioControlState(AudioMode.CONNECTING, activationId = activation)
        return true
    }
    fun idle(wake: Boolean) {
        epoch++
        wakePreferred = wake
        state = AudioControlState(if (wake) AudioMode.WAKE_STARTING else AudioMode.PAUSED)
    }
    fun waiting(token: Long) { if (token == epoch) state = AudioControlState(AudioMode.WAITING) }
    fun stopping(token: Long) { if (token == epoch) state = AudioControlState(AudioMode.STOPPING) }
    fun paused(token: Long) { if (token == epoch) state = AudioControlState(AudioMode.PAUSED) }
    fun accepted(token: Long, session: String, generation: Int) {
        if (token == epoch && state.liveActive) state = state.copy(sessionId = session, generation = generation)
    }
    fun ready(token: Long) { if (token == epoch && state.liveActive) state = state.copy(mode = AudioMode.LIVE) }
    fun fail(token: Long, message: String) {
        if (token == epoch) state = AudioControlState(AudioMode.ERROR, error = message)
    }
    fun permitsStop(activation: String?, session: String?, version: Int?): Boolean =
        state.liveActive && activation != null && activation == state.activationId &&
            (session == null || session == state.sessionId && version == state.generation)
}
