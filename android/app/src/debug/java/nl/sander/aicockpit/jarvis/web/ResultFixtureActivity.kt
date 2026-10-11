package nl.sander.aicockpit.jarvis.web

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.util.Log
import android.webkit.CookieManager
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import nl.sander.aicockpit.jarvis.JarvisApplication
import nl.sander.aicockpit.jarvis.data.CockpitApi
import nl.sander.aicockpit.jarvis.data.CredentialStore

/** Debug APK only: owner-supplied disposable pairing, no login bypass. */
class ResultFixtureActivity : Activity() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        scope.launch {
            try {
                val path = intent.getStringExtra("path") ?: error("Missing result path")
                check(Regex("^/results/[0-9a-fA-F-]{36}$").matches(path))
                val store = CredentialStore(this@ResultFixtureActivity)
                intent.getStringExtra("pairing_code")?.let { code ->
                    val server = intent.getStringExtra("server_url") ?: error("Missing fixture server")
                    val api = CockpitApi(store)
                    val paired = api.pair(server, code, "M5 result emulator fixture")
                    store.savePairing(server, paired)
                    api.updateDevice("unlocked")
                }
                (application as JarvisApplication).eventStream.start()
                if (intent.getBooleanExtra("clear_cookies", false)) {
                    val cleared = CompletableDeferred<Unit>()
                    CookieManager.getInstance().removeAllCookies { cleared.complete(Unit) }
                    cleared.await()
                    CookieManager.getInstance().flush()
                }
                startActivity(Intent(this@ResultFixtureActivity, JarvisWebViewActivity::class.java)
                    .putExtra(JarvisWebViewActivity.EXTRA_PATH, path))
                Log.i("JarvisResultFixture", "opened=$path")
                finish()
            } catch (error: Exception) {
                Log.e("JarvisResultFixture", "Fixture setup failed", error)
                finish()
            }
        }
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }
}
