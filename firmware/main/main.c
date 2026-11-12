#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <inttypes.h>
#include <stdatomic.h>
#include <time.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_netif_sntp.h"
#include "esp_http_client.h"
#include "esp_crt_bundle.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "esp_random.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include "cJSON.h"
#include "jpeg_decoder.h"
#include "board.h"
#include "font.h"
#include "multipart.h"
#include "viewer_state.h"
#include "settings.h"
#include "ui.h"
#include "web.h"
#if __has_include("provision.h")
#include "provision.h"
#else
#include "provision.example.h"
#endif

#define PIXELS (320*480)
typedef struct {char id[33],name[32];bool portrait;} camera_t;
typedef struct {uint8_t *data; size_t len; uint32_t gen; int64_t received;} frame_t;
static camera_t cameras[16];      // shown cameras, in display order
static camera_t all_cams[16];     // full gateway catalog
static unsigned all_count;
static viewer_state_t state={.generation=1};
static SemaphoreHandle_t mutex;
static QueueHandle_t free_frames,ready;
static frame_t frames[2];
static bool wifi,controls;
static unsigned brightness=70;
static char status[32]="WIFI DISCONNECTED";
static _Atomic unsigned received,dropped,reconnects;
static _Atomic unsigned received_bytes;
static int64_t last_input;
static int64_t selected_at;
static TaskHandle_t render_handle,touch_handle,network_handle;
static uint32_t stream_gen;
static int64_t wifi_retry_at;
static unsigned wifi_backoff=500;

static void lock(void) {xSemaphoreTake(mutex,portMAX_DELAY);}
static void unlock(void) {xSemaphoreGive(mutex);}
static viewer_state_t snapshot(void) {lock(); viewer_state_t s=state; unlock(); return s;}
static void set_status(const char *s,uint32_t gen) {
    lock(); if(state.generation==gen) snprintf(status,sizeof status,"%s",s); unlock();
}
static esp_err_t http_event(esp_http_client_event_t *e) {
    if(e->event_id==HTTP_EVENT_ON_HEADER && e->user_data && !strcasecmp(e->header_key,"Content-Type"))
        snprintf((char *)e->user_data,128,"%s",e->header_value);
    return ESP_OK;
}
static esp_http_client_handle_t client(const char *path,int timeout, char *content_type) {
    char url[256],auth[160]; settings_t s; settings_get(&s);
    if(snprintf(url,sizeof url,"%s://%s:%u%s",s.https?"https":"http",s.host,(unsigned)s.port,path)>=(int)sizeof url) return NULL;
    esp_http_client_config_t cfg={.url=url,.timeout_ms=timeout,.buffer_size=4096,
        .disable_auto_redirect=true,.cert_pem=VIEWER_CA_PEM,.event_handler=http_event,.user_data=content_type,
        .crt_bundle_attach=VIEWER_CA_PEM?NULL:esp_crt_bundle_attach};
    esp_http_client_handle_t h=esp_http_client_init(&cfg);
    if(h) {snprintf(auth,sizeof auth,"Bearer %s",s.token); esp_http_client_set_header(h,"Authorization",auth);}
    return h;
}
static const char *wifi_reason_name(int r) {
    switch(r) {
    case 2: return "AUTH_EXPIRE"; case 8: return "ASSOC_LEAVE"; case 15: return "4WAY_HANDSHAKE_TIMEOUT";
    case 200: return "BEACON_TIMEOUT"; case 201: return "NO_AP_FOUND"; case 202: return "AUTH_FAIL";
    case 203: return "ASSOC_FAIL"; case 204: return "HANDSHAKE_TIMEOUT"; case 205: return "CONNECTION_FAIL";
    case 210: return "NO_AP_FOUND_W_COMPATIBLE_SECURITY"; case 211: return "NO_AP_FOUND_IN_AUTHMODE_THRESHOLD";
    case 212: return "NO_AP_FOUND_IN_RSSI_THRESHOLD"; default: return "see esp_wifi_types_generic.h";
    }
}
// Diagnostic: list what the radio can see and whether VIEWER_SSID is among it. Called between connect attempts.
static void wifi_diag_scan(void) {
    esp_wifi_disconnect();
    wifi_scan_config_t sc={.show_hidden=true};
    esp_err_t e=esp_wifi_scan_start(&sc,true);
    if(e!=ESP_OK) {ESP_LOGW("wifi_diag","scan failed: %s",esp_err_to_name(e));return;}
    wifi_ap_record_t recs[20]; uint16_t n=sizeof recs/sizeof recs[0], total=0;
    esp_wifi_scan_get_ap_num(&total);
    if(esp_wifi_scan_get_ap_records(&n,recs)!=ESP_OK) n=0;
    bool seen=false; settings_t cs; settings_get(&cs);
    ESP_LOGI("wifi_diag","scan: %u APs visible (showing %u strongest-first as reported)",total,n);
    for(int i=0;i<n;i++) {
        bool target=!strcmp((const char *)recs[i].ssid,cs.ssid); seen|=target;
        ESP_LOGI("wifi_diag","  %-32s rssi=%d ch=%d auth=%d%s",(const char *)recs[i].ssid,recs[i].rssi,recs[i].primary,recs[i].authmode,target?"  <== target":"");
    }
    if(!seen) ESP_LOGW("wifi_diag","target SSID \"%s\" NOT seen in scan",cs.ssid);
}
static void event(void *a,esp_event_base_t base,int32_t id,void *data) {
    if(base==WIFI_EVENT && id==WIFI_EVENT_STA_START) esp_wifi_connect();
    if(base==IP_EVENT && id==IP_EVENT_STA_GOT_IP) {lock(); wifi=true;wifi_backoff=500; unlock();}
    if(base==WIFI_EVENT && id==WIFI_EVENT_STA_DISCONNECTED) {
        const wifi_event_sta_disconnected_t *d=data;
        ESP_LOGW("wifi_diag","disconnected: reason=%d (%s) rssi=%d",d->reason,wifi_reason_name(d->reason),d->rssi);
        lock(); wifi=false; state.generation++; strcpy(status,"WIFI DISCONNECTED");
        wifi_retry_at=esp_timer_get_time()+(wifi_backoff+esp_random()%(wifi_backoff/4+1))*1000LL;
        wifi_backoff=wifi_backoff<15000?wifi_backoff*2:30000;
        unlock();
    }
}
// Called after the user picks a network on the device.
static void apply_wifi(void) {
    settings_t s; settings_get(&s);
    wifi_config_t c={0};
    memcpy(c.sta.ssid,s.ssid,strnlen(s.ssid,sizeof c.sta.ssid));
    memcpy(c.sta.password,s.pass,strnlen(s.pass,sizeof c.sta.password));
    esp_wifi_set_config(WIFI_IF_STA,&c);
    lock(); wifi_retry_at=0; wifi_backoff=500; unlock();
    esp_wifi_disconnect();
}
// Called after encoder IP/port/key change on the web page: reload the camera list from the new gateway.
static void apply_gateway(void) {
    lock(); state.count=0; state.selected=0; all_count=0; state.generation++; snprintf(status,sizeof status,"LOADING CAMERAS"); unlock();
}
// Rebuild the shown list from the full catalog and the saved selection (comma separated ids, display order).
static void rebuild_cameras(void) {
    char pref[544]; settings_get_cams(pref,sizeof pref);
    lock();
    char keep[33]=""; if(state.count) snprintf(keep,sizeof keep,"%s",cameras[state.selected].id);
    camera_t out[16]; unsigned n=0;
    for(const char *p=pref;*p;) {
        size_t l=strcspn(p,",");
        for(unsigned i=0;i<all_count;i++) {
            if(strlen(all_cams[i].id)!=l||strncmp(all_cams[i].id,p,l)) continue;
            bool dup=false; for(unsigned k=0;k<n;k++) dup|=!strcmp(out[k].id,all_cams[i].id);
            if(!dup) out[n++]=all_cams[i];
        }
        p+=l; if(*p==',') p++;
    }
    if(!n) {memcpy(out,all_cams,sizeof out);n=all_count;} // nothing selected or nothing matches: show everything
    memcpy(cameras,out,sizeof out);
    state.count=n; state.selected=0;
    for(unsigned i=0;i<n;i++) if(!strcmp(cameras[i].id,keep)) state.selected=i;
    state.generation++; snprintf(status,sizeof status,"CONNECTING");
    unlock();
}
static int list_cameras(web_cam_t *o,int max) {
    int n=0; lock();
    for(unsigned i=0;i<state.count&&n<max;i++,n++) {
        memcpy(o[n].id,cameras[i].id,sizeof o[n].id);memcpy(o[n].name,cameras[i].name,sizeof o[n].name);o[n].enabled=true;
    }
    for(unsigned i=0;i<all_count&&n<max;i++) {
        bool shown=false; for(unsigned k=0;k<state.count;k++) shown|=!strcmp(cameras[k].id,all_cams[i].id);
        if(shown) continue;
        memcpy(o[n].id,all_cams[i].id,sizeof o[n].id);memcpy(o[n].name,all_cams[i].name,sizeof o[n].name);o[n].enabled=false;n++;
    }
    unlock(); return n;
}
static void apply_cameras(const char ids[][33],int n) {
    char csv[544]=""; size_t used=0;
    for(int i=0;i<n;i++) used+=snprintf(csv+used,sizeof csv-used,"%s%s",i?",":"",ids[i]);
    if(used<sizeof csv) {settings_set_cams(csv);rebuild_cameras();}
}
static bool catalog(void) {
    esp_http_client_handle_t h=client("/v1/cameras",2000,NULL); if(!h) return false;
    char body[4096]; int used=0,n; bool ok=false;
    if(esp_http_client_open(h,0)==ESP_OK && esp_http_client_fetch_headers(h)>=0 && esp_http_client_get_status_code(h)==200) {
        while(used<(int)sizeof(body)-1 && (n=esp_http_client_read(h,body+used,sizeof(body)-1-used))>0) used+=n;
        body[used]=0;
        cJSON *root=cJSON_Parse(body),*list=cJSON_GetObjectItem(root,"cameras");
        unsigned count=cJSON_GetArraySize(list);
        static camera_t parsed[16];
        if(cJSON_IsArray(list)&&count>0&&count<=16) {
            ok=true;
            for(unsigned i=0;i<count;i++) {
                cJSON *c=cJSON_GetArrayItem(list,i),*id=cJSON_GetObjectItem(c,"id"),*name=cJSON_GetObjectItem(c,"name");
                if(!cJSON_IsString(id)||!cJSON_IsString(name)||strlen(id->valuestring)>32||!strlen(id->valuestring)||strlen(name->valuestring)>31) {ok=false;break;}
                if(strspn(id->valuestring,"abcdefghijklmnopqrstuvwxyz0123456789_-")!=strlen(id->valuestring)) {ok=false;break;}
                snprintf(parsed[i].id,sizeof parsed[i].id,"%s",id->valuestring);
                snprintf(parsed[i].name,sizeof parsed[i].name,"%s",name->valuestring);
                cJSON *orient=cJSON_GetObjectItem(c,"orientation");
                parsed[i].portrait=cJSON_IsString(orient)&&!strcmp(orient->valuestring,"portrait");
            }
            if(ok) {lock(); memcpy(all_cams,parsed,sizeof all_cams); all_count=count; unlock();}
        }
        cJSON_Delete(root);
    }
    esp_http_client_close(h); esp_http_client_cleanup(h);
    if(ok) rebuild_cameras();
    return ok;
}
static void publish(void *ctx,const uint8_t *jpeg,size_t len) {
    viewer_state_t s=snapshot();
    if(!viewer_accept(&s,stream_gen)) return;
    frame_t *f=NULL;
    if(xQueueReceive(free_frames,&f,0)!=pdTRUE) {
        if(xQueueReceive(ready,&f,0)!=pdTRUE) {dropped++;return;}
        dropped++;
    }
    memcpy(f->data,jpeg,len); f->len=len; f->gen=stream_gen; f->received=esp_timer_get_time();
    frame_t *old;
    if(xQueueReceive(ready,&old,0)==pdTRUE) {xQueueSend(free_frames,&old,0);dropped++;}
    if(xQueueSend(ready,&f,0)!=pdTRUE) xQueueSend(free_frames,&f,0);
    received++;
    received_bytes+=len;
}
static void network_task(void *arg) {
    uint8_t *compressed=heap_caps_malloc(JPEG_LIMIT,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT); assert(compressed);
    uint8_t chunk[4096]; unsigned backoff=500;
    for(;;) {
        viewer_state_t s=snapshot(); lock(); bool connected=wifi; unlock();
        if(!connected) {
            lock(); bool retry=esp_timer_get_time()>wifi_retry_at; if(retry) wifi_retry_at=esp_timer_get_time()+30000000; unlock();
            if(retry) {
                static unsigned attempts; if(attempts++>0) wifi_diag_scan(); // not on the first attempt: STA_START already connects
                esp_wifi_connect();
            }
            vTaskDelay(pdMS_TO_TICKS(100));continue;
        }
        static bool web_up; if(!web_up) {web_start(&(web_hooks_t){.gateway_changed=apply_gateway,.list_cameras=list_cameras,.apply_cameras=apply_cameras});web_up=true;}
        if(s.off) {vTaskDelay(pdMS_TO_TICKS(100));continue;}
        settings_t cfg_now; settings_get(&cfg_now);
        if(cfg_now.https && time(NULL)<1700000000) {
            set_status("WAITING LOCAL CLOCK",s.generation);vTaskDelay(pdMS_TO_TICKS(500));continue;
        }
        if(!s.count) {
            set_status("LOADING CAMERAS",s.generation);
            if(!catalog()) {set_status("GATEWAY UNAVAILABLE",s.generation);vTaskDelay(pdMS_TO_TICKS(2000));}
            continue;
        }
        char path[80]; snprintf(path,sizeof path,"/v1/stream/%s",cameras[s.selected].id);
        stream_gen=s.generation;
        char ct[128]={0};
        esp_http_client_handle_t h=client(path,500,ct); if(!h) {vTaskDelay(pdMS_TO_TICKS(1000));continue;}
        set_status("CONNECTING",s.generation);
        bool fresh=false;
        esp_err_t open_err=esp_http_client_open(h,0);
        int64_t header_len=open_err==ESP_OK?esp_http_client_fetch_headers(h):-1;
        if(open_err==ESP_OK && header_len>=0) {
            int code=esp_http_client_get_status_code(h);
            ESP_LOGI("net","stream %s: HTTP %d, content-type '%s'",path,code,ct);
            esp_http_client_set_timeout_ms(h,100);
            multipart_t parser;
            if(code==200 && mp_init(&parser,ct,compressed,publish,NULL)) {
                int64_t last=esp_timer_get_time(); unsigned before=received;
                while(true) {
                    viewer_state_t current=snapshot();
                    if(!viewer_accept(&current,stream_gen)) break;
                    int64_t read_start=esp_timer_get_time();
                    int n=esp_http_client_read(h,(char *)chunk,sizeof chunk);
                    if(n== -ESP_ERR_HTTP_EAGAIN) {
                        if(esp_timer_get_time()-last>(fresh?3000000:8000000)) break;
                        vTaskDelay(pdMS_TO_TICKS(1)); continue;
                    }
                    if(n<=0) break;
                    int64_t parse_start=esp_timer_get_time();
                    mp_feed(&parser,chunk,n);
                    if(received!=before && received%100==0) ESP_LOGI("timing","read_us=%lld parser_us=%lld",parse_start-read_start,esp_timer_get_time()-parse_start);
                    if(received!=before) {before=received; last=esp_timer_get_time();fresh=true;backoff=500;}
                    if(esp_timer_get_time()-last>(fresh?3000000:8000000)) break;
                    taskYIELD();
                }
            } else set_status(code==502?"SOURCE UNAVAILABLE":"GATEWAY ERROR",s.generation);
        } else {
            ESP_LOGW("net","stream %s: request failed (open=%s, headers=%lld, errno=%d)",path,esp_err_to_name(open_err),(long long)header_len,esp_http_client_get_errno(h));
            set_status("GATEWAY UNAVAILABLE",s.generation);
        }
        esp_http_client_close(h); esp_http_client_cleanup(h);
        reconnects++;
        if(fresh) set_status("RECONNECTING",s.generation);
        else if(!strcmp(ct,"multipart/x-mixed-replace; boundary=frame")) set_status("SOURCE UNAVAILABLE",s.generation);
        unsigned wait=backoff+esp_random()%(backoff/4+1);
        for(unsigned t=0;t<wait;t+=25) {
            viewer_state_t current=snapshot(); if(current.generation!=s.generation || current.off) break;
            vTaskDelay(pdMS_TO_TICKS(25));
        }
        if(snapshot().generation==s.generation) backoff=backoff<15000?backoff*2:30000;
    }
}
static void touch_task(void *arg) {
    bool down=false,woke=false; int sx=0,sy=0,x=0,y=0; int64_t start=0;
    for(;;) {
        bool in_ui=ui_active(),open_ui=false,tap_ui=false;
        lock(); bool portrait=!in_ui&&state.count&&cameras[state.selected].portrait; unlock();
        bool pressed=board_touch(&x,&y,portrait); int64_t now=esp_timer_get_time();
        lock();
        if(pressed) {
            last_input=now;
            if(!down) {
                sx=x;sy=y;start=now; woke=state.off;
                if(state.off) {state.off=false; state.generation++; strcpy(status,"CONNECTING");}
            }
        }
        if(!pressed&&down&&!woke&&in_ui) {
            tap_ui=abs(x-sx)<15&&abs(y-sy)<15&&now-start<800000;
        } else if(!pressed&&down&&!woke) {
            int direction=viewer_gesture(x-sx,y-sy,(now-start)/1000);
            if(direction && viewer_switch(&state,direction)) {
                strcpy(status,"CONNECTING");selected_at=now;
            } else if(abs(x-sx)<15&&abs(y-sy)<15&&now-start<500000) {
                int lw=portrait?320:480,lh=portrait?480:320;
                if(!VIEWER_BRINGUP_ONLY&&ui_gear_hit(sx,sy,lw)) open_ui=true;
                else if(controls&&sy>lh-60) {
                    if(sx<lw/3) brightness=brightness>20?brightness-20:10;
                    else if(sx<2*lw/3) brightness=brightness<80?brightness+20:100;
                    else if(viewer_switch(&state,1)) {strcpy(status,"CONNECTING");selected_at=now;}
                } else controls=!controls;
            }
        }
        if(VIEWER_SCREEN_OFF_SECONDS>0 && !state.off && now-last_input>(int64_t)VIEWER_SCREEN_OFF_SECONDS*1000000) {
            state.off=true;state.generation++;
        }
        bool off=state.off; unsigned level=brightness;
        if(VIEWER_DIM_SECONDS>0 && now-last_input>(int64_t)VIEWER_DIM_SECONDS*1000000) level=10;
        unlock();
        if(open_ui) ui_open();
        if(tap_ui) ui_tap(sx,sy);
        board_brightness(off?0:level);
        down=pressed; vTaskDelay(pdMS_TO_TICKS(15));
    }
}
static bool current_display(void *ctx) {
    viewer_state_t s=snapshot(); return !s.off&&s.generation==*(uint32_t *)ctx;
}
static void render_task(void *arg) {
    uint16_t *video=heap_caps_calloc(PIXELS,2,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    uint16_t *canvas=heap_caps_malloc(PIXELS*2,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT); assert(video&&canvas);
    uint8_t *work=heap_caps_malloc(8192,MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT); assert(work);
    uint32_t video_gen=0,reported_gen=0; int64_t last=0,next_stats=0; unsigned displayed=0,stats_displayed=0,stats_bytes=0;
    for(;;) {
        frame_t *f=NULL; bool redraw=false; viewer_state_t s=snapshot();
        if(xQueueReceive(ready,&f,pdMS_TO_TICKS(40))==pdTRUE) {
            if(viewer_accept(&s,f->gen)&&esp_timer_get_time()-f->received<1000000) {
                int64_t begin=esp_timer_get_time();
                esp_jpeg_image_cfg_t cfg={.indata=f->data,.indata_size=f->len,.outbuf=(uint8_t *)video,
                    .outbuf_size=PIXELS*2,.out_format=JPEG_IMAGE_FORMAT_RGB565,.out_scale=JPEG_IMAGE_SCALE_0,
                    .flags.swap_color_bytes=1};
                cfg.advanced.working_buffer=work; cfg.advanced.working_buffer_size=8192;
                esp_jpeg_image_output_t out;
                if(jpeg_profile_supported(f->data,f->len) && esp_jpeg_get_image_info(&cfg,&out)==ESP_OK && out.width==320&&out.height==480 &&
                   esp_jpeg_decode(&cfg,&out)==ESP_OK) {
                    viewer_state_t current=snapshot();
                    if(viewer_accept(&current,f->gen)) {video_gen=f->gen;last=esp_timer_get_time();redraw=true;
                        set_status("LIVE",f->gen);
                        if(displayed%100==0) ESP_LOGI("timing","decode_us=%lld receipt_to_decode_us=%lld bytes=%u",last-begin,last-f->received,(unsigned)f->len);
                    }
                } else {video_gen=0;set_status("UNSUPPORTED JPEG",f->gen);}
            } else dropped++;
            xQueueSend(free_frames,&f,0);
        }
        static int64_t overlay_at=0; static uint32_t shown_gen=0; static bool shown_controls=false;
        static int shown_age=-1; static char shown_label[32]=""; static bool shown_ui=false; static uint32_t shown_uiv=0;
        bool uia=ui_active();
        s=snapshot(); int64_t now=esp_timer_get_time();
        lock(); bool show=controls; char label[32]; snprintf(label,sizeof label,"%s",status); unlock();
        if(s.off) {vTaskDelay(pdMS_TO_TICKS(40));continue;}
        int age=video_gen==s.generation?(now-last)/1000000:0;
        bool need=uia?(!shown_ui||ui_version()!=shown_uiv||now-overlay_at>1000000)
                     :(shown_ui||redraw||shown_gen!=s.generation||shown_controls!=show||strcmp(shown_label,label)||age!=shown_age||(show&&now-overlay_at>1000000));
        if(need) {
            uint32_t uiv=ui_version();
            if(uia) {overlay_set_portrait(false);ui_draw(canvas);}
            else {
            if(video_gen==s.generation) memcpy(canvas,video,PIXELS*2); else memset(canvas,0,PIXELS*2);
            char text[64];
            bool portrait=s.count&&cameras[s.selected].portrait; overlay_set_portrait(portrait);
            int lw=portrait?320:480,lh=portrait?480:320;
            overlay_rect(canvas,0,0,lw,32,0);
            snprintf(text,sizeof text,"%s %s",s.count?cameras[s.selected].name:"VIEWER",label);
            overlay_text(canvas,6,4,text,0xffff);
            if(!VIEWER_BRINGUP_ONLY) ui_draw_gear(canvas,lw);
            if(video_gen==s.generation && now-last>1000000) {
                snprintf(text,sizeof text,"STALE %lld S",(now-last)/1000000);
                overlay_rect(canvas,0,32,lw,20,0xf800);overlay_text(canvas,6,36,text,0xffff);
            }
            if(show) {
                overlay_rect(canvas,0,lh-65,lw,65,0);
                wifi_ap_record_t ap; int rssi=esp_wifi_sta_get_ap_info(&ap)==ESP_OK?ap.rssi:0;
                snprintf(text,sizeof text,"RSSI %d HEAP %u",rssi,(unsigned)esp_get_free_heap_size());
                overlay_text(canvas,6,lh-60,text,0xffff);
                if(portrait) overlay_text(canvas,6,lh-30,"DIM    BRIGHT    NEXT",0xffff);
                else overlay_text(canvas,6,lh-30,"DIM          BRIGHT         NEXT",0xffff);
            }
            }
            // Recheck immediately before handing pixels to the sole display owner.
            viewer_state_t current=snapshot();
            if(current.generation==s.generation&&!current.off) {
                int64_t begin=esp_timer_get_time(); bool complete=board_present(canvas,current_display,&s.generation);
                if(complete&&redraw) displayed++;
                if(complete&&video_gen==s.generation&&reported_gen!=s.generation) {
                    lock();int64_t selected=selected_at;unlock();
                    ESP_LOGI("timing","generation=%"PRIu32" switch_display_us=%lld",s.generation,selected?esp_timer_get_time()-selected:0);
                    reported_gen=s.generation;
                }
                if(displayed&&displayed%100==0&&redraw) ESP_LOGI("timing","display_us=%lld",esp_timer_get_time()-begin);
            }
            shown_ui=uia;shown_uiv=uiv;
            shown_gen=s.generation;shown_controls=show;overlay_at=now;shown_age=age;
            snprintf(shown_label,sizeof shown_label,"%s",label);
        }
        if(now>next_stats) {
            wifi_ap_record_t ap;int rssi=esp_wifi_sta_get_ap_info(&ap)==ESP_OK?ap.rssi:0;
            unsigned bytes=received_bytes;
            ESP_LOGI("stats","rx=%u displayed=%u drop=%u reconnect=%u internal_free=%u psram_free=%u rssi=%d frames_10s=%u bytes_10s=%u",received,displayed,dropped,reconnects,
                (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),(unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM),rssi,displayed-stats_displayed,bytes-stats_bytes);
            ESP_LOGI("memory","internal_min=%u internal_largest=%u psram_min=%u psram_largest=%u render_stack=%u touch_stack=%u network_stack=%u",
                (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL),(unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL),
                (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_SPIRAM),(unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_SPIRAM),
                (unsigned)uxTaskGetStackHighWaterMark(NULL),touch_handle?(unsigned)uxTaskGetStackHighWaterMark(touch_handle):0,network_handle?(unsigned)uxTaskGetStackHighWaterMark(network_handle):0);
            stats_displayed=displayed;stats_bytes=bytes;
            next_stats=now+10000000;
        }
    }
}
void app_main(void) {
    web_log_init();
    mutex=xSemaphoreCreateMutex(); assert(mutex);
    ui_init(apply_wifi);
    free_frames=xQueueCreate(2,sizeof(frame_t *));ready=xQueueCreate(1,sizeof(frame_t *));assert(free_frames&&ready);
    for(int i=0;i<2;i++) {frames[i].data=heap_caps_malloc(JPEG_LIMIT,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);assert(frames[i].data);frame_t *f=&frames[i];xQueueSend(free_frames,&f,0);}
    board_init();last_input=esp_timer_get_time();
    if(VIEWER_BRINGUP_ONLY) {
        extern const uint8_t card_start[] asm("_binary_testcard_jpg_start");
        extern const uint8_t card_end[] asm("_binary_testcard_jpg_end");
        state.count=1;strcpy(cameras[0].name,"LOCAL JPEG");stream_gen=state.generation;
        publish(NULL,card_start,card_end-card_start);
        assert(xTaskCreate(render_task,"render",6144,NULL,4,&render_handle)==pdPASS);
        assert(xTaskCreate(touch_task,"touch",4096,NULL,5,&touch_handle)==pdPASS);
        return;
    }
    ESP_ERROR_CHECK(nvs_flash_init());settings_init();ESP_ERROR_CHECK(esp_netif_init());ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();
    wifi_init_config_t init=WIFI_INIT_CONFIG_DEFAULT();ESP_ERROR_CHECK(esp_wifi_init(&init));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT,ESP_EVENT_ANY_ID,event,NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT,IP_EVENT_STA_GOT_IP,event,NULL));
    wifi_config_t cfg={0}; settings_t saved; settings_get(&saved);
    memcpy(cfg.sta.ssid,saved.ssid,strnlen(saved.ssid,sizeof cfg.sta.ssid));
    memcpy(cfg.sta.password,saved.pass,strnlen(saved.pass,sizeof cfg.sta.password));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA,&cfg));ESP_ERROR_CHECK(esp_wifi_start());
    esp_sntp_config_t ntp=ESP_NETIF_SNTP_DEFAULT_CONFIG(VIEWER_NTP_SERVER);
    ESP_ERROR_CHECK(esp_netif_sntp_init(&ntp));
    assert(xTaskCreate(render_task,"render",6144,NULL,4,&render_handle)==pdPASS);
    assert(xTaskCreate(touch_task,"touch",4096,NULL,5,&touch_handle)==pdPASS);
    assert(xTaskCreate(network_task,"network",12288,NULL,3,&network_handle)==pdPASS);
}
