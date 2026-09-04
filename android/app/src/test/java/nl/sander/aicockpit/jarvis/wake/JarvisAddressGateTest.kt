package nl.sander.aicockpit.jarvis.wake

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class JarvisAddressGateTest {
    @Test fun acceptsDirectAddress() {
        assertTrue(JarvisAddressGate.classify("Hey Jarvis, remind me at six", 0.82f, 400).accepted)
    }

    @Test fun rejectsIncidentalMentionsButKeepsCandidateVerificationPermissive() {
        assertFalse(JarvisAddressGate.classify("I was talking about Jarvis", 0.95f, 400).accepted)
        assertTrue(JarvisAddressGate.classify("Jarvis", 0.19f, 50).accepted)
        assertTrue(JarvisAddressGate.classify("continue with the report", 0.20f, 50).accepted)
        assertFalse(JarvisAddressGate.classify("continue with the report", 0.10f, 50).accepted)
    }
}
