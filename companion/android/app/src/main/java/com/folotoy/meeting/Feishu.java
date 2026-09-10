package com.folotoy.meeting;

import org.json.*;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.io.IOException;

final class Feishu {
    private final Vault vault;
    Feishu(Vault vault) { this.vault=vault; }
    private static String form(Map<String,String> fields) {
        List<String> pairs=new ArrayList<>(); fields.forEach((k,v)-> {
            try { pairs.add(URLEncoder.encode(k,"UTF-8")+"="+URLEncoder.encode(v,"UTF-8")); }
            catch(java.io.UnsupportedEncodingException impossible) { throw new IllegalStateException(impossible); }
        });
        return String.join("&",pairs);
    }
    JSONObject beginAuthorization(JSONObject settings) throws Exception {
        String id=settings.optString("feishu_app_id"), secret=settings.optString("feishu_app_secret");
        if(id.isBlank() || secret.isBlank()) throw new IOException("请先填写并保存飞书应用信息");
        String basic=Base64.getEncoder().encodeToString((id+":"+secret).getBytes(StandardCharsets.UTF_8));
        JSONObject result=CloudHttp.request("POST","https://accounts.feishu.cn/oauth/v1/device_authorization",
            Map.of("Content-Type","application/x-www-form-urlencoded","Authorization","Basic "+basic),
            form(Map.of("client_id",id,"scope","docx:document drive:drive offline_access")),32768,true);
        if(!result.has("device_code")) throw new IOException("该飞书应用暂不能完成设备授权，请检查应用权限和适用范围");
        return result;
    }
    JSONObject pollAuthorization(JSONObject settings,JSONObject authorization) throws Exception {
        JSONObject result=CloudHttp.request("POST","https://open.feishu.cn/open-apis/authen/v2/oauth/token",
            Map.of("Content-Type","application/x-www-form-urlencoded"),form(Map.of(
                "grant_type","urn:ietf:params:oauth:grant-type:device_code","client_id",settings.optString("feishu_app_id"),
                "client_secret",settings.optString("feishu_app_secret"),"device_code",authorization.getString("device_code"))),32768,true);
        String error=result.optString("error");
        if(!error.isEmpty()) return result;
        if(result.optString("access_token").isBlank()) throw new IOException("飞书尚未返回访问令牌");
        result.put("expires_at",System.currentTimeMillis()+result.optLong("expires_in",7200)*1000);
        return result;
    }
    synchronized String accessToken() throws Exception {
        JSONObject tokens=vault.load("feishu_phone"), settings=vault.load("settings");
        if(tokens.optLong("expires_at")>System.currentTimeMillis()+60000) return tokens.getString("access_token");
        if(tokens.optString("refresh_token").isBlank()) throw new IOException("请前往「设置 → 飞书连接」授权手机访问飞书");
        JSONObject body=new JSONObject().put("grant_type","refresh_token").put("client_id",settings.optString("feishu_app_id"))
            .put("client_secret",settings.optString("feishu_app_secret")).put("refresh_token",tokens.getString("refresh_token"));
        JSONObject fresh=CloudHttp.request("POST","https://open.feishu.cn/open-apis/authen/v2/oauth/token",
            Map.of("Content-Type","application/json"),body.toString(),32768,true);
        if(fresh.optString("access_token").isBlank() || fresh.optString("refresh_token").isBlank()) throw new IOException("飞书登录已失效，请重新授权");
        fresh.put("expires_at",System.currentTimeMillis()+fresh.optLong("expires_in",7200)*1000);
        vault.save("feishu_phone",fresh); // Atomic refresh before any document operation.
        return fresh.getString("access_token");
    }
    JSONObject api(String method,String path,JSONObject body) throws Exception {
        JSONObject response=CloudHttp.request(method,"https://open.feishu.cn/open-apis"+path,
            Map.of("Content-Type","application/json; charset=utf-8","Authorization","Bearer "+accessToken()),body==null?null:body.toString(),2*1024*1024);
        if(response.optInt("code",-1)!=0) throw new IOException("飞书接口未成功（"+response.optInt("code")+"），请检查权限或稍后核对文档");
        return response.getJSONObject("data");
    }
    static void checkId(String id) throws IOException { if(!id.matches("[A-Za-z0-9_]{1,64}")) throw new IOException("飞书文档编号无效"); }
    JSONObject fetch(String id) throws Exception {
        checkId(id);
        JSONObject doc=api("GET","/docx/v1/documents/"+id,null).getJSONObject("document");
        JSONArray items=new JSONArray(); String cursor=""; Set<String> seen=new HashSet<>();
        do {
            JSONObject children=api("GET","/docx/v1/documents/"+id+"/blocks/"+id+"/children?page_size=100"
                +(cursor.isEmpty()?"":"&page_token="+URLEncoder.encode(cursor,"UTF-8")),null);
            JSONArray page=children.getJSONArray("items"); for(int i=0;i<page.length();i++) items.put(page.get(i));
            if(items.length()>10000) throw new IOException("文档超过当前排版更新上限，请在飞书中编辑");
            if(!children.optBoolean("has_more")) break;
            cursor=children.optString("page_token");
            if(cursor.isEmpty() || !seen.add(cursor)) throw new IOException("飞书分页未完成，请稍后重试");
        } while(true);
        JSONObject after=api("GET","/docx/v1/documents/"+id,null).getJSONObject("document");
        if(after.getLong("revision_id")!=doc.getLong("revision_id")) throw new IOException("读取期间飞书文档发生变化，请重新获取");
        return new JSONObject().put("title",doc.optString("title")).put("revision",doc.getLong("revision_id"))
            .put("items",items).put("fingerprint",AcsSigner.sha(doc.optString("title")+"\n"+items.toString()));
    }
    static JSONArray elements(String text) throws Exception {
        JSONArray elements=new JSONArray();
        for(int i=0;i<text.length();) {
            int end=Math.min(i+1500,text.length()); if(end<text.length() && Character.isHighSurrogate(text.charAt(end-1))) end--;
            elements.put(new JSONObject().put("text_run",new JSONObject().put("content",text.substring(i,end)))); i=end;
        }
        if(elements.length()==0) elements.put(new JSONObject().put("text_run",new JSONObject().put("content"," ")));
        return elements;
    }
    static String textOf(JSONObject block) {
        if(block==null) return "";
        String key=MeetingDocument.key(block.optInt("block_type",2));
        JSONObject text=block.optJSONObject(key); if(text==null) return "";
        JSONArray elements=text.optJSONArray("elements"); StringBuilder out=new StringBuilder();
        if(elements!=null) for(int i=0;i<elements.length();i++) {
            JSONObject element=elements.optJSONObject(i),run=element==null?null:element.optJSONObject("text_run"); if(run!=null) out.append(run.optString("content"));
        }
        return out.toString();
    }
    String create(String title,String folder) throws Exception {
        JSONObject body=new JSONObject().put("title",title); if(!folder.isBlank()) body.put("folder_token",folder);
        return api("POST","/docx/v1/documents",body).getJSONObject("document").getString("document_id");
    }
    void acceptCurrent(String id) throws Exception {
        checkId(id);
        vault.save("document_write_"+id,new JSONObject());
    }
    JSONObject write(String id,String title,JSONArray blocks,JSONObject before) throws Exception {
        checkId(id);
        String root="/docx/v1/documents/"+id+"/blocks/"+id;
        return new DocumentWriter(new DocumentWriter.Port() {
            public JSONObject fetch() throws Exception { return Feishu.this.fetch(id); }
            public void append(JSONArray items,long revision) throws Exception {
                api("POST",root+"/children?document_revision_id="+revision,new JSONObject().put("children",items).put("index",-1));
            }
            public void delete(int count,long revision) throws Exception {
                api("DELETE",root+"/children/batch_delete?document_revision_id="+revision,new JSONObject().put("start_index",0).put("end_index",count));
            }
            public void title(String value,long revision) throws Exception {
                api("PATCH","/docx/v1/documents/"+id+"/blocks/batch_update?document_revision_id="+revision,
                    new JSONObject().put("requests",new JSONArray().put(new JSONObject().put("block_id",id)
                    .put("update_text_elements",new JSONObject().put("elements",elements(value))))));
            }
            public JSONObject load() throws Exception { return vault.load("document_write_"+id); }
            public void save(JSONObject journal) throws Exception { vault.save("document_write_"+id,journal); }
        }).write(title,blocks,before);
    }
}
