package com.folotoy.meeting;

import org.json.*;
import java.util.*;

final class MeetingDraft {
    final JSONObject data;
    MeetingDraft(JSONObject data) { this.data=data; }
    void mergeDevice(JSONObject device) throws Exception {
        String id=device.optString("task_id");
        if(id.isEmpty()) return;
        if(data.has("task_id") && !data.optString("task_id").equals(id)) throw new IllegalArgumentException("会议编号不一致");
        data.put("task_id",id).put("device",new JSONObject(device.toString()));
        if(!data.has("title")) data.put("title",device.optString("title","会议纪要"));
        if(!device.optString("document_id").isEmpty()) data.put("document_id",device.getString("document_id"));
    }
    void importResult(JSONObject artifacts) throws Exception {
        // A refresh can refresh the original, but can NEVER replace a user draft.
        data.put("original",artifacts);
        if(!data.has("body")) data.put("body",originalBody());
        if(!data.has("speakers")) data.put("speakers",new JSONObject());
        if(!data.has("transcript")) data.put("transcript",renderTranscript());
    }
    String originalBody() {
        JSONObject artifacts=data.optJSONObject("original"); if(artifacts==null) return "";
        JSONObject summary=artifacts.optJSONObject("Summarization");
        summary=summary==null?null:summary.optJSONObject("Summarization");
        StringBuilder out=new StringBuilder(summary==null ? "" : summary.optString("ParagraphSummary"));
        JSONObject assistance=artifacts.optJSONObject("MeetingAssistance");
        assistance=assistance==null?null:assistance.optJSONObject("MeetingAssistance");
        JSONArray actions=assistance==null?null:assistance.optJSONArray("Actions");
        if(actions!=null && actions.length()>0) {
            out.append("\n\n待办事项\n");
            for(int i=0;i<actions.length();i++) {
                Object action=actions.opt(i);
                String text=action instanceof JSONObject ? ((JSONObject)action).optString("Text",((JSONObject)action).optString("Content")) : String.valueOf(action);
                if(!text.isBlank()) out.append("• ").append(text).append('\n');
            }
        }
        return out.toString().strip();
    }
    JSONArray paragraphs() {
        JSONObject original=data.optJSONObject("original"), t=original==null?null:original.optJSONObject("Transcription");
        t=t==null?null:t.optJSONObject("Transcription");
        JSONArray p=t==null?null:t.optJSONArray("Paragraphs"); return p==null?new JSONArray():p;
    }
    List<String> speakerIds() {
        Set<String> ids=new LinkedHashSet<>(); JSONArray paragraphs=paragraphs();
        for(int i=0;i<paragraphs.length();i++) { JSONObject p=paragraphs.optJSONObject(i); if(p!=null) ids.add(p.optString("SpeakerId","未知")); }
        return new ArrayList<>(ids);
    }
    String renderTranscript() {
        StringBuilder out=new StringBuilder(); JSONObject names=data.optJSONObject("speakers"); JSONArray paragraphs=paragraphs();
        for(int i=0;i<paragraphs.length();i++) {
            JSONObject p=paragraphs.optJSONObject(i); if(p==null) continue;
            String id=p.optString("SpeakerId","未知"), name=names==null?"":names.optString(id);
            out.append(name.isBlank()?"发言人 "+id:name).append("：");
            JSONArray words=p.optJSONArray("Words"); if(words!=null) for(int j=0;j<words.length();j++) {
                JSONObject word=words.optJSONObject(j); if(word!=null) out.append(word.optString("Text"));
            }
            out.append("\n\n");
        }
        return out.toString().strip();
    }
}
