package com.folotoy.meeting;

import org.json.*;
import java.io.IOException;
import java.util.*;

/** Revision-checked replacement with a durable journal. Old blocks survive until all new blocks are verified. */
final class DocumentWriter {
    interface Port {
        JSONObject fetch() throws Exception;
        void append(JSONArray blocks,long revision) throws Exception;
        void delete(int count,long revision) throws Exception;
        void title(String title,long revision) throws Exception;
        JSONObject load() throws Exception;
        void save(JSONObject journal) throws Exception;
    }
    private final Port port;
    DocumentWriter(Port port) { this.port=port; }
    static String canonical(Object value) throws Exception {
        if(value instanceof JSONObject) {
            JSONObject o=(JSONObject)value; List<String> keys=new ArrayList<>(); o.keys().forEachRemaining(keys::add); Collections.sort(keys);
            StringBuilder out=new StringBuilder("{");
            for(String k:keys) out.append(JSONObject.quote(k)).append(':').append(canonical(o.get(k))).append(',');
            return out.append('}').toString();
        }
        if(value instanceof JSONArray) {
            StringBuilder out=new StringBuilder("["); JSONArray a=(JSONArray)value;
            for(int i=0;i<a.length();i++) out.append(canonical(a.get(i))).append(',');
            return out.append(']').toString();
        }
        return value instanceof String ? JSONObject.quote((String)value) : String.valueOf(value);
    }
    static boolean same(Object a,Object b) throws Exception { return canonical(a).equals(canonical(b)); }
    static JSONArray slice(JSONArray items,int start,int end) throws Exception {
        JSONArray out=new JSONArray(); for(int i=start;i<end;i++) out.put(items.get(i)); return out;
    }
    static boolean desiredBlocks(JSONArray actual,JSONArray desired) throws Exception {
        if(actual.length()!=desired.length()) return false;
        for(int i=0;i<actual.length();i++) {
            JSONObject a=actual.getJSONObject(i),d=desired.getJSONObject(i);
            if(a.optInt("block_type")!=d.optInt("block_type") || !Feishu.textOf(a).equals(Feishu.textOf(d))) return false;
            if(a.optJSONArray("children")!=null && a.getJSONArray("children").length()>0) return false;
            String key=MeetingDocument.key(d.optInt("block_type"));
            JSONArray ae=a.getJSONObject(key).getJSONArray("elements"),de=d.getJSONObject(key).getJSONArray("elements");
            if(ae.length()!=de.length()) return false;
            for(int j=0;j<de.length();j++) {
                JSONObject ar=ae.getJSONObject(j).optJSONObject("text_run"),dr=de.getJSONObject(j).getJSONObject("text_run");
                if(ar==null || !ar.optString("content").equals(dr.optString("content"))) return false;
                JSONObject as=ar.optJSONObject("text_element_style"),ds=dr.optJSONObject("text_element_style");
                if((as!=null && as.optBoolean("bold"))!=(ds!=null && ds.optBoolean("bold"))) return false;
            }
        }
        return true;
    }
    static void simpleOnly(JSONArray items) throws Exception {
        for(int i=0;i<items.length();i++) {
            JSONObject b=items.getJSONObject(i); String key=MeetingDocument.key(b.optInt("block_type"));
            if(key.isEmpty() || (b.optJSONArray("children")!=null && b.getJSONArray("children").length()>0)
                || (b.optJSONArray("comment_ids")!=null && b.getJSONArray("comment_ids").length()>0))
                throw new IOException("飞书文档含嵌套内容、附件或评论，请在飞书中编辑，避免丢失内容");
            JSONArray e=b.getJSONObject(key).getJSONArray("elements");
            for(int j=0;j<e.length();j++) if(e.getJSONObject(j).optJSONObject("text_run")==null)
                throw new IOException("飞书文档含引用或其他富文本，请在飞书中编辑");
        }
    }
    static boolean unchanged(JSONObject a,JSONObject b) throws Exception {
        return a.optString("title").equals(b.optString("title")) && same(a.getJSONArray("items"),b.getJSONArray("items"));
    }
    private static IOException conflict() { return new IOException("飞书内容已变化，已停止写入。请核对飞书当前版本后再发布"); }
    JSONObject write(String title,JSONArray desired,JSONObject before) throws Exception {
        JSONObject journal=port.load(),latest=port.fetch();
        if(journal.has("snapshot")) {
            // Resume the exact saved operation even after process death or a lost HTTP response.
            latest=resume(journal,latest);
            before=latest;
        }
        if(latest.optString("title").equals(title) && desiredBlocks(latest.getJSONArray("items"),desired)) return latest;
        if(!unchanged(latest,before)) throw conflict();
        simpleOnly(latest.getJSONArray("items"));
        journal=new JSONObject().put("title",title).put("desired",desired).put("snapshot",latest)
            .put("remaining_old",latest.getJSONArray("items").length()).put("appended",0);
        port.save(journal);
        return resume(journal,latest);
    }
    private JSONObject resume(JSONObject j,JSONObject latest) throws Exception {
        while(true) {
            JSONObject previous=j.getJSONObject("snapshot"),pending=j.optJSONObject("pending");
            if(pending!=null) {
                String kind=pending.getString("kind"); int count=pending.optInt("count");
                JSONArray old=previous.getJSONArray("items"),now=latest.getJSONArray("items");
                boolean committed=false;
                if(kind.equals("append") && latest.optString("title").equals(previous.optString("title")) && now.length()==old.length()+count) {
                    committed=same(slice(now,0,old.length()),old) && desiredBlocks(slice(now,old.length(),now.length()),
                        slice(j.getJSONArray("desired"),j.getInt("appended"),j.getInt("appended")+count));
                } else if(kind.equals("delete")) committed=latest.optString("title").equals(previous.optString("title")) && same(now,slice(old,count,old.length()));
                else if(kind.equals("title")) committed=latest.optString("title").equals(j.getString("title")) && same(now,old);
                if(committed) {
                    if(kind.equals("append")) j.put("appended",j.getInt("appended")+count);
                    if(kind.equals("delete")) j.put("remaining_old",j.getInt("remaining_old")-count);
                    j.put("snapshot",latest); j.remove("pending"); port.save(j); continue;
                }
                if(!unchanged(latest,previous)) throw conflict();
                long revision=latest.getLong("revision");
                if(kind.equals("append")) port.append(slice(j.getJSONArray("desired"),j.getInt("appended"),j.getInt("appended")+count),revision);
                else if(kind.equals("delete")) port.delete(count,revision);
                else port.title(j.getString("title"),revision);
                latest=port.fetch();
                // Don't repeat a successful response whose readback has unexpected content.
                if(unchanged(latest,previous)) throw new IOException("飞书写入后尚未读到更新，请稍后重试原文档");
                continue;
            }
            if(!unchanged(latest,previous)) throw conflict();
            JSONArray desired=j.getJSONArray("desired");
            if(j.getInt("appended")<desired.length()) pending=new JSONObject().put("kind","append").put("count",Math.min(50,desired.length()-j.getInt("appended")));
            else if(j.getInt("remaining_old")>0) pending=new JSONObject().put("kind","delete").put("count",Math.min(50,j.getInt("remaining_old")));
            else if(!latest.optString("title").equals(j.getString("title"))) pending=new JSONObject().put("kind","title");
            else {
                if(!desiredBlocks(latest.getJSONArray("items"),desired)) throw new IOException("飞书排版回读不一致，请核对文档");
                port.save(new JSONObject()); return latest;
            }
            j.put("pending",pending); port.save(j);
        }
    }
}
