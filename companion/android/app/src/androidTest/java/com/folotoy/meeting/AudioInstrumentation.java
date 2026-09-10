package com.folotoy.meeting;

import android.app.Instrumentation;
import android.media.MediaDataSource;
import android.os.Bundle;
import java.util.Arrays;

/** On-device codec test uses only generated tones. No microphone, credentials or network. */
public class AudioInstrumentation extends Instrumentation {
    @Override public void onCreate(Bundle arguments) { super.onCreate(arguments); start(); }
    @Override public void onStart() {
        Bundle result=new Bundle(); String stage="asset"; int[] counters=new int[3];
        try {
            byte[] bytes;
            try(var in=getContext().getAssets().open("decoder-tones.mp3")) { bytes=in.readAllBytes(); }
            MediaDataSource source=new MediaDataSource() {
                boolean closed;
                public int readAt(long position,byte[] target,int offset,int length) throws java.io.IOException {
                    counters[0]++;
                    if(closed) throw new java.io.IOException("test source closed");
                    if(position>=bytes.length) return -1;
                    int n=Math.min(length,bytes.length-(int)position); System.arraycopy(bytes,(int)position,target,offset,n); return n;
                }
                public long getSize() { counters[1]++; return bytes.length; }
                public void close() { counters[2]++; closed=true; Arrays.fill(bytes,(byte)0); }
            };
            try(MeetingAudio audio=new MeetingAudio(source,new MeetingAudio.Stop())) {
                stage="first"; check(audio.sample(new SpeakerSamples.Span("1",150,5150)),440);
                stage="second"; check(audio.sample(new SpeakerSamples.Span("2",13150,18150)),880);
                stage="boundary";
                short[] boundary=audio.sample(new SpeakerSamples.Span("3",8150,13150));
                // The tone changes at exactly 10s: the extracted transition must be 1.85s in.
                checkFrequency(boundary,1000,16000,440); checkFrequency(boundary,40000,16000,880);
                int transition=-1;
                for(int start=25000;start<35000;start+=80) if(frequency(boundary,start,800)>650) { transition=start; break; }
                if(Math.abs(transition-29200)>1600) throw new AssertionError("seek timestamp alignment");
                Arrays.fill(boundary,(short)0);
                boolean refused=false; try { audio.sample(new SpeakerSamples.Span("4",18500,23500)); } catch(java.io.IOException expected) { refused=true; }
                if(!refused) throw new AssertionError("truncated audio must fail");
            }
            for(byte b:bytes) if(b!=0) throw new AssertionError("source was not wiped");
            result.putString("stream","PASS: MP3 48k stereo -> 16k mono; two seeks; timestamp transition; short-audio rejection; memory cleanup\n");
            finish(-1,result);
        } catch(Throwable failure) {
            result.putString("stream","FAIL "+stage+" "+Arrays.toString(counters)+": "+failure.getClass().getSimpleName()+": "+failure.getMessage()+"\n"); finish(0,result);
        }
    }
    private void check(short[] pcm,int expected) throws Exception {
        if(pcm.length!=80000 || !VoiceSample.qualityProblem(pcm).isEmpty()) throw new AssertionError("invalid sample");
        checkFrequency(pcm,8000,64000,expected); VoiceSample.requireWav(VoiceSample.wav(pcm)); Arrays.fill(pcm,(short)0);
    }
    private double frequency(short[] pcm,int start,int count) {
        int rises=0; for(int i=start+1;i<start+count;i++) if(pcm[i-1]<=0 && pcm[i]>0) rises++; return rises*16000.0/count;
    }
    private void checkFrequency(short[] pcm,int start,int count,int expected) { if(Math.abs(frequency(pcm,start,count)-expected)>3) throw new AssertionError("wrong audio time or resampling"); }
}
