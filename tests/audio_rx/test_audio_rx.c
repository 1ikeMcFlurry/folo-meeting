#include "services/audio_rx.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

static uint8_t g_buf[256]; static int g_n; static int g_began_id; static int g_ended;
static int s_begin(void*u,int id,uint32_t t){(void)u;(void)t; if(id<0||id>=3)return -1; g_began_id=id; g_n=0; return 0;}
static int s_write(void*u,const uint8_t*d,int n){(void)u; memcpy(g_buf+g_n,d,n); g_n+=n; return 0;}
static int s_end(void*u){(void)u; g_ended=1; return 0;}

int main(void){
    audio_clip_sink_t sink={s_begin,s_write,s_end,0};
    audio_clip_rx_t r; audio_clip_rx_init(&r); int done;
    // BEGIN clip_id=2, total=3 (total 为 u32 小端)
    uint8_t begin[6]={0x00, 2, 3, 0, 0, 0};
    assert(audio_clip_rx_frame(&r,begin,6,&sink,&done)==0); assert(g_began_id==2);
    uint8_t data[4]={0x01,'x','y','z'};
    assert(audio_clip_rx_frame(&r,data,4,&sink,&done)==0); assert(done==0);
    uint8_t end[1]={0x02};
    assert(audio_clip_rx_frame(&r,end,1,&sink,&done)==0); assert(done==1);
    assert(g_ended==1); assert(g_n==3); assert(memcmp(g_buf,"xyz",3)==0);

    // 非法 clip_id → 3
    audio_clip_rx_init(&r);
    uint8_t bad[6]={0x00, 9, 1, 0, 0, 0};
    assert(audio_clip_rx_frame(&r,bad,6,&sink,&done)==3);

    // DATA 先于 BEGIN → 1
    audio_clip_rx_init(&r);
    uint8_t d2[2]={0x01,'a'};
    assert(audio_clip_rx_frame(&r,d2,2,&sink,&done)==1);

    printf("test_audio_rx PASS\n");
    return 0;
}
