package nl.sander.aicockpit.jarvis.data

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Test
import java.time.Instant

class CockpitApiStatusTest {
    @Test
    fun parsesWorkerWaitsOverdueRemindersAndDegradedComponents() {
        val body = JSONObject()
            .put("worker_health", JSONObject().put("status", "offline"))
            .put("tasks", JSONArray()
                .put(JSONObject().put("status", "waiting_for_worker"))
                .put(JSONObject()
                    .put("status", "waiting_until")
                    .put("resume_at", "2026-09-03T08:00:00Z")
                    .put("scheduled_delivery", JSONObject().put("kind", "reminder")))
                .put(JSONObject()
                    .put("status", "waiting_until")
                    .put("resume_at", "2026-09-03T10:00:00Z")
                    .put("scheduled_delivery", JSONObject().put("kind", "reminder"))))
            .put("component_health", JSONObject()
                .put("worker", JSONObject().put("status", "offline"))
                .put("reminder_scheduler", JSONObject().put("status", "degraded"))
                .put("notification_router", JSONObject().put("status", "healthy")))

        val status = parseJarvisAssistantStatus(body, Instant.parse("2026-09-03T09:00:00Z"))

        assertEquals("offline", status.workerStatus)
        assertEquals(1, status.waitingForWorkerCount)
        assertEquals(1, status.overdueReminderCount)
        assertEquals(listOf("reminder_scheduler"), status.degradedComponents)
    }
}
