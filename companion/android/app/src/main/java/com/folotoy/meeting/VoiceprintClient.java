package com.folotoy.meeting;

import org.json.*;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.io.IOException;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.time.*;
import java.time.format.DateTimeFormatter;
import java.util.*;

/** Xfyun standalone voiceprint v1 (s1aa729d0), separate from Tingwu and realtime ASR. */
final class VoiceprintClient {
    static final String HOST="api.xf-yun.com", PATH="/v1/private/s1aa729d0", SERVICE="s1aa729d0";
    interface Transport { JSONObject post(String url,String body) throws Exception; }
    static final class Credentials {
        final String appId,apiKey,apiSecret;
        Credentials(String appId,String apiKey,String apiSecret) {
            this.appId=appId.strip(); this.apiKey=apiKey.strip(); this.apiSecret=apiSecret.strip();
            if(!this.appId.matches("[A-Za-z0-9_-]{1,64}") || !this.apiKey.matches("[A-Za-z0-9_-]{1,128}") ||
                    !this.apiSecret.matches("[A-Za-z0-9_-]{1,128}"))
                throw new IllegalArgumentException("请填写声纹服务的 AppID、APIKey 和 APISecret，检查是否有空格");
        }
        JSONObject json() throws JSONException { return new JSONObject().put("app_id",appId).put("api_key",apiKey).put("api_secret",apiSecret); }
        static Credentials from(JSONObject value) { return new Credentials(value.optString("app_id"),value.optString("api_key"),value.optString("api_secret")); }
    }
    private final Credentials credentials;
    private final Transport transport;
    static final class Failure extends IOException {
        final boolean missingGroup;
        Failure(String message) { this(message,false); }
        Failure(String message,boolean missingGroup) { super(message); this.missingGroup=missingGroup; }
    }
    VoiceprintClient(Credentials credentials,Transport transport) { this.credentials=credentials; this.transport=transport; }
    static VoiceprintClient live(Credentials credentials) {
        return new VoiceprintClient(credentials,VoiceprintHttp::post);
    }
    static String signedUrl(Credentials credentials,String date) throws Exception {
        String original="host: "+HOST+"\ndate: "+date+"\nPOST "+PATH+" HTTP/1.1";
        Mac mac=Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(credentials.apiSecret.getBytes(StandardCharsets.UTF_8),"HmacSHA256"));
        String signature=Base64.getEncoder().encodeToString(mac.doFinal(original.getBytes(StandardCharsets.UTF_8)));
        String authorization="api_key=\""+credentials.apiKey+"\", algorithm=\"hmac-sha256\", headers=\"host date request-line\", signature=\""+signature+"\"";
        return "https://"+HOST+PATH+"?authorization="+escape(Base64.getEncoder().encodeToString(authorization.getBytes(StandardCharsets.UTF_8)))+
                "&host="+HOST+"&date="+escape(date);
    }
    private static String escape(String value) throws Exception { return URLEncoder.encode(value,"UTF-8"); }
    Object call(String func,String groupId,JSONObject extra,byte[] wav) throws Exception {
        if(!Arrays.asList("createGroup","createFeature","queryFeatureList","searchFea","deleteFeature","updateFeature").contains(func) ||
                !groupId.matches("[A-Za-z0-9_]{1,32}")) throw new IllegalArgumentException("声纹请求参数无效");
        JSONObject parameters=new JSONObject(extra.toString()).put("func",func).put("groupId",groupId)
                .put(func+"Res",new JSONObject().put("encoding","utf8").put("compress","raw").put("format","json"));
        JSONObject request=new JSONObject().put("header",new JSONObject().put("app_id",credentials.appId).put("status",3))
                .put("parameter",new JSONObject().put(SERVICE,parameters));
        if(wav!=null) {
            VoiceSample.requireWav(wav);
            request.put("payload",new JSONObject().put("resource",new JSONObject().put("encoding","raw")
                    .put("sample_rate",16000).put("channels",1).put("bit_depth",16).put("status",3)
                    .put("audio",Base64.getEncoder().encodeToString(wav))));
        } else if(func.equals("createFeature") || func.equals("searchFea") || func.equals("updateFeature")) throw new IllegalArgumentException("请先录制一段声音");
        String date=DateTimeFormatter.ofPattern("EEE, dd MMM yyyy HH:mm:ss 'GMT'",Locale.US).withZone(ZoneOffset.UTC).format(Instant.now());
        JSONObject response;
        try { response=transport.post(signedUrl(credentials,date),request.toString()); }
        catch(Exception e) { throw transportFailure(e); }
        return decode(response,func);
    }
    static Object decode(JSONObject response,String func) throws Exception {
        JSONObject header=response.optJSONObject("header");
        if(header==null || !header.has("code")) throw new IOException("声纹服务响应不完整，请核对状态后重试");
        int code=header.optInt("code",-1);
        if(code!=0) {
            String message=header.optString("message").toLowerCase(Locale.ROOT);
            // Live API returns 500/23009 for BOTH nonexistent and existing-empty groups.
            // Only the exact empty-group condition can establish that a group exists.
            if(func.equals("queryFeatureList") && code==23009 && message.endsWith("this groupid is empty")) return new JSONArray();
            boolean missing=func.equals("queryFeatureList") && code==23009 && message.endsWith("this group does not exist");
            throw new Failure(missing?"声纹库尚未创建，请点击准备声纹库（讯飞 23009）":
                    "讯飞错误码 "+code+"；请在控制台检查声纹服务权限、配额和配置",missing);
        }
        try {
            String text=response.getJSONObject("payload").getJSONObject(func+"Res").getString("text");
            byte[] bytes=Base64.getDecoder().decode(text);
            try {
                Object value=new JSONTokener(new String(bytes,StandardCharsets.UTF_8)).nextValue();
                if(func.equals("queryFeatureList") && value instanceof JSONArray) return value;
                if(!func.equals("queryFeatureList") && value instanceof JSONObject) return value;
            } finally { Arrays.fill(bytes,(byte)0); }
        } catch(Exception ignored) {}
        throw new IOException("声纹服务未返回可确认的业务结果，请核对云端状态");
    }
    static JSONObject httpResponse(int status,JSONObject response) throws Exception {
        if(status>=200 && status<300) return response;
        String message=response.optString("message").toLowerCase(Locale.ROOT);
        if(status==401 && message.contains("apikey not found")) throw new Failure("APIKey 未被讯飞识别（HTTP 401）。请重新复制 APIKey，检查是否与 APISecret 填反");
        if(status==401 && message.contains("signature does not match")) throw new Failure("签名校验失败（HTTP 401）。请重新复制 APISecret，保留大小写，检查是否与 APIKey 填反");
        if(status==403 && (message.contains("date") || message.contains("clock"))) throw new Failure("手机时间校验失败（HTTP 403），请在系统设置中开启自动日期和时间");
        JSONObject header=response.optJSONObject("header");
        // Preserve structured provider business errors for decode(), even on HTTP 500.
        if(header!=null && header.has("code") && header.optInt("code",0)!=0) return response;
        if(status==401) throw new Failure("声纹鉴权失败（HTTP 401），请核对同一应用的 APIKey 和 APISecret");
        if(status==403) throw new Failure("声纹请求被拒绝（HTTP 403），请检查账号权限、IP 白名单和手机时间");
        if(status==429) throw new Failure("声纹调用过于频繁（HTTP 429），请稍后重试并检查额度");
        throw new Failure("声纹服务返回 HTTP "+status+"，请稍后重试并核对云端状态");
    }
    static Failure transportFailure(Exception e) {
        if(e instanceof Failure failure) return failure;
        if(e instanceof java.net.UnknownHostException) return new Failure("无法解析讯飞域名，请检查手机联网和 DNS，或换一个网络重试");
        if(e instanceof java.net.SocketTimeoutException) return new Failure("声纹请求超时，请检查网络后重试；注册或删除需先核对云端状态");
        if(e instanceof java.net.ConnectException) return new Failure("无法连接讯飞服务，请检查手机网络、代理或防火墙");
        if(e instanceof javax.net.ssl.SSLException) return new Failure("声纹安全连接失败，请检查系统时间、网络代理和证书");
        return new Failure("声纹连接中断，请检查网络后重试；注册或删除需先核对云端状态");
    }
    static JSONObject object(Object value) throws IOException {
        if(value instanceof JSONObject object) {
            if(object.has("error") || (object.has("code") && object.optInt("code",-1)!=0) ||
                    (object.has("msg") && !"success".equals(object.optString("msg"))))
                throw new IOException("声纹服务未确认操作成功，请核对权限、配额和云端状态");
            return object;
        }
        throw new IOException("声纹服务结果格式异常");
    }
    static void requireId(Object value,String key,String expected) throws Exception {
        if(!expected.equals(object(value).optString(key))) throw new IOException("声纹服务尚未确认保存，请核对云端状态");
    }
    static void requireDeleted(Object value) throws Exception {
        if(!"success".equals(object(value).optString("msg"))) throw new IOException("云端尚未确认删除；本地保留待核对记录");
    }
}
