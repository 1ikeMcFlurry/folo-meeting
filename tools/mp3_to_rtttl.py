#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mp3_to_rtttl.py —— 把普通音频(mp3/wav/…)转成 RTTTL 单音曲谱。

用途:给 TRAE-CARD 工牌生成能直接下发播放的铃声谱(设备端解析器见
components/core/services/src/rtttl.c)。

原理与局限(务必先读):
  RTTTL 是**单音**格式(同一时刻只有一个音)。普通歌曲是复音(人声+和声+鼓),
  本脚本用单音基频跟踪(librosa.pyin)抽取"主导音高"再量化成音符 —— 对旋律
  突出、配器简单的片段效果尚可;对鼓点重/多声部混杂的段落会跑偏。它做的是
  "近似哼唱版",不是还原编曲。建议:
    - 先用 --start/--duration 截取主歌/副歌里旋律最清楚的一段(几十秒);
    - 用人声/口哨/单乐器录音效果最好;
    - 生成后在 --out 文件里手工微调(去掉明显错音、并音)。

生成结果严格贴合固件解析器子集:
    名:d=4,o=6,b=NNN:  时值音符[#][八度][.] , ...
  - 音名只用升号 c c# d d# e f f# g g# a a# b(无降号);
  - 八度为科学音高(o=4 = 中央 C，与 rtttl.c 的 OCT4 表一致);
  - 时值取 {1,2,4,8,16,32} + 附点(.);
  - p = 休止符。
  设备端缓冲上限 1024 字节(SCORE_MAX),超了会告警。

依赖:
    pip install librosa numpy
  mp3 解码还需系统有 ffmpeg(librosa 经 audioread 调用),或用较新的 libsndfile。

用法示例:
    ./mp3_to_rtttl.py song.mp3 --name MySong --start 30 --duration 20
    ./mp3_to_rtttl.py song.mp3 --bpm 120 --out mysong.rtttl.txt
    ./mp3_to_rtttl.py voice.wav --oct-lo 4 --oct-hi 7 --min-note-ms 80
"""

import argparse
import math
import sys

try:
    import numpy as np
    import librosa
except ImportError as e:
    sys.exit("缺少依赖: %s\n请先安装: pip install librosa numpy (mp3 解码另需系统 ffmpeg)" % e)

# RTTTL 允许的时值集合(与固件一致;附点在下方单独处理)
RTTTL_DURATIONS = [1, 2, 4, 8, 16, 32]
# 半音 → 音名(只用升号,匹配 rtttl.c 的 note_semi + '#')
SEMI_NAMES = ["c", "c#", "d", "d#", "e", "f", "f#", "g", "g#", "a", "a#", "b"]
# 设备端 RTTTL 缓冲上限(app.c: SCORE_MAX)
DEVICE_MAX_BYTES = 1024


def hz_to_midi(f):
    """频率(Hz) → MIDI 音符号(浮点)。A4=440=MIDI69。"""
    return 69.0 + 12.0 * math.log2(f / 440.0)


def midi_to_name_oct(midi):
    """MIDI 音符号 → (音名, 八度)。MIDI 60 = C4(中央 C，科学音高)。"""
    m = int(round(midi))
    name = SEMI_NAMES[m % 12]
    octave = m // 12 - 1
    return name, octave


def fold_octave(octave, lo, hi):
    """把八度折进 [lo, hi]:超范围就整八度上/下移,保留音名不变(音高可听即可)。"""
    while octave < lo:
        octave += 1
    while octave > hi:
        octave -= 1
    return octave


def quantize_duration(ms, whole_ms):
    """把一个音符时长(ms)量化到最接近的 RTTTL 时值。
    返回 (dur_value, dotted_bool)。dur_value ∈ RTTTL_DURATIONS。"""
    best = None
    for d in RTTTL_DURATIONS:
        for dotted in (False, True):
            cand = whole_ms / d * (1.5 if dotted else 1.0)
            err = abs(cand - ms)
            if best is None or err < best[0]:
                best = (err, d, dotted)
    return best[1], best[2]


def extract_notes(y, sr, fmin, fmax, hop_length, frame_length, median_frames):
    """基频跟踪 → 每帧 MIDI 音(或休止),中值平滑后返回按帧的音序列。
    返回列表,元素为 None(休止)或 int(MIDI 音符号)。"""
    f0, voiced_flag, _ = librosa.pyin(
        y, fmin=fmin, fmax=fmax, sr=sr,
        hop_length=hop_length, frame_length=frame_length,
    )
    seq = []
    for i, f in enumerate(f0):
        if voiced_flag[i] and f is not None and not math.isnan(f) and f > 0:
            seq.append(int(round(hz_to_midi(f))))
        else:
            seq.append(None)

    # 中值平滑:压掉单帧抖动(pyin 偶尔跳八度/邻音)。只对有声帧做,休止保持。
    if median_frames > 1:
        half = median_frames // 2
        smoothed = []
        for i in range(len(seq)):
            window = [seq[j] for j in range(max(0, i - half), min(len(seq), i + half + 1))
                      if seq[j] is not None]
            if seq[i] is None:
                smoothed.append(None)
            elif window:
                smoothed.append(int(np.median(window)))
            else:
                smoothed.append(seq[i])
        seq = smoothed
    return seq


def segment_notes(seq, frame_ms, min_note_ms):
    """把按帧的音序列合并成 (midi_or_None, duration_ms) 段落,并丢弃过短碎段。"""
    segments = []
    if not seq:
        return segments
    cur = seq[0]
    count = 1
    for v in seq[1:]:
        if v == cur:
            count += 1
        else:
            segments.append((cur, count * frame_ms))
            cur = v
            count = 1
    segments.append((cur, count * frame_ms))

    # 丢弃过短碎段:把时长并给前一段(音高不变),避免生成一堆 1/32 噪点。
    merged = []
    for midi, ms in segments:
        if ms < min_note_ms and merged:
            merged[-1] = (merged[-1][0], merged[-1][1] + ms)
        elif ms < min_note_ms and not merged:
            continue  # 开头的碎段直接扔
        else:
            merged.append((midi, ms))
    return merged


def build_rtttl(segments, name, bpm, oct_lo, oct_hi):
    """把段落序列渲染成 RTTTL 字符串,自动选最省字节的默认 d/o。"""
    whole_ms = 4 * 60000.0 / bpm

    # 先把每段量化成 (音名, 八度或 None, 时值, 附点)
    tokens = []
    for midi, ms in segments:
        dur, dotted = quantize_duration(ms, whole_ms)
        if midi is None:
            tokens.append(("p", None, dur, dotted))
        else:
            name_, octave = midi_to_name_oct(midi)
            octave = fold_octave(octave, oct_lo, oct_hi)
            tokens.append((name_, octave, dur, dotted))

    if not tokens:
        raise SystemExit("未能从音频中提取到任何音符(试试换段落 / 调低 --min-note-ms)。")

    # 选出现最多的时值/八度作为默认值,可省略同值音符的显式标注,缩短字符串。
    from collections import Counter
    def_dur = Counter(t[2] for t in tokens).most_common(1)[0][0]
    octs = [t[1] for t in tokens if t[1] is not None]
    def_oct = Counter(octs).most_common(1)[0][0] if octs else 6

    parts = []
    for nm, octave, dur, dotted in tokens:
        s = ""
        if dur != def_dur:
            s += str(dur)
        s += nm
        if octave is not None and octave != def_oct:
            s += str(octave)
        if dotted:
            s += "."
        parts.append(s)

    header = "%s:d=%d,o=%d,b=%d:" % (name, def_dur, def_oct, int(round(bpm)))
    return header + ",".join(parts)


def convert(input_path, name="Song", start=0.0, duration=None, bpm=None,
            oct_lo=4, oct_hi=7, min_note_ms=70.0, fmin="C2", fmax="C7",
            median=5, log=None):
    """音频→RTTTL 字符串。log(str) 可选,接收进度提示。可被 GUI/CLI 复用。"""
    def _log(s):
        if log:
            log(s)
        else:
            sys.stderr.write(s + "\n")

    def parse_freq(v):
        try:
            return float(v)
        except (ValueError, TypeError):
            return float(librosa.note_to_hz(v))

    fmin_hz, fmax_hz = parse_freq(fmin), parse_freq(fmax)
    _log("加载音频(可能较慢)...")
    y, sr = librosa.load(input_path, sr=22050, mono=True, offset=start, duration=duration)
    if y.size == 0:
        raise ValueError("音频为空(检查 start/duration 是否越界)")
    if bpm:
        b = float(bpm)
    else:
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        b = float(np.atleast_1d(tempo)[0]) or 120.0
        _log("自动估计 BPM ≈ %.1f(不理想可手动指定)" % b)
    hop_length, frame_length = 512, 2048
    frame_ms = hop_length / sr * 1000.0
    _log("基频跟踪中(pyin,长音频会慢)...")
    seq = extract_notes(y, sr, fmin_hz, fmax_hz, hop_length, frame_length, median)
    segments = segment_notes(seq, frame_ms, min_note_ms)
    return build_rtttl(segments, name, b, oct_lo, oct_hi)


def main():
    ap = argparse.ArgumentParser(
        description="把音频转成 RTTTL 单音曲谱(近似旋律)。",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("input", help="输入音频(mp3/wav/…)")
    ap.add_argument("--name", default="Song", help="曲名(RTTTL 头,默认 Song)")
    ap.add_argument("--out", help="输出到文件(默认只打印到终端)")
    ap.add_argument("--start", type=float, default=0.0, help="起始秒(截取一段处理)")
    ap.add_argument("--duration", type=float, default=None, help="截取时长(秒);不给=整首")
    ap.add_argument("--bpm", type=float, default=None,
                    help="每分钟拍数;不给则自动估计(节奏乱时建议手动指定)")
    ap.add_argument("--oct-lo", type=int, default=4, help="八度下限(默认 4)")
    ap.add_argument("--oct-hi", type=int, default=7, help="八度上限(默认 7)")
    ap.add_argument("--min-note-ms", type=float, default=70.0,
                    help="最短音符(ms),更短的碎段并入相邻音(默认 70)")
    ap.add_argument("--fmin", default="C2", help="基频跟踪下限(音名或 Hz,默认 C2)")
    ap.add_argument("--fmax", default="C7", help="基频跟踪上限(音名或 Hz,默认 C7)")
    ap.add_argument("--median", type=int, default=5,
                    help="中值平滑窗口(帧数,奇数;越大越稳但越钝,默认 5)")
    args = ap.parse_args()

    rtttl = convert(args.input, name=args.name, start=args.start, duration=args.duration,
                    bpm=args.bpm, oct_lo=args.oct_lo, oct_hi=args.oct_hi,
                    min_note_ms=args.min_note_ms, fmin=args.fmin, fmax=args.fmax,
                    median=args.median)

    n_bytes = len(rtttl.encode("utf-8"))
    note_count = rtttl.count(",") + 1
    sys.stderr.write("生成音符数 ≈ %d,字节数 = %d\n" % (note_count, n_bytes))
    if n_bytes > DEVICE_MAX_BYTES:
        sys.stderr.write(
            "⚠ 超过设备缓冲上限 %d 字节!下发会被拒。请用 --duration 截短片段,"
            "或调大 --min-note-ms 减少音符。\n" % DEVICE_MAX_BYTES)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(rtttl + "\n")
        sys.stderr.write("已写入 %s\n" % args.out)
    else:
        print(rtttl)


if __name__ == "__main__":
    main()
