// gcc 直编:验证解码器对已知向量的逐样本输出
#include "services/adpcm.h"
#include <assert.h>
#include <stdio.h>

int main(void) {
    adpcm_state_t st; adpcm_state_reset(&st);
    uint8_t in[1] = {0x08};          // 样本0=code0=0x8, 样本1=code1=0x0
    int16_t out[2] = {123,123};
    int n = adpcm_decode(&st, in, 1, out);
    assert(n == 2);
    assert(out[0] == 0);             // code8: diff=0,pred 0-0=0
    assert(out[1] == 0);             // code0: pred 0+0=0
    adpcm_state_reset(&st);
    uint8_t up[8]; for (int i=0;i<8;i++) up[i]=0x77;   // 16 个 code=7(最大正步进)
    int16_t o2[16];
    adpcm_decode(&st, up, 8, o2);
    assert(o2[0] > 0);
    assert(o2[15] >= o2[0]);         // 单调不降
    printf("test_adpcm PASS\n");
    return 0;
}
