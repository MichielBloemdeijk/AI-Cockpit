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
import nl.sander.aicockpit.jarvis.MainActivity
import nl.sander.aicockpit.jarvis.data.CockpitApi
import nl.sander.aicockpit.jarvis.data.CredentialStore
import nl.sander.aicockpit.jarvis.wake.WakeForegroundService
import nl.sander.aicockpit.jarvis.web.JarvisWebViewActivity

class NotificationActionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        val notificationId = intent?.getIntExtra("notification_id", 0) ?: 0
        val deliveryId = intent?.getStringExtra("delivery_id").orEmpty()
        when (intent?.action) {
            "open" -> {
                val taskPath = intent.getStringExtra("task_path")
                val openIntent = if (taskPath.isNullOrBlank()) {
                    Intent(context, MainActivity::class.java)
                } else {
                    Intent(context, JarvisWebViewActivity::class.java)
                        .putExtra(JarvisWebViewActivity.EXTRA_PATH, taskPath)
                }.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                context.startActivity(openIntent)
                if (notificationId != 0) {
                    context.getSystemService(NotificationManager::class.java).cancel(notificationId)
                }
                if (deliveryId.isBlank()) return
                val pending = goAsync()
                CoroutineScope(SupervisorJob() + Dispatchers.IO).launch {
                    try {
                        runCatching {
                            CockpitApi(CredentialStore(context)).acknowledgeDelivery(deliveryId)
                        }
                    } finally {
                        pending.finish()
                    }
                }
            }
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
                                if (deliveryId.isNotBlank()) {
                                    runCatching {
                                        CockpitApi(CredentialStore(context)).acknowledgeDelivery(deliveryId, "snoozed")
                                    }
                                }
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
