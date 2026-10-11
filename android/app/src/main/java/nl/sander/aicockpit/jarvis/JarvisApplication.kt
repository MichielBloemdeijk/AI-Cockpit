package nl.sander.aicockpit.jarvis

import android.app.Application
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import androidx.core.content.ContextCompat
import nl.sander.aicockpit.jarvis.notifications.JarvisNotifications
import nl.sander.aicockpit.jarvis.notifications.JarvisEventStream
import nl.sander.aicockpit.jarvis.assistant.ScreenCaptureManager
import org.webrtc.PeerConnectionFactory

class JarvisApplication : Application() {
    internal var liveControllerFactory: (Context, nl.sander.aicockpit.jarvis.data.CockpitApi) -> nl.sander.aicockpit.jarvis.live.LiveController =
        { context, api -> nl.sander.aicockpit.jarvis.live.LiveController(context, api) }
    lateinit var eventStream: JarvisEventStream
        private set
    lateinit var screenCapture: ScreenCaptureManager
        private set
    lateinit var phoneCapture: nl.sander.aicockpit.jarvis.assistant.PhoneCaptureCoordinator
        private set
    @Volatile var voiceSessionVisible: Boolean = false
    @Volatile var voiceSessionHandlesCapture: Boolean = false

    override fun onCreate() {
        super.onCreate()
        JarvisNotifications.createChannels(this)
        PeerConnectionFactory.initialize(
            PeerConnectionFactory.InitializationOptions.builder(this).createInitializationOptions()
        )
        screenCapture = ScreenCaptureManager(this)
        phoneCapture = nl.sander.aicockpit.jarvis.assistant.PhoneCaptureCoordinator(this, screenCapture)
        ContextCompat.registerReceiver(this, object : BroadcastReceiver() {
            override fun onReceive(context: Context, intent: Intent) {
                if (intent.action == Intent.ACTION_SCREEN_OFF) screenCapture.unavailable("locked")
            }
        }, IntentFilter(Intent.ACTION_SCREEN_OFF), ContextCompat.RECEIVER_NOT_EXPORTED)
        eventStream = JarvisEventStream(this) { screenCapture.onStreamConnected() }
        eventStream.addListener { event ->
            phoneCapture.onEvent(event)
            if (event.optString("event_type") == "context.requested") screenCapture.onContextRequested()
        }
        eventStream.start()
    }
}
