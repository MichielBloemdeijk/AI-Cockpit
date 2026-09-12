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

    @Test fun activationAudioFlowsOnceBeforeNewMicrophoneFrames() {
        val buffer = LiveActivationAudioBuffer(maxSamples = 8)
        assertTrue(buffer.begin("a-1", shortArrayOf(1, 2, 3)))
        assertTrue(buffer.append("a-1", shortArrayOf(4, 5), 2))
        assertTrue(buffer.requestRecorderRelease("a-1"))
        buffer.markRecorderReleased("a-1")
        assertTrue(buffer.claim("a-1"))

        val firstWebRtcFrame = shortArrayOf(6, 7)
        assertTrue(buffer.replaceWithQueued(firstWebRtcFrame))
        assertArrayEquals(shortArrayOf(1, 2), firstWebRtcFrame)
        val secondWebRtcFrame = shortArrayOf(8, 9, 10)
        assertTrue(buffer.replaceWithQueued(secondWebRtcFrame))
        assertArrayEquals(shortArrayOf(3, 4, 5), secondWebRtcFrame)
    }

    @Test fun activationAudioOverflowIsBoundedAndCannotBeClaimed() {
        val buffer = LiveActivationAudioBuffer(maxSamples = 3)
        assertTrue(buffer.begin("a-2", shortArrayOf(1, 2)))
        assertFalse(buffer.append("a-2", shortArrayOf(3, 4), 2))
        assertTrue(buffer.overflowed)
        assertEquals(0, buffer.queuedSamples())
        assertFalse(buffer.requestRecorderRelease("a-2"))
    }

    @Test fun activationAudioCatchesUpOnlyThroughConfidentSilence() {
        val buffer = LiveActivationAudioBuffer(
            maxSamples = 20,
            targetBacklogSamples = 2,
            retainedSilenceSamples = 2,
        )
        assertTrue(buffer.begin("a-3", ShortArray(8)))
        assertTrue(buffer.requestRecorderRelease("a-3"))
        buffer.markRecorderReleased("a-3")
        assertTrue(buffer.claim("a-3"))

        val first = ShortArray(2)
        assertTrue(buffer.replaceWithQueued(first))
        assertEquals(8, buffer.queuedSamples())
        val second = ShortArray(2)
        assertTrue(buffer.replaceWithQueued(second))
        assertEquals(2, buffer.queuedSamples())
    }

    @Test fun activationAudioPreservesUninterruptedQuietSpeech() {
        val buffer = LiveActivationAudioBuffer(
            maxSamples = 20,
            targetBacklogSamples = 2,
            retainedSilenceSamples = 2,
        )
        assertTrue(buffer.begin("a-4", ShortArray(8) { 64 }))
        assertTrue(buffer.requestRecorderRelease("a-4"))
        buffer.markRecorderReleased("a-4")
        assertTrue(buffer.claim("a-4"))

        val frame = ShortArray(2) { 64 }
        assertTrue(buffer.replaceWithQueued(frame))
        assertArrayEquals(shortArrayOf(64, 64), frame)
        assertEquals(8, buffer.queuedSamples())
    }
}
