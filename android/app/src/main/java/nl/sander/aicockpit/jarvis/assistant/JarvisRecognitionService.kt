package nl.sander.aicockpit.jarvis.assistant

import android.content.Intent
import android.os.Bundle
import android.speech.RecognitionService

/** Recognition is owned by the Live voice session; this satisfies the assistant-service contract. */
class JarvisRecognitionService : RecognitionService() {
    override fun onStartListening(intent: Intent?, callback: Callback?) {
        callback?.readyForSpeech(Bundle())
    }
    override fun onStopListening(callback: Callback?) { callback?.endOfSpeech() }
    override fun onCancel(callback: Callback?) { callback?.error(android.speech.SpeechRecognizer.ERROR_CLIENT) }
}
