package nl.sander.aicockpit.jarvis.notifications

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import nl.sander.aicockpit.jarvis.data.CredentialStore

/** Respect dismissal until the user opens Jarvis or explicitly uses its controls. */
class NotificationActionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        if (intent?.action == "dismiss_controls") CredentialStore(context).controlsDismissed = true
    }
}
