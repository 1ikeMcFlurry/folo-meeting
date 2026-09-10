#!/usr/bin/env python3
# tools/wav_to_adpcm.py —— 音频(wav/mp3/…) → 单声 ADPCM 片段容器(magic "ADP1")
# 片段默认 16k(高质,写 flash 后离线播放);实时流用 8k(见 ble_card_client 说明:BLE 中心
# 常强制 60ms 连接间隔,只够 ~4KB/s,8k ADPCM=4KB/s 才能边传边放不卡顿)。
import os, sys, wave, struct, contextlib
sys.path.insert(0, __file__.rsplit("/", 1)[0])
import adpcm_codec as A


@contextlib.contextmanager
def _silence_c_stderr():
    """临时屏蔽 C 库(mpg123/audioread)直接写 fd 2 的提示(如 "Illegal Audio-MPEG-Header",
    源于 mp3 尾部标签/填充,无害)。Python 异常不经 fd 2,真出错仍会抛出,不被吞。"""
    try:
        sys.stderr.flush()
        saved = os.dup(2)
        devnull = os.open(os.devnull, os.O_WRONLY)
    except Exception:
        yield            # 平台不支持 fd 复制则不屏蔽,照常执行
        return
    try:
        os.dup2(devnull, 2)
        yield
    finally:
        os.dup2(saved, 2); os.close(devnull); os.close(saved)


def load_wav_mono(path, target_sr):
    """标准库解 wav(零依赖):取左声道 + 线性重采样到 target_sr,返回 int16 列表。"""
    w = wave.open(path, "rb")
    ch = w.getnchannels(); sw = w.getsampwidth(); sr = w.getframerate()
    raw = w.readframes(w.getnframes()); w.close()
    assert sw == 2, "只支持 16-bit PCM wav"
    samp = list(struct.unpack("<%dh" % (len(raw) // 2), raw))
    if ch == 2:
        samp = samp[0::2]                # 取左声道
    return _resample_int16(samp, sr, target_sr)


def _resample_int16(samp, src_sr, target_sr):
    """线性重采样 int16 列表到 target_sr。"""
    if src_sr == target_sr:
        return samp
    out = []; step = src_sr / float(target_sr); i = 0.0
    while int(i) < len(samp):
        out.append(samp[int(i)]); i += step
    return out


def _load_via_backend_mono(path, target_sr):
    """非 wav(mp3/m4a/ogg…):依次尝试 soundfile → librosa → pydub。任一成功即返回。
    全失败则给出可操作的清晰提示(最省事:转成 wav)。返回 target_sr 单声 int16 列表。"""
    errors = []

    # 1) miniaudio:纯 pip 轮子,自带 mp3/flac/ogg 解码器,无需 ffmpeg/libsndfile —— Windows 最省心。
    #    decode_file 直接给出目标声道数/采样率的 int16 样本(内部完成下混+重采样)。
    try:
        import miniaudio
        dec = miniaudio.decode_file(path, output_format=miniaudio.SampleFormat.SIGNED16,
                                    nchannels=1, sample_rate=target_sr)
        return list(dec.samples)
    except Exception as e:
        errors.append("miniaudio: %s" % e)

    # 2) soundfile:新版 libsndfile(≥1.1)自带 mp3 解码,无需 ffmpeg。
    try:
        import soundfile as sf
        data, sr = sf.read(path, dtype="int16", always_2d=True)
        samp = [int(row[0]) for row in data]           # 取左声道
        return _resample_int16(samp, sr, target_sr)
    except Exception as e:
        errors.append("soundfile: %s" % e)

    # 2) librosa(需 audioread 后端;mp3 通常靠 ffmpeg,坏帧头的 mp3 会 NoBackendError)
    try:
        import warnings, librosa
        with warnings.catch_warnings(), _silence_c_stderr():   # 静音其一堆 C/Python 警告
            warnings.simplefilter("ignore")
            y, _ = librosa.load(path, sr=target_sr, mono=True)   # float32 [-1,1]
        return [max(-32768, min(32767, int(round(float(s) * 32767)))) for s in y]
    except Exception as e:
        errors.append("librosa: %s" % e)

    # 4) pydub(需系统 ffmpeg)
    try:
        import warnings
        from pydub import AudioSegment
        with warnings.catch_warnings(), _silence_c_stderr():   # 静音 "Couldn't find ffmpeg" 之类
            warnings.simplefilter("ignore")
            seg = (AudioSegment.from_file(path)
                   .set_channels(1).set_frame_rate(target_sr).set_sample_width(2))
        raw = seg.raw_data
        return list(struct.unpack("<%dh" % (len(raw) // 2), raw))
    except Exception as e:
        errors.append("pydub: %s" % e)

    raise SystemExit(
        "无法解码 %s —— 本机没有可用的 mp3 解码后端。二选一:\n"
        "★ 最省事(推荐):pip install miniaudio\n"
        "  纯 pip 轮子,自带 mp3 解码,无需 ffmpeg,装完直接选 mp3 即可。\n"
        "★ 或把 mp3 转成 16-bit WAV 再选(WAV 走标准库,零依赖):\n"
        "  Audacity / 在线转换 / (有 ffmpeg 时) ffmpeg -i in.mp3 out.wav\n"
        "各后端报错:\n  %s" % (os.path.splitext(path)[1], "\n  ".join(errors)))


def load_audio_mono(path, target_sr=16000):
    """通用入口:wav 走标准库(零依赖),其余(mp3 等)走解码后端。返回 target_sr 单声 int16 列表。"""
    if os.path.splitext(path)[1].lower() == ".wav":
        return load_wav_mono(path, target_sr)
    return _load_via_backend_mono(path, target_sr)


def build_clip(path, target_sr=16000):
    samp = load_audio_mono(path, target_sr)
    adp = A.encode(samp)
    hdr = struct.pack("<IHBBI", 0x31504441, target_sr, 1, 0, len(samp))   # magic,sr,ch,rsv,num_samples
    return hdr + adp


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit("用法: wav_to_adpcm.py in.(wav|mp3|…) out.adpcm [采样率(默认16000)]")
    sr = int(sys.argv[3]) if len(sys.argv) > 3 else 16000
    open(sys.argv[2], "wb").write(build_clip(sys.argv[1], sr))
    print("已写入", sys.argv[2], "采样率", sr)
