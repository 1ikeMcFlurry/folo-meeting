package com.folotoy.meeting;

import org.junit.Test;
import org.json.*;
import java.util.*;
import static org.junit.Assert.*;

public class SpeakerSamplesTest {
    private MeetingDraft draft(Object... turns) throws Exception {
        JSONArray p=new JSONArray();
        for(int i=0;i<turns.length;i+=3) p.put(new JSONObject().put("SpeakerId",turns[i]).put("Words",new JSONArray().put(new JSONObject().put("Start",turns[i+1]).put("End",turns[i+2]).put("Text","修订前。"))));
        MeetingDraft d=new MeetingDraft(new JSONObject()); d.importResult(new JSONObject().put("Transcription",new JSONObject().put("Transcription",new JSONObject().put("Paragraphs",p)))); return d;
    }
    @Test public void twoDisjointClipsHaveMarginsAndFiveSeconds() throws Exception {
        var clips=SpeakerSamples.plan(draft("1",0,12000)).get("1"); assertEquals(2,clips.size());
        assertEquals(150,clips.get(0).start); assertEquals(5150,clips.get(0).end); assertTrue(clips.get(1).start>=clips.get(0).end);
    }
    @Test public void removesOverlappingSpeechIncludingNestedTurns() throws Exception {
        var clips=SpeakerSamples.plan(draft("1",0,22000,"2",4000,12000,"3",6000,8000)).get("1");
        assertEquals(1,clips.size()); assertTrue(clips.get(0).start>=12150);
        assertEquals(0,SpeakerSamples.plan(draft("1",0,12000,"2",0,12000)).get("1").size());
    }
    @Test public void mergesWordsButDoesNotBridgeLongSilenceOrOtherTurns() throws Exception {
        assertEquals(1,SpeakerSamples.plan(draft("1",0,3000,"1",3100,6000)).get("1").size());
        assertEquals(0,SpeakerSamples.plan(draft("1",0,3000,"1",4000,7000)).get("1").size());
        assertEquals(0,SpeakerSamples.plan(draft("1",0,3000,"2",3000,3100,"1",3100,6000)).get("1").size());
    }
    @Test public void invalidTimesAndUnknownSpeakerAreNotUploaded() throws Exception {
        var plan=SpeakerSamples.plan(draft("1",-1,9000,"2",1000,1000,"未知",0,12000,"3",0,86400001));
        for(var clips:plan.values()) assertTrue(clips.isEmpty());
    }
    @Test public void boundedToEightSpeakersAndSixteenCalls() throws Exception {
        Object[] turns=new Object[30]; for(int i=0;i<10;i++) { turns[i*3]=""+(i+1); turns[i*3+1]=i*15000; turns[i*3+2]=i*15000+12000; }
        var plan=SpeakerSamples.plan(draft(turns)); assertEquals(16,plan.values().stream().mapToInt(List::size).sum()); assertTrue(plan.get("9").isEmpty());
    }
    private Voiceprints.Match match(String id,double score) { return new Voiceprints.Match("姓名"+id,"需确认",score,id); }
    @Test public void twoMatchesMustAgreeAndSingleClipNeedsHigherThreshold() throws Exception {
        assertEquals("姓名a",SpeakerSamples.candidate("1",List.of(match("a",.8),match("a",.81))).getString("name"));
        assertEquals("",SpeakerSamples.candidate("1",List.of(match("a",.99),match("b",.99))).getString("name"));
        assertTrue(SpeakerSamples.candidate("1",List.of(match("a",.84))).getBoolean("tentative"));
        assertEquals("姓名a",SpeakerSamples.candidate("1",List.of(match("a",.85))).getString("name"));
        assertEquals("",SpeakerSamples.candidate("1",List.of(match("a",.99),new Voiceprints.Match("","低分",.5))).getString("name"));
    }
    @Test public void weakSuggestionNeedsExplicitConfirmationAndStillRejectsConflict() throws Exception {
        var weak=new Voiceprints.Match("","低分",.62,"a","本人");
        var row=SpeakerSamples.candidate("1",List.of(weak)); assertEquals("本人",row.getString("name")); assertTrue(row.getBoolean("tentative"));
        assertEquals("",SpeakerSamples.candidate("1",List.of(weak,new Voiceprints.Match("","太低",.59,"a",""))).getString("name"));
        assertEquals("",SpeakerSamples.candidate("1",List.of(weak,new Voiceprints.Match("","低分",.65,"b","另一个人"))).getString("name"));
    }
    @Test public void confirmationPreservesEditsOriginalAndOtherSpeakerNumbers() throws Exception {
        MeetingDraft d=draft("1",0,12000,"10",13000,25000,"100",26000,38000);
        d.data.put("body","发言人1负责修订。发言人 10审核，发言人100待定。").put("transcript","发言人 1：我修订的内容里有发言人 10。\n\n发言人 10：已校对。");
        String before=d.data.toString();
        var next=SpeakerSamples.apply(d,new JSONObject().put("1","$甲\\").put("10","乙"),SpeakerSamples.fingerprint(d));
        assertEquals(before,d.data.toString());
        assertEquals("$甲\\负责修订。乙审核，发言人100待定。",next.data.getString("body"));
        assertEquals("$甲\\：我修订的内容里有发言人 10。\n\n乙：已校对。",next.data.getString("transcript"));
        assertEquals(d.data.getJSONObject("original").toString(),next.data.getJSONObject("original").toString());
    }
    @Test public void manualNamesAndClearedDraftStayUntouched() throws Exception {
        var d=draft("1",0,12000); d.data.put("speakers",new JSONObject().put("1","原姓名")).put("body","").put("transcript","");
        var next=SpeakerSamples.apply(d,new JSONObject().put("1","候选姓名"),SpeakerSamples.fingerprint(d));
        assertEquals("原姓名",next.data.getJSONObject("speakers").getString("1")); assertEquals("",next.data.getString("body")); assertEquals("",next.data.getString("transcript"));
    }
    @Test(expected=IllegalStateException.class) public void changedCloudSourceRejectsStaleCandidates() throws Exception {
        var d=draft("1",0,12000); SpeakerSamples.apply(d,new JSONObject().put("1","甲"),"stale");
    }
    @Test public void audioUrlUsesDataFieldAndPreservesSignature() throws Exception {
        assertEquals("https://x.oss-cn-beijing.aliyuncs.com/a.mp3?q=a%2Fb",Tingwu.audioUrl(new JSONObject().put("TaskStatus","COMPLETED").put("OutputMp3Path","http://x.oss-cn-beijing.aliyuncs.com/a.mp3?q=a%2Fb")));
    }
    @Test(expected=java.io.IOException.class) public void oldMeetingDoesNotInventAudioUrl() throws Exception {
        Tingwu.audioUrl(new JSONObject().put("TaskStatus","COMPLETED").put("Result",new JSONObject().put("AudioUrl","https://x.aliyuncs.com/audio")));
    }
    @Test public void pausedAndFailedTasksDoNotPretendToBeProcessingNormally() throws Exception {
        for(String status:List.of("PAUSED","FAILED","INVALID")) {
            try { Tingwu.audioUrl(new JSONObject().put("TaskStatus",status)); fail("must reject unfinished task"); }
            catch(java.io.IOException e) { assertFalse(e.getMessage().contains("稍后")); }
        }
    }
    @Test public void neverVerifiesUsingAudioThatWasUsedForEnrollmentEvenWhenOutcomeIsUncertain() throws Exception {
        var d=draft("1",0,12000); String task="a".repeat(32); d.data.put("task_id",task);
        JSONObject state=new JSONObject().put("people",new JSONObject().put("a",new JSONObject().put("state","pending_update")
            .put("meeting_samples",new JSONArray().put(new JSONObject().put("task_id",task).put("start",150).put("end",5150)))));
        var clips=SpeakerSamples.recognitionPlan(d,state).get("1"); assertEquals(1,clips.size()); assertEquals(5150,clips.get(0).start);
        assertTrue(SpeakerSamples.usedForTraining(state,task,new SpeakerSamples.Span("renamed",4000,9000)));
        assertFalse(SpeakerSamples.usedForTraining(state,"b".repeat(32),new SpeakerSamples.Span("1",150,5150)));
        assertEquals(2,SpeakerSamples.plan(d).get("1").size());
    }
}
