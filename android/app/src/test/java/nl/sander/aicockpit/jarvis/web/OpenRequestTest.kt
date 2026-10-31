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

    @Test fun acceptsOnlyCockpitTaskAppAndRecordPages() {
        val result = "/apps/recipes?collection=recipe&key=0f3c-a1"
        val request = OpenRequest.from(event(result))!!
        assertEquals(result, request.path)
        assertEquals("Packing list", request.title)
        assertEquals("/tasks/abc", OpenRequest.from(event("/tasks/abc"))!!.path)
        assertEquals("/apps/notes", OpenRequest.from(event("/apps/notes"))!!.path)
        for (path in listOf("/jarvis", "//evil.test/apps/x", "https://evil.test/apps/x", "/results/x", "/tasks/x?next=//evil",
                "/apps/notes?next=//evil", "/apps/notes?collection=note&key=a/../b", "/tasks/", "/settings/devices",
                "/apps/notes/../../login", "/apps/"))
            assertNull(path, OpenRequest.from(event(path)))
    }

    @Test fun ignoresOtherEventsAndMalformedPayloads() {
        assertNull(OpenRequest.from(event("/tasks/x", type = "context.requested")))
        assertNull(OpenRequest.from(JSONObject().put("event_type", "display.open_requested").put("id", "e")))
        assertNull(OpenRequest.from(event("/tasks/x").apply { getJSONObject("payload").put("expires_at", "soon") }))
    }
}
