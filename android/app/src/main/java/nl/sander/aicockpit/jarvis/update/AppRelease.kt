package nl.sander.aicockpit.jarvis.update

import org.json.JSONObject

/** One published APK from the paired AI Cockpit's update channel. */
data class AppRelease(
    val versionCode: Long,
    val versionName: String,
    val sha256: String,
    val size: Long,
    val downloadPath: String,
) {
    companion object {
        private const val RELEASE_PATH = "/api/jarvis/devices/android/releases/"
        private val SHA256 = Regex("[0-9a-f]{64}")

        /** Null when nothing is published; malformed manifests are rejected rather than half-trusted. */
        fun parse(body: JSONObject): AppRelease? {
            val release = body.optJSONObject("release") ?: return null
            val path = release.getString("download_path")
            val versionCode = release.getLong("version_code")
            require(path == "$RELEASE_PATH$versionCode.apk") { "Unexpected update download path" }
            val sha256 = release.getString("sha256")
            require(SHA256.matches(sha256)) { "Malformed update checksum" }
            val size = release.getLong("size")
            require(versionCode > 0 && size > 0) { "Malformed update manifest" }
            return AppRelease(versionCode, release.getString("version_name"), sha256, size, path)
        }
    }
}

enum class UpdateDecision { UP_TO_DATE, OFFER, WAIT_FOR_UNMETERED, NEEDS_PERMISSION, INSTALL }

/**
 * Automatic checks only download on unmetered networks (a release is ~150 MB)
 * and only when automatic installs are enabled; a tap on Update always proceeds.
 */
fun decideUpdate(
    release: AppRelease?,
    installedVersionCode: Long,
    userInitiated: Boolean,
    automaticInstalls: Boolean,
    unmetered: Boolean,
    canInstall: Boolean,
): UpdateDecision = when {
    release == null || release.versionCode <= installedVersionCode -> UpdateDecision.UP_TO_DATE
    !userInitiated && !automaticInstalls -> UpdateDecision.OFFER
    !userInitiated && !unmetered -> UpdateDecision.WAIT_FOR_UNMETERED
    !canInstall -> UpdateDecision.NEEDS_PERMISSION
    else -> UpdateDecision.INSTALL
}
