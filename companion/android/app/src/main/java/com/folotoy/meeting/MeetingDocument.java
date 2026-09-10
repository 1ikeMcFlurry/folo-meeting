package com.folotoy.meeting;

import org.json.*;
import java.time.*;
import java.time.format.DateTimeFormatter;
import java.util.*;

/** Presentation only: exports the saved edits, never asks a model to invent content. */
final class MeetingDocument {
    static String key(int type) {
        switch(type) {
            case 2: return "text";
            case 3: return "heading1";
            case 4: return "heading2";
            case 12: return "bullet";
            case 15: return "quote";
            default: return "";
        }
    }
    static JSONObject block(int type,String text,boolean bold) throws Exception {
        JSONArray elements=Feishu.elements(text);
        if(bold) for(int i=0;i<elements.length();i++) elements.getJSONObject(i).getJSONObject("text_run")
            .put("text_element_style",new JSONObject().put("bold",true));
        return new JSONObject().put("block_type",type).put(key(type),new JSONObject().put("elements",elements));
    }
    static void add(JSONArray out,int type,String text,boolean bold) throws Exception {
        if(text.isBlank()) return;
        // Bound each block as well as each request; don't truncate long recordings.
        for(int start=0;start<text.length();) {
            int end=Math.min(start+2000,text.length());
            if(end<text.length() && Character.isHighSurrogate(text.charAt(end-1))) end--;
            out.put(block(type,text.substring(start,end),bold)); start=end;
        }
    }
    static String time(long ms) {
        long seconds=ms/1000;
        return seconds>=3600 ? String.format(Locale.ROOT,"%02d:%02d:%02d",seconds/3600,seconds/60%60,seconds%60)
            : String.format(Locale.ROOT,"%02d:%02d",seconds/60,seconds%60);
    }
    static String range(JSONObject paragraph) {
        JSONArray words=paragraph.optJSONArray("Words"); long start=Long.MAX_VALUE,end=-1;
        if(words!=null) for(int i=0;i<words.length();i++) {
            JSONObject word=words.optJSONObject(i); if(word==null) continue;
            long s=word.optLong("Start",-1),e=word.optLong("End",-1);
            if(s>=0 && e>=s) { start=Math.min(start,s); end=Math.max(end,e); }
        }
        return end<0 ? "" : (start/1000==end/1000 ? time(start) : time(start)+"–"+time(end));
    }
    static JSONArray build(MeetingDraft draft) throws Exception {
        JSONArray out=new JSONArray(); JSONObject device=draft.data.optJSONObject("device");
        add(out,4,"会议信息",false);
        if(device!=null) {
            long created=device.optLong("created_at");
            if(created>0) add(out,12,"开始时间："+DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm z")
                .withZone(ZoneId.systemDefault()).format(Instant.ofEpochSecond(created)),false);
            long duration=device.optLong("elapsed_ms");
            if(duration>0) add(out,12,"录音时长："+time(duration),false);
            if(!device.optBoolean("complete")) add(out,15,"本场录音上传不完整，请结合实际会议核对内容。",false);
        }
        add(out,12,"转写与摘要：阿里听悟 · 发布内容采用手机当前编辑稿",false);
        add(out,4,"纪要与待办",false);
        String body=draft.data.optString("body").strip(); boolean actions=false;
        if(body.isEmpty()) add(out,2,"当前编辑稿未填写纪要。",false);
        for(String line:body.split("\\R")) {
            String text=line.strip(); if(text.isEmpty()) continue;
            if(text.equals("待办事项") || text.equals("## 待办事项")) {
                add(out,4,"待办事项",false); actions=true;
            } else if(text.matches("^[•*-]\\s+.*")) add(out,12,text.replaceFirst("^[•*-]\\s+",""),false);
            else add(out,2,line,false);
        }
        // Do not regenerate actions after a user has removed them from the draft.
        if(!actions && body.equals(draft.originalBody())) {
            add(out,4,"待办事项",false);
            add(out,2,"本次云端结果未提取到明确待办。",false);
        }
        add(out,4,"逐字稿",false);
        String transcript=draft.data.optString("transcript").strip();
        boolean aligned=!transcript.isEmpty() && transcript.equals(draft.renderTranscript());
        if(transcript.isEmpty()) add(out,2,"当前编辑稿没有逐字稿内容。",false);
        else {
            add(out,15,aligned ? "按发言顺序排列；时间为相对录音起点。发言人标签由自动分离生成，可在手机中改名。"
                : "以下为手机修订的逐字稿；为避免错配，修订后不自动附加原稿时间。",false);
            if(aligned) {
                JSONArray paragraphs=draft.paragraphs(); JSONObject names=draft.data.optJSONObject("speakers");
                for(int i=0;i<paragraphs.length();i++) {
                    JSONObject paragraph=paragraphs.optJSONObject(i); if(paragraph==null) continue;
                    String id=paragraph.optString("SpeakerId","未知"),name=names==null?"":names.optString(id);
                    if(name.isBlank()) name="发言人 "+id;
                    String span=range(paragraph);
                    add(out,2,name+(span.isEmpty()?"":"  ·  "+span),true);
                    StringBuilder words=new StringBuilder(); JSONArray w=paragraph.optJSONArray("Words");
                    if(w!=null) for(int j=0;j<w.length();j++) { JSONObject word=w.optJSONObject(j); if(word!=null) words.append(word.optString("Text")); }
                    add(out,2,words.toString(),false);
                }
            } else for(String paragraph:transcript.split("\\n\\s*\\n")) {
                int colon=paragraph.indexOf('：');
                if(colon>0 && colon<=45 && paragraph.substring(0,colon).indexOf('\n')<0) {
                    add(out,2,paragraph.substring(0,colon),true); add(out,2,paragraph.substring(colon+1),false);
                } else add(out,2,paragraph,false);
            }
        }
        add(out,15,"文字记录可能存在识别偏差，请核对姓名、数字和承诺事项。本文件不附加录音。",false);
        if(out.length()>5000) throw new IllegalArgumentException("当前记录超过 5000 个排版段落，请拆分后发布");
        return out;
    }
    static String plain(JSONArray blocks) {
        StringBuilder out=new StringBuilder();
        for(int i=0;i<blocks.length();i++) out.append(Feishu.textOf(blocks.optJSONObject(i))).append('\n');
        return out.toString();
    }
}
