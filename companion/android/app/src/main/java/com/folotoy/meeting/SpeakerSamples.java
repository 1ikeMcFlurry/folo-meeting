package com.folotoy.meeting;

import org.json.*;
import java.util.*;
import java.util.regex.*;

/** Conservative, bounded sampling and explicit confirmation of per-meeting identities. */
final class SpeakerSamples {
    static final int MAX_SPEAKERS=8, CLIP_MS=5000;
    static final class Span {
        final String speaker; final long start,end;
        Span(String speaker,long start,long end) { this.speaker=speaker; this.start=start; this.end=end; }
    }
    static LinkedHashMap<String,List<Span>> plan(MeetingDraft draft) {
        LinkedHashMap<String,List<Span>> result=new LinkedHashMap<>();
        for(String id:draft.speakerIds()) result.put(id,new ArrayList<>());
        List<Span> words=new ArrayList<>();
        JSONArray paragraphs=draft.paragraphs();
        for(int i=0;i<paragraphs.length();i++) {
            JSONObject p=paragraphs.optJSONObject(i); if(p==null) continue;
            String id=p.optString("SpeakerId","未知"); JSONArray w=p.optJSONArray("Words");
            if(w==null) continue;
            for(int j=0;j<w.length();j++) {
                JSONObject word=w.optJSONObject(j); if(word==null) continue;
                double a=word.optDouble("Start",-1),b=word.optDouble("End",-1);
                if(!Double.isFinite(a) || !Double.isFinite(b) || a<0 || b<=a || b>86400000 || a!=Math.rint(a) || b!=Math.rint(b)) continue;
                words.add(new Span(id,(long)a,(long)b));
                if(words.size()>100000) throw new IllegalArgumentException("逐字稿过长，请手动设置本场姓名");
            }
        }
        words.sort(Comparator.comparingLong(s->s.start));
        // Merge only consecutive same-speaker words. Never bridge another person's turn.
        List<Span> runs=new ArrayList<>();
        for(Span s:words) {
            if(!runs.isEmpty()) {
                Span last=runs.get(runs.size()-1);
                if(last.speaker.equals(s.speaker) && s.start<=last.end+400) {
                    runs.set(runs.size()-1,new Span(s.speaker,last.start,Math.max(last.end,s.end))); continue;
                }
            }
            runs.add(s);
        }
        int count=0;
        for(var entry:result.entrySet()) {
            if(count++>=MAX_SPEAKERS || !entry.getKey().matches("[0-9]+")) continue;
            for(Span run:runs) {
                if(!entry.getKey().equals(run.speaker)) continue;
                if(run.end-run.start<CLIP_MS+300) continue;
                long cursor=run.start+150, end=run.end-150;
                // Subtract all other speakers' time ranges, including nested overlapping turns.
                for(Span other:runs) {
                    if(other.start>=end) break;
                    if(other.speaker.equals(run.speaker) || other.end<=cursor) continue;
                    append(entry.getValue(),run.speaker,cursor,Math.min(end,other.start-150));
                    cursor=Math.max(cursor,other.end+150);
                    if(cursor>=end) break;
                }
                append(entry.getValue(),run.speaker,cursor,end);
                if(entry.getValue().size()==2) break;
            }
        }
        return result;
    }
    private static void append(List<Span> out,String id,long start,long end) {
        while(out.size()<2 && start+CLIP_MS<=end) {
            out.add(new Span(id,start,start+CLIP_MS)); start+=CLIP_MS;
        }
    }
    static boolean usedForTraining(JSONObject state,String taskId,Span span) {
        JSONObject people=state.optJSONObject("people"); if(people==null) return false;
        for(Iterator<String> it=people.keys();it.hasNext();) {
            JSONObject person=people.optJSONObject(it.next()); JSONArray samples=person==null?null:person.optJSONArray("meeting_samples");
            if(samples==null) continue;
            for(int i=0;i<samples.length();i++) {
                JSONObject sample=samples.optJSONObject(i);
                if(sample!=null && taskId.equals(sample.optString("task_id")) && sample.optLong("start")<span.end && sample.optLong("end")>span.start) return true;
            }
        }
        return false;
    }
    static LinkedHashMap<String,List<Span>> recognitionPlan(MeetingDraft draft,JSONObject state) {
        var plan=plan(draft); String task=draft.data.optString("task_id");
        for(var clips:plan.values()) clips.removeIf(span->usedForTraining(state,task,span));
        return plan;
    }
    static JSONObject candidate(String id,List<Voiceprints.Match> matches) throws Exception {
        JSONObject row=new JSONObject().put("speaker",id).put("name","").put("clips",matches.size());
        if(matches.isEmpty()) return row.put("reason","没有可识别的清晰片段；请手动设置姓名");
        Voiceprints.Match first=matches.get(0);
        if(Double.isFinite(first.score)) row.put("score",first.score);
        if(first.candidateName.isBlank() || first.featureId.isBlank()) return row.put("reason",first.reason);
        for(Voiceprints.Match match:matches) {
            if(!first.featureId.equals(match.featureId) || match.candidateName.isBlank())
                return row.put("reason","两段声音未得到一致结果，保留发言人编号");
        }
        row.put("score",matches.stream().mapToDouble(m->m.score).min().orElse(first.score));
        boolean tentative=matches.stream().anyMatch(m->m.name.isBlank()) || (matches.size()==1 && first.score<0.85);
        if(tentative) return row.put("name",first.candidateName).put("feature_id",first.featureId).put("tentative",true)
            .put("reason","低相似度候选（仅供本人核对），不能据此认定身份。确认无误才勾选；不确定请保留编号");
        return row.put("name",first.name).put("feature_id",first.featureId).put("tentative",false)
            .put("reason",matches.size()==1?"仅一段声音支持，请仔细确认":"两段声音匹配一致，仍需你确认");
    }
    static String fingerprint(MeetingDraft draft) throws Exception {
        byte[] hash=java.security.MessageDigest.getInstance("SHA-256").digest(draft.paragraphs().toString().getBytes(java.nio.charset.StandardCharsets.UTF_8));
        StringBuilder out=new StringBuilder(); for(byte b:hash) out.append(String.format(Locale.ROOT,"%02x",b&255)); return out.toString();
    }
    static MeetingDraft apply(MeetingDraft original,JSONObject chosen,String fingerprint) throws Exception {
        if(!fingerprint(original).equals(fingerprint)) throw new IllegalStateException("逐字稿来源已变化，请重新识别");
        MeetingDraft next=new MeetingDraft(new JSONObject(original.data.toString()));
        JSONObject names=next.data.optJSONObject("speakers"); if(names==null) names=new JSONObject();
        Map<String,String> labels=new LinkedHashMap<>(),numbers=new LinkedHashMap<>();
        for(Iterator<String> it=chosen.keys();it.hasNext();) {
            String id=it.next(), name=chosen.getString(id).strip();
            if(!next.speakerIds().contains(id) || !id.matches("[0-9]+") || name.isEmpty() || name.length()>40 || name.chars().anyMatch(Character::isISOControl))
                throw new IllegalArgumentException("发言人姓名无效，请重新确认");
            if(!names.optString(id).isBlank()) continue; // A manual name always wins.
            labels.put("发言人 "+id,name); labels.put("发言人"+id,name);
            numbers.put(id,name); names.put(id,name);
        }
        String transcript=next.data.optString("transcript"), body=next.data.optString("body");
        if(!labels.isEmpty()) {
            String keys=String.join("|",labels.keySet().stream().map(Pattern::quote).toArray(String[]::new));
            Matcher m=Pattern.compile("(?m)^("+keys+")(?=：)").matcher(transcript); StringBuffer out=new StringBuffer();
            while(m.find()) m.appendReplacement(out,Matcher.quoteReplacement(labels.get(m.group(1))));
            m.appendTail(out); transcript=out.toString();
            // Replace explicit speaker-number references once; never infer pronouns or rewrite prose.
            m=Pattern.compile("发言人[ ]?([0-9]+)(?![0-9])").matcher(body); out=new StringBuffer();
            while(m.find()) m.appendReplacement(out,Matcher.quoteReplacement(numbers.getOrDefault(m.group(1),m.group())));
            m.appendTail(out); body=out.toString();
        }
        next.data.put("speakers",names).put("transcript",transcript).put("body",body);
        if(!numbers.isEmpty()) next.data.put("voice_names_confirmed",true);
        return next;
    }
}
