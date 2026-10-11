package nl.sander.aicockpit.jarvis.assistant

import nl.sander.aicockpit.jarvis.data.LiveSessionState
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.time.Instant

class PhoneCaptureRequestTest {
    private val now = Instant.parse("2026-10-11T10:00:00Z")
    private fun event() = JSONObject().put("event_type", "capture.fresh_requested")
        .put("payload", JSONObject().put("capture_transport", "phone_browser").put("capture_mode", "fresh")
            .put("capture_id", "capture-1").put("activation_id", "activation-1")
            .put("session_id", "session-1").put("generation", 3).put("expires_at", now.plusSeconds(90).toString()))

    @Test fun captureRequiresCurrentPairedSessionAndUnexpiredIdentity() {
        val request = PhoneCaptureRequest.from(event())!!
        val current = LiveSessionState("session-1", 3, "ready", "phone-1", "activation-1")
        assertTrue(request.matches(current, "phone-1", now))
        assertFalse(request.matches(current.copy(generation = 4), "phone-1", now))
        assertFalse(request.matches(current.copy(activationId = "later"), "phone-1", now))
        assertFalse(request.matches(current.copy(lifecycleState = "disconnected"), "phone-1", now))
        assertFalse(request.matches(current, "other-phone", now))
        assertFalse(request.matches(current, "phone-1", now.plusSeconds(90)))
    }

    @Test fun nativeAndRedactedEventsCannotStartBrowserCapture() {
        val native = event().also { it.getJSONObject("payload").put("capture_transport", "native_voice") }
        assertNull(PhoneCaptureRequest.from(native))
        assertNull(PhoneCaptureRequest.from(JSONObject().put("event_type", "capture.fresh_requested")
            .put("payload", JSONObject().put("targeted", true))))
        assertNull(PhoneCaptureRequest.from(event().put("event_type", "capture.activation_requested")))
    }
}
