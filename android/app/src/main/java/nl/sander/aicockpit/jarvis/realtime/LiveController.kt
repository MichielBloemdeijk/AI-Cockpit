package nl.sander.aicockpit.jarvis.realtime

import android.content.Context
import nl.sander.aicockpit.jarvis.data.CockpitApi
import nl.sander.aicockpit.jarvis.data.RealtimeCall
import nl.sander.aicockpit.jarvis.data.VoiceEngine

/** Experimental GPT-Live transport. Media and the oai-events channel are WebRTC-owned. */
class LiveController(context: Context, api: CockpitApi) : AutoCloseable {
    private val delegate = RealtimeController(context, api)
    suspend fun connect(wakeScore: Float? = null, onState: (String) -> Unit = {}): RealtimeCall =
        delegate.connect(wakeScore, VoiceEngine.GPT_LIVE, onState)
    suspend fun disconnect() = delegate.disconnect()
    override fun close() = delegate.close()
}
