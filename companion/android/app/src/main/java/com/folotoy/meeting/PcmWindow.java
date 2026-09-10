package com.folotoy.meeting;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.util.Arrays;

/** Timestamp-aligned linear resampling. Missing audio is an error, never padded into a match. */
final class PcmWindow {
    private final long startUs; private short[] samples; private int filled;
    private double previousTime=Double.NaN,previousValue;
    PcmWindow(long startUs,int count) { this.startUs=startUs; samples=new short[count]; }
    void accept(ByteBuffer pcm,long pts,int rate,int channels,boolean floats) throws IOException {
        if(rate<8000 || rate>192000 || channels<1 || channels>8) throw new IOException("录音采样格式不支持");
        int bytes=floats?4:2,frameBytes=bytes*channels;
        if(pcm.remaining()%frameBytes!=0) throw new IOException("录音采样数据不完整");
        int frames=pcm.remaining()/frameBytes;
        for(int frame=0;frame<frames && !complete();frame++) {
            double value=0;
            for(int channel=0;channel<channels;channel++) {
                double v=floats?pcm.getFloat()*32768:pcm.getShort();
                if(!Double.isFinite(v)) throw new IOException("录音采样包含无效数值"); value+=v/channels;
            }
            double time=pts+frame*1000000.0/rate;
            if(Double.isFinite(previousTime) && time<=previousTime) continue;
            double target=startUs+filled*1000000.0/VoiceSample.RATE;
            if(!Double.isFinite(previousTime) && Math.abs(time-target)<1) { previousTime=time-1000000.0/rate; previousValue=value; }
            while(target<=time && !complete()) {
                if(!Double.isFinite(previousTime) || previousTime>target+1 || time-previousTime>2000)
                    throw new IOException("录音时间戳不连续，不能可靠识别这段声音");
                double fraction=Math.max(0,Math.min(1,(target-previousTime)/(time-previousTime)));
                samples[filled++]=(short)Math.max(-32768,Math.min(32767,Math.round(previousValue+(value-previousValue)*fraction)));
                target=startUs+filled*1000000.0/VoiceSample.RATE;
            }
            previousTime=time; previousValue=value;
        }
    }
    boolean complete() { return samples!=null && filled==samples.length; }
    short[] take() throws IOException { if(!complete()) throw new IOException("录音不足完整 5 秒，保留发言人编号"); short[] out=samples; samples=null; return out; }
    void clear() { if(samples!=null) Arrays.fill(samples,(short)0); samples=null; }
}
