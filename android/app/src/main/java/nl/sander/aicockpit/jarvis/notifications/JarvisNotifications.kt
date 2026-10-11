package nl.sander.aicockpit.jarvis.notifications

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat
import androidx.activity.ComponentActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch
import nl.sander.aicockpit.jarvis.MainActivity
import nl.sander.aicockpit.jarvis.data.CockpitApi
import nl.sander.aicockpit.jarvis.data.CredentialStore
import nl.sander.aicockpit.jarvis.R
import nl.sander.aicockpit.jarvis.audio.AudioControlState
import nl.sander.aicockpit.jarvis.audio.AudioMode
import nl.sander.aicockpit.jarvis.audio.JarvisAudioControls
import nl.sander.aicockpit.jarvis.web.ResultNavigation
import nl.sander.aicockpit.jarvis.web.JarvisWebViewActivity

object JarvisNotifications {
    const val CONTROL_NOTIFICATION_ID = 2101
    const val UPDATE_NOTIFICATION_ID = 2102
    private const val WAKE_CHANNEL = "jarvis_wake"
    private const val UPDATE_CHANNEL = "jarvis_updates"

    fun createChannels(context: Context) {
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel(
            WAKE_CHANNEL, context.getString(R.string.wake_channel), NotificationManager.IMPORTANCE_LOW
        ))
        manager.createNotificationChannel(NotificationChannel(
            UPDATE_CHANNEL, context.getString(R.string.updates_channel), NotificationManager.IMPORTANCE_HIGH
        ))
    }

    fun audioControls(context: Context, state: AudioControlState): Notification {
        val builder = NotificationCompat.Builder(context, WAKE_CHANNEL)
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentTitle("Jarvis").setContentText(state.text)
            .setContentIntent(open(context)).setOngoing(true).setOnlyAlertOnce(true).setSilent(true)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setDeleteIntent(PendingIntent.getBroadcast(context, CONTROL_NOTIFICATION_ID,
                Intent(context, NotificationActionReceiver::class.java).setAction("dismiss_controls"), PendingIntent.FLAG_IMMUTABLE))
        state.actions.forEach { control ->
            val intent = if (state.mode == AudioMode.ERROR && state.error == "Microphone permission needed")
                JarvisAudioControls.setup(context) else JarvisAudioControls.intent(context, control, state)
            // Identity includes the activation/session, so replacing the notification
            // cannot rewrite an already queued Stop action to target a newer call.
            intent.data = android.net.Uri.parse("jarvis-control://${control.wire}/${state.activationId.orEmpty()}/${state.sessionId.orEmpty()}/${state.generation ?: 0}")
            val pending = if (intent.component?.className?.endsWith("MainActivity") == true)
                PendingIntent.getActivity(context, control.ordinal, intent, PendingIntent.FLAG_IMMUTABLE)
            else PendingIntent.getForegroundService(context, control.ordinal, intent, PendingIntent.FLAG_IMMUTABLE)
            builder.addAction(0, control.label, pending)
        }
        return builder.build()
    }

    fun update(
        context: Context,
        title: String,
        summary: String,
        notificationId: Int,
        taskPath: String? = null,
        deliveryId: String? = null,
    ): Notification {
        val builder = NotificationCompat.Builder(context, UPDATE_CHANNEL)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle(title)
            .setContentText(summary)
            .setStyle(NotificationCompat.BigTextStyle().bigText(summary))
            .setAutoCancel(true)
            .setContentIntent(open(context, taskPath, deliveryId, notificationId))
            .addAction(0, ResultNavigation.actionLabel(taskPath), open(context, taskPath, deliveryId, notificationId))
        return builder.build()
    }

    private fun open(
        context: Context,
        taskPath: String? = null,
        deliveryId: String? = null,
        notificationId: Int = 0,
    ): PendingIntent {
        // Launch the activity directly: notification actions must not use a
        // broadcast trampoline or leave the shade covering the opened page.
        val intent = if (taskPath.isNullOrBlank()) Intent(context, MainActivity::class.java)
            else Intent(context, JarvisWebViewActivity::class.java)
                .putExtra(JarvisWebViewActivity.EXTRA_PATH, taskPath)
        intent
            .putExtra("delivery_id", deliveryId)
            .putExtra("notification_id", notificationId)
        return PendingIntent.getActivity(
            context,
            2 + notificationId,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
    }

    fun acknowledgeOpen(activity: ComponentActivity, intent: Intent) {
        val notificationId = intent.getIntExtra("notification_id", 0)
        if (notificationId != 0) activity.getSystemService(NotificationManager::class.java).cancel(notificationId)
        val deliveryId = intent.getStringExtra("delivery_id")?.takeIf { it.isNotBlank() } ?: return
        activity.lifecycleScope.launch {
            runCatching { CockpitApi(CredentialStore(activity)).acknowledgeDelivery(deliveryId) }
        }
    }

}
