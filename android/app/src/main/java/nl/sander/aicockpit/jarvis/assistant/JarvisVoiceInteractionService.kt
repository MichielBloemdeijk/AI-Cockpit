package nl.sander.aicockpit.jarvis.assistant

import android.os.Bundle
import android.service.voice.VoiceInteractionService
import android.service.voice.VoiceInteractionSession
import nl.sander.aicockpit.jarvis.data.CredentialStore
import java.lang.ref.WeakReference

class JarvisVoiceInteractionService : VoiceInteractionService() {
    override fun onReady() {
        super.onReady()
        active = WeakReference(this)
        val assistStructure = CredentialStore(this).assistStructureOptIn
        setDisabledShowContext(
            VoiceInteractionSession.SHOW_WITH_SCREENSHOT or
                (if (assistStructure) 0 else VoiceInteractionSession.SHOW_WITH_ASSIST)
        )
    }

    override fun onShutdown() {
        active.clear()
        super.onShutdown()
    }

    companion object {
        private var active = WeakReference<JarvisVoiceInteractionService>(null)
        fun invokeFromWake(wakeScore: Float): Boolean {
            val service = active.get() ?: return false
            service.showSession(
                Bundle().apply {
                    putString("source", "local_wake")
                    putFloat("wake_score", wakeScore)
                },
                VoiceInteractionSession.SHOW_SOURCE_APPLICATION,
            )
            return true
        }
    }
}
