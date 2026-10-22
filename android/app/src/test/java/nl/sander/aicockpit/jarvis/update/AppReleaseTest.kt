package nl.sander.aicockpit.jarvis.update

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class AppReleaseTest {
    private val sha = "a".repeat(64)
    private fun manifest(code: Long = 42, path: String = "/api/jarvis/devices/android/releases/$code.apk", checksum: String = sha) =
        JSONObject().put("release", JSONObject().put("version_code", code).put("version_name", "2026.10.22+abc")
            .put("sha256", checksum).put("size", 1000).put("download_path", path))

    @Test fun parsesPublishedRelease() {
        assertEquals(AppRelease(42, "2026.10.22+abc", sha, 1000, "/api/jarvis/devices/android/releases/42.apk"),
            AppRelease.parse(manifest()))
        assertNull(AppRelease.parse(JSONObject().put("release", JSONObject.NULL)))
    }

    @Test fun rejectsForeignPathsAndMalformedChecksums() {
        for (body in listOf(manifest(path = "https://elsewhere.example/jarvis.apk"),
                manifest(path = "/api/jarvis/devices/android/releases/41.apk"), manifest(checksum = "ABC"))) {
            assertThrows(IllegalArgumentException::class.java) { AppRelease.parse(body) }
        }
    }

    @Test fun automaticChecksRespectPreferenceNetworkAndPermission() {
        val release = AppRelease.parse(manifest())
        fun decide(installed: Long = 1, user: Boolean = false, auto: Boolean = true, unmetered: Boolean = true, canInstall: Boolean = true) =
            decideUpdate(release, installed, user, auto, unmetered, canInstall)
        assertEquals(UpdateDecision.UP_TO_DATE, decide(installed = 42))
        assertEquals(UpdateDecision.UP_TO_DATE, decideUpdate(null, 1, true, true, true, true))
        assertEquals(UpdateDecision.OFFER, decide(auto = false))
        assertEquals(UpdateDecision.WAIT_FOR_UNMETERED, decide(unmetered = false))
        assertEquals(UpdateDecision.NEEDS_PERMISSION, decide(canInstall = false))
        assertEquals(UpdateDecision.INSTALL, decide())
        // A tap on Update ignores the automatic-install preference and metered networks.
        assertEquals(UpdateDecision.INSTALL, decide(user = true, auto = false, unmetered = false))
        assertEquals(UpdateDecision.NEEDS_PERMISSION, decide(user = true, canInstall = false))
    }
}
