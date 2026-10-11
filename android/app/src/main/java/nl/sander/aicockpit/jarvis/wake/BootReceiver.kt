package nl.sander.aicockpit.jarvis.wake

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.core.content.ContextCompat
import nl.sander.aicockpit.jarvis.data.CredentialStore
import nl.sander.aicockpit.jarvis.audio.JarvisAudioService

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        val store = CredentialStore(context)
        if (intent?.action == Intent.ACTION_BOOT_COMPLETED && store.wakeAfterBoot && !store.sleep) {
            runCatching {
                ContextCompat.startForegroundService(context, Intent(context, JarvisAudioService::class.java).setAction(nl.sander.aicockpit.jarvis.audio.AudioAction.WAKE.wire))
            }
        }
    }
}
