package nl.sander.aicockpit.jarvis.realtime

import org.junit.Assert.*
import org.junit.Test

class LiveSessionPolicyTest {
    @Test fun liveEventsRequireStartedAndRecordUsage() {
        var s = LiveEventState()
        s = reduceLiveEvent(s, "{\"type\":\"session.started\"}")
        assertTrue(s.started)
        s = reduceLiveEvent(s, "{\"type\":\"session.closed\",\"duration\":12.5}")
        assertTrue(s.closed); assertEquals(12.5, s.usageSeconds!!, 0.01)
    }
    @Test fun idleCloseWaitsForPendingExchangeAndPlayback() {
        val p = LiveSessionPolicy(); p.listeningChanged(true, 0); p.exchangeStarted(1)
        assertFalse(p.shouldClose(60_000))
        assertFalse(p.shouldClose(120_000)); p.exchangeFinished(120_000)
        assertFalse(p.shouldClose(179_999)); assertTrue(p.shouldClose(180_000))
        p.playbackChanged(true, 180_000); assertFalse(p.shouldClose(240_000))
    }

    @Test fun activationAudioFailsRatherThanClaimingReplay() {
        val result = boundedActivationHandoff("a-1", shortArrayOf(1, 2), canReplay = false)
        assertTrue(result.isFailure)
        assertTrue(result.exceptionOrNull() is UnsupportedOperationException)
    }

    @Test fun emptyActivationIsSafe() {
        val handoff = boundedActivationHandoff("a-2", shortArrayOf(), canReplay = false).getOrThrow()
        assertFalse(handoff.replayed)
    }
}
