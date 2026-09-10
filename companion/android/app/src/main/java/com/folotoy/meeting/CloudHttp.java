package com.folotoy.meeting;

import org.json.JSONObject;
import javax.net.ssl.HttpsURLConnection;
import java.net.URI;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.Map;

/** JSON-only transport: no audio capture, disk cache, redirects or secret logging. */
final class CloudHttp {
    static JSONObject request(String method, String url, Map<String,String> headers, String body, int limit) throws Exception {
        return request(method,url,headers,body,limit,false);
    }
    static JSONObject request(String method, String url, Map<String,String> headers, String body, int limit, boolean oauth) throws Exception {
        URI uri=URI.create(url);
        if(!"https".equals(uri.getScheme()) || uri.getUserInfo()!=null) throw new IOException("仅允许 HTTPS 服务");
        HttpsURLConnection connection=(HttpsURLConnection)uri.toURL().openConnection();
        connection.setRequestMethod(method); connection.setConnectTimeout(15000); connection.setReadTimeout(20000);
        connection.setInstanceFollowRedirects(false); connection.setUseCaches(false);
        for(var h:headers.entrySet()) connection.setRequestProperty(h.getKey(),h.getValue());
        try {
            if(body!=null) {
                connection.setDoOutput(true); byte[] bytes=body.getBytes(StandardCharsets.UTF_8);
                connection.setFixedLengthStreamingMode(bytes.length);
                try(OutputStream out=connection.getOutputStream()) { out.write(bytes); }
            }
            int status=connection.getResponseCode();
            if((status<200 || status>=300) && !(oauth && status==400)) throw new IOException("云服务返回 HTTP "+status+"，请检查账号权限和网络");
            try(InputStream in=status==400?connection.getErrorStream():connection.getInputStream(); ByteArrayOutputStream out=new ByteArrayOutputStream()) {
                if(in==null) throw new IOException("云服务没有返回内容");
                byte[] buffer=new byte[4096]; int count;
                while((count=in.read(buffer))!=-1) {
                    if(out.size()+count>limit) throw new IOException("结果超过本版读取上限，请在云服务中查看");
                    out.write(buffer,0,count);
                }
                return new JSONObject(out.toString(StandardCharsets.UTF_8.name()));
            }
        } catch(javax.net.ssl.SSLException e) { throw new IOException("安全连接失败，请检查手机时间和网络"); }
        finally { connection.disconnect(); }
    }
}
