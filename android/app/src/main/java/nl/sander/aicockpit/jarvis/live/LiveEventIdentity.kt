package nl.sander.aicockpit.jarvis.live

import org.json.JSONObject

/** Global stream events may describe the session this activation replaced. */
object LiveEventIdentity {
    fun matches(event: JSONObject, sessionId: String?, generation: Int?): Boolean {
        if (sessionId == null || generation == null) return false
        val payload = event.optJSONObject("payload") ?: JSONObject()
        val sourceSession = if (event.optString("source_domain") == "session") event.optString("source_id")
            else payload.optString("session_id")
        return sourceSession == sessionId && (!payload.has("generation") || payload.optInt("generation") == generation)
    }
}
