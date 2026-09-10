package com.folotoy.meeting;

import org.junit.Test;
import java.nio.*;
import static org.junit.Assert.*;

public class PcmWindowTest {
    private ByteBuffer tone(int start,int count,int rate,int channels) {
        ByteBuffer out=ByteBuffer.allocate(count*channels*2).order(ByteOrder.nativeOrder());
        for(int i=start;i<start+count;i++) for(int c=0;c<channels;c++) out.putShort((short)Math.round(10000*Math.sin(2*Math.PI*440*i/rate)));
        out.flip(); return out;
    }
    @Test public void resamplesStereoAcrossDecoderBuffersAtCorrectTime() throws Exception {
        var w=new PcmWindow(150000,16000); int rate=44100;
        for(int i=0;i<60000 && !w.complete();i+=997) w.accept(tone(i,997,rate,2),Math.round(i*1000000.0/rate),rate,2,false);
        short[] out=w.take(); assertEquals(16000,out.length);
        for(int i=0;i<out.length;i++) assertEquals(10000*Math.sin(2*Math.PI*440*(.15+i/16000.0)),out[i],20);
    }
    @Test public void firstFrameAtZeroAndFloatPcmWork() throws Exception {
        ByteBuffer b=ByteBuffer.allocate(16).order(ByteOrder.nativeOrder()); b.putFloat(.25f).putFloat(.25f).putFloat(-.25f).putFloat(-.25f).flip();
        var w=new PcmWindow(0,2); w.accept(b,0,16000,2,true); assertArrayEquals(new short[]{8192,-8192},w.take());
    }
    @Test(expected=java.io.IOException.class) public void refusesMissingBeginningInsteadOfFillingSilence() throws Exception {
        new PcmWindow(0,80000).accept(tone(0,100,16000,1),500000,16000,1,false);
    }
    @Test(expected=java.io.IOException.class) public void refusesGapsInsteadOfShiftingIdentityTimes() throws Exception {
        var w=new PcmWindow(0,1000); w.accept(tone(0,100,16000,1),0,16000,1,false); w.accept(tone(100,100,16000,1),100000,16000,1,false);
    }
    @Test(expected=java.io.IOException.class) public void rejectsShortAudio() throws Exception {
        var w=new PcmWindow(0,80000); w.accept(tone(0,100,16000,1),0,16000,1,false); w.take();
    }
}
