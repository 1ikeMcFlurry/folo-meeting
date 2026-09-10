package com.folotoy.meeting;

import android.content.Context;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.AtomicFile;
import org.json.JSONObject;
import javax.crypto.*;
import javax.crypto.spec.GCMParameterSpec;
import java.io.*;
import java.security.KeyStore;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;

/** Credentials and text drafts stay in app-private, encrypted, non-backed-up files. */
final class Vault {
    private final Context context;
    Vault(Context context) { this.context=context; }
    private SecretKey key() throws Exception {
        KeyStore store=KeyStore.getInstance("AndroidKeyStore"); store.load(null);
        if(!store.containsAlias("folo_meeting")) {
            KeyGenerator generator=KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES,"AndroidKeyStore");
            generator.init(new KeyGenParameterSpec.Builder("folo_meeting",KeyProperties.PURPOSE_ENCRYPT|KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build());
            generator.generateKey();
        }
        return ((KeyStore.SecretKeyEntry)store.getEntry("folo_meeting",null)).getSecretKey();
    }
    private AtomicFile file(String name) {
        if(!name.matches("[a-zA-Z0-9_-]{1,90}")) throw new IllegalArgumentException("记录名称无效");
        return new AtomicFile(new File(context.getNoBackupFilesDir(),name+".sealed"));
    }
    synchronized JSONObject load(String name) throws Exception {
        AtomicFile file=file(name); if(!file.getBaseFile().exists()) return new JSONObject();
        byte[] encrypted=file.readFully();
        if(encrypted.length<29) throw new IOException("本地记录损坏，未覆盖原文件");
        Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE,key(),new GCMParameterSpec(128,Arrays.copyOfRange(encrypted,0,12)));
        cipher.updateAAD(name.getBytes(StandardCharsets.UTF_8));
        byte[] plain=cipher.doFinal(encrypted,12,encrypted.length-12);
        try { return new JSONObject(new String(plain,StandardCharsets.UTF_8)); }
        finally { Arrays.fill(plain,(byte)0); }
    }
    synchronized void save(String name,JSONObject value) throws Exception {
        Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding"); cipher.init(Cipher.ENCRYPT_MODE,key());
        cipher.updateAAD(name.getBytes(StandardCharsets.UTF_8));
        byte[] plain=value.toString().getBytes(StandardCharsets.UTF_8), encrypted;
        try { encrypted=cipher.doFinal(plain); } finally { Arrays.fill(plain,(byte)0); }
        AtomicFile file=file(name); FileOutputStream stream=null;
        try { stream=file.startWrite(); stream.write(cipher.getIV()); stream.write(encrypted); file.finishWrite(stream); }
        catch(Exception e) { if(stream!=null) file.failWrite(stream); throw e; }
    }
}
