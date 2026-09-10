package com.folotoy.meeting;

import android.Manifest;
import android.content.Context;
import android.content.pm.PackageManager;
import android.media.*;
import android.os.SystemClock;
import java.util.Arrays;

/** Foreground-only capture; cancel is nonblocking and the recorder is released on its owner thread. */
final class VoiceCapture {
    interface Listener { void level(int milliseconds,int percent); void complete(byte[] wav,String problem); }
    private volatile boolean cancelled;
    void cancel() { cancelled=true; }
    void start(Context context,Listener listener) {
        new Thread(()-> {
            AudioRecord recorder=null; short[] pcm=new short[VoiceSample.SAMPLES]; byte[] wav=null; String problem="";
            try {
                if(context.checkSelfPermission(Manifest.permission.RECORD_AUDIO)!=PackageManager.PERMISSION_GRANTED)
                    throw new IllegalStateException("请先允许麦克风权限");
                int min=AudioRecord.getMinBufferSize(VoiceSample.RATE,AudioFormat.CHANNEL_IN_MONO,AudioFormat.ENCODING_PCM_16BIT);
                if(min<=0) throw new IllegalStateException("手机不支持所需录音格式");
                recorder=new AudioRecord.Builder().setAudioSource(MediaRecorder.AudioSource.VOICE_RECOGNITION)
                        .setAudioFormat(new AudioFormat.Builder().setSampleRate(VoiceSample.RATE).setChannelMask(AudioFormat.CHANNEL_IN_MONO)
                                .setEncoding(AudioFormat.ENCODING_PCM_16BIT).build()).setBufferSizeInBytes(Math.max(min,6400)).build();
                if(recorder.getState()!=AudioRecord.STATE_INITIALIZED) throw new IllegalStateException("麦克风初始化失败");
                if(!cancelled) recorder.startRecording();
                int position=0; long deadline=SystemClock.elapsedRealtime()+10000, update=0;
                while(!cancelled && position<pcm.length) {
                    if(SystemClock.elapsedRealtime()>deadline) throw new IllegalStateException("未能取得完整录音，请检查麦克风是否被占用");
                    int count=recorder.read(pcm,position,Math.min(1600,pcm.length-position),AudioRecord.READ_NON_BLOCKING);
                    if(count<0) throw new IllegalStateException("麦克风录音中断，请重试");
                    if(count==0) { Thread.sleep(10); continue; }
                    double rms=VoiceSample.rms(pcm,position,count); position+=count;
                    if(SystemClock.elapsedRealtime()-update>=80) {
                        update=SystemClock.elapsedRealtime();
                        listener.level(position*1000/VoiceSample.RATE,(int)Math.min(100,rms*500));
                    }
                }
                if(!cancelled) { problem=VoiceSample.qualityProblem(pcm); if(problem.isEmpty()) wav=VoiceSample.wav(pcm); }
            } catch(Exception e) {
                // Audio errors are local; do not expose arbitrary vendor or OS exception messages.
                problem="录音未完成，请检查麦克风权限、系统麦克风开关，以及是否被其他应用占用";
            } finally {
                if(recorder!=null) { try { recorder.stop(); } catch(Exception ignored) {} recorder.release(); }
                Arrays.fill(pcm,(short)0);
            }
            if(cancelled) { if(wav!=null) Arrays.fill(wav,(byte)0); listener.complete(null,""); }
            else listener.complete(wav,problem);
        },"folo-voice-capture").start();
    }
}
