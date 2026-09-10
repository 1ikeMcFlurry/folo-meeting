package com.folotoy.meeting;

import java.nio.*;
import java.nio.charset.StandardCharsets;

/** A five-second, mono PCM16/16 kHz sample. Never creates files. */
final class VoiceSample {
    static final int RATE=16000, SECONDS=5, SAMPLES=RATE*SECONDS;
    static byte[] wav(short[] pcm) {
        if(pcm.length!=SAMPLES) throw new IllegalArgumentException("录音未满 5 秒，请重新录制");
        ByteBuffer out=ByteBuffer.allocate(44+pcm.length*2).order(ByteOrder.LITTLE_ENDIAN);
        out.put("RIFF".getBytes(StandardCharsets.US_ASCII)).putInt(out.capacity()-8);
        out.put("WAVEfmt ".getBytes(StandardCharsets.US_ASCII)).putInt(16).putShort((short)1).putShort((short)1);
        out.putInt(RATE).putInt(RATE*2).putShort((short)2).putShort((short)16);
        out.put("data".getBytes(StandardCharsets.US_ASCII)).putInt(pcm.length*2);
        for(short sample:pcm) out.putShort(sample);
        return out.array();
    }
    static void requireWav(byte[] wav) {
        if(wav.length!=44+SAMPLES*2) throw new IllegalArgumentException("声纹音频格式不正确");
        ByteBuffer in=ByteBuffer.wrap(wav).order(ByteOrder.LITTLE_ENDIAN);
        if(in.getInt(0)!=0x46464952 || in.getInt(4)!=wav.length-8 || in.getInt(8)!=0x45564157 ||
                in.getInt(12)!=0x20746d66 || in.getInt(16)!=16 || in.getShort(20)!=1 || in.getShort(22)!=1 ||
                in.getInt(24)!=RATE || in.getInt(28)!=RATE*2 || in.getShort(32)!=2 || in.getShort(34)!=16 ||
                in.getInt(36)!=0x61746164 || in.getInt(40)!=SAMPLES*2) throw new IllegalArgumentException("声纹音频格式不正确");
    }
    static double rms(short[] pcm,int offset,int length) {
        double sum=0; for(int i=offset;i<offset+length;i++) { double x=pcm[i]/32768.0; sum+=x*x; }
        return Math.sqrt(sum/Math.max(1,length));
    }
    static String qualityProblem(short[] pcm) {
        int audible=0,clipped=0;
        // A level gate, not speech detection. Noise/multiple speakers still need human review.
        for(int start=0;start+320<=pcm.length;start+=320) if(rms(pcm,start,320)>0.008) audible+=320;
        for(short sample:pcm) if(Math.abs((int)sample)>=32700) clipped++;
        if(audible<RATE) return "声音太轻或静音过长，请靠近手机，连续说话 5 秒";
        if(clipped>pcm.length/50) return "声音失真较多，请稍远离手机后重新录制";
        return "";
    }
}
