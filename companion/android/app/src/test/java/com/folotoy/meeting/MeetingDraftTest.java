package com.folotoy.meeting;

import org.junit.Test;
import org.json.*;
import static org.junit.Assert.*;

public class MeetingDraftTest {
    @Test public void cloudResultAlwaysUsesTlsWithoutChangingSignature() throws Exception {
        assertEquals("https://example.oss-cn-beijing.aliyuncs.com/result?a=x%2Fy&b=1",Tingwu.resultUrl("http://example.oss-cn-beijing.aliyuncs.com/result?a=x%2Fy&b=1"));
    }
    @Test(expected=java.io.IOException.class) public void rejectResultCredentialRedirect() throws Exception {
        Tingwu.resultUrl("https://example.aliyuncs.com@evil.example/result");
    }
    @Test(expected=java.io.IOException.class) public void rejectLookalikeCloudDomain() throws Exception {
        Tingwu.resultUrl("https://example.aliyuncs.com.evil.example/result");
    }
    private JSONObject result(String summary) throws Exception {
        JSONObject artifacts=new JSONObject().put("Summarization",new JSONObject().put("Summarization",new JSONObject().put("ParagraphSummary",summary)));
        JSONArray paragraphs=new JSONArray().put(new JSONObject().put("SpeakerId","1").put("Words",new JSONArray()
            .put(new JSONObject().put("Text","星期五")) .put(new JSONObject().put("Text","交付。"))));
        return artifacts.put("Transcription",new JSONObject().put("Transcription",new JSONObject().put("Paragraphs",paragraphs)));
    }
    @Test public void refreshedCloudMustNotOverwriteEdits() throws Exception {
        MeetingDraft draft=new MeetingDraft(new JSONObject()); draft.importResult(result("原始摘要"));
        draft.data.put("body","我修订的结论").put("transcript","小王：下周五交付。");
        draft.importResult(result("云端再次返回的摘要"));
        assertEquals("我修订的结论",draft.data.getString("body"));
        assertEquals("小王：下周五交付。",draft.data.getString("transcript"));
        assertEquals("云端再次返回的摘要",draft.originalBody());
    }
    @Test public void clearedDraftIsAlsoPreserved() throws Exception {
        MeetingDraft draft=new MeetingDraft(new JSONObject().put("body","")); draft.importResult(result("摘要"));
        assertEquals("",draft.data.getString("body"));
    }
    @Test public void deviceSyncPreservesTitleAndDocument() throws Exception {
        MeetingDraft draft=new MeetingDraft(new JSONObject().put("title","我命名的会议").put("document_id","existing"));
        draft.mergeDevice(new JSONObject().put("task_id","a".repeat(32)).put("title","默认标题").put("document_id",""));
        assertEquals("我命名的会议",draft.data.getString("title")); assertEquals("existing",draft.data.getString("document_id"));
    }
    @Test public void speakerNamesArePerMeeting() throws Exception {
        MeetingDraft first=new MeetingDraft(new JSONObject().put("speakers",new JSONObject().put("1","小王")));
        first.importResult(result("摘要"));
        MeetingDraft second=new MeetingDraft(new JSONObject()); second.importResult(result("摘要"));
        assertEquals("小王：星期五交付。",first.renderTranscript());
        assertEquals("发言人 1：星期五交付。",second.renderTranscript());
    }
    @Test(expected=IllegalArgumentException.class) public void cannotMergeOtherMeeting() throws Exception {
        MeetingDraft draft=new MeetingDraft(new JSONObject().put("task_id","a".repeat(32)));
        draft.mergeDevice(new JSONObject().put("task_id","b".repeat(32)));
    }
    @Test public void unicodeChunksReassembleExactly() throws Exception {
        String body="纪要".repeat(749)+"🙂中文"+"会议".repeat(1000);
        JSONObject block=new JSONObject().put("text",new JSONObject().put("elements",Feishu.elements(body)));
        assertEquals(body,Feishu.textOf(block));
        JSONArray elements=block.getJSONObject("text").getJSONArray("elements");
        for(int i=0;i<elements.length();i++) {
            String part=elements.getJSONObject(i).getJSONObject("text_run").getString("content");
            assertFalse(Character.isHighSurrogate(part.charAt(part.length()-1)));
        }
    }
    @Test public void signedRequestHasStableHeaders() throws Exception {
        var headers=AcsSigner.sign("GET","/openapi/tingwu/v2/tasks/"+"a".repeat(32),"","","test_id","test_secret","2026-09-09T00:00:00Z","fixed_nonce");
        assertEquals("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",headers.get("x-acs-content-sha256"));
        assertTrue(headers.get("Authorization").contains("SignedHeaders=content-type;host;x-acs-action;x-acs-content-sha256;x-acs-date;x-acs-signature-nonce;x-acs-version"));
        var changed=AcsSigner.sign("GET","/openapi/tingwu/v2/tasks/"+"b".repeat(32),"","","test_id","test_secret","2026-09-09T00:00:00Z","fixed_nonce");
        assertNotEquals(headers.get("Authorization"),changed.get("Authorization"));
    }
}
