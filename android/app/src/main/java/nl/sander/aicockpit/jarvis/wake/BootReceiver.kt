package nl.sander.aicockpit.jarvis.wake

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.core.content.ContextCompat
import nl.sander.aicockpit.jarvis.data.CredentialStore

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        val store = CredentialStore(context)
        if (intent?.action == Intent.ACTION_BOOT_COMPLETED && store.wakeAfterBoot && !store.sleep) {
            runCatching {
                ContextCompat.startForegroundService(context, Intent(context, WakeForegroundService::class.java).setAction(WakeForegroundService.ACTION_START))
            }
        }
    }
}
