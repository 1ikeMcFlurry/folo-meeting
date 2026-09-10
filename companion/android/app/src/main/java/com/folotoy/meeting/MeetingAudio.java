package com.folotoy.meeting;

import android.media.*;
import android.os.SystemClock;
import java.io.*;
import java.net.*;
import java.nio.*;
import java.util.*;
import java.util.regex.*;
import javax.net.ssl.HttpsURLConnection;

/** MP3 is read by HTTPS byte ranges; compressed audio and PCM never go to a file. */
final class MeetingAudio implements AutoCloseable {
    static final class Stop {
        volatile boolean cancelled; volatile HttpsURLConnection connection;
        final long deadline=SystemClock.elapsedRealtime()+10*60*1000;
        void check() throws IOException {
            if(cancelled || Thread.currentThread().isInterrupted()) throw new IOException("识别已取消");
            if(SystemClock.elapsedRealtime()>deadline) throw new IOException("识别用时超过 10 分钟，请稍后重试");
        }
        void cancel() { cancelled=true; HttpsURLConnection c=connection; if(c!=null) c.disconnect(); }
    }
    static final class Source extends MediaDataSource {
        private static final int BLOCK=128*1024;
        private final String url; private final Stop stop;
        private final LinkedHashMap<Long,byte[]> cache=new LinkedHashMap<>(8,0.75f,true);
        private long size=-1,received; private boolean closed;
        Source(String url,Stop stop) throws Exception { this.url=Tingwu.resultUrl(url); this.stop=stop; block(0); }
        private byte[] block(long start) throws IOException {
            if(closed) throw new IOException("录音读取已关闭"); stop.check();
            byte[] existing=cache.get(start); if(existing!=null) return existing;
            if(received>=32L*1024*1024) throw new IOException("录音取样流量已达 32 MB，本次停止；可手动设置姓名");
            HttpsURLConnection c=null; byte[] bytes=null; boolean kept=false;
            try {
                c=(HttpsURLConnection)new URL(url).openConnection(); stop.connection=c; stop.check();
                c.setInstanceFollowRedirects(false); c.setUseCaches(false); c.setConnectTimeout(15000); c.setReadTimeout(15000);
                c.setRequestProperty("Accept-Encoding","identity");
                c.setRequestProperty("Range","bytes="+start+"-"+(size<0?start+BLOCK-1:Math.min(size-1,start+BLOCK-1)));
                if(c.getResponseCode()!=206) throw new IOException("云端录音暂不支持分段读取，或地址已过期；请重新识别");
                Matcher m=Pattern.compile("bytes ([0-9]+)-([0-9]+)/([0-9]+)").matcher(Objects.toString(c.getHeaderField("Content-Range"),""));
                if(!m.matches()) throw new IOException("云端录音分段信息无效");
                long first=Long.parseLong(m.group(1)),last=Long.parseLong(m.group(2)),total=Long.parseLong(m.group(3));
                if(first!=start || last<first || last-first>=BLOCK || total<=last || total>2L*1024*1024*1024 || (size>=0 && size!=total))
                    throw new IOException("云端录音分段信息不一致");
                size=total; bytes=new byte[(int)(last-first+1)];
                try(InputStream in=c.getInputStream()) {
                    int offset=0;
                    while(offset<bytes.length) { stop.check(); int n=in.read(bytes,offset,bytes.length-offset); if(n<0) throw new EOFException(); offset+=n; }
                }
                received+=bytes.length;
                if(cache.size()==4) { Long key=cache.keySet().iterator().next(); Arrays.fill(cache.remove(key),(byte)0); }
                cache.put(start,bytes); kept=true; return bytes;
            } catch(IOException e) {
                stop.check();
                // Do not expose networking exceptions: they may contain the signed URL.
                throw new IOException("录音分段读取失败，请检查网络后重试（单次最多读取 32 MB）");
            } catch(RuntimeException e) { throw new IOException("云端录音分段格式无效"); }
            finally { if(!kept && bytes!=null) Arrays.fill(bytes,(byte)0); stop.connection=null; if(c!=null) c.disconnect(); }
        }
        @Override public synchronized int readAt(long position,byte[] buffer,int offset,int count) throws IOException {
            stop.check(); if(closed || position<0) throw new IOException("录音读取位置无效");
            if(count==0) return 0; if(position>=size) return -1;
            byte[] bytes=block(position/BLOCK*BLOCK); int within=(int)(position%BLOCK);
            int n=Math.min(count,bytes.length-within); if(n<=0) throw new IOException("录音分段缺失");
            System.arraycopy(bytes,within,buffer,offset,n); return n;
        }
        @Override public long getSize() { return size; }
        @Override public synchronized void close() { closed=true; for(byte[] b:cache.values()) Arrays.fill(b,(byte)0); cache.clear(); }
    }
    private final MediaDataSource source; private final Stop stop;
    MeetingAudio(MediaDataSource source,Stop stop) { this.source=source; this.stop=stop; }
    short[] sample(SpeakerSamples.Span span) throws Exception {
        MediaExtractor extractor=new MediaExtractor(); MediaCodec decoder=null;
        PcmWindow window=new PcmWindow(span.start*1000,VoiceSample.SAMPLES);
        long deadline=SystemClock.elapsedRealtime()+45000;
        try {
            // The meeting owns the source across multiple seeks/extractors.
            extractor.setDataSource(new MediaDataSource() {
                @Override public int readAt(long position,byte[] bytes,int offset,int size) throws IOException { return source.readAt(position,bytes,offset,size); }
                @Override public long getSize() throws IOException { return source.getSize(); }
                @Override public void close() {}
            }); MediaFormat format=null;
            for(int i=0;i<extractor.getTrackCount();i++) {
                MediaFormat f=extractor.getTrackFormat(i); String mime=f.getString(MediaFormat.KEY_MIME);
                if(mime!=null && mime.startsWith("audio/")) { extractor.selectTrack(i); format=f; break; }
            }
            if(format==null) throw new IOException("录音中没有可解码的音轨");
            extractor.seekTo(Math.max(0,span.start*1000-500000),MediaExtractor.SEEK_TO_PREVIOUS_SYNC);
            decoder=MediaCodec.createDecoderByType(format.getString(MediaFormat.KEY_MIME));
            decoder.configure(format,null,null,0); decoder.start();
            int rate=format.getInteger(MediaFormat.KEY_SAMPLE_RATE),channels=format.getInteger(MediaFormat.KEY_CHANNEL_COUNT),encoding=AudioFormat.ENCODING_PCM_16BIT;
            boolean inputEnd=false,outputEnd=false; MediaCodec.BufferInfo info=new MediaCodec.BufferInfo();
            while(!outputEnd && !window.complete()) {
                stop.check(); if(SystemClock.elapsedRealtime()>deadline) throw new IOException("这段录音解码超时，请重试");
                if(!inputEnd) {
                    int index=decoder.dequeueInputBuffer(10000);
                    if(index>=0) {
                        ByteBuffer input=decoder.getInputBuffer(index); input.clear();
                        int n=extractor.readSampleData(input,0); long pts=extractor.getSampleTime();
                        if(n<0) { decoder.queueInputBuffer(index,0,0,0,MediaCodec.BUFFER_FLAG_END_OF_STREAM); inputEnd=true; }
                        else { decoder.queueInputBuffer(index,0,n,pts,0); extractor.advance(); }
                    }
                }
                int index=decoder.dequeueOutputBuffer(info,10000);
                if(index==MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                    MediaFormat f=decoder.getOutputFormat(); rate=f.getInteger(MediaFormat.KEY_SAMPLE_RATE); channels=f.getInteger(MediaFormat.KEY_CHANNEL_COUNT);
                    encoding=f.containsKey(MediaFormat.KEY_PCM_ENCODING)?f.getInteger(MediaFormat.KEY_PCM_ENCODING):AudioFormat.ENCODING_PCM_16BIT;
                    if(encoding!=AudioFormat.ENCODING_PCM_16BIT && encoding!=AudioFormat.ENCODING_PCM_FLOAT) throw new IOException("录音解码位深不支持");
                } else if(index>=0) {
                    try {
                        ByteBuffer pcm=decoder.getOutputBuffer(index);
                        if(info.size>0) {
                            pcm.position(info.offset); pcm.limit(info.offset+info.size);
                            window.accept(pcm.slice().order(ByteOrder.nativeOrder()),info.presentationTimeUs,rate,channels,encoding==AudioFormat.ENCODING_PCM_FLOAT);
                            // Explicitly wipe decoded buffers before returning them to the codec.
                            if(!pcm.isReadOnly()) for(int j=info.offset;j<info.offset+info.size;j++) pcm.put(j,(byte)0);
                        }
                        outputEnd=(info.flags&MediaCodec.BUFFER_FLAG_END_OF_STREAM)!=0;
                        if(info.presentationTimeUs>(span.end+1000)*1000) outputEnd=true;
                    } finally { decoder.releaseOutputBuffer(index,false); }
                }
            }
            return window.take();
        } catch(IOException e) { throw e; }
        catch(Exception e) { throw new IOException("录音解码失败，无法取出可靠的 5 秒片段"); }
        finally { window.clear(); if(decoder!=null) { try { decoder.stop(); } catch(Exception ignored) {} decoder.release(); } extractor.release(); }
    }
    @Override public void close() throws IOException { source.close(); }
}
