package nl.sander.aicockpit.jarvis.web

import android.webkit.WebView
import android.util.Log
import android.webkit.JavascriptInterface
import nl.sander.aicockpit.jarvis.data.CredentialStore

/** Debug-only page exercising the production exact-origin native message bridge. */
class NativeVoiceFixtureActivity : JarvisWebViewActivity() {
    class Result {
        @JavascriptInterface fun report(value: String) { Log.i("JarvisNativeVoiceFixture", value.take(300)) }
    }
    override fun loadInitialPage(view: WebView, url: String) {
        view.addJavascriptInterface(Result(), "VoiceFixture")
        val origin = if (intent.getBooleanExtra("untrusted_origin", false)) "https://untrusted.invalid/" else CredentialStore(this).serverUrl + "/"
        view.loadDataWithBaseURL(origin, """
            <!doctype html><meta name="viewport" content="width=device-width"><h1>Native voice controls</h1>
            <p id="state">Loading</p><button onclick="send('listen_now')">Listen now</button>
            <button onclick="send('stop_live')">Stop Live</button><button onclick="send('hey_jarvis')">Hey Jarvis</button>
            <script>
            let state = {};
            function send(command) {
              if (!window.JarvisPhoneVoice) { VoiceFixture.report('foreign_origin_denied'); return; }
              JarvisPhoneVoice.postMessage(JSON.stringify({command, activation_id:state.activation_id,
                session_id:state.session_id, generation:state.generation}));
            }
            if (window.JarvisPhoneVoice) {
              JarvisPhoneVoice.onmessage = event => {
                state = JSON.parse(event.data); document.querySelector('#state').textContent = state.text;
                VoiceFixture.report('state=' + state.mode);
              };
              send('get_state');
              const frame = document.createElement('iframe');
              frame.srcdoc = '<script>if(window.JarvisPhoneVoice) JarvisPhoneVoice.postMessage(JSON.stringify({command:"hey_jarvis"}));<\/script>';
              document.body.append(frame);
            } else VoiceFixture.report('foreign_origin_denied');
            </script>
        """.trimIndent(), "text/html", "UTF-8", null)
    }
}
