package nl.sander.aicockpit.jarvis.data

import org.junit.Assert.*
import org.junit.Test

class PairingLinkTest {
    @Test fun validLinkAndManualOrigin() {
        assertEquals(PairingLink("https://host.tail.ts.net", "123456"), PairingLink.parse("jarvis://pair?server=https%3A%2F%2Fhost.tail.ts.net&code=123456"))
        assertEquals("http://100.80.1.2:3000", PairingLink.serverOrigin("http://100.80.1.2:3000/"))
    }
    @Test fun rejectsAmbiguousOrCredentialBearingLinks() {
        for (link in listOf("https://pair?server=https://host&code=123456", "jarvis://pair?server=https://host&code=123456&code=234567", "jarvis://pair?server=https://user:password@host&code=123456", "jarvis://pair?server=https://host/path&code=123456", "jarvis://pair?server=file:///foo&code=123456", "jarvis://pair?server=https://host&code=12345", "jarvis://pair?server=%ZZ&code=123456")) assertNull(link, PairingLink.parse(link))
    }
}
