#!/usr/bin/env bash
# Python 编码器产出的 ADPCM,喂给固件真实 C 解码器(adpcm.c),验证两侧逐样本一致。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

# C 驱动:从 stdin 读 ADPCM 字节,解码后把 int16 样本按十进制逐行打印
cat > "$TMP/drv.c" <<'EOF'
#include "services/adpcm.h"
#include <stdio.h>
int main(void){
    unsigned char buf[65536]; int n=0,c;
    while((c=getchar())!=EOF && n<(int)sizeof buf) buf[n++]=(unsigned char)c;
    static int16_t out[131072];
    adpcm_state_t st; adpcm_state_reset(&st);
    int m=adpcm_decode(&st,buf,n,out);
    for(int i=0;i<m;i++) printf("%d\n",out[i]);
    return 0;
}
EOF
gcc -std=c11 -I"$ROOT/components/core/services/include" \
    "$ROOT/components/core/services/src/adpcm.c" "$TMP/drv.c" -o "$TMP/drv"

# Python 生成 PCM → 编码为 ADPCM(bytes 写文件) → 同时算出 Python 解码参考
python3 - "$TMP" <<'PY'
import sys, math
sys.path.insert(0, "tools")
import adpcm_codec as a
pcm=[int(16000*math.sin(2*math.pi*440*i/16000)) for i in range(4000)]
adp=a.encode(pcm)
open(sys.argv[1]+"/adp.bin","wb").write(adp)
open(sys.argv[1]+"/pyref.txt","w").write("\n".join(str(x) for x in a.decode(adp))+"\n")
PY

"$TMP/drv" < "$TMP/adp.bin" > "$TMP/cout.txt"
if diff -q "$TMP/pyref.txt" "$TMP/cout.txt" >/dev/null; then
    echo "✓ ADPCM 互操作一致:Python 编码 → C 解码 == Python 解码"
else
    echo "✗ C 解码与 Python 解码不一致(检查 STEP/INDEX 表或 nibble 顺序)"; exit 1
fi
