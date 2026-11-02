package nl.sander.aicockpit.jarvis.assistant

import android.os.Bundle
import android.os.Build
import android.service.voice.VoiceInteractionService
import android.service.voice.VoiceInteractionSession
import nl.sander.aicockpit.jarvis.data.CredentialStore
import java.lang.ref.WeakReference

class JarvisVoiceInteractionService : VoiceInteractionService() {
    override fun onReady() {
        super.onReady()
        active = WeakReference(this)
        applyContextPolicy()
    }

    override fun onShutdown() {
        active.clear()
        super.onShutdown()
    }

    private fun applyContextPolicy() {
        val rich = CredentialStore(this).assistStructureOptIn
        setDisabledShowContext(if (rich) 0 else if (Build.VERSION.SDK_INT >= 37)
            VoiceInteractionSession.SHOW_WITH_ASSIST_STRUCTURE_SCREEN_CONTENT
        else VoiceInteractionSession.SHOW_WITH_ASSIST)
    }

    companion object {
        fun refreshContextPolicy() { active.get()?.applyContextPolicy() }
        private var active = WeakReference<JarvisVoiceInteractionService>(null)
        /** The running service, for the debug capture fixtures. */
        internal val current: JarvisVoiceInteractionService? get() = active.get()
        private fun captureFlags(service: JarvisVoiceInteractionService): Int =
            VoiceInteractionSession.SHOW_SOURCE_APPLICATION or VoiceInteractionSession.SHOW_WITH_SCREENSHOT or
                VoiceInteractionSession.SHOW_WITH_ASSIST or
                (if (Build.VERSION.SDK_INT >= 37 && CredentialStore(service).assistStructureOptIn)
                    VoiceInteractionSession.SHOW_WITH_ASSIST_STRUCTURE_SCREEN_CONTENT else 0)
        fun requestContextRefresh(session: VoiceInteractionSession): Boolean {
            val service = active.get() ?: return false
            // Refresh through the current session's token rather than issuing
            // another service-level self-triggered activation.
            session.show(Bundle().apply { putBoolean("context_refresh", true) },
                captureFlags(service))
            return true
        }
        fun invokeFromWake(wakeScore: Float, activationId: String): Boolean {
            val service = active.get() ?: return false
            service.showSession(
                Bundle().apply {
                    putString("source", "local_wake")
                    putFloat("wake_score", wakeScore)
                    putString("activation_id", activationId)
                },
                // API37 background requests strip capture flags. Stage exactly
                // once from the visible assistant window in the session.
                if (Build.VERSION.SDK_INT >= 37) VoiceInteractionSession.SHOW_SOURCE_APPLICATION
                else captureFlags(service),
            )
            return true
        }

        fun invokeListening(activationId: String): Boolean {
            val service = active.get() ?: return false
            service.showSession(Bundle().apply {
                putString("source", "shared_live")
                putString("activation_id", activationId)
            }, if (Build.VERSION.SDK_INT >= 37) VoiceInteractionSession.SHOW_SOURCE_APPLICATION else captureFlags(service))
            return true
        }

        internal fun invokePhoneCapture(request: PhoneCaptureRequest): Boolean {
            val service = active.get() ?: return false
            service.showSession(Bundle().apply {
                putString("source", "phone_capture")
                putString("activation_id", request.activationId)
                putString("phone_capture_id", request.captureId)
            }, if (Build.VERSION.SDK_INT >= 37) VoiceInteractionSession.SHOW_SOURCE_APPLICATION
                else captureFlags(service))
            return true
        }
    }
}
