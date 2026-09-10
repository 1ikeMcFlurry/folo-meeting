package com.folotoy.meeting;

import org.junit.Test;
import org.json.*;
import java.io.IOException;
import java.net.*;
import java.nio.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import static org.junit.Assert.*;

public class VoiceprintTest {
    private final VoiceprintClient.Credentials credentials=new VoiceprintClient.Credentials("demo_app","test_key","test_secret");
    private static byte[] wav() { return VoiceSample.wav(new short[VoiceSample.SAMPLES]); }
    private static JSONObject envelope(String func,Object value) throws Exception {
        return new JSONObject().put("header",new JSONObject().put("code",0)).put("payload",new JSONObject().put(func+"Res",
                new JSONObject().put("text",Base64.getEncoder().encodeToString(value.toString().getBytes(StandardCharsets.UTF_8)))));
    }
    private static JSONObject person(String name,String state) throws Exception { return new JSONObject().put("name",name).put("state",state); }
    private static JSONObject scores(Object... pairs) throws Exception {
        JSONArray rows=new JSONArray(); for(int i=0;i<pairs.length;i+=2) rows.put(new JSONObject().put("featureId",pairs[i]).put("score",pairs[i+1]));
        return new JSONObject().put("scoreList",rows);
    }
    @Test public void signatureMatchesIndependentHmacVector() throws Exception {
        String url=VoiceprintClient.signedUrl(credentials,"Fri, 23 Apr 2021 02:35:47 GMT");
        URI uri=URI.create(url); assertEquals("https",uri.getScheme()); assertEquals("api.xf-yun.com",uri.getHost());
        assertEquals("/v1/private/s1aa729d0",uri.getPath());
        Map<String,String> query=new HashMap<>(); for(String part:uri.getRawQuery().split("&")) {
            String[] pair=part.split("=",2); query.put(pair[0],URLDecoder.decode(pair[1],"UTF-8"));
        }
        // Fixture independently calculated with Python hmac/hashlib, not this implementation.
        String auth=new String(Base64.getDecoder().decode(query.get("authorization")),StandardCharsets.UTF_8);
        assertEquals("api_key=\"test_key\", algorithm=\"hmac-sha256\", headers=\"host date request-line\", signature=\"u2JOPYBZc3jGVDzlTmpg18MYQRs8BwXfgkl2e1s7kXk=\"",auth);
        assertEquals("Fri, 23 Apr 2021 02:35:47 GMT",query.get("date")); assertFalse(url.contains("test_secret"));
    }
    @Test public void wavHasLittleEndianPcmAndLevelGateRejectsSilenceAndClipping() {
        short[] pcm=new short[VoiceSample.SAMPLES]; assertFalse(VoiceSample.qualityProblem(pcm).isEmpty());
        for(int i=0;i<pcm.length;i++) pcm[i]=(short)(2000*Math.sin(i*0.2)); pcm[0]=0x1234;
        assertEquals("",VoiceSample.qualityProblem(pcm)); byte[] wav=VoiceSample.wav(pcm); VoiceSample.requireWav(wav);
        ByteBuffer bytes=ByteBuffer.wrap(wav).order(ByteOrder.LITTLE_ENDIAN);
        assertEquals(160044,wav.length); assertEquals(16000,bytes.getInt(24)); assertEquals(32000,bytes.getInt(28));
        assertEquals(0x34,wav[44]&255); assertEquals(0x12,wav[45]&255);
        Arrays.fill(pcm,Short.MAX_VALUE); assertFalse(VoiceSample.qualityProblem(pcm).isEmpty());
        wav[24]=0; assertThrows(IllegalArgumentException.class,()->VoiceSample.requireWav(wav));
    }
    @Test public void payloadUsesNewStandaloneServiceAndRawWav() throws Exception {
        VoiceprintClient client=new VoiceprintClient(credentials,(url,body)-> {
            JSONObject request=new JSONObject(body),service=request.getJSONObject("parameter").getJSONObject("s1aa729d0");
            assertEquals("createFeature",service.getString("func")); assertEquals("p_a",service.getString("featureId"));
            assertEquals("utf8",service.getJSONObject("createFeatureRes").getString("encoding"));
            JSONObject audio=request.getJSONObject("payload").getJSONObject("resource");
            assertEquals("raw",audio.getString("encoding")); assertEquals(16000,audio.getInt("sample_rate"));
            assertEquals(16,audio.getInt("bit_depth")); assertEquals(1,audio.getInt("channels"));
            VoiceSample.requireWav(Base64.getDecoder().decode(audio.getString("audio")));
            assertFalse(body.contains("test_key")); assertFalse(body.contains("test_secret"));
            return envelope("createFeature",new JSONObject().put("featureId","p_a"));
        });
        VoiceprintClient.requireId(client.call("createFeature","folo_a",new JSONObject().put("featureId","p_a"),wav()),"featureId","p_a");
    }
    @Test public void successfulEnvelopeDoesNotProveBusinessSuccess() throws Exception {
        Object rejected=VoiceprintClient.decode(envelope("deleteFeature",new JSONObject().put("msg","failed")),"deleteFeature");
        assertThrows(IOException.class,()->VoiceprintClient.requireDeleted(rejected));
        Object wrongId=VoiceprintClient.decode(envelope("createFeature",new JSONObject().put("featureId","another")),"createFeature");
        assertThrows(IOException.class,()->VoiceprintClient.requireId(wrongId,"featureId","expected"));
        assertThrows(IOException.class,()->VoiceprintClient.requireId(new JSONObject().put("featureId","expected").put("code",99),"featureId","expected"));
        assertThrows(IOException.class,()->VoiceprintClient.decode(envelope("queryFeatureList",new JSONObject().put("msg","failed")),"queryFeatureList"));
        assertThrows(IOException.class,()->VoiceprintClient.decode(new JSONObject().put("header",new JSONObject().put("code",0)),"searchFea"));
    }
    @Test public void networkErrorsNeverExposeUrlOrCredentials() {
        VoiceprintClient client=new VoiceprintClient(credentials,(url,body)-> { throw new IOException(url+" test_secret "+body); });
        Exception error=assertThrows(IOException.class,()->client.call("createGroup","folo_a",new JSONObject(),null));
        assertFalse(error.getMessage().contains("test_secret")); assertFalse(error.getMessage().contains("https://")); assertNull(error.getCause());
    }
    private static JSONObject groupError(String suffix) throws Exception {
        return new JSONObject().put("header",new JSONObject().put("code",23009)
                .put("message","failed to query feature list detail: groupId: folo_test, "+suffix));
    }
    @Test public void existingEmptyGroupIsAnEmptyListEvenOnHttp500() throws Exception {
        JSONObject response=VoiceprintClient.httpResponse(500,groupError("this groupId is empty"));
        Object result=VoiceprintClient.decode(response,"queryFeatureList");
        assertTrue(result instanceof JSONArray); assertEquals(0,((JSONArray)result).length());
        assertThrows(VoiceprintClient.Failure.class,()->VoiceprintClient.decode(response,"createGroup"));
        assertThrows(VoiceprintClient.Failure.class,()->VoiceprintClient.httpResponse(500,envelope("queryFeatureList",new JSONArray())));
    }
    @Test public void missingGroupIsNotMistakenForAnExistingEmptyGroup() throws Exception {
        JSONObject response=VoiceprintClient.httpResponse(500,groupError("this group does not exist"));
        VoiceprintClient.Failure error=assertThrows(VoiceprintClient.Failure.class,()->VoiceprintClient.decode(response,"queryFeatureList"));
        assertTrue(error.missingGroup);
        VoiceprintClient.Failure other=assertThrows(VoiceprintClient.Failure.class,()->VoiceprintClient.decode(groupError("other failure"),"queryFeatureList"));
        assertFalse(other.missingGroup);
    }
    @Test public void authenticationAndNetworkFailuresAreActionableWithoutEchoingSecrets() throws Exception {
        VoiceprintClient.Failure key=assertThrows(VoiceprintClient.Failure.class,()->VoiceprintClient.httpResponse(401,
                new JSONObject().put("message","HMAC signature cannot be verified: apikey not found test_secret")));
        assertTrue(key.getMessage().contains("APIKey")); assertFalse(key.getMessage().contains("test_secret"));
        VoiceprintClient.Failure signature=assertThrows(VoiceprintClient.Failure.class,()->VoiceprintClient.httpResponse(401,
                new JSONObject().put("message","HMAC signature does not match https://example.invalid/secret")));
        assertTrue(signature.getMessage().contains("APISecret")); assertFalse(signature.getMessage().contains("example"));
        VoiceprintClient.Failure time=assertThrows(VoiceprintClient.Failure.class,()->VoiceprintClient.httpResponse(403,
                new JSONObject().put("message","a valid date or x-date header is required")));
        assertTrue(time.getMessage().contains("时间"));
        assertTrue(VoiceprintClient.transportFailure(new UnknownHostException("secret URL")).getMessage().contains("DNS"));
        assertTrue(VoiceprintClient.transportFailure(new SocketTimeoutException("secret URL")).getMessage().contains("超时"));
        assertTrue(VoiceprintClient.transportFailure(new javax.net.ssl.SSLException("secret URL")).getMessage().contains("安全连接"));
        assertSame(key,VoiceprintClient.transportFailure(key));
    }
    @Test public void connectionCheckCanConfirmAuthenticationWithoutCreatingOrMarkingAGroupReady() throws Exception {
        Memory memory=new Memory(); List<String> calls=new ArrayList<>();
        Voiceprints service=new Voiceprints(memory,new VoiceprintClient(credentials,(url,body)-> {
            calls.add(new JSONObject(body).getJSONObject("parameter").getJSONObject("s1aa729d0").getString("func"));
            return VoiceprintClient.httpResponse(500,groupError("this group does not exist"));
        }),credentials.appId);
        service.checkConnection(); assertEquals(Collections.singletonList("queryFeatureList"),calls); assertTrue(memory.values.isEmpty());
    }
    @Test public void lostCreateResponseRecoversThroughAnExistingEmptyGroup() throws Exception {
        Memory memory=new Memory();
        Voiceprints service=new Voiceprints(memory,new VoiceprintClient(credentials,(url,body)-> {
            String func=new JSONObject(body).getJSONObject("parameter").getJSONObject("s1aa729d0").getString("func");
            if(func.equals("createGroup")) throw new IOException("lost response after successful creation");
            return VoiceprintClient.httpResponse(500,groupError("this groupId is empty"));
        }),credentials.appId);
        service.prepare(); assertTrue(service.snapshot().getBoolean("ready"));
        service.prepare(); assertTrue(service.snapshot().getBoolean("ready")); assertEquals(0,Voiceprints.readyCount(service.snapshot()));
    }
    @Test public void matchingSortsAndRejectsWeakAmbiguousOrUnknownCandidates() throws Exception {
        JSONObject people=new JSONObject().put("a",person("小王","ready")).put("b",person("小李","ready"));
        assertEquals("小王",Voiceprints.match(scores("b",0.60,"a",0.90),people,2).name);
        assertEquals("",Voiceprints.match(scores("a",0.70),people,1).name);
        assertEquals("",Voiceprints.match(scores("a",0.90,"b",0.85),people,2).name);
        assertEquals("",Voiceprints.match(scores("foreign",0.99),people,1).name);
        assertEquals("",Voiceprints.match(scores("a",0.99),people,2).name);
        assertEquals("",Voiceprints.match(scores("a",.59),people,1).candidateName);
        var weak=Voiceprints.match(scores("a",.62),people,1); assertEquals("",weak.name); assertEquals("小王",weak.candidateName);
        assertEquals("",Voiceprints.match(scores("a",.65,"b",.60),people,2).candidateName);
        assertEquals("",Voiceprints.match(scores(),people,1).name);
        people.getJSONObject("a").put("state","pending_delete");
        assertEquals("",Voiceprints.match(scores("a",0.99),people,1).name);
    }
    @Test public void malformedScoresAreNotPresentedAsIdentity() throws Exception {
        JSONObject people=new JSONObject().put("a",person("小王","ready"));
        assertThrows(IOException.class,()->Voiceprints.match(scores("a",1.1),people,1));
        assertThrows(IOException.class,()->Voiceprints.match(scores("a","NaN"),people,1));
        assertThrows(IOException.class,()->Voiceprints.match(scores("a",0.99,"a",0.50),people,2));
        assertThrows(IOException.class,()->Voiceprints.match(new JSONObject().put("code",0),people,1));
    }
    private static final class Memory implements Voiceprints.Store {
        final Map<String,String> values=new HashMap<>();
        public JSONObject load(String key) throws Exception { return new JSONObject(values.getOrDefault(key,"{}")); }
        public void save(String key,JSONObject value) { values.put(key,value.toString()); }
    }
    @Test public void supplementMergesOnlyAfterPersistingIntentAndCannotReplayTrainingAudio() throws Exception {
        Memory memory=new Memory(); String key=Voiceprints.accountKey(credentials.appId),task="a".repeat(32); int[] calls={0};
        memory.save(key,new JSONObject().put("ready",true).put("group_id","folo_group").put("people",new JSONObject().put("p_a",person("本人","ready"))));
        Voiceprints service=new Voiceprints(memory,new VoiceprintClient(credentials,(url,body)-> {
            calls[0]++; JSONObject p=new JSONObject(body).getJSONObject("parameter").getJSONObject(VoiceprintClient.SERVICE);
            assertEquals("updateFeature",p.getString("func")); assertFalse(p.getBoolean("cover")); assertEquals("p_a",p.getString("featureId"));
            assertFalse(body.contains("本人")); assertFalse(body.contains(task));
            JSONObject stored=memory.load(key).getJSONObject("people").getJSONObject("p_a"); assertEquals("pending_update",stored.getString("state"));
            assertEquals(p.getString("featureInfo"),stored.getString("update_tag")); assertEquals(1,stored.getJSONArray("meeting_samples").length());
            return envelope("updateFeature",new JSONObject().put("msg","success"));
        }),credentials.appId);
        var span=new SpeakerSamples.Span("1",150,5150); service.supplement("p_a",wav(),task,span);
        assertEquals(1,Voiceprints.readyCount(service.snapshot())); assertEquals(1,calls[0]);
        assertThrows(IllegalStateException.class,()->service.supplement("p_a",wav(),task,span)); assertEquals(1,calls[0]);
    }
    @Test public void uncertainSupplementRequiresMatchingUpdateMarkerToBecomeReady() throws Exception {
        Memory memory=new Memory(); String key=Voiceprints.accountKey(credentials.appId),task="a".repeat(32); String[] tag={""}; boolean[] reflect={false};
        memory.save(key,new JSONObject().put("ready",true).put("group_id","folo_group").put("people",new JSONObject().put("p_a",person("本人","ready"))));
        Voiceprints service=new Voiceprints(memory,new VoiceprintClient(credentials,(url,body)-> {
            JSONObject p=new JSONObject(body).getJSONObject("parameter").getJSONObject(VoiceprintClient.SERVICE);
            if(p.getString("func").equals("updateFeature")) { tag[0]=p.getString("featureInfo"); throw new IOException("response lost"); }
            return envelope("queryFeatureList",new JSONArray().put(new JSONObject().put("featureId","p_a").put("featureInfo",reflect[0]?tag[0]:"old")));
        }),credentials.appId);
        var span=new SpeakerSamples.Span("1",150,5150);
        assertThrows(IOException.class,()->service.supplement("p_a",wav(),task,span));
        assertEquals(0,Voiceprints.readyCount(service.snapshot())); assertTrue(SpeakerSamples.usedForTraining(service.snapshot(),task,span));
        service.reconcile(); assertEquals(0,Voiceprints.readyCount(service.snapshot()));
        reflect[0]=true; service.reconcile(); assertEquals(1,Voiceprints.readyCount(service.snapshot()));
    }
    private final class Fake {
        final Memory memory=new Memory(); final Set<String> remote=new HashSet<>();
        final List<String> createdGroups=new ArrayList<>();
        boolean groupExists,loseCreate,loseGroup,failDelete,partialList;
        Voiceprints instance(String appId) throws Exception {
            return new Voiceprints(memory,new VoiceprintClient(new VoiceprintClient.Credentials(appId,"test_key","test_secret"),(url,body)-> {
                JSONObject params=new JSONObject(body).getJSONObject("parameter").getJSONObject("s1aa729d0");
                String func=params.getString("func"),group=params.getString("groupId"); Object result;
                if(func.equals("createGroup")) {
                    createdGroups.add(group); groupExists=true;
                    if(loseGroup) throw new IOException("response lost");
                    result=new JSONObject().put("groupId",group);
                } else if(func.equals("queryFeatureList")) {
                    if(!groupExists || loseGroup) throw new IOException("offline");
                    JSONArray list=new JSONArray(); if(!partialList) for(String id:remote) list.put(new JSONObject().put("featureId",id)); result=list;
                } else if(func.equals("createFeature")) {
                    String id=params.getString("featureId");
                    JSONObject local=memory.load(Voiceprints.accountKey(appId)).getJSONObject("people");
                    assertEquals("pending_create",local.getJSONObject(id).getString("state"));
                    assertFalse(body.contains("小王")); remote.add(id);
                    if(loseCreate) throw new IOException("response lost");
                    result=new JSONObject().put("featureId",id);
                } else if(func.equals("deleteFeature")) {
                    String id=params.getString("featureId");
                    assertEquals("pending_delete",memory.load(Voiceprints.accountKey(appId)).getJSONObject("people").getJSONObject(id).getString("state"));
                    if(!failDelete) remote.remove(id); result=new JSONObject().put("msg",failDelete?"failed":"success");
                } else { assertEquals("searchFea",func); assertEquals(1,params.getInt("topK")); result=scores(remote.iterator().next(),0.90); }
                return envelope(func,result);
            }),appId);
        }
    }
    @Test public void registrationSurvivesLostResponseAndRestartWithoutDuplicate() throws Exception {
        Fake fake=new Fake(); Voiceprints first=fake.instance("demo_app"); first.prepare(); fake.loseCreate=true;
        assertThrows(IOException.class,()->first.enroll("小王",wav())); assertEquals(1,fake.remote.size());
        Voiceprints restarted=fake.instance("demo_app"); assertEquals(0,Voiceprints.readyCount(restarted.snapshot()));
        assertThrows(IllegalStateException.class,()->restarted.enroll("小王",wav())); assertEquals(1,fake.remote.size());
        restarted.reconcile(); assertEquals(1,Voiceprints.readyCount(restarted.snapshot()));
        assertEquals("小王",restarted.identify(wav()).name);
    }
    @Test public void uncertainGroupCreationReusesPersistedId() throws Exception {
        Fake fake=new Fake(); fake.loseGroup=true; Voiceprints first=fake.instance("demo_app");
        assertThrows(IOException.class,first::prepare); assertFalse(first.snapshot().optBoolean("ready"));
        String group=first.snapshot().getString("group_id"); fake.loseGroup=false;
        Voiceprints next=fake.instance("demo_app"); next.prepare();
        assertEquals(group,next.snapshot().getString("group_id")); assertEquals(fake.createdGroups.get(0),fake.createdGroups.get(1));
    }
    @Test public void deletionMustBeConfirmedAndPartialListCannotClearPendingRecord() throws Exception {
        Fake fake=new Fake(); Voiceprints service=fake.instance("demo_app"); service.prepare(); service.enroll("小王",wav());
        String id=fake.remote.iterator().next(); fake.failDelete=true;
        assertThrows(IOException.class,()->service.delete(id)); assertEquals(0,Voiceprints.readyCount(service.snapshot()));
        fake.partialList=true; service.reconcile(); assertTrue(service.snapshot().getJSONObject("people").has(id));
        assertEquals("pending_delete",service.snapshot().getJSONObject("people").getJSONObject(id).getString("state"));
        fake.failDelete=false; service.delete(id); assertEquals(0,service.snapshot().getJSONObject("people").length()); assertTrue(fake.remote.isEmpty());
    }
    @Test public void changingAppIdDoesNotMixOrErasePeople() throws Exception {
        Fake fake=new Fake(); Voiceprints first=fake.instance("first_app"); first.prepare(); first.enroll("小王",wav());
        assertEquals(0,Voiceprints.readyCount(fake.instance("other_app").snapshot()));
        assertEquals(1,Voiceprints.readyCount(fake.instance("first_app").snapshot()));
        assertNotEquals(Voiceprints.accountKey("first_app"),Voiceprints.accountKey("other_app"));
        assertTrue(Voiceprints.accountKey("first_app").length()<=90);
    }
}
