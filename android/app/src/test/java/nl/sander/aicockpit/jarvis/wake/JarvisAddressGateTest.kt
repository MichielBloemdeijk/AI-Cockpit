package nl.sander.aicockpit.jarvis.wake

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class JarvisAddressGateTest {
    @Test fun acceptsDirectAddress() {
        assertTrue(JarvisAddressGate.classify("Hey Jarvis, remind me at six", 0.82f, 400).accepted)
    }

    @Test fun rejectsIncidentalMentionsAndWeakTiming() {
        assertFalse(JarvisAddressGate.classify("I was talking about Jarvis", 0.95f, 400).accepted)
        assertFalse(JarvisAddressGate.classify("Jarvis", 0.51f, 50).accepted)
    }
}
