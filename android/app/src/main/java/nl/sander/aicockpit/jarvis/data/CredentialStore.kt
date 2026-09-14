package nl.sander.aicockpit.jarvis.data

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

class CredentialStore(context: Context) {
    private val preferences = context.getSharedPreferences("jarvis_device", Context.MODE_PRIVATE)

    var serverUrl: String
        get() = preferences.getString("server_url", "") ?: ""
        set(value) { preferences.edit().putString("server_url", value.trimEnd('/')).apply() }
    var deviceId: String
        get() = preferences.getString("device_id", "") ?: ""
        set(value) { preferences.edit().putString("device_id", value).apply() }
    var sleep: Boolean
        get() = preferences.getBoolean("sleep", false)
        set(value) { preferences.edit().putBoolean("sleep", value).apply() }
    var wakeAfterBoot: Boolean
        get() = preferences.getBoolean("wake_after_boot", false)
        set(value) { preferences.edit().putBoolean("wake_after_boot", value).apply() }
    var eventCursor: Long
        get() = preferences.getLong("event_cursor", 0L)
        set(value) { preferences.edit().putLong("event_cursor", value.coerceAtLeast(0L)).apply() }
    var assistStructureOptIn: Boolean
        get() = preferences.getBoolean("assist_structure_opt_in", false)
        set(value) { preferences.edit().putBoolean("assist_structure_opt_in", value).apply() }
    var appFunctionsEnabled: Boolean
        get() = preferences.getBoolean("app_functions_enabled", false)
        set(value) { preferences.edit().putBoolean("app_functions_enabled", value).apply() }

    fun saveCredential(value: String) {
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, key())
        val encoded = Base64.encodeToString(cipher.iv + cipher.doFinal(value.toByteArray()), Base64.NO_WRAP)
        preferences.edit().putString("credential", encoded).apply()
    }
    fun credential(): String? {
        val encoded = preferences.getString("credential", null) ?: return null
        return runCatching {
            val bytes = Base64.decode(encoded, Base64.NO_WRAP)
            val cipher = Cipher.getInstance(TRANSFORMATION)
            cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, bytes.copyOfRange(0, 12)))
            String(cipher.doFinal(bytes.copyOfRange(12, bytes.size)))
        }.getOrNull()
    }

    fun clear() {
        preferences.edit().clear().apply()
    }

    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(KEY_ALIAS, null) as? SecretKey)?.let { return it }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        generator.init(
            KeyGenParameterSpec.Builder(
                KEY_ALIAS,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
            ).setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .build()
        )
        return generator.generateKey()
    }

    companion object {
        private const val KEY_ALIAS = "jarvis-device-credential-v1"
        private const val TRANSFORMATION = "AES/GCM/NoPadding"
    }
}
