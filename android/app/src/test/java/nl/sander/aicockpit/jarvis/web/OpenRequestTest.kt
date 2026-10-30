package nl.sander.aicockpit.jarvis.web

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class OpenRequestTest {
    private fun event(path: String, type: String = "display.open_requested") = JSONObject()
        .put("id", "event-1").put("event_type", type)
        .put("payload", JSONObject().put("path", path).put("title", "Packing list")
            .put("expires_at", "2026-10-28T12:00:30Z"))

    @Test fun acceptsOnlyCockpitResultTaskAndAppPages() {
        val request = OpenRequest.from(event("/results/0f3c-a1"))!!
        assertEquals("/results/0f3c-a1", request.path)
        assertEquals("Packing list", request.title)
        assertEquals("/tasks/abc", OpenRequest.from(event("/tasks/abc"))!!.path)
        assertEquals("/apps/notes", OpenRequest.from(event("/apps/notes"))!!.path)
        for (path in listOf("/jarvis", "//evil.test/results/x", "https://evil.test/results/x", "/results/../login",
                "/results/x?next=//evil", "/tasks/", "/settings/devices", "/apps/notes/../../login", "/apps/"))
            assertNull(path, OpenRequest.from(event(path)))
    }

    @Test fun ignoresOtherEventsAndMalformedPayloads() {
        assertNull(OpenRequest.from(event("/results/x", type = "context.requested")))
        assertNull(OpenRequest.from(JSONObject().put("event_type", "display.open_requested").put("id", "e")))
        assertNull(OpenRequest.from(event("/results/x").apply { getJSONObject("payload").put("expires_at", "soon") }))
    }
}
