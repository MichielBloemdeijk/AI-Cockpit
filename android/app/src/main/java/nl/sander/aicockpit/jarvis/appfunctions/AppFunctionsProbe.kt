package nl.sander.aicockpit.jarvis.appfunctions

import android.content.Context
import nl.sander.aicockpit.jarvis.data.CredentialStore

/** Reflection keeps the experimental, allowlisted API out of the required runtime. */
object AppFunctionsProbe {
    fun status(context: Context): String {
        if (!CredentialStore(context).appFunctionsEnabled) return "Disabled"
        return if (runCatching { Class.forName("androidx.appfunctions.AppFunctionManager") }.isSuccess) {
            "Experimental API available; read-only calls enabled"
        } else {
            "Experimental API unavailable on this build"
        }
    }
}
