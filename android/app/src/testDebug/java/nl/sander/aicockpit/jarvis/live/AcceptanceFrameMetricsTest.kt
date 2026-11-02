package nl.sander.aicockpit.jarvis.live

import org.junit.Assert.assertEquals
import org.junit.Test

class AcceptanceFrameMetricsTest {
    @Test fun recordsDeadlinesAndLostObservationsWithoutInventingSmoothFrames() {
        val frames = AcceptanceFrameMetrics()
        frames.add(9, 10, 0)
        frames.add(10, 10, 2)
        frames.add(11, 10, 0)
        frames.add(-1, 10, 0)
        frames.add(9, -1, -1)
        assertEquals(3L, frames.count)
        assertEquals(2L, frames.janky)
        assertEquals(2L, frames.dropped)
        assertEquals(3L, frames.invalid)
        frames.reset()
        assertEquals(0L, frames.count)
        assertEquals(0L, frames.janky)
        assertEquals(0L, frames.dropped)
        assertEquals(0L, frames.invalid)
    }
    @Test fun reportsFirstDrawSeparatelyAndRequiresLaterMeasuredFrames() {
        val frames = AcceptanceFrameMetrics()
        frames.add(20, 10, 0, 1)
        frames.add(9, 10, 0, 0)
        frames.add(9, 10, 0, -1)
        assertEquals(2L, frames.count)
        assertEquals(1L, frames.firstDraws)
        assertEquals(1L, frames.firstDrawOverruns)
        assertEquals(0L, frames.janky)
        assertEquals(1L, frames.invalid)
    }
}
