package nl.sander.aicockpit.jarvis.wake

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class WakeCandidateGateTest {
    @Test fun acceptsStrongWakeAfterRecentSpeech() {
        val gate = WakeCandidateGate()
        assertNull(gate.evaluate(WakeInference(0.9f, 0.02f)))
        assertEquals(0.48f, gate.evaluate(WakeInference(0.1f, 0.48f))!!, 0.0001f)
    }

    @Test fun acceptsTwoLowConfidenceHits() {
        val gate = WakeCandidateGate()
        assertNull(gate.evaluate(WakeInference(0.9f, 0.19f)))
        assertEquals(0.22f, gate.evaluate(WakeInference(0.2f, 0.22f))!!, 0.0001f)
    }

    @Test fun ignoresSingleLowConfidenceHitAndWakeWithoutRecentSpeech() {
        val gate = WakeCandidateGate()
        assertNull(gate.evaluate(WakeInference(0.9f, 0.19f)))
        assertNull(gate.evaluate(WakeInference(0.2f, 0.05f)))

        val quietGate = WakeCandidateGate()
        assertNull(quietGate.evaluate(WakeInference(0.1f, 0.9f)))
    }
}
