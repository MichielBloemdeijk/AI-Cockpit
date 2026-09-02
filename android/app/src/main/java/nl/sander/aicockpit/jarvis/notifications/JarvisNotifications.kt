package nl.sander.aicockpit.jarvis.notifications

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat
import nl.sander.aicockpit.jarvis.R
import nl.sander.aicockpit.jarvis.wake.WakeForegroundService

object JarvisNotifications {
    const val WAKE_NOTIFICATION_ID = 2101
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

    fun wakeListening(context: Context): Notification = NotificationCompat.Builder(context, WAKE_CHANNEL)
        .setSmallIcon(android.R.drawable.ic_btn_speak_now)
        .setContentTitle("Jarvis is listening locally")
        .setContentText("Background audio is evaluated on this device and is not retained.")
        .setOngoing(true)
        .addAction(0, "Sleep", action(context, "sleep", notificationId = WAKE_NOTIFICATION_ID))
        .addAction(0, "Stop", PendingIntent.getService(
            context, 1,
            Intent(context, WakeForegroundService::class.java).setAction(WakeForegroundService.ACTION_STOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        ))
        .build()

    fun update(
        context: Context,
        title: String,
        summary: String,
        taskLabel: String,
        notificationId: Int,
        taskPath: String? = null,
        deliveryId: String? = null,
        snoozable: Boolean = false,
    ): Notification {
        val builder = NotificationCompat.Builder(context, UPDATE_CHANNEL)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle(title)
            .setContentText(summary)
            .setStyle(NotificationCompat.BigTextStyle().bigText(summary))
            .setAutoCancel(true)
            .setContentIntent(open(context, taskPath, deliveryId, notificationId))
            .addAction(0, "Open task", open(context, taskPath, deliveryId, notificationId))
            .addAction(0, "Stop", action(context, "stop", taskLabel, notificationId, deliveryId))
            .addAction(0, "Sleep", action(context, "sleep", taskLabel, notificationId, deliveryId))
        if (snoozable) {
            builder.addAction(0, "Snooze 10m", action(context, "snooze", taskLabel, notificationId, deliveryId))
        }
        return builder.build()
    }

    private fun open(
        context: Context,
        taskPath: String? = null,
        deliveryId: String? = null,
        notificationId: Int = 0,
    ): PendingIntent {
        val intent = Intent(context, NotificationActionReceiver::class.java)
            .setAction("open")
            .putExtra("task_path", taskPath)
            .putExtra("delivery_id", deliveryId)
            .putExtra("notification_id", notificationId)
        return PendingIntent.getBroadcast(
            context,
            2 + notificationId,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
    }

    private fun action(
        context: Context,
        action: String,
        label: String = "",
        notificationId: Int = 0,
        deliveryId: String? = null,
    ) = PendingIntent.getBroadcast(
        context, 31 * action.hashCode() + notificationId,
        Intent(context, NotificationActionReceiver::class.java)
            .setAction(action)
            .putExtra("task_label", label)
            .putExtra("delivery_id", deliveryId)
            .putExtra("notification_id", notificationId),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
    )
}
