package nl.sander.aicockpit.jarvis.live

import org.json.JSONObject
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class LiveEventIdentityTest {
    @Test fun replacementDoesNotCloseTheNewOrConnectingSession() {
        val event = JSONObject().put("source_domain", "session").put("source_id", "previous")
            .put("event_type", "session.superseded").put("payload", JSONObject())
        assertFalse(LiveEventIdentity.matches(event, null, null))
        assertFalse(LiveEventIdentity.matches(event, "current", 2))
        assertTrue(LiveEventIdentity.matches(event, "previous", 1))
        event.getJSONObject("payload").put("generation", 1)
        assertFalse(LiveEventIdentity.matches(event, "previous", 2))
    }
}
