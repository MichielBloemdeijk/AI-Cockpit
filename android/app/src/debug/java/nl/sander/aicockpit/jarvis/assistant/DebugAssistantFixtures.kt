package nl.sander.aicockpit.jarvis.assistant

import android.content.Context
import android.os.Build
import android.os.Bundle
import android.service.voice.VoiceInteractionSession
import android.util.Log
import nl.sander.aicockpit.jarvis.JarvisApplication

/** The debug build's capture fixtures: armed invocations that bind to a harness's fake session. */
object DebugAssistantFixtures : AssistantFixtureHooks {
    private var pendingFixture: Bundle? = null
    private var startedFixtureActivation: String? = null

    /** Invoke the assistant through Android's real screenshot path for a fixture capture. */
    fun invokeFixture(context: Context, activationId: String, captureId: String): Boolean {
        AssistantFixtures.install(context, this)
        val service = JarvisVoiceInteractionService.current ?: return false
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
            // fixture, then let the harness invoke the system gesture.
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

    /** A wake activation whose capture binds to an existing fake session. */
    fun invokeWakeFixture(context: Context, activationId: String, captureId: String, sessionId: String, generation: Int): Boolean {
        AssistantFixtures.install(context, this)
        if (JarvisVoiceInteractionService.current == null) return false
        pendingFixture = Bundle().apply {
            putString("source", "local_wake")
            putFloat("wake_score", 1f)
            putString("activation_id", activationId)
            putString("capture_id", captureId)
            putString("fixture_session_id", sessionId)
            putInt("fixture_generation", generation)
            putBoolean("fixture_skip_live", true)
        }
        return JarvisVoiceInteractionService.invokeFromWake(1f, activationId)
    }

    /** A fresh screenshot through the visible session's own token. */
    fun refreshFixture(activationId: String, captureId: String): Boolean =
        JarvisVoiceInteractionSession.requestFreshCapture(activationId, captureId)

    override fun activationArguments(args: Bundle?): Bundle? {
        val fixture = pendingFixture ?: return args
        pendingFixture = null
        startedFixtureActivation = fixture.getString("activation_id")
        return Bundle(args ?: Bundle()).apply { putAll(fixture) }
    }

    override fun isFixtureShow(args: Bundle?): Boolean = args?.getBoolean("fixture_skip_live") == true

    override fun fixtureCaptureId(args: Bundle?): String? = args?.getString("capture_id")

    override fun fixtureStarted(context: Context, activationId: String, args: Bundle?) {
        val sessionId = args?.getString("fixture_session_id") ?: return
        (context.applicationContext as JarvisApplication).screenCapture
            .bindSession(activationId, sessionId, args.getInt("fixture_generation"))
    }

    override fun fixtureEnded() = JarvisVoiceInteractionService.refreshContextPolicy()
}
