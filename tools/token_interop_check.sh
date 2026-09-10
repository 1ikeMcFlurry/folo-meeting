#!/usr/bin/env bash
# tools/token_interop_check.sh —— 上位机 ↔ 固件 端到端互通检查
#
# 用上位机(tools/token_broadcast_gui.py)生成广播帧,直接喂给固件里**真实的**
# 解析代码(components/core/services/src/token_bcast.c),验证两边逐字节一致。
#
# 这是不接硬件能做的最强验证:它同时覆盖了字节序、字段偏移、签名范围、
# 密钥编码这四件最容易写错、且错了完全没有日志线索的事。
# 改过协议、改过任一侧的代码之后都应该跑一遍。
#
# 依赖: gcc + libssl-dev(提供 HMAC-SHA256,固件侧用 mbedtls,这里用 openssl 替身)
# 用法: tools/token_interop_check.sh [ProductKey] [SN]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PK="${1:-TESTPK0123456789}"        # 默认用文档 §7 的公开假密钥
SN="${2:-f0f5bd84a86c}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# --- 固件解析代码的宿主机驱动 ---------------------------------------------
# 支持一次喂多帧到**同一个卡片状态**,这样才能测出去重与多 pad 交叉的真实行为。
cat > "$TMP/drv.c" <<'EOF'
#include "services/token_bcast.h"
#include <openssl/hmac.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static const char *PK;
static int real_mac(const uint8_t *m, int n, uint8_t o[8], void *u) {
    (void)u; unsigned char d[32]; unsigned int dl = 0;
    if (!HMAC(EVP_sha256(), PK, (int)strlen(PK), m, (size_t)n, d, &dl) || dl != 32)
        return -1;
    memcpy(o, d, 8); return 0;
}
static int hex2bin(const char *s, uint8_t *b, int cap) {
    int n = 0; unsigned v;
    while (*s && n < cap) {
        if (*s == ' ') { s++; continue; }
        if (sscanf(s, "%2x", &v) != 1) return -1;
        b[n++] = (uint8_t)v; s += 2;
    }
    return n;
}
/* 用法: drv <pk> <本机SN> <帧hex>:<期望action> [<帧hex>:<期望action> ...]
   全部帧依次喂给同一个 token_bcast_t。期望 action: 0=IGNORE 1=ADD 2=SUB 3=FAIL 4=SYNC */
int main(int argc, char **argv) {
    if (argc < 4) return 2;
    PK = argv[1];
    uint8_t me[6];
    if (hex2bin(argv[2], me, 6) != 6) return 2;

    token_bcast_t tb;
    token_bcast_init(&tb, me, real_mac, NULL);
    static const char *A[] = {"IGNORE","ADD","SUB","FAIL","SYNC"};
    int bad = 0;
    for (int i = 3; i < argc; i++) {
        char *colon = strrchr(argv[i], ':');
        if (!colon) return 2;
        *colon = '\0';
        int want = atoi(colon + 1);
        uint8_t f[64];
        int n = hex2bin(argv[i], f, 64);
        token_result_t r;
        token_bcast_handle(&tb, f, n, 100000u + (unsigned)i * 100u, &r);
        printf("    第%d帧 action=%-6s reason=%-2d balance=%-7u",
               i - 2, A[r.action], (int)r.reason, (unsigned)r.balance);
        if ((int)r.action != want) { printf("  ✗ 期望 %s\n", A[want]); bad = 1; }
        else                       { printf("  ✓\n"); }
    }
    return bad;
}
EOF

echo "编译固件解析代码(宿主机)…"
gcc -Wall -std=c11 -I"$ROOT/components/core/services/include" \
    "$ROOT/components/core/services/src/token_bcast.c" "$TMP/drv.c" \
    -lcrypto -o "$TMP/drv"

frame_of() {   # $1=op $2=seq $3=balance  → 26 字节 hex(无空格)
    CARDID_PK="$PK" python3 "$ROOT/tools/token_broadcast_gui.py" --frame \
        --sn "$SN" --op "$1" --seq "$2" --balance "$3" 2>/dev/null \
        | sed -n '/完整厂商数据/{n;p}' | tr -d ' \n'
}
corrupt() {    # $1=帧hex $2=要篡改的字节下标 → 该字节改成 ff
    local i=$(( $2 * 2 ))
    echo "${1:0:$i}ff${1:$((i+2))}"
}

fail=0
run() {        # $1=说明,其余=<帧hex>:<期望action>
    local desc="$1"; shift
    echo "  $desc"
    if "$TMP/drv" "$PK" "$SN" "$@"; then :; else fail=$((fail+1)); fi
}

echo
echo "上位机生成帧 → 固件解析代码判定  (pk=${PK:0:4}…  sn=$SN)"
run "加分 balance=8888"              "$(frame_of add  1 8888):1"
run "扣分 balance=100"               "$(frame_of sub  2 100):2"
run "业务失败 balance=50"            "$(frame_of fail 3 50):3"
run "静默同步 op=0x04"               "$(frame_of sync 4 6666):4"
run "余额上限 999999"                "$(frame_of add  5 999999):1"
run "余额 0"                         "$(frame_of add  6 0):1"
run "seq=0(现在只是 nonce)"          "$(frame_of add  0 321):1"
run "签名被篡改 → 失败音,整包不采信" "$(corrupt "$(frame_of add 7 123)" 18):3"
run "target 被篡改 → 静默,不响音"    "$(corrupt "$(frame_of add 8 123)" 6):0"

echo
echo "同一张卡的连续行为(多帧喂进同一个状态)"
F="$(frame_of add 10 555)"
run "同一帧连发 4 次 → 只有第 1 次生效" "$F:1" "$F:0" "$F:0" "$F:0"

# ---- 多 pad 交叉:本次协议改动要修的核心 bug ----
# 旧设计(单调 seq + 单一 last_seq)下,padB 从小序号发会被全部当成重复包丢弃。
run "多 pad 交叉(padA seq=500 / padB seq=1 / padA 501 / padB 2)" \
    "$(frame_of add 500 100):1" \
    "$(frame_of add 1   200):1" \
    "$(frame_of sub 501 300):2" \
    "$(frame_of add 2   400):1"

echo
if [ "$fail" -eq 0 ]; then
    echo "✓ 全部通过 —— 上位机与固件逐字节一致"
else
    echo "✗ $fail 项失败 —— 上位机与固件已不一致,检查协议改动是否只改了一边"
    exit 1
fi
