package com.folotoy.meeting;

import org.junit.Test;
import org.json.*;
import java.io.IOException;
import static org.junit.Assert.*;

public class DocumentWriterTest {
    static JSONObject copy(JSONObject j) throws Exception { return new JSONObject(j.toString()); }
    static JSONArray blocks(int count) throws Exception {
        JSONArray a=new JSONArray(); for(int i=0;i<count;i++) a.put(MeetingDocument.block(i%3==0?4:2,"段落 "+i,i%5==0)); return a;
    }
    static class Server implements DocumentWriter.Port {
        JSONObject doc=new JSONObject(),journal=new JSONObject(); int operations=0,failAfter=-1,failBefore=-1; boolean injectEdit=false;
        Server() throws Exception { doc.put("title","旧标题").put("revision",1).put("items",new JSONArray().put(MeetingDocument.block(2,"旧摘要",false).put("block_id","old"))); }
        public JSONObject fetch() throws Exception { return copy(doc); }
        void guard(long revision) throws Exception {
            if(injectEdit) { doc.put("revision",doc.getLong("revision")+1); injectEdit=false; }
            if(revision!=doc.getLong("revision")) throw new IOException("revision conflict");
            if(operations==failBefore) { failBefore=-1; throw new IOException("offline before request"); }
        }
        void commit() throws Exception { doc.put("revision",doc.getLong("revision")+1); operations++; if(operations==failAfter) { failAfter=-1; throw new IOException("response lost"); } }
        public void append(JSONArray blocks,long revision) throws Exception {
            guard(revision); assertTrue(blocks.length()<=50);
            for(int i=0;i<blocks.length();i++) doc.getJSONArray("items").put(copy(blocks.getJSONObject(i)).put("block_id","new_"+operations+"_"+i));
            commit();
        }
        public void delete(int count,long revision) throws Exception {
            guard(revision); assertTrue(count<=50); JSONArray items=doc.getJSONArray("items");
            doc.put("items",DocumentWriter.slice(items,count,items.length())); commit();
        }
        public void title(String title,long revision) throws Exception { guard(revision); doc.put("title",title); commit(); }
        public JSONObject load() throws Exception { return copy(journal); }
        public void save(JSONObject j) throws Exception { journal=copy(j); }
    }
    @Test public void replacesLegacyDocumentInBatchesAndRepeatDoesNotDuplicate() throws Exception {
        Server s=new Server(); JSONObject before=s.fetch(); JSONArray desired=blocks(121);
        JSONObject result=new DocumentWriter(s).write("会议",desired,before);
        assertEquals(121,result.getJSONArray("items").length()); assertEquals("会议",result.getString("title"));
        assertEquals(5,s.operations); new DocumentWriter(s).write("会议",desired,before); assertEquals(5,s.operations);
    }
    @Test public void resumesEveryLostResponseIncludingDeletionAndTitle() throws Exception {
        for(int fail=1;fail<=5;fail++) {
            Server s=new Server(); s.failAfter=fail; JSONObject before=s.fetch(); JSONArray desired=blocks(121);
            try { new DocumentWriter(s).write("会议",desired,before); fail("expected response loss"); } catch(IOException expected) {}
            if(fail<=3) assertEquals("old",s.doc.getJSONArray("items").getJSONObject(0).getString("block_id"));
            JSONObject result=new DocumentWriter(s).write("会议",desired,before);
            assertTrue(DocumentWriter.desiredBlocks(result.getJSONArray("items"),desired)); assertEquals(5,s.operations);
        }
    }
    @Test public void retriesRequestThatNeverReachedServer() throws Exception {
        Server s=new Server(); s.failBefore=1; JSONObject before=s.fetch();
        try { new DocumentWriter(s).write("会议",blocks(60),before); fail(); } catch(IOException expected) {}
        new DocumentWriter(s).write("会议",blocks(60),before); assertEquals(4,s.operations);
    }
    @Test public void externalEditAfterInterruptedAppendIsNotOverwritten() throws Exception {
        Server s=new Server(); s.failAfter=1; JSONObject before=s.fetch();
        try { new DocumentWriter(s).write("会议",blocks(60),before); fail(); } catch(IOException expected) {}
        s.doc.getJSONArray("items").getJSONObject(0).put("text",MeetingDocument.block(2,"飞书人工补充",false).getJSONObject("text"));
        try { new DocumentWriter(s).write("会议",blocks(60),before); fail(); } catch(IOException expected) {}
        assertEquals(1,s.operations); assertEquals("飞书人工补充",Feishu.textOf(s.doc.getJSONArray("items").getJSONObject(0)));
    }
    @Test public void commentsAndNestedBlocksAreProtected() throws Exception {
        for(String field:new String[]{"children","comment_ids"}) {
            Server s=new Server(); s.doc.getJSONArray("items").getJSONObject(0).put(field,new JSONArray().put("id"));
            try { new DocumentWriter(s).write("会议",blocks(1),s.fetch()); fail(); } catch(IOException expected) {}
            assertEquals(0,s.operations);
        }
    }
    @Test public void concurrentRevisionStopsMutation() throws Exception {
        Server s=new Server(); s.injectEdit=true;
        try { new DocumentWriter(s).write("会议",blocks(1),s.fetch()); fail(); } catch(IOException expected) {}
        assertEquals(0,s.operations); assertEquals("旧摘要",Feishu.textOf(s.doc.getJSONArray("items").getJSONObject(0)));
    }
}
