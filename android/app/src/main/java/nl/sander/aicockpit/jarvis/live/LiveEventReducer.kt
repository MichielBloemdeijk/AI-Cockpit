package nl.sander.aicockpit.jarvis.live

data class LiveEventState(val started: Boolean = false, val closed: Boolean = false, val listening: Boolean = false, val playing: Boolean = false, val usageSeconds: Double? = null)

/** Deterministic reduction of server events; Live speech interruption is server/VAD driven. */
fun reduceLiveEvent(state: LiveEventState, json: String): LiveEventState {
    val type = Regex("\\\"type\\\"\\s*:\\s*\\\"([^\\\"]+)").find(json)?.groupValues?.get(1) ?: return state
    val usage = Regex("\\\"(duration|audio_seconds|seconds)\\\"\\s*:\\s*([0-9.]+)").find(json)?.groupValues?.get(2)?.toDoubleOrNull()
    return when {
        type == "session.started" -> state.copy(started = true, listening = true)
        type == "session.closed" -> state.copy(closed = true, listening = false, playing = false, usageSeconds = usage ?: state.usageSeconds)
        type.contains("input_audio_buffer") || type.contains("user_speaking") -> state.copy(listening = true)
        type.contains("response.audio.done") || type.contains("assistant_stopped") -> state.copy(playing = false)
        type.contains("response.audio") || type.contains("assistant_speaking") -> state.copy(playing = true)
        else -> state.copy(usageSeconds = usage ?: state.usageSeconds)
    }
}
