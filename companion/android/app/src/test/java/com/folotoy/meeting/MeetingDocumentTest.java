package com.folotoy.meeting;

import org.junit.Test;
import org.json.*;
import static org.junit.Assert.*;

public class MeetingDocumentTest {
    private MeetingDraft draft() throws Exception {
        JSONObject p=new JSONObject().put("SpeakerId","1").put("Words",new JSONArray()
            .put(new JSONObject().put("Text","星期五交付。").put("Start",4970).put("End",8560)));
        MeetingDraft draft=new MeetingDraft(new JSONObject());
        draft.importResult(new JSONObject().put("Transcription",new JSONObject().put("Transcription",new JSONObject().put("Paragraphs",new JSONArray().put(p)))));
        return draft;
    }
    @Test public void originalTranscriptHasVerifiedTimesAndBoldSpeaker() throws Exception {
        MeetingDraft d=draft(); d.data.put("speakers",new JSONObject().put("1","小王")).put("transcript","小王：星期五交付。");
        JSONArray blocks=MeetingDocument.build(d); String text=MeetingDocument.plain(blocks);
        assertTrue(text.contains("小王  ·  00:04–00:08")); assertTrue(text.contains("星期五交付。"));
        assertTrue(blocks.toString().contains("\"bold\":true"));
    }
    @Test public void correctedTranscriptIsExportedWithoutMisleadingTimestamp() throws Exception {
        MeetingDraft d=draft(); d.data.put("body","修订纪要").put("transcript","小王：下周五交付，预算🙂100元。");
        String text=MeetingDocument.plain(MeetingDocument.build(d));
        assertTrue(text.contains("下周五交付，预算🙂100元。")); assertTrue(text.contains("修订纪要"));
        assertFalse(text.contains("00:04")); assertFalse(text.contains("未提取到明确待办"));
    }
    @Test public void deliberatelyClearedTranscriptIsNotRestored() throws Exception {
        MeetingDraft d=draft(); d.data.put("transcript","");
        assertFalse(MeetingDocument.plain(MeetingDocument.build(d)).contains("星期五交付"));
    }
    @Test public void longUnicodeParagraphHasNoTruncation() throws Exception {
        String text="中".repeat(1999)+"🙂"+"长会议".repeat(4000); JSONArray blocks=new JSONArray();
        MeetingDocument.add(blocks,2,text,false); StringBuilder joined=new StringBuilder();
        for(int i=0;i<blocks.length();i++) {
            String part=Feishu.textOf(blocks.getJSONObject(i)); assertTrue(part.length()<=2000);
            assertFalse(Character.isHighSurrogate(part.charAt(part.length()-1))); joined.append(part);
        }
        assertEquals(text,joined.toString());
    }
    @Test public void absentTimestampDoesNotBecomeZero() throws Exception {
        assertEquals("",MeetingDocument.range(new JSONObject().put("Words",new JSONArray().put(new JSONObject().put("Text","文字")))));
        assertEquals("01:02:03",MeetingDocument.time(3723000));
    }
}
