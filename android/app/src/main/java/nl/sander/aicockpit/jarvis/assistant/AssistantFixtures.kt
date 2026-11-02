package nl.sander.aicockpit.jarvis.assistant

import android.content.Context
import android.content.pm.ApplicationInfo
import android.os.Bundle

/**
 * The seam the capture harnesses use to drive the real assistant session.
 *
 * A fixture show uses Android's real invocation and screenshot callback, but binds
 * the capture to the harness's fake backend session instead of starting Live. The
 * defaults do nothing. Only a debuggable build can install an implementation (from
 * `src/debug`), so a release build has no fixture state or entry points.
 */
interface AssistantFixtureHooks {
    /** A system invocation's arguments, with an armed fixture's correlation added. */
    fun activationArguments(args: Bundle?): Bundle? = args
    /** True when this show belongs to a fixture; it then skips Live. */
    fun isFixtureShow(args: Bundle?): Boolean = false
    /** The capture id a fixture show starts with. */
    fun fixtureCaptureId(args: Bundle?): String? = null
    /** A fixture show's activation started; bind it to the harness's session. */
    fun fixtureStarted(context: Context, activationId: String, args: Bundle?) = Unit
    /** A fixture session ended; undo any temporary context-policy override. */
    fun fixtureEnded() = Unit
}

object AssistantFixtures {
    @Volatile
    var hooks: AssistantFixtureHooks = object : AssistantFixtureHooks {}
        private set

    /** Debug harnesses only; a release (non-debuggable) app refuses. */
    fun install(context: Context, implementation: AssistantFixtureHooks) {
        check(context.applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0) { "Assistant fixtures are debug-only" }
        hooks = implementation
    }
}
