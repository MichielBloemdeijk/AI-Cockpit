package nl.sander.aicockpit.jarvis.assistant

import org.json.JSONObject
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant

class ScreenCaptureLeaseTest {
    private val now = Instant.parse("2026-09-25T10:00:00Z")

    @Test fun exactLiveGenerationAndCaptureAreRequired() {
        val capture = ScreenCaptureManager.Capture(
            activationId = "activation-a", captureId = "capture-a", revision = 3,
            status = "captured", expiresAt = now.plusSeconds(90), capturedAt = now,
            callbackMonotonicMs = 123.0, jpeg = byteArrayOf(1, 2, 3),
            sessionId = "session-a", generation = 7,
        )
        val request = JSONObject()
            .put("activation_id", "activation-a")
            .put("capture_id", "capture-a")
            .put("generation", 7)
        assertTrue(capture.usableFor(request, now))
        assertFalse(capture.usableFor(request, now.plusSeconds(90)))
        assertFalse(capture.usableFor(JSONObject(request.toString()).put("capture_id", "capture-b"), now))
        assertFalse(capture.usableFor(JSONObject(request.toString()).put("generation", 8), now))
        assertFalse(capture.copy(status = "unavailable").usableFor(request, now))
        assertFalse(capture.copy(jpeg = null).usableFor(request, now))
    }
}
