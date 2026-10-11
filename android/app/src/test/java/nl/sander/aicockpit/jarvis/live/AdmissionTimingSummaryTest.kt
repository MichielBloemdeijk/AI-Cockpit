package nl.sander.aicockpit.jarvis.live

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class AdmissionTimingSummaryTest {
    @Test fun largeSpanCollectionCannotTruncateAdmissionReceipt() {
        val source = JSONObject().put("clock", "server_monotonic").put("admission_commit_ns", 123456789012345L)
            .put("intent_id", "intent").put("commit_is_original", false)
            .put("spans", JSONArray().apply { repeat(100) { put(JSONObject().put("stage", "db_connection_open")) } })
        val summary = admissionTimingSummary(source)!!
        assertFalse(summary.has("spans"))
        assertEquals(123456789012345L, summary.getLong("admission_commit_ns"))
        assertEquals("intent", summary.getString("intent_id"))
        assertFalse(summary.getBoolean("commit_is_original"))
        assertTrue(summary.toString().toByteArray(Charsets.UTF_8).size < 1024)
        assertEquals(100, source.getJSONArray("spans").length())
        assertNull(admissionTimingSummary(null))
    }
}
