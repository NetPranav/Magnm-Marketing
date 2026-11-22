package com.magnm.studio;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyPermanentlyInvalidatedException;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.KeyStore;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/**
 * Encrypted key-value store backed by a non-exportable AES-256-GCM Android Keystore key.
 * Uses a fresh random IV per write and binds the key name as GCM AAD.
 * Never falls back to plaintext storage.
 */
final class SecureKeyStoreHelper {

    private static final String ANDROID_KEYSTORE = "AndroidKeyStore";
    private static final String KEY_ALIAS = "com.magnm.studio.securestore.v1";
    private static final String PREFS_NAME = "magnm_secure_store_v1";
    private static final String TRANSFORMATION = "AES/GCM/NoPadding";
    private static final int KEY_SIZE_BITS = 256;
    private static final int GCM_TAG_BITS = 128;
    private static final int EXPECTED_IV_BYTES = 12;
    private static final String FORMAT_PREFIX = "v1:";

    private final SharedPreferences prefs;
    private final Object lock = new Object();

    SecureKeyStoreHelper(Context context) {
        this.prefs = context.getApplicationContext()
                .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
    }

    String get(String key) {
        validateKey(key);
        synchronized (lock) {
            String encoded = prefs.getString(key, null);
            if (encoded == null) {
                return null;
            }
            try {
                SecretKey secretKey = loadExistingKey();
                if (secretKey == null) {
                    clearValueLocked(key);
                    return null;
                }
                if (!encoded.startsWith(FORMAT_PREFIX)) {
                    clearValueLocked(key);
                    return null;
                }
                String rest = encoded.substring(FORMAT_PREFIX.length());
                int sep = rest.indexOf(':');
                if (sep <= 0 || sep >= rest.length() - 1) {
                    clearValueLocked(key);
                    return null;
                }
                byte[] iv = Base64.decode(rest.substring(0, sep), Base64.NO_WRAP);
                byte[] ciphertext = Base64.decode(rest.substring(sep + 1), Base64.NO_WRAP);
                if (iv.length != EXPECTED_IV_BYTES || ciphertext.length == 0) {
                    clearValueLocked(key);
                    return null;
                }
                Cipher cipher = Cipher.getInstance(TRANSFORMATION);
                cipher.init(Cipher.DECRYPT_MODE, secretKey, new GCMParameterSpec(GCM_TAG_BITS, iv));
                cipher.updateAAD(key.getBytes(StandardCharsets.UTF_8));
                byte[] plaintext = cipher.doFinal(ciphertext);
                return new String(plaintext, StandardCharsets.UTF_8);
            } catch (Exception ex) {
                clearValueLocked(key);
                return null;
            }
        }
    }

    void set(String key, String value) throws GeneralSecurityException {
        validateKey(key);
        if (value == null) {
            throw new IllegalArgumentException("Secure store value is required.");
        }
        synchronized (lock) {
            byte[] plaintext = value.getBytes(StandardCharsets.UTF_8);
            byte[] aad = key.getBytes(StandardCharsets.UTF_8);
            SecretKey secretKey = getOrCreateKey();
            try {
                writeEncryptedLocked(key, secretKey, plaintext, aad);
            } catch (KeyPermanentlyInvalidatedException ex) {
                SecretKey regenerated = generateNewKey();
                writeEncryptedLocked(key, regenerated, plaintext, aad);
            }
        }
    }

    void remove(String key) {
        validateKey(key);
        synchronized (lock) {
            clearValueLocked(key);
        }
    }

    private void writeEncryptedLocked(
            String key,
            SecretKey secretKey,
            byte[] plaintext,
            byte[] aad
    ) throws GeneralSecurityException {
        Cipher cipher = Cipher.getInstance(TRANSFORMATION);
        cipher.init(Cipher.ENCRYPT_MODE, secretKey);
        byte[] iv = cipher.getIV();
        if (iv == null || iv.length != EXPECTED_IV_BYTES) {
            throw new GeneralSecurityException("Unexpected GCM IV length.");
        }
        cipher.updateAAD(aad);
        byte[] ciphertext = cipher.doFinal(plaintext);
        String stored = FORMAT_PREFIX
                + Base64.encodeToString(iv, Base64.NO_WRAP)
                + ":"
                + Base64.encodeToString(ciphertext, Base64.NO_WRAP);
        if (!prefs.edit().putString(key, stored).commit()) {
            throw new GeneralSecurityException("Failed to persist encrypted value.");
        }
    }

    private void clearValueLocked(String key) {
        prefs.edit().remove(key).commit();
    }

    private static SecretKey loadExistingKey() throws GeneralSecurityException {
        try {
            KeyStore ks = KeyStore.getInstance(ANDROID_KEYSTORE);
            ks.load(null);
            if (!ks.containsAlias(KEY_ALIAS)) {
                return null;
            }
            KeyStore.Entry entry = ks.getEntry(KEY_ALIAS, null);
            if (!(entry instanceof KeyStore.SecretKeyEntry)) {
                return null;
            }
            return ((KeyStore.SecretKeyEntry) entry).getSecretKey();
        } catch (GeneralSecurityException ex) {
            throw ex;
        } catch (Exception ex) {
            throw new GeneralSecurityException("Failed to load keystore key.", ex);
        }
    }

    private static SecretKey getOrCreateKey() throws GeneralSecurityException {
        SecretKey existing = loadExistingKey();
        if (existing != null) {
            return existing;
        }
        return generateNewKey();
    }

    private static SecretKey generateNewKey() throws GeneralSecurityException {
        KeyGenerator keyGen = KeyGenerator.getInstance(
                KeyProperties.KEY_ALGORITHM_AES,
                ANDROID_KEYSTORE
        );
        KeyGenParameterSpec spec = new KeyGenParameterSpec.Builder(
                KEY_ALIAS,
                KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT
        )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(KEY_SIZE_BITS)
                .setRandomizedEncryptionRequired(true)
                .build();
        keyGen.init(spec);
        return keyGen.generateKey();
    }

    private static void validateKey(String key) {
        if (key == null || key.trim().isEmpty()) {
            throw new IllegalArgumentException("Secure store key is required.");
        }
    }
}
