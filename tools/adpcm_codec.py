# tools/adpcm_codec.py —— 纯 Python IMA-ADPCM 单声道连续流编/解码(与 C 侧 adpcm.c 逐位对齐)
# 不依赖 audioop(Py3.13 已移除)。低 nibble = 先出现的样本。
STEP = [
    7,8,9,10,11,12,13,14,16,17,19,21,23,25,28,31,34,37,41,45,50,55,60,66,73,80,88,97,
    107,118,130,143,157,173,190,209,230,253,279,307,337,371,408,449,494,544,598,658,
    724,796,876,963,1060,1166,1282,1411,1552,1707,1878,2066,2272,2499,2749,3024,3327,
    3660,4026,4428,4871,5358,5894,6484,7132,7845,8630,9493,10442,11487,12635,13899,
    15289,16818,18500,20350,22385,24623,27086,29794,32767]
INDEX = [-1,-1,-1,-1,2,4,6,8,-1,-1,-1,-1,2,4,6,8]

def _decode_code(st, code):
    step = STEP[st['index']]
    diff = step >> 3
    if code & 4: diff += step
    if code & 2: diff += step >> 1
    if code & 1: diff += step >> 2
    pred = st['predictor'] - diff if (code & 8) else st['predictor'] + diff
    pred = max(-32768, min(32767, pred))
    st['predictor'] = pred
    idx = st['index'] + INDEX[code & 0x0F]
    st['index'] = max(0, min(88, idx))
    return pred

def _encode_sample(st, sample):
    step = STEP[st['index']]
    diff = sample - st['predictor']
    code = 8 if diff < 0 else 0
    if diff < 0: diff = -diff
    if diff >= step: code |= 4; diff -= step
    if diff >= step >> 1: code |= 2; diff -= step >> 1
    if diff >= step >> 2: code |= 1
    _decode_code(st, code)          # 与解码同步更新 predictor/index
    return code & 0x0F

def encode(samples):
    """samples: int16 序列 → bytes(低 nibble 先)。奇数样本末尾补 0 nibble。"""
    st = {'predictor': 0, 'index': 0}
    out = bytearray()
    codes = [_encode_sample(st, int(s)) for s in samples]
    if len(codes) % 2: codes.append(0)
    for i in range(0, len(codes), 2):
        out.append((codes[i] & 0x0F) | ((codes[i+1] & 0x0F) << 4))
    return bytes(out)

def decode(data):
    st = {'predictor': 0, 'index': 0}
    out = []
    for b in data:
        out.append(_decode_code(st, b & 0x0F))
        out.append(_decode_code(st, (b >> 4) & 0x0F))
    return out

if __name__ == "__main__":
    # 自测:编码→解码近似原信号
    import math
    pcm = [int(16000*math.sin(2*math.pi*440*i/16000)) for i in range(2000)]
    dec = decode(encode(pcm))
    err = sum(abs(a-b) for a,b in zip(pcm,dec))/len(pcm)
    print("mean abs err =", round(err,1))
