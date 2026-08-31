package nl.sander.aicockpit.jarvis

import android.app.Application
import nl.sander.aicockpit.jarvis.notifications.JarvisNotifications
import nl.sander.aicockpit.jarvis.notifications.JarvisEventStream
import org.webrtc.PeerConnectionFactory

class JarvisApplication : Application() {
    lateinit var eventStream: JarvisEventStream
        private set
    @Volatile var voiceSessionVisible: Boolean = false

    override fun onCreate() {
        super.onCreate()
        JarvisNotifications.createChannels(this)
        PeerConnectionFactory.initialize(
            PeerConnectionFactory.InitializationOptions.builder(this).createInitializationOptions()
        )
        eventStream = JarvisEventStream(this)
        eventStream.start()
    }
}
