package nl.sander.aicockpit.jarvis.live

import nl.sander.aicockpit.jarvis.data.LiveSessionState
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class LiveStartupReadinessTest {
    private fun readyState(id: String = "session-1", generation: Int = 1) =
        LiveSessionState(id, generation, "ready")

    @Test fun backendMayBecomeReadyBeforeTransportWithoutBrowserStarted() {
        val readiness = LiveStartupReadiness("session-1", 1)
        readiness.observeBackend(readyState())
        readiness.markSdpApplied()
        readiness.observePeer("CONNECTED")
        assertFalse(readiness.current().ready)
        readiness.observeDataChannel("OPEN")

        assertTrue(readiness.current().ready)
        assertFalse(readiness.current().browserSessionStarted)
    }

    @Test fun backendMayBecomeReadyAfterTransport() {
        val readiness = LiveStartupReadiness("session-1", 1)
        readiness.markSdpApplied()
        readiness.observePeer("CONNECTED")
        readiness.observeDataChannel("OPEN")
        readiness.markBrowserSessionStarted()
        assertFalse(readiness.current().ready)
        readiness.observeBackend(readyState())

        assertTrue(readiness.current().ready)
        assertTrue(readiness.current().browserSessionStarted)
    }

    @Test fun missingTransportRemainsExplicit() {
        val readiness = LiveStartupReadiness("session-1", 1)
        readiness.markSdpApplied()
        readiness.observeBackend(readyState())

        assertEquals(listOf("WebRTC connected", "oai-events open"), readiness.current().missing())
        assertFalse(readiness.current().ready)
    }

    @Test fun sidebandFailureIsTerminal() {
        val readiness = LiveStartupReadiness("session-1", 1)
        readiness.observeBackend(LiveSessionState("session-1", 1, "disconnected"))
        assertEquals("GPT-Live backend sideband disconnected during startup", readiness.current().failure)
    }

    @Test fun sidebandFailureAfterNominalReadinessInvalidatesTheBoundary() {
        val readiness = LiveStartupReadiness("session-1", 1)
        readiness.markSdpApplied()
        readiness.observePeer("CONNECTED")
        readiness.observeDataChannel("OPEN")
        readiness.observeBackend(readyState())
        assertTrue(readiness.current().ready)

        readiness.observeBackend(LiveSessionState("session-1", 1, "disconnected"))
        assertFalse(readiness.current().ready)
        assertEquals("GPT-Live backend sideband disconnected during startup", readiness.current().failure)
    }

    @Test fun cancellationIsTerminalAndLateSignalsCannotReviveAttempt() {
        val readiness = LiveStartupReadiness("session-1", 1)
        readiness.cancel()
        readiness.markSdpApplied()
        readiness.observePeer("CONNECTED")
        readiness.observeDataChannel("OPEN")
        readiness.observeBackend(readyState())

        assertFalse(readiness.current().ready)
        assertEquals("GPT-Live startup was cancelled", readiness.current().failure)
    }

    @Test fun staleBackendSessionFailsInsteadOfSatisfyingReadiness() {
        val readiness = LiveStartupReadiness("session-2", 2)
        readiness.observeBackend(readyState("session-1", 1))

        assertEquals("GPT-Live session was replaced during startup", readiness.current().failure)
        assertNull(readiness.current().takeIf { it.ready })
    }
}
