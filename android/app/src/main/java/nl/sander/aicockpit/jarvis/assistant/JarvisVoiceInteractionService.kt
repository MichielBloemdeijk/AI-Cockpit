package nl.sander.aicockpit.jarvis.assistant

import android.os.Bundle
import android.os.Build
import android.content.pm.ApplicationInfo
import android.util.Log
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
        private var pendingFixture: Bundle? = null
        private var startedFixtureActivation: String? = null
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

        /** Debug fixture still uses Android's real assistant screenshot callback. */
        fun invokeFixture(activationId: String, captureId: String): Boolean {
            val service = active.get() ?: return false
            if ((service.applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE) == 0) return false
            // The disposable fixture opts into assist structure as well, matching
            // the system's full-context invocation path without changing user settings.
            service.setDisabledShowContext(0)
            val fixtureArgs = Bundle().apply {
                putString("source", "fixture")
                putString("activation_id", activationId)
                putString("capture_id", captureId)
                putBoolean("fixture_skip_live", true)
            }
            if (Build.VERSION.SDK_INT >= 37) {
                // Android 17 restricts background self-triggered captures. Arm the
                // debug fixture, then let the harness invoke the system gesture.
                if (startedFixtureActivation == activationId) return true
                if (pendingFixture?.getString("activation_id") != activationId) {
                    pendingFixture = fixtureArgs
                    Log.i("JarvisCaptureFixture", "system_invocation_ready=$activationId")
                }
                return false
            }
            service.showSession(
                fixtureArgs,
                VoiceInteractionSession.SHOW_SOURCE_ASSIST_GESTURE or
                    VoiceInteractionSession.SHOW_WITH_ASSIST or
                    VoiceInteractionSession.SHOW_WITH_SCREENSHOT,
            )
            return true
        }

        /** Attach only armed debug correlation to a real system invocation. */
        fun consumeFixtureArguments(args: Bundle?): Bundle? {
            val service = active.get() ?: return args
            if ((service.applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE) == 0) return args
            val fixture = pendingFixture ?: return args
            pendingFixture = null
            startedFixtureActivation = fixture.getString("activation_id")
            return Bundle(args ?: Bundle()).apply { putAll(fixture) }
        }

        fun invokeWakeFixture(activationId: String, captureId: String, sessionId: String, generation: Int): Boolean {
            val service = active.get() ?: return false
            if ((service.applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE) == 0) return false
            pendingFixture = Bundle().apply {
                putString("source", "local_wake")
                putFloat("wake_score", 1f)
                putString("activation_id", activationId)
                putString("capture_id", captureId)
                putString("fixture_session_id", sessionId)
                putInt("fixture_generation", generation)
                putBoolean("fixture_skip_live", true)
            }
            return invokeFromWake(1f, activationId)
        }

        /** Undo the debug fixture's temporary assist-structure override. */
        fun restoreFixtureContextPolicy() {
            val service = active.get() ?: return
            if ((service.applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE) == 0) return
            service.applyContextPolicy()
        }
    }
}
