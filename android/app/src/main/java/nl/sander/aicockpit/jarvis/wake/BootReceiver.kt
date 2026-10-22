package nl.sander.aicockpit.jarvis.wake

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.core.content.ContextCompat
import nl.sander.aicockpit.jarvis.data.CredentialStore
import nl.sander.aicockpit.jarvis.audio.JarvisAudioService
import nl.sander.aicockpit.jarvis.notifications.JarvisNotifications
import nl.sander.aicockpit.jarvis.update.AppUpdater

/** Resumes wake listening after a reboot or after a self-update replaced the running app. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        val action = intent?.action
        if (action != Intent.ACTION_BOOT_COMPLETED && action != Intent.ACTION_MY_PACKAGE_REPLACED) return
        val store = CredentialStore(context)
        if (action == Intent.ACTION_MY_PACKAGE_REPLACED && store.credential() != null) {
            JarvisNotifications.appUpdate(context, "Jarvis updated", "Now running ${AppUpdater.installedVersionName(context)}.", silent = true)
        }
        if (store.wakeAfterBoot && !store.sleep) {
            runCatching {
                ContextCompat.startForegroundService(context, Intent(context, JarvisAudioService::class.java).setAction(nl.sander.aicockpit.jarvis.audio.AudioAction.WAKE.wire))
            }
        }
    }
}
