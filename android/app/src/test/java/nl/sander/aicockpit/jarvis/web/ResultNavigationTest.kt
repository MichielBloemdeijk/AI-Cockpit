package nl.sander.aicockpit.jarvis.web

import org.junit.Assert.assertEquals
import org.junit.Test

class ResultNavigationTest {
    @Test fun notificationActionUsesResultOnlyForResultPath() {
        assertEquals("Open result", ResultNavigation.actionLabel("/results/opaque?delivery=one"))
        for (path in listOf(null, "/tasks/one", "https://evil/results/one", "//results/one", "/results/"))
            assertEquals("Open task", ResultNavigation.actionLabel(path))
    }

    @Test fun sameOriginIncludesDefaultPortsAndLoginReturnPath() {
        for (path in listOf("/results/opaque", "/login?next=%2Fresults%2Fopaque", "/tasks/one"))
            assertEquals(ResultNavigation.Destination.INTERNAL, ResultNavigation.destination("https://cockpit.test", "https://cockpit.test:443$path"))
        assertEquals(ResultNavigation.Destination.EXTERNAL, ResultNavigation.destination("https://cockpit.test", "https://source.test/recipe"))
        assertEquals(ResultNavigation.Destination.EXTERNAL, ResultNavigation.destination("https://cockpit.test", "http://cockpit.test/results/one"))
        assertEquals(ResultNavigation.Destination.EXTERNAL, ResultNavigation.destination("https://cockpit.test", "https://cockpit.test:8443/results/one"))
    }

    @Test fun untrustedSchemesCredentialsAndMalformedUrlsAreBlocked() {
        for (target in listOf("javascript:alert(1)", "intent://example", "file:///private", "data:text/html,hi", "https://user:pass@evil.test/", "not a url"))
            assertEquals(ResultNavigation.Destination.BLOCKED, ResultNavigation.destination("https://cockpit.test", target))
    }
}
