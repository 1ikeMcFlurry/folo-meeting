package com.folotoy.meeting;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;

final class AcsSigner {
    static String hex(byte[] bytes) {
        StringBuilder out=new StringBuilder(); for(byte b:bytes) out.append(String.format(Locale.ROOT,"%02x",b&255)); return out.toString();
    }
    static String sha(String value) throws Exception { return hex(MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8))); }
    static Map<String,String> sign(String method,String path,String query,String body,String key,String secret,String date,String nonce) throws Exception {
        if(key.isBlank() || secret.isBlank() || key.contains("\n") || key.contains("\r")) throw new IllegalArgumentException("请填写阿里云账号");
        TreeMap<String,String> headers=new TreeMap<>();
        headers.put("content-type","application/json"); headers.put("host","tingwu.cn-beijing.aliyuncs.com");
        headers.put("x-acs-action",method.equals("GET")?"GetTaskInfo":"CreateTask");
        headers.put("x-acs-content-sha256",sha(body)); headers.put("x-acs-date",date);
        headers.put("x-acs-signature-nonce",nonce); headers.put("x-acs-version","2023-09-30");
        StringBuilder canonical=new StringBuilder(); for(var entry:headers.entrySet()) canonical.append(entry.getKey()).append(':').append(entry.getValue()).append('\n');
        String signed=String.join(";",headers.keySet());
        String request=method+'\n'+path+'\n'+query+'\n'+canonical+'\n'+signed+'\n'+sha(body);
        Mac hmac=Mac.getInstance("HmacSHA256"); hmac.init(new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8),"HmacSHA256"));
        String signature=hex(hmac.doFinal(("ACS3-HMAC-SHA256\n"+sha(request)).getBytes(StandardCharsets.UTF_8)));
        headers.put("Authorization","ACS3-HMAC-SHA256 Credential="+key+",SignedHeaders="+signed+",Signature="+signature);
        return headers;
    }
}
