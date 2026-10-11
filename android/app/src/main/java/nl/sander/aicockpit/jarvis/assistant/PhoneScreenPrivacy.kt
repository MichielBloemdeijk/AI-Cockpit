package nl.sander.aicockpit.jarvis.assistant

import android.app.KeyguardManager
import android.content.Context
import android.os.PowerManager

/** Screen-off and swipe locks also forbid screenshot context, even without a PIN. */
object PhoneScreenPrivacy {
    fun locked(context: Context): Boolean {
        val keyguard = context.getSystemService(KeyguardManager::class.java)
        return keyguard.isDeviceLocked || keyguard.isKeyguardLocked || !context.getSystemService(PowerManager::class.java).isInteractive
    }
}
