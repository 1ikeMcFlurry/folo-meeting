package com.folotoy.meeting;

import org.json.*;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;

/** Serialized by the activity's process-wide worker. Persist intent before a cloud mutation. */
final class Voiceprints {
    interface Store { JSONObject load(String key) throws Exception; void save(String key,JSONObject value) throws Exception; }
    private final Store store;
    private final VoiceprintClient client;
    private final String key;
    Voiceprints(Store store,VoiceprintClient client,String appId) throws Exception {
        this.store=store; this.client=client; this.key=accountKey(appId);
    }
    static String accountKey(String appId) throws Exception {
        byte[] hash=MessageDigest.getInstance("SHA-256").digest(appId.getBytes(StandardCharsets.UTF_8));
        StringBuilder value=new StringBuilder("voice_people_"); for(byte b:hash) value.append(String.format(Locale.ROOT,"%02x",b&255)); return value.toString();
    }
    JSONObject snapshot() throws Exception { return store.load(key); }
    void checkConnection() throws Exception {
        String group=snapshot().optString("group_id","folo_connection_check");
        try { client.call("queryFeatureList",group,new JSONObject(),null); }
        catch(VoiceprintClient.Failure e) { if(!e.missingGroup) throw e; }
        // A known 'group does not exist' business response still proves successful authentication.
        // This check does not change the group-ready state or claim registration/deletion succeeded.
    }
    private JSONObject people(JSONObject state) throws JSONException {
        JSONObject people=state.optJSONObject("people");
        if(people==null) { people=new JSONObject(); state.put("people",people); } return people;
    }
    void prepare() throws Exception {
        JSONObject state=snapshot();
        if(state.optBoolean("ready")) { reconcile(); return; }
        if(!state.has("group_id")) {
            state.put("group_id","folo_"+UUID.randomUUID().toString().replace("-","").substring(0,24)); store.save(key,state);
        }
        String group=state.getString("group_id");
        try { VoiceprintClient.requireId(client.call("createGroup",group,new JSONObject().put("groupName","Folo phone trial"),null),"groupId",group); }
        catch(Exception e) {
            // The first request may have succeeded before the connection was lost. Reuse this ID.
            try { client.call("queryFeatureList",group,new JSONObject(),null); }
            catch(Exception ignored) { throw e; }
        }
        state.put("ready",true); store.save(key,state);
    }
    void enroll(String name,byte[] wav) throws Exception {
        name=name.strip(); if(name.isEmpty() || name.length()>40) throw new IllegalArgumentException("姓名应为 1 至 40 个字符");
        VoiceSample.requireWav(wav);
        JSONObject state=snapshot(); if(!state.optBoolean("ready")) throw new IllegalStateException("请先准备声纹库");
        JSONObject people=people(state);
        for(Iterator<String> it=people.keys();it.hasNext();) if(name.equals(people.getJSONObject(it.next()).optString("name")))
            throw new IllegalStateException("已有同名声纹或待核对记录，请先核对或删除，再重新录入");
        String id="p_"+UUID.randomUUID().toString().replace("-","").substring(0,28);
        JSONObject person=new JSONObject().put("name",name).put("state","pending_create"); people.put(id,person);
        store.save(key,state);
        // Real names stay on this phone; the provider receives only opaque feature IDs.
        VoiceprintClient.requireId(client.call("createFeature",state.getString("group_id"),new JSONObject().put("featureId",id),wav),"featureId",id);
        person.put("state","ready"); store.save(key,state);
    }
    void reconcile() throws Exception {
        JSONObject state=snapshot(); if(!state.has("group_id")) throw new IllegalStateException("请先准备声纹库");
        JSONArray remote=(JSONArray)client.call("queryFeatureList",state.getString("group_id"),new JSONObject(),null);
        JSONObject people=people(state);
        for(int i=0;i<remote.length();i++) {
            JSONObject item=remote.optJSONObject(i); if(item==null) continue;
            JSONObject person=people.optJSONObject(item.optString("featureId"));
            if(person!=null && person.optString("state").equals("pending_create")) person.put("state","ready");
            if(person!=null && person.optString("state").equals("pending_update") &&
                    !person.optString("update_tag").isBlank() && person.optString("update_tag").equals(item.optString("featureInfo")))
                person.put("state","ready");
        }
        // The API may return a partial list. Absence does not confirm a deletion or failed creation.
        state.put("ready",true); store.save(key,state);
    }
    void supplement(String id,byte[] wav,String taskId,SpeakerSamples.Span span) throws Exception {
        VoiceSample.requireWav(wav);
        if(!taskId.matches("[a-f0-9]{32}")) throw new IllegalArgumentException("会议编号无效");
        JSONObject state=snapshot(), person=people(state).optJSONObject(id);
        if(person==null || !person.optString("state").equals("ready")) throw new IllegalStateException("请先核对这位发言人的云端声纹状态");
        JSONArray samples=person.optJSONArray("meeting_samples"); if(samples==null) samples=new JSONArray();
        if(SpeakerSamples.usedForTraining(state,taskId,span)) throw new IllegalStateException("这段声音已用于补充声纹；请换一场新录音，避免重复补录");
        if(samples.length()>=32) throw new IllegalStateException("已保存 32 次会议补录记录，请先在声纹管理核对样本");
        String tag="u_"+UUID.randomUUID().toString().replace("-","");
        samples.put(new JSONObject().put("task_id",taskId).put("start",span.start).put("end",span.end));
        person.put("meeting_samples",samples).put("state","pending_update").put("update_tag",tag);
        store.save(key,state); // Keep provenance even if the response is lost; never verify on training audio.
        Object response=client.call("updateFeature",state.getString("group_id"),new JSONObject().put("featureId",id)
            .put("featureInfo",tag).put("cover",false),wav);
        if(!"success".equals(VoiceprintClient.object(response).optString("msg"))) throw new IOException("讯飞尚未确认补录成功，请先核对云端状态");
        person.put("state","ready"); store.save(key,state);
    }
    void delete(String id) throws Exception {
        JSONObject state=snapshot(),people=people(state),person=people.optJSONObject(id);
        if(person==null) throw new IllegalStateException("本地没有这条声纹记录");
        person.put("state","pending_delete"); store.save(key,state);
        VoiceprintClient.requireDeleted(client.call("deleteFeature",state.getString("group_id"),new JSONObject().put("featureId",id),null));
        people.remove(id); store.save(key,state);
    }
    Match identify(byte[] wav) throws Exception {
        JSONObject state=snapshot(),people=people(state); int count=readyCount(state);
        if(count==0) throw new IllegalStateException("请先录入至少一位发言人的声纹");
        int topK=Math.min(2,count);
        JSONObject result=VoiceprintClient.object(client.call("searchFea",state.getString("group_id"),new JSONObject().put("topK",topK),wav));
        return match(result,people,topK);
    }
    static int readyCount(JSONObject state) {
        JSONObject people=state.optJSONObject("people"); int count=0;
        if(people!=null) for(Iterator<String> it=people.keys();it.hasNext();) {
            JSONObject person=people.optJSONObject(it.next()); if(person!=null && "ready".equals(person.optString("state"))) count++;
        } return count;
    }
    static final class Match {
        final String name,reason,featureId,candidateName; final double score;
        Match(String name,String reason,double score) { this(name,reason,score,""); }
        Match(String name,String reason,double score,String featureId) { this(name,reason,score,featureId,name); }
        Match(String name,String reason,double score,String featureId,String candidateName) { this.name=name; this.reason=reason; this.score=score; this.featureId=featureId; this.candidateName=candidateName; }
    }
    static Match match(JSONObject result,JSONObject people,int topK) throws Exception {
        JSONArray scores=result.optJSONArray("scoreList");
        if(scores==null || scores.length()>topK) throw new IOException("声纹服务未返回有效的相似度结果");
        if(scores.length()==0) return new Match("","没有匹配候选",Double.NaN);
        List<JSONObject> sorted=new ArrayList<>(); Set<String> seen=new HashSet<>();
        for(int i=0;i<scores.length();i++) {
            JSONObject row=scores.getJSONObject(i); double score=row.optDouble("score",Double.NaN);
            String id=row.optString("featureId");
            if(!Double.isFinite(score) || score < -1 || score>1 || id.isEmpty() || !seen.add(id)) throw new IOException("声纹服务相似度结果异常");
            sorted.add(row);
        }
        sorted.sort((a,b)->Double.compare(b.optDouble("score"),a.optDouble("score")));
        JSONObject best=sorted.get(0),person=people.optJSONObject(best.getString("featureId")); double score=best.getDouble("score");
        if(person==null || !person.optString("state").equals("ready")) return new Match("","候选声纹尚未在本机确认",score);
        if(scores.length()<topK) return new Match("","候选结果不足，暂不判断",score);
        if(sorted.size()>1 && score-sorted.get(1).getDouble("score")<0.10-1e-9) return new Match("","两位候选过于接近，请重新录制",score);
        if(score<0.75) return new Match("","相似度未达到试验门槛 0.75",score,best.getString("featureId"),score>=0.60?person.optString("name"):"");
        return new Match(person.optString("name"),"试验结果，需要本人确认",score,best.getString("featureId"));
    }
}
