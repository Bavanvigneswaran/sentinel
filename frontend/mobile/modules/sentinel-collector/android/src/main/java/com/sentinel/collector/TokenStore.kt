package com.sentinel.collector

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Where the agent token lives on the phone.
 *
 * **Not plain SharedPreferences.** The token is a long-lived credential scoped
 * to one device and it authenticates every metric this phone ever writes; a
 * world-readable-to-root, backed-up, plaintext string is the wrong home for it.
 * The desktop agent writes its config 0600 for the same reason.
 *
 * The token is sealed with AES-256-GCM under a key generated inside
 * `AndroidKeyStore`. The key is *non-exportable*: it never exists in the app's
 * address space, hardware-backed on any device with a TEE or StrongBox, and
 * `setRandomizedEncryptionRequired` forces a fresh IV per encryption. Only the
 * ciphertext reaches SharedPreferences, so an `adb backup`, a rooted `cat` of
 * the prefs file, or a stolen unlocked-bootloader image yields a blob that
 * cannot be decrypted off the device it came from.
 *
 * Deliberately *not* `EncryptedSharedPreferences`: androidx.security-crypto is
 * deprecated, and this is ~40 lines of standard Keystore usage with no
 * dependency and no migration story to inherit.
 *
 * No `setUserAuthenticationRequired`. The collector has to start at boot and
 * keep running with the screen locked, which is exactly when a phone is most
 * likely to be the thing being monitored; requiring an unlock to decrypt would
 * mean monitoring only happens while somebody is holding the device.
 */
class TokenStore(context: Context) {

    private val prefs = context.applicationContext
        .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    fun read(): String? {
        val stored = prefs.getString(KEY_CIPHERTEXT, null) ?: return null
        return try {
            val blob = Base64.decode(stored, Base64.NO_WRAP)
            if (blob.size <= IV_BYTES) return null
            val cipher = Cipher.getInstance(TRANSFORMATION)
            cipher.init(
                Cipher.DECRYPT_MODE,
                secretKey(),
                GCMParameterSpec(TAG_BITS, blob, 0, IV_BYTES),
            )
            String(cipher.doFinal(blob, IV_BYTES, blob.size - IV_BYTES), Charsets.UTF_8)
        } catch (_: Throwable) {
            // A failed unseal means the key is gone: the app's data was cleared,
            // the device was restored to different hardware, or the keystore was
            // invalidated. There is no token any more, and saying so sends the
            // user back through enrollment instead of retrying a dead credential.
            null
        }
    }

    fun write(token: String) {
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, secretKey())
        val iv = cipher.iv
        val body = cipher.doFinal(token.toByteArray(Charsets.UTF_8))
        val blob = ByteArray(iv.size + body.size)
        System.arraycopy(iv, 0, blob, 0, iv.size)
        System.arraycopy(body, 0, blob, iv.size, body.size)
        prefs.edit()
            .putString(KEY_CIPHERTEXT, Base64.encodeToString(blob, Base64.NO_WRAP))
            .apply()
    }

    /** Used on un-enrol. Drops the ciphertext and the key that could open it. */
    fun clear() {
        prefs.edit().remove(KEY_CIPHERTEXT).apply()
        try {
            KeyStore.getInstance(KEYSTORE).apply { load(null) }.deleteEntry(KEY_ALIAS)
        } catch (_: Throwable) {
            // Nothing to delete, or a keystore that will not talk to us. The
            // ciphertext is already gone, which is what makes the token unusable.
        }
    }

    private fun secretKey(): SecretKey {
        val keystore = KeyStore.getInstance(KEYSTORE).apply { load(null) }
        (keystore.getEntry(KEY_ALIAS, null) as? KeyStore.SecretKeyEntry)?.let { return it.secretKey }

        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, KEYSTORE)
        generator.init(
            KeyGenParameterSpec.Builder(
                KEY_ALIAS,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                // GCM catastrophically fails on IV reuse. Let the keystore pick.
                .setRandomizedEncryptionRequired(true)
                .build()
        )
        return generator.generateKey()
    }

    private companion object {
        const val KEYSTORE = "AndroidKeyStore"
        const val KEY_ALIAS = "sentinel.collector.agent-token.v1"
        const val PREFS_NAME = "sentinel_collector_secure"
        const val KEY_CIPHERTEXT = "agent_token_sealed"
        const val TRANSFORMATION = "AES/GCM/NoPadding"
        const val IV_BYTES = 12
        const val TAG_BITS = 128
    }
}
