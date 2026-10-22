package nl.sander.aicockpit.jarvis.update

import android.app.Activity
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.SystemClock
import android.util.Log
import androidx.core.content.IntentCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import nl.sander.aicockpit.jarvis.JarvisApplication
import nl.sander.aicockpit.jarvis.audio.JarvisAudioControls
import nl.sander.aicockpit.jarvis.data.CockpitApi
import nl.sander.aicockpit.jarvis.data.CredentialStore
import nl.sander.aicockpit.jarvis.notifications.JarvisNotifications
import java.io.File
import java.security.MessageDigest
import java.util.concurrent.atomic.AtomicBoolean

data class UpdateState(val phase: Phase = Phase.IDLE, val release: AppRelease? = null, val detail: String = "") {
    enum class Phase { IDLE, CHECKING, UP_TO_DATE, AVAILABLE, NEEDS_PERMISSION, DOWNLOADING, WAITING, INSTALLING, NEEDS_CONFIRMATION, FAILED }
    val working: Boolean get() = phase in setOf(Phase.CHECKING, Phase.DOWNLOADING, Phase.WAITING, Phase.INSTALLING)
}

/**
 * Installs newer APKs from the paired AI Cockpit over the running app.
 * Android only accepts an APK signed with the installed app's key. Once Jarvis
 * installed itself, Android 12+ lets later self-updates skip the confirmation.
 */
object AppUpdater {
    const val UPDATE_PUBLISHED = "android.update_published"
    private const val TAG = "JarvisUpdate"
    private const val PERIODIC_MS = 6 * 60 * 60 * 1000L
    private const val AUTOMATIC_THROTTLE_MS = 15 * 60 * 1000L
    private const val CONVERSATION_RETRY_MS = 60 * 1000L

    private val mutableState = MutableStateFlow(UpdateState())
    val state: StateFlow<UpdateState> = mutableState.asStateFlow()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val running = AtomicBoolean(false)
    private lateinit var app: JarvisApplication
    @Volatile private var lastAutomaticCheck = 0L
    @Volatile private var notifiedVersion = 0L
    @Volatile private var pendingConfirmation: Intent? = null

    fun start(application: JarvisApplication) {
        app = application
        application.eventStream.addListener { event ->
            if (event.optString("event_type") == UPDATE_PUBLISHED) checkAutomatically(force = true)
        }
        scope.launch { while (isActive) { delay(PERIODIC_MS); checkAutomatically() } }
    }

    fun installedVersionCode(context: Context): Long =
        context.packageManager.getPackageInfo(context.packageName, 0).longVersionCode
    fun installedVersionName(context: Context): String =
        context.packageManager.getPackageInfo(context.packageName, 0).versionName.orEmpty()

    fun checkAutomatically(force: Boolean = false) {
        val now = SystemClock.elapsedRealtime()
        if (!force && lastAutomaticCheck != 0L && now - lastAutomaticCheck < AUTOMATIC_THROTTLE_MS) return
        lastAutomaticCheck = now
        launchCheck(userInitiated = false)
    }

    fun checkNow() = launchCheck(userInitiated = true)

    private fun launchCheck(userInitiated: Boolean) {
        if (!running.compareAndSet(false, true)) return
        scope.launch {
            try {
                check(userInitiated)
            } catch (error: Exception) {
                Log.w(TAG, "Update failed", error)
                mutableState.value = UpdateState(UpdateState.Phase.FAILED, detail = error.message ?: "Update check failed.")
            } finally {
                running.set(false)
            }
        }
    }

    private suspend fun check(userInitiated: Boolean) {
        val store = CredentialStore(app)
        if (store.credential() == null) return
        mutableState.value = UpdateState(UpdateState.Phase.CHECKING)
        val release = CockpitApi(store).androidUpdate()
        val decision = decideUpdate(release, installedVersionCode(app), userInitiated, store.automaticUpdates,
            unmetered(), app.packageManager.canRequestPackageInstalls())
        Log.i(TAG, "Update check: ${release?.versionCode} -> $decision")
        when (decision) {
            UpdateDecision.UP_TO_DATE -> {
                File(app.cacheDir, "updates").deleteRecursively()
                cancelNotification(app)
                mutableState.value = UpdateState(UpdateState.Phase.UP_TO_DATE); return
            }
            UpdateDecision.OFFER -> {
                mutableState.value = UpdateState(UpdateState.Phase.AVAILABLE, release)
                notifyOnce(release!!, "Jarvis ${release.versionName} is available", "Open Jarvis to install it."); return
            }
            UpdateDecision.WAIT_FOR_UNMETERED -> {
                mutableState.value = UpdateState(UpdateState.Phase.AVAILABLE, release, "Downloads automatically on Wi-Fi."); return
            }
            UpdateDecision.NEEDS_PERMISSION -> {
                mutableState.value = UpdateState(UpdateState.Phase.NEEDS_PERMISSION, release)
                if (!userInitiated) notifyOnce(release!!, "Jarvis update ready", "Allow Jarvis to install updates to continue.")
                return
            }
            UpdateDecision.INSTALL -> Unit
        }
        val apk = download(store, release!!)
        // Installing restarts the app, which would cut off a conversation.
        while (!userInitiated && conversationActive()) {
            mutableState.value = UpdateState(UpdateState.Phase.WAITING, release, "Installs after the current conversation.")
            delay(CONVERSATION_RETRY_MS)
        }
        install(apk, release)
    }

    private fun conversationActive() = JarvisAudioControls.state.value.liveActive || app.voiceSessionVisible

    private fun unmetered(): Boolean = app.getSystemService(ConnectivityManager::class.java).let { manager ->
        manager.getNetworkCapabilities(manager.activeNetwork)?.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED) == true
    }

    private suspend fun download(store: CredentialStore, release: AppRelease): File {
        val directory = File(app.cacheDir, "updates").apply { mkdirs() }
        val target = File(directory, "jarvis-${release.versionCode}.apk")
        if (target.length() == release.size && sha256(target) == release.sha256) return target
        directory.listFiles()?.forEach { it.delete() }
        val partial = File(directory, "${target.name}.partial")
        var percent = -1
        val digest = CockpitApi(store).downloadRelease(release.downloadPath, partial) { written ->
            val current = (written * 100 / release.size).toInt()
            if (current != percent) {
                percent = current
                mutableState.value = UpdateState(UpdateState.Phase.DOWNLOADING, release, "$current%")
            }
        }
        if (digest != release.sha256 || partial.length() != release.size) {
            partial.delete()
            error("The downloaded update failed verification.")
        }
        check(partial.renameTo(target)) { "Could not store the downloaded update." }
        return target
    }

    private fun sha256(file: File): String = MessageDigest.getInstance("SHA-256").let { digest ->
        file.inputStream().use { input ->
            val buffer = ByteArray(1 shl 16)
            while (true) { val read = input.read(buffer); if (read < 0) break; digest.update(buffer, 0, read) }
        }
        digest.digest().joinToString("") { "%02x".format(it) }
    }

    private fun install(apk: File, release: AppRelease) {
        mutableState.value = UpdateState(UpdateState.Phase.INSTALLING, release)
        val installer = app.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
            setAppPackageName(app.packageName)
            setSize(apk.length())
            setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
        }
        val sessionId = installer.createSession(params)
        installer.openSession(sessionId).use { session ->
            try {
                session.openWrite("jarvis.apk", 0, apk.length()).use { output ->
                    apk.inputStream().use { it.copyTo(output) }
                    session.fsync(output)
                }
                val callback = Intent(app, UpdateInstallReceiver::class.java).setAction(UpdateInstallReceiver.ACTION)
                // The installer fills in status extras, so this explicit intent must be mutable.
                session.commit(PendingIntent.getBroadcast(app, sessionId, callback,
                    PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_MUTABLE).intentSender)
            } catch (error: Exception) {
                session.abandon()
                throw error
            }
        }
        Log.i(TAG, "Committed update to ${release.versionCode}")
    }

    internal fun onInstallStatus(context: Context, intent: Intent) {
        val status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)
        val release = mutableState.value.release
        Log.i(TAG, "Install status $status ${intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE).orEmpty()}")
        when (status) {
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                val confirmation = IntentCompat.getParcelableExtra(intent, Intent.EXTRA_INTENT, Intent::class.java) ?: return
                pendingConfirmation = confirmation
                mutableState.value = UpdateState(UpdateState.Phase.NEEDS_CONFIRMATION, release)
                JarvisNotifications.appUpdate(context, "Install Jarvis update", "Tap to finish installing.",
                    PendingIntent.getActivity(context, 0, Intent(confirmation).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE))
            }
            // The replaced app's "Jarvis updated" notification already superseded any prompt.
            PackageInstaller.STATUS_SUCCESS -> mutableState.value = UpdateState(UpdateState.Phase.UP_TO_DATE)
            else -> {
                val message = if (status == PackageInstaller.STATUS_FAILURE_ABORTED) "The update was cancelled."
                    else intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE) ?: "Android could not install the update."
                mutableState.value = UpdateState(UpdateState.Phase.FAILED, release, message)
            }
        }
    }

    /** Shows Android's install confirmation when this self-update still needs one. */
    fun confirmPending(activity: Activity) {
        val confirmation = pendingConfirmation ?: return
        pendingConfirmation = null
        cancelNotification(activity)
        activity.startActivity(confirmation)
    }

    private fun notifyOnce(release: AppRelease, title: String, text: String) {
        if (notifiedVersion == release.versionCode) return
        notifiedVersion = release.versionCode
        JarvisNotifications.appUpdate(app, title, text)
    }

    private fun cancelNotification(context: Context) =
        context.getSystemService(NotificationManager::class.java).cancel(JarvisNotifications.APP_UPDATE_NOTIFICATION_ID)
}
