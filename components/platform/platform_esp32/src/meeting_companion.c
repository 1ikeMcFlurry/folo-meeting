#include "meeting_companion.h"
#include "meeting_cloud.h"
#include "meeting_ble.h"
#include "meeting_provision.h"
#include "platform/meeting_wifi.h"
#include "platform/meeting_trial.h"
#include "nvs.h"
#include "esp_timer.h"
#include "esp_random.h"
#include "mbedtls/platform_util.h"
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

enum { EMPTY, CREATING, STREAMING, STOP_NEEDED, STOP_SENT, PROCESSING, READY, CLOUD_FAILED };
typedef struct {
    uint32_t version, phase, export_stage, seconds, elapsed_ms, complete, automatic, error;
    int64_t created;
    char task[33], title[161], doc[65], claim[33];
} record_t;
static record_t current;
static atomic_bool start_requested, setup_requested, busy;
static int64_t next_poll, ble_until;
static void poll_cloud(void);
static void uuid(char out[33]) { uint8_t b[16]; esp_fill_random(b,16); for(int i=0;i<16;i++) snprintf(out+i*2,3,"%02x",b[i]); }
static void copy(char *out, size_t size, const char *value) {
    size_t n=strlen(value); if(n>=size) { n=size-1; while(n && ((unsigned char)value[n]&0xc0)==0x80) --n; }
    memcpy(out,value,n); out[n]=0;
}
static bool save_record(void) {
    nvs_handle_t h; if(nvs_open("meeting_user",NVS_READWRITE,&h)!=ESP_OK) return false;
    bool ok=nvs_set_blob(h,"current",&current,sizeof current)==ESP_OK && nvs_commit(h)==ESP_OK;
    nvs_close(h); return ok;
}
static bool find_record(const char *task,record_t *record,char key[16]) {
    if(!*task) return false;
    if(!strcmp(task,current.task)) { *record=current; strcpy(key,"current"); return true; }
    nvs_handle_t h; if(nvs_open("meeting_user",NVS_READONLY,&h)!=ESP_OK) return false;
    bool found=false;
    for(int i=0;i<8;i++) {
        snprintf(key,16,"record%d",i); size_t size=sizeof *record;
        if(nvs_get_blob(h,key,record,&size)==ESP_OK && size==sizeof *record && record->version==1 && !strcmp(record->task,task)) { found=true; break; }
    }
    nvs_close(h); return found;
}
static bool update_record(const char *key,const record_t *record) {
    nvs_handle_t h; if(nvs_open("meeting_user",NVS_READWRITE,&h)!=ESP_OK) return false;
    bool ok=nvs_set_blob(h,key,record,sizeof *record)==ESP_OK && nvs_commit(h)==ESP_OK;
    nvs_close(h); if(ok && !strcmp(key,"current")) current=*record; return ok;
}
static bool archive(void) {
    if(!current.version) return true;
    nvs_handle_t h; if(nvs_open("meeting_user",NVS_READWRITE,&h)!=ESP_OK) return false;
    uint32_t index=0; nvs_get_u32(h,"archive_seq",&index);
    char key[16]; snprintf(key,sizeof key,"record%u",(unsigned)(index%8));
    bool ok=nvs_set_blob(h,key,&current,sizeof current)==ESP_OK && nvs_set_u32(h,"archive_seq",index+1)==ESP_OK && nvs_commit(h)==ESP_OK;
    nvs_close(h); return ok;
}
static cJSON *record_json(const record_t *r) {
    cJSON *out=cJSON_CreateObject();
    cJSON_AddStringToObject(out,"task_id",r->task); cJSON_AddStringToObject(out,"title",r->title);
    cJSON_AddStringToObject(out,"document_id",r->doc); cJSON_AddNumberToObject(out,"phase",r->phase);
    cJSON_AddNumberToObject(out,"export_stage",r->export_stage); cJSON_AddNumberToObject(out,"elapsed_ms",r->elapsed_ms);
    cJSON_AddNumberToObject(out,"created_at",(double)r->created); cJSON_AddBoolToObject(out,"complete",r->complete);
    cJSON_AddStringToObject(out,"export_mode",r->automatic ? "auto" : "phone"); cJSON_AddNumberToObject(out,"error_code",r->error);
    return out;
}
static cJSON *reply(bool ok,const char *error) {
    cJSON *out=cJSON_CreateObject(); cJSON_AddBoolToObject(out,"ok",ok);
    if(error) cJSON_AddStringToObject(out,"error",error);
    return out;
}
void meeting_companion_snapshot(meeting_status_t *view) {
    view->companion=true; view->ble_enabled=meeting_ble_active(); view->ble_connected=meeting_ble_connected();
    view->pairing_code=meeting_ble_passkey();
    if(view->state==MEETING_IDLE && current.phase>=STOP_NEEDED) {
        view->elapsed_ms=current.elapsed_ms;
        view->state=!current.complete ? MEETING_ERROR : current.phase==READY ? MEETING_SUMMARY_READY :
            current.phase==CLOUD_FAILED ? MEETING_SUMMARY_FAILED : MEETING_ENDED;
    }
    view->document_ready=current.export_stage==5;
    meeting_provision_snapshot(view);
}
void meeting_companion_button(void) {
    if(meeting_provision_busy()) return;
    if(atomic_load(&busy)) { platform_meeting_trial_stop(); return; }
    atomic_store(&start_requested,true);
}
void meeting_companion_setup(void) {
    if(meeting_provision_busy()) meeting_provision_cancel();
    else if(!atomic_load(&busy)) atomic_store(&setup_requested,true);
}
bool meeting_companion_take_start(void) { return atomic_exchange(&start_requested,false); }
void meeting_companion_init(void) {
    nvs_handle_t h;
    if(nvs_open("meeting_user",NVS_READONLY,&h)==ESP_OK) {
        size_t n=sizeof current;
        if(nvs_get_blob(h,"current",&current,&n)!=ESP_OK || n!=sizeof current || current.version!=1) memset(&current,0,sizeof current);
        nvs_close(h);
    }
    if(current.phase==STREAMING) { current.complete=0; current.phase=STOP_NEEDED; save_record(); }
    if(current.phase<STOP_NEEDED || current.phase>=READY) meeting_ble_start(true);
    ble_until=esp_timer_get_time()+300000000;
}
static bool pending_record(void) {
    return current.phase && (current.phase<READY || (current.automatic && current.export_stage>0 && current.export_stage<5));
}
cJSON *meeting_companion_prepare(void) {
    if(pending_record() || meeting_provision_busy()) return NULL;
    atomic_store(&busy,true);
    if(!meeting_ble_stop() || !meeting_wifi_connect(NULL,NULL,true) || !meeting_clock_sync()) goto failure;
    cJSON *settings=meeting_settings_load();
    if(!*meeting_json_string(settings,"app_key") || !*meeting_json_string(settings,"ak_id") || !*meeting_json_string(settings,"ak_secret")) {
        meeting_json_wipe(settings); cJSON_Delete(settings); goto failure;
    }
    if(!archive()) { meeting_json_wipe(settings); cJSON_Delete(settings); goto failure; }
    memset(&current,0,sizeof current); current.version=1; current.phase=CREATING; current.created=time(NULL);
    current.automatic=!strcmp(meeting_json_string(settings,"export_mode"),"auto");
    cJSON *duration=cJSON_GetObjectItemCaseSensitive(settings,"max_seconds");
    current.seconds=cJSON_IsNumber(duration) ? duration->valueint : 7200;
    copy(current.title,sizeof current.title,meeting_json_string(settings,"title"));
    if(!*current.title) { time_t now=current.created+8*3600; struct tm t; gmtime_r(&now,&t); strftime(current.title,sizeof current.title,"%Y-%m-%d %H:%M 会议",&t); }
    uuid(current.claim);
    cJSON *body=cJSON_CreateObject(), *input=cJSON_AddObjectToObject(body,"Input"), *params=cJSON_AddObjectToObject(body,"Parameters");
    cJSON_AddStringToObject(body,"AppKey",meeting_json_string(settings,"app_key"));
    cJSON_AddStringToObject(input,"TaskKey",current.claim); cJSON_AddStringToObject(input,"Format","pcm");
    cJSON_AddNumberToObject(input,"SampleRate",16000); cJSON_AddStringToObject(input,"SourceLanguage","cn");
    cJSON_AddBoolToObject(input,"ProgressiveCallbacksEnabled",false);
    cJSON *trans=cJSON_AddObjectToObject(params,"Transcription"); cJSON_AddBoolToObject(trans,"DiarizationEnabled",true);
    cJSON_AddNumberToObject(trans,"OutputLevel",2); cJSON_AddNumberToObject(cJSON_AddObjectToObject(trans,"Diarization"),"SpeakerCount",0);
    if(cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(settings,"audio_output_enabled")))
        cJSON_AddStringToObject(cJSON_AddObjectToObject(params,"Transcoding"),"TargetAudioFormat","mp3");
    cJSON_AddBoolToObject(params,"SummarizationEnabled",true);
    cJSON *types=cJSON_AddArrayToObject(cJSON_AddObjectToObject(params,"Summarization"),"Types"); cJSON_AddItemToArray(types,cJSON_CreateString("Paragraph"));
    cJSON_AddBoolToObject(params,"MeetingAssistanceEnabled",true);
    types=cJSON_AddArrayToObject(cJSON_AddObjectToObject(params,"MeetingAssistance"),"Types"); cJSON_AddItemToArray(types,cJSON_CreateString("Actions"));
    meeting_json_wipe(settings); cJSON_Delete(settings);
    if(!save_record()) { cJSON_Delete(body); goto failure; }
    cJSON *result=meeting_tingwu("PUT","/openapi/tingwu/v2/tasks","type=realtime",body); cJSON_Delete(body);
    cJSON *data=cJSON_GetObjectItemCaseSensitive(result,"Data");
    const char *task=meeting_json_string(data,"TaskId"), *url=meeting_json_string(data,"MeetingJoinUrl");
    cJSON *config=NULL;
    if(strlen(task)==32 && strspn(task,"0123456789abcdef")==32 && *url) {
        copy(current.task,sizeof current.task,task); current.phase=STREAMING;
        if(save_record()) {
            config=cJSON_CreateObject(); cJSON_AddStringToObject(config,"task_id",task); cJSON_AddStringToObject(config,"url",url);
            cJSON_AddBoolToObject(config,"use_device_wifi",true); cJSON_AddNumberToObject(config,"seconds",current.seconds);
            cJSON_AddNumberToObject(config,"epoch",(double)time(NULL));
        } else { current.phase=STOP_NEEDED; }
    } else {
        current.error=meeting_cloud_error();
        if(current.error==1001 || current.error==1008 || current.error==1010 ||
           (current.error>=400 && current.error<500 && current.error!=408)) current.phase=CLOUD_FAILED;
        save_record();
    }
    meeting_json_wipe(result); cJSON_Delete(result);
    if(config) return config;
failure:
    atomic_store(&busy,false); meeting_ble_start(false); ble_until=esp_timer_get_time()+300000000; return NULL;
}
void meeting_companion_finished(bool complete,unsigned elapsed_ms) {
    current.complete=complete; current.elapsed_ms=elapsed_ms; current.phase=STOP_NEEDED;
    if(!save_record()) current.error=2001;
    atomic_store(&busy,false); next_poll=0;
    // Finish the cloud task with the BLE heap still released. Pairing may
    // resume immediately afterwards while the provider generates the result.
    poll_cloud();
    // Give the cloud finalization exclusive radio/TLS memory. BLE resumes
    // once final task metadata is available, or after a bounded recovery wait.
    ble_until=esp_timer_get_time()+120000000;
}
static void paragraph(cJSON *array,const char *text) {
    cJSON *block=cJSON_CreateObject(); cJSON_AddNumberToObject(block,"block_type",2);
    cJSON *elements=cJSON_AddArrayToObject(cJSON_AddObjectToObject(block,"text"),"elements");
    for(size_t offset=0,length=strlen(text);offset<length;) {
        char part[1501]; size_t count=length-offset; if(count>1500) count=1500;
        while(count && offset+count<length && ((unsigned char)text[offset+count]&0xc0)==0x80) --count;
        memcpy(part,text+offset,count); part[count]=0; offset+=count;
        cJSON *element=cJSON_CreateObject(); cJSON_AddStringToObject(cJSON_AddObjectToObject(element,"text_run"),"content",part);
        cJSON_AddItemToArray(elements,element);
    }
    cJSON_AddItemToArray(array,block);
}
static bool verify_document(const char *token,const char *summary) {
    char path[256]; snprintf(path,sizeof path,"/open-apis/docx/v1/documents/%s/blocks/%s/children?page_size=50",current.doc,current.doc);
    cJSON *result=meeting_feishu("GET",path,NULL,token), *data=cJSON_GetObjectItemCaseSensitive(result,"data");
    cJSON *items=cJSON_GetObjectItemCaseSensitive(data,"items"); bool found=false;
    cJSON *item=NULL;
    cJSON_ArrayForEach(item,items) {
        cJSON *elements=cJSON_GetObjectItemCaseSensitive(cJSON_GetObjectItemCaseSensitive(item,"text"),"elements");
        size_t offset=0,expected=strlen(summary); cJSON *element=NULL; bool match=true;
        cJSON_ArrayForEach(element,elements) {
            const char *part=meeting_json_string(cJSON_GetObjectItemCaseSensitive(element,"text_run"),"content"); size_t length=strlen(part);
            if(offset+length>expected || memcmp(summary+offset,part,length)) { match=false; break; }
            offset+=length;
        }
        if(match && offset==expected) found=true;
    }
    cJSON_Delete(result); return found;
}
static void auto_export(cJSON *cloud_result) {
    if(!current.automatic || !current.complete || current.export_stage) return;
    cJSON *data=cJSON_GetObjectItemCaseSensitive(cloud_result,"Data");
    const char *url=meeting_json_string(cJSON_GetObjectItemCaseSensitive(data,"Result"),"Summarization");
    cJSON *summary_json=meeting_download_result(url,12288);
    const char *original=meeting_json_string(cJSON_GetObjectItemCaseSensitive(summary_json,"Summarization"),"ParagraphSummary");
    char *summary=calloc(1,12001);
    if(!summary || !*original || strlen(original)>12000) { free(summary); cJSON_Delete(summary_json); current.error=2002; save_record(); return; }
    strcpy(summary,original); cJSON_Delete(summary_json);
    url=meeting_json_string(cJSON_GetObjectItemCaseSensitive(data,"Result"),"MeetingAssistance");
    cJSON *assistance=*url ? meeting_download_result(url,12288) : NULL;
    cJSON *actions=cJSON_GetObjectItemCaseSensitive(cJSON_GetObjectItemCaseSensitive(assistance,"MeetingAssistance"),"Actions"), *action=NULL;
    bool valid=!*url || assistance!=NULL;
    bool heading=false;
    cJSON_ArrayForEach(action,actions) {
        const char *text=cJSON_IsString(action) ? action->valuestring : meeting_json_string(action,"Text");
        if(!*text) text=meeting_json_string(action,"Content");
        if(!*text) continue;
        const char *prefix=heading ? "\n• " : "\n\n待办事项\n• ";
        if(strlen(summary)+strlen(prefix)+strlen(text)>12000) { valid=false; break; }
        strcat(summary,prefix); strcat(summary,text); heading=true;
    }
    cJSON_Delete(assistance);
    cJSON *credentials=meeting_settings_load();
    const char *sensitive[]={"ak_id","ak_secret","feishu_app_secret","feishu_refresh_token"};
    for(unsigned i=0;i<sizeof sensitive/sizeof sensitive[0];i++) {
        const char *secret=meeting_json_string(credentials,sensitive[i]);
        if(strlen(secret)>=6 && (strstr(summary,secret) || strstr(current.title,secret))) valid=false;
    }
    if(strstr(summary,"Bearer ") || strstr(summary,"LTAI") || strstr(summary,"-----BEGIN ")) valid=false;
    meeting_json_wipe(credentials); cJSON_Delete(credentials);
    if(!valid) { free(summary); current.error=2005; save_record(); return; }
    char *token=meeting_feishu_token();
    if(!token) { free(summary); current.error=2003; save_record(); return; }
    cJSON *settings=meeting_settings_load(), *body=cJSON_CreateObject();
    cJSON_AddStringToObject(body,"title",current.title);
    const char *folder=meeting_json_string(settings,"folder_token"); if(*folder) cJSON_AddStringToObject(body,"folder_token",folder);
    meeting_json_wipe(settings); cJSON_Delete(settings);
    // Persist intent before creating. An ambiguous response is never retried automatically.
    current.export_stage=1;
    cJSON *created=save_record() ? meeting_feishu("POST","/open-apis/docx/v1/documents",body,token) : NULL;
    cJSON_Delete(body);
    const char *id=meeting_json_string(cJSON_GetObjectItemCaseSensitive(cJSON_GetObjectItemCaseSensitive(created,"data"),"document"),"document_id");
    if(*id && strlen(id)<sizeof current.doc) {
        copy(current.doc,sizeof current.doc,id); current.export_stage=2;
        if(save_record()) {
            body=cJSON_CreateObject(); cJSON *children=cJSON_AddArrayToObject(body,"children");
            paragraph(children,summary);
            char path[256]; snprintf(path,sizeof path,"/open-apis/docx/v1/documents/%s/blocks/%s/children",id,id);
            current.export_stage=3;
            cJSON *written=save_record() ? meeting_feishu("POST",path,body,token) : NULL;
            if(written) { current.export_stage=4; save_record(); if(verify_document(token,summary)) { current.export_stage=5; current.error=0; save_record(); } }
            cJSON_Delete(written); cJSON_Delete(body);
        }
    }
    cJSON_Delete(created); free(summary); mbedtls_platform_zeroize(token,strlen(token)); free(token);
}
static void poll_cloud(void) {
    if(!*current.task || current.phase<STOP_NEEDED || current.phase==CLOUD_FAILED) return;
    if(current.phase==READY && (!current.automatic || current.export_stage || current.error)) return;
    if(!meeting_wifi_connected() && !meeting_wifi_connect(NULL,NULL,true)) return;
    if(!meeting_clock_sync()) return;
    // The previous attempt failed before sending its JSON request body.
    if(current.phase==STOP_SENT && current.error==1001) { current.phase=STOP_NEEDED; save_record(); }
    if(current.phase==STOP_NEEDED) {
        cJSON *settings=meeting_settings_load(), *body=cJSON_CreateObject();
        cJSON_AddStringToObject(body,"AppKey",meeting_json_string(settings,"app_key"));
        cJSON_AddStringToObject(cJSON_AddObjectToObject(body,"Input"),"TaskId",current.task);
        meeting_json_wipe(settings); cJSON_Delete(settings);
        current.phase=STOP_SENT;
        // A restart/network ambiguity must query first, never charge for a blind repeated stop.
        cJSON *stopped=save_record() ? meeting_tingwu("PUT","/openapi/tingwu/v2/tasks","operation=stop&type=realtime",body) : NULL;
        if(stopped) { current.phase=PROCESSING; current.error=0; }
        else current.error=meeting_cloud_error();
        cJSON_Delete(stopped); cJSON_Delete(body); save_record(); return;
    }
    char path[96]; snprintf(path,sizeof path,"/openapi/tingwu/v2/tasks/%s",current.task);
    cJSON *result=meeting_tingwu("GET",path,NULL,NULL);
    const char *state=meeting_json_string(cJSON_GetObjectItemCaseSensitive(result,"Data"),"TaskStatus");
    if(!strcmp(state,"COMPLETED")) { current.phase=READY; current.error=0; save_record(); auto_export(result); }
    else if(!strcmp(state,"FAILED") || !strcmp(state,"INVALID")) { current.phase=CLOUD_FAILED; save_record(); }
    // A crashed stream can remain PAUSED even after our single finalization
    // request. Preserve the incomplete record for review instead of blocking
    // all future recordings, and never repeat a billable stop automatically.
    else if(!strcmp(state,"PAUSED") && current.phase==PROCESSING && !current.complete) {
        current.phase=CLOUD_FAILED; current.error=2002; save_record();
    }
    cJSON_Delete(result);
}
cJSON *meeting_companion_command(cJSON *request) {
    const char *cmd=meeting_json_string(request,"cmd");
    cJSON *out=NULL;
    if(!strcmp(cmd,"status")) {
        out=record_json(&current); cJSON *settings=meeting_settings_load();
        cJSON_AddBoolToObject(out,"configured",*meeting_json_string(settings,"ak_id") && *meeting_json_string(settings,"app_key"));
        cJSON_AddBoolToObject(out,"wifi",meeting_wifi_connected()); cJSON_AddBoolToObject(out,"ble",meeting_ble_active());
        cJSON_AddBoolToObject(out,"audio_output_supported",true);
        cJSON_AddBoolToObject(out,"audio_output_enabled",cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(settings,"audio_output_enabled")));
        meeting_json_wipe(settings); cJSON_Delete(settings);
    } else if(!strcmp(cmd,"record")) {
        cJSON *value=cJSON_GetObjectItemCaseSensitive(request,"index");
        int index=cJSON_IsNumber(value) && value->valuedouble==value->valueint ? value->valueint : -1;
        record_t record={0}; nvs_handle_t h;
        if(index>=0 && index<8 && nvs_open("meeting_user",NVS_READONLY,&h)==ESP_OK) {
            char key[16]; snprintf(key,sizeof key,"record%d",index); size_t n=sizeof record;
            if(nvs_get_blob(h,key,&record,&n)!=ESP_OK || n!=sizeof record) memset(&record,0,sizeof record);
            nvs_close(h);
        }
        out=record_json(&record);
    } else if(!strcmp(cmd,"configure") && !atomic_load(&busy) && !pending_record()) {
        cJSON *settings=meeting_settings_load(), *fields=cJSON_GetObjectItemCaseSensitive(request,"settings");
        const struct {const char *key; size_t max;} limits[]={
            {"ak_id",64},{"ak_secret",128},{"app_key",128},{"feishu_app_id",64},{"feishu_app_secret",128},
            {"feishu_refresh_token",2048},{"folder_token",128},{"title",160},{"export_mode",5}};
        bool valid=cJSON_IsObject(fields);
        for(unsigned i=0;i<sizeof limits/sizeof limits[0];i++) {
            cJSON *value=cJSON_GetObjectItemCaseSensitive(fields,limits[i].key);
            if(!value) continue;
            if(!cJSON_IsString(value) || strlen(value->valuestring)>limits[i].max) { valid=false; break; }
            for(const unsigned char *p=(const unsigned char *)value->valuestring;*p;p++)
                if(*p<32 || *p==127 || (strcmp(limits[i].key,"title") && (*p==' ' || *p>=127))) valid=false;
            // Omitted credentials remain unchanged; explicit empty values remove them.
            cJSON_DeleteItemFromObjectCaseSensitive(settings,limits[i].key);
            cJSON_AddStringToObject(settings,limits[i].key,value->valuestring);
        }
        cJSON *duration=cJSON_GetObjectItemCaseSensitive(fields,"max_seconds");
        if(duration) {
            if(!cJSON_IsNumber(duration) || duration->valueint<1 || duration->valueint>86400 || duration->valuedouble!=duration->valueint) valid=false;
            else { cJSON_DeleteItemFromObjectCaseSensitive(settings,"max_seconds"); cJSON_AddNumberToObject(settings,"max_seconds",duration->valueint); }
        }
        cJSON *audio_output=cJSON_GetObjectItemCaseSensitive(fields,"audio_output_enabled");
        if(audio_output) {
            if(!cJSON_IsBool(audio_output)) valid=false;
            else { cJSON_DeleteItemFromObjectCaseSensitive(settings,"audio_output_enabled"); cJSON_AddBoolToObject(settings,"audio_output_enabled",cJSON_IsTrue(audio_output)); }
        }
        const char *mode=meeting_json_string(settings,"export_mode");
        if(*mode && strcmp(mode,"auto") && strcmp(mode,"phone")) valid=false;
        if(!strcmp(mode,"auto") && !*meeting_json_string(settings,"feishu_refresh_token")) valid=false;
        bool ok=valid && meeting_settings_save(settings);
        meeting_json_wipe(settings); cJSON_Delete(settings); out=reply(ok,ok ? NULL : "invalid_settings_or_storage");
    } else if(!strcmp(cmd,"provision") && !atomic_load(&busy) && !pending_record()) {
        out=cJSON_CreateObject(); bool ok=meeting_provision_request(out);
        cJSON_AddBoolToObject(out,"ok",ok);
        if(!ok) cJSON_AddStringToObject(out,"error","provision_busy");
    } else if(!strcmp(cmd,"wifi") && !atomic_load(&busy) && !pending_record() && !meeting_provision_busy()) {
        bool ok=meeting_wifi_provision(meeting_json_string(request,"ssid"),meeting_json_string(request,"password"));
        out=reply(ok,ok ? NULL : "wifi_failed");
    } else if(!strcmp(cmd,"start") && !atomic_load(&busy) && !pending_record()) {
        atomic_store(&start_requested,true); out=reply(true,NULL);
    } else if(!strcmp(cmd,"claim") || !strcmp(cmd,"document") || !strcmp(cmd,"published") || !strcmp(cmd,"result_ready")) {
        record_t record; char key[16]; bool valid=find_record(meeting_json_string(request,"task_id"),&record,key);
        bool return_claim=false;
        if(valid && !strcmp(cmd,"claim")) {
            const char *claim=meeting_json_string(request,"claim");
            valid=strlen(claim)==32 && strspn(claim,"0123456789abcdef")==32 &&
                record.phase==READY && !record.automatic && (record.export_stage==0 ||
                (record.export_stage==1 && !strcmp(claim,record.claim)));
            if(valid) { copy(record.claim,sizeof record.claim,claim); record.export_stage=1; return_claim=true; }
        } else if(valid && !strcmp(cmd,"document")) {
            const char *id=meeting_json_string(request,"document_id");
            valid=*id && strlen(id)<sizeof record.doc && strspn(id,"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")==strlen(id) &&
                !strcmp(record.claim,meeting_json_string(request,"claim")) &&
                (record.export_stage==1 || (record.export_stage>=2 && !strcmp(record.doc,id)));
            if(valid) { copy(record.doc,sizeof record.doc,id); if(record.export_stage<2) record.export_stage=2; }
        } else if(valid && !strcmp(cmd,"published")) {
            valid=*record.doc && !strcmp(record.doc,meeting_json_string(request,"document_id"));
            if(valid) record.export_stage=5;
        } else if(valid) {
            // An authenticated, user-owned phone already verified COMPLETED via
            // Tingwu. It can acknowledge that result while BLE defers device TLS.
            valid=!record.automatic && record.phase>=STOP_SENT && record.phase<=READY;
            if(valid) { record.phase=READY; record.error=0; }
        }
        bool ok=valid && update_record(key,&record); out=reply(ok,ok ? NULL : "record_state_or_storage");
        if(ok && return_claim) cJSON_AddStringToObject(out,"claim",record.claim);
    } else if(!strcmp(cmd,"retry_result") && current.phase>=STOP_SENT) {
        current.error=0; next_poll=0; out=reply(true,NULL);
    } else if(!strcmp(cmd,"ble_cycle") && !atomic_load(&busy)) {
        bool ok=meeting_ble_stop() && meeting_ble_start(true);
        if(ok) ble_until=esp_timer_get_time()+300000000;
        out=reply(ok,ok ? NULL : "ble_cycle_failed");
    } else out=reply(false,"busy_or_invalid_command");
    cJSON *seq=cJSON_GetObjectItemCaseSensitive(request,"seq");
    if(cJSON_IsNumber(seq)) cJSON_AddNumberToObject(out,"seq",seq->valuedouble);
    return out;
}
void meeting_companion_poll(void) {
    if(meeting_provision_poll()) { ble_until=esp_timer_get_time()+300000000; return; }
    if(atomic_exchange(&setup_requested,false)) { meeting_ble_start(true); ble_until=esp_timer_get_time()+300000000; }
    cJSON *request=meeting_ble_take();
    if(request) {
        cJSON *out=meeting_companion_command(request); meeting_ble_reply(out);
        meeting_json_wipe(request); cJSON_Delete(request); meeting_json_wipe(out); cJSON_Delete(out);
        ble_until=esp_timer_get_time()+300000000;
    }
    int64_t now=esp_timer_get_time();
    if(now>=next_poll && !meeting_ble_connected()) {
        next_poll=now+15000000;
        bool needed=*current.task && current.phase>=STOP_NEEDED && current.phase!=CLOUD_FAILED &&
            (current.phase!=READY || (current.automatic && !current.export_stage && !current.error));
        if(needed) {
            bool restore=meeting_ble_active();
            if(meeting_ble_stop()) poll_cloud();
            if(restore || current.phase>=READY || now>ble_until) {
                meeting_ble_start(false); ble_until=esp_timer_get_time()+300000000;
            }
        }
    }
    if(now>ble_until && !meeting_ble_connected() && meeting_ble_active()) meeting_ble_stop();
}
