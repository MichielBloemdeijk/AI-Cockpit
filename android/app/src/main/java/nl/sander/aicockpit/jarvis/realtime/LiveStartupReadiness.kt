package nl.sander.aicockpit.jarvis.realtime

import nl.sander.aicockpit.jarvis.data.RealtimeSessionState

internal data class LiveStartupSnapshot(
    val sdpApplied: Boolean,
    val transportConnected: Boolean,
    val dataChannelOpen: Boolean,
    val backendReady: Boolean,
    val browserSessionStarted: Boolean,
    val failure: String?,
) {
    val ready: Boolean
        get() = failure == null && sdpApplied && transportConnected && dataChannelOpen && backendReady

    fun missing(): List<String> = buildList {
        if (!sdpApplied) add("SDP answer application")
        if (!transportConnected) add("WebRTC connected")
        if (!dataChannelOpen) add("oai-events open")
        if (!backendReady) add("backend sideband ready")
    }
}

/** Thread-safe startup contract shared by the WebRTC callbacks and backend observer. */
internal class LiveStartupReadiness(
    private val sessionId: String,
    private val generation: Int,
) {
    private var snapshot = LiveStartupSnapshot(false, false, false, false, false, null)

    @Synchronized fun markSdpApplied() = update(snapshot.copy(sdpApplied = true))

    @Synchronized fun observePeer(state: String) {
        when (state) {
            "CONNECTED" -> update(snapshot.copy(transportConnected = true))
            "FAILED", "CLOSED" -> failLocked("GPT-Live WebRTC connection ${state.lowercase()} during startup")
        }
    }

    @Synchronized fun observeIce(state: String) {
        if (state == "FAILED" || state == "CLOSED") {
            failLocked("GPT-Live ICE connection ${state.lowercase()} during startup")
        }
    }

    @Synchronized fun observeDataChannel(state: String) {
        when (state) {
            "OPEN" -> update(snapshot.copy(dataChannelOpen = true))
            "CLOSING", "CLOSED" -> failLocked("GPT-Live events channel ${state.lowercase()} during startup")
        }
    }

    @Synchronized fun observeBackend(state: RealtimeSessionState?) {
        if (state == null) return
        if (state.sessionId != sessionId || state.generation != generation) {
            failLocked("GPT-Live session was replaced during startup")
            return
        }
        when (state.lifecycleState) {
            "ready" -> update(snapshot.copy(backendReady = true))
            "disconnected", "failed" -> failLocked("GPT-Live backend sideband ${state.lifecycleState} during startup")
        }
    }

    @Synchronized fun markBrowserSessionStarted() =
        update(snapshot.copy(browserSessionStarted = true))

    @Synchronized fun fail(reason: String) = failLocked(reason)

    @Synchronized fun cancel() = failLocked("GPT-Live startup was cancelled")

    @Synchronized fun current(): LiveStartupSnapshot = snapshot

    private fun failLocked(message: String) {
        if (snapshot.failure != null) return
        update(snapshot.copy(failure = message))
    }

    private fun update(next: LiveStartupSnapshot) {
        snapshot = next
    }
}
