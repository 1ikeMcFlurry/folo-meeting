package com.folotoy.meeting;

import org.json.*;
import javax.net.ssl.HttpsURLConnection;
import java.net.URI;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicBoolean;

/** Xfyun uses non-2xx for business errors, including an existing but empty feature group. */
final class VoiceprintHttp {
    private static final ScheduledExecutorService DEADLINES=Executors.newSingleThreadScheduledExecutor(r-> {
        Thread thread=new Thread(r,"voiceprint-http-deadline"); thread.setDaemon(true); return thread;
    });
    static JSONObject post(String url,String body) throws Exception {
        URI uri=URI.create(url);
        if(!"https".equals(uri.getScheme()) || !VoiceprintClient.HOST.equals(uri.getHost()) ||
                !VoiceprintClient.PATH.equals(uri.getPath()) || uri.getUserInfo()!=null || uri.getPort()!=-1)
            throw new VoiceprintClient.Failure("声纹服务地址无效");
        HttpsURLConnection connection=(HttpsURLConnection)uri.toURL().openConnection();
        connection.setConnectTimeout(10000); connection.setReadTimeout(15000);
        connection.setInstanceFollowRedirects(false); connection.setUseCaches(false); connection.setRequestMethod("POST");
        connection.setRequestProperty("Content-Type","application/json; charset=utf-8"); connection.setDoOutput(true);
        AtomicBoolean timedOut=new AtomicBoolean();
        ScheduledFuture<?> deadline=DEADLINES.schedule(()-> { timedOut.set(true); connection.disconnect(); },25,TimeUnit.SECONDS);
        byte[] bytes=body.getBytes(StandardCharsets.UTF_8);
        try {
            connection.setFixedLengthStreamingMode(bytes.length);
            try(OutputStream out=connection.getOutputStream()) { out.write(bytes); }
            int status=connection.getResponseCode();
            try(InputStream in=status>=400?connection.getErrorStream():connection.getInputStream(); ByteArrayOutputStream out=new ByteArrayOutputStream()) {
                if(in==null) return VoiceprintClient.httpResponse(status,new JSONObject());
                byte[] buffer=new byte[4096]; int count;
                try {
                    while((count=in.read(buffer))!=-1) {
                        if(out.size()+count>1024*1024) throw new VoiceprintClient.Failure("声纹服务结果超出读取上限，请到控制台核对");
                        out.write(buffer,0,count);
                    }
                } finally { Arrays.fill(buffer,(byte)0); }
                JSONObject response;
                try { response=new JSONObject(out.toString(StandardCharsets.UTF_8.name())); }
                catch(JSONException e) {
                    if(status<200 || status>=300) return VoiceprintClient.httpResponse(status,new JSONObject());
                    throw new VoiceprintClient.Failure("声纹服务返回内容无法解析，请稍后核对状态");
                }
                return VoiceprintClient.httpResponse(status,response);
            }
        } catch(Exception e) {
            if(timedOut.get()) throw new VoiceprintClient.Failure("声纹请求超时，请检查网络后重试；注册或删除需先核对云端状态");
            throw e;
        } finally { Arrays.fill(bytes,(byte)0); deadline.cancel(false); connection.disconnect(); }
    }
}
