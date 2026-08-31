package nl.sander.aicockpit.jarvis.notifications

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.app.NotificationManager
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import nl.sander.aicockpit.jarvis.assistant.JarvisVoiceInteractionSession
import nl.sander.aicockpit.jarvis.data.CockpitApi
import nl.sander.aicockpit.jarvis.data.CredentialStore
import nl.sander.aicockpit.jarvis.wake.WakeForegroundService

class NotificationActionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        val notificationId = intent?.getIntExtra("notification_id", 0) ?: 0
        when (intent?.action) {
            "sleep" -> {
                CredentialStore(context).sleep = true
                JarvisVoiceInteractionSession.stopActive()
                context.stopService(Intent(context, WakeForegroundService::class.java))
                if (notificationId != 0) context.getSystemService(NotificationManager::class.java).cancel(notificationId)
            }
            "snooze" -> {
                val pending = goAsync()
                val label = intent.getStringExtra("task_label").orEmpty()
                CoroutineScope(SupervisorJob() + Dispatchers.IO).launch {
                    try {
                        runCatching { CockpitApi(CredentialStore(context)).snoozeByDescription(label) }
                            .onSuccess {
                                if (notificationId != 0) context.getSystemService(NotificationManager::class.java).cancel(notificationId)
                            }
                    } finally {
                        pending.finish()
                    }
                }
            }
            "stop" -> {
                JarvisVoiceInteractionSession.stopActive()
                if (notificationId != 0) context.getSystemService(NotificationManager::class.java).cancel(notificationId)
            }
        }
    }
}
