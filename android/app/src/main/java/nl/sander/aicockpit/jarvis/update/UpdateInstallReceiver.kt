package nl.sander.aicockpit.jarvis.update

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** Receives PackageInstaller session results for self-updates. */
class UpdateInstallReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == ACTION) AppUpdater.onInstallStatus(context, intent)
    }

    companion object {
        const val ACTION = "nl.sander.aicockpit.jarvis.UPDATE_INSTALL_STATUS"
    }
}
