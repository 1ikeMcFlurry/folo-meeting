package com.folotoy.meeting;

import org.json.*;
import java.net.URI;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.*;
import java.io.IOException;

final class Tingwu {
    private final JSONObject settings;
    Tingwu(JSONObject settings) { this.settings=settings; }
    JSONObject task(String id) throws Exception {
        if(!id.matches("[a-f0-9]{32}")) throw new IOException("会议编号无效");
        String path="/openapi/tingwu/v2/tasks/"+id;
        var headers=AcsSigner.sign("GET",path,"","",settings.optString("ak_id"),settings.optString("ak_secret"),
            Instant.now().truncatedTo(ChronoUnit.SECONDS).toString(),UUID.randomUUID().toString());
        JSONObject result=CloudHttp.request("GET","https://tingwu.cn-beijing.aliyuncs.com"+path,headers,null,128*1024);
        if(!"0".equals(result.optString("Code"))) throw new IOException("听悟查询失败，请检查账号权限");
        return result.getJSONObject("Data");
    }
    JSONObject results(String id) throws Exception {
        JSONObject task=task(id);
        requireComplete(task);
        JSONObject urls=task.optJSONObject("Result"), artifacts=new JSONObject();
        if(urls==null) throw new IOException("听悟没有返回结果");
        for(String kind:new String[]{"Transcription","Summarization","MeetingAssistance"}) {
            String url=urls.optString(kind); if(url.isEmpty()) continue;
            artifacts.put(kind,CloudHttp.request("GET",resultUrl(url),Collections.emptyMap(),null,16*1024*1024));
        }
        // Signed result URLs are deliberately never saved to the phone.
        return artifacts;
    }
    String audio(String id) throws Exception { return audioUrl(task(id)); }
    static String audioUrl(JSONObject task) throws Exception {
        requireComplete(task);
        String url=task.optString("OutputMp3Path");
        if(url.isBlank()) throw new IOException("本场没有可用录音。请开启「会后声纹识别用音频」，发送给录音器后重新录一场；旧会议不能补录音");
        return resultUrl(url);
    }
    private static void requireComplete(JSONObject task) throws IOException {
        String state=task.optString("TaskStatus");
        if("FAILED".equals(state) || "INVALID".equals(state)) throw new IOException("本场云端处理失败或任务无效，无法识别发言人；请重新录制");
        if("PAUSED".equals(state)) throw new IOException("本场录音已暂停或中断，听悟尚未生成最终结果；请检查录音器状态");
        if(!"COMPLETED".equals(state)) throw new IOException("纪要尚未完成，稍后点击获取结果");
    }
    static String resultUrl(String url) throws Exception {
        URI uri=URI.create(url);
        if(uri.getUserInfo()!=null || uri.getHost()==null || !uri.getHost().endsWith(".aliyuncs.com") ||
            uri.getFragment()!=null || (uri.getPort()!=-1 && uri.getPort()!=443) ||
            !("https".equals(uri.getScheme()) || "http".equals(uri.getScheme())))
            throw new IOException("听悟结果地址未通过验证");
        // Tingwu may return an HTTP capability. Change only its scheme before
        // any request; never transmit the signed URL over plaintext HTTP.
        return "http".equals(uri.getScheme()) ? "https"+url.substring(4) : url;
    }
}
