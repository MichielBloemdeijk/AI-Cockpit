package nl.sander.aicockpit.jarvis.web

import org.junit.Assert.*
import org.junit.Test

class NativeVoicePolicyTest {
    @Test fun bridgeIsExactOriginMainFrameAndLimitedCommands() {
        val origin = "https://jarvis.example"
        assertTrue(NativeVoicePolicy.permits(origin, "$origin:443", true, "listen_now"))
        for (command in listOf("get_state", "stop_live", "hey_jarvis"))
            assertTrue(NativeVoicePolicy.permits(origin, origin, true, command))
        assertFalse(NativeVoicePolicy.permits(origin, origin, false, "listen_now"))
        for (source in listOf("http://jarvis.example", "$origin:444", "https://jarvis.example.attacker.test", "file:///android_asset/test.html"))
            assertFalse(NativeVoicePolicy.permits(origin, source, true, "listen_now"))
        for (command in listOf("", "get_credentials", "screenshot", "execute"))
            assertFalse(NativeVoicePolicy.permits(origin, origin, true, command))
    }
}
