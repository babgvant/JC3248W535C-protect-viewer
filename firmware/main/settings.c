#include "settings.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include "freertos/FreeRTOS.h"
#include "nvs.h"
#include "esp_log.h"
#if __has_include("provision.h")
#include "provision.h"
#else
#include "provision.example.h"
#endif

static portMUX_TYPE mux=portMUX_INITIALIZER_UNLOCKED;
static settings_t cur;
static bool have;
static char cams[544];

static void defaults(settings_t *s) {
    memset(s,0,sizeof *s);
    snprintf(s->ssid,sizeof s->ssid,"%s",VIEWER_SSID);
    snprintf(s->pass,sizeof s->pass,"%s",VIEWER_PASSWORD);
    snprintf(s->token,sizeof s->token,"%s",VIEWER_TOKEN);
    const char *g=VIEWER_GATEWAY;
    s->https=!strncmp(g,"https://",8);
    if(s->https) g+=8; else if(!strncmp(g,"http://",7)) g+=7;
    size_t n=strcspn(g,":/"); if(n>=sizeof s->host) n=sizeof s->host-1;
    memcpy(s->host,g,n);
    int port=g[n]==':'?atoi(g+n+1):0;
    s->port=port>0&&port<65536?port:(s->https?443:80);
}
static void ensure(void) {
    if(have) return;
    settings_t d; defaults(&d);
    portENTER_CRITICAL(&mux); if(!have) {cur=d;have=true;} portEXIT_CRITICAL(&mux);
}
static void load_str(nvs_handle_t h,const char *key,char *dst,size_t cap) {
    char tmp[544]; size_t len=cap<sizeof tmp?cap:sizeof tmp;
    if(nvs_get_str(h,key,tmp,&len)==ESP_OK) {
        size_t n=strnlen(tmp,sizeof tmp); if(n>=cap) n=cap-1;
        memcpy(dst,tmp,n); dst[n]=0;
    }
}
void settings_init(void) {
    ensure();
    nvs_handle_t h; if(nvs_open("viewer",NVS_READONLY,&h)!=ESP_OK) return;
    settings_t s; settings_get(&s);
    load_str(h,"ssid",s.ssid,sizeof s.ssid); load_str(h,"pass",s.pass,sizeof s.pass);
    load_str(h,"host",s.host,sizeof s.host); load_str(h,"token",s.token,sizeof s.token);
    char c[544]={0}; load_str(h,"cams",c,sizeof c);
    portENTER_CRITICAL(&mux); memcpy(cams,c,sizeof c); portEXIT_CRITICAL(&mux);
    uint16_t port; if(nvs_get_u16(h,"port",&port)==ESP_OK&&port) s.port=port;
    nvs_close(h);
    portENTER_CRITICAL(&mux); cur=s; portEXIT_CRITICAL(&mux);
}
void settings_get(settings_t *out) {
    ensure();
    portENTER_CRITICAL(&mux); *out=cur; portEXIT_CRITICAL(&mux);
}
static bool save(const settings_t *s) {
    nvs_handle_t h; if(nvs_open("viewer",NVS_READWRITE,&h)!=ESP_OK) return false;
    bool ok=nvs_set_str(h,"ssid",s->ssid)==ESP_OK&&nvs_set_str(h,"pass",s->pass)==ESP_OK&&
            nvs_set_str(h,"host",s->host)==ESP_OK&&nvs_set_str(h,"token",s->token)==ESP_OK&&
            nvs_set_u16(h,"port",s->port)==ESP_OK&&nvs_commit(h)==ESP_OK;
    nvs_close(h);
    if(!ok) ESP_LOGW("settings","saving to NVS failed");
    return ok;
}
bool settings_set_wifi(const char *ssid,const char *pass) {
    if(!ssid||!*ssid||strlen(ssid)>32||(pass&&strlen(pass)>64)) return false;
    settings_t s; settings_get(&s);
    snprintf(s.ssid,sizeof s.ssid,"%s",ssid); snprintf(s.pass,sizeof s.pass,"%s",pass?pass:"");
    portENTER_CRITICAL(&mux); cur=s; portEXIT_CRITICAL(&mux);
    return save(&s);
}
bool settings_valid_host(const char *host) {
    size_t n=host?strlen(host):0;
    if(!n||n>=sizeof((settings_t *)0)->host) return false;
    for(size_t i=0;i<n;i++) if(!isalnum((unsigned char)host[i])&&host[i]!='.'&&host[i]!='-') return false;
    return true;
}
bool settings_valid_token(const char *token) {
    size_t n=token?strlen(token):0;
    if(!n||n>=sizeof((settings_t *)0)->token) return false;
    for(size_t i=0;i<n;i++) if(!isgraph((unsigned char)token[i])) return false;
    return true;
}
bool settings_set_gateway(const char *host,uint16_t port,const char *token) {
    if(!settings_valid_host(host)||!port) return false;
    if(token&&*token&&!settings_valid_token(token)) return false;
    settings_t s; settings_get(&s);
    snprintf(s.host,sizeof s.host,"%s",host); s.port=port;
    if(token&&*token) snprintf(s.token,sizeof s.token,"%s",token);
    portENTER_CRITICAL(&mux); cur=s; portEXIT_CRITICAL(&mux);
    return save(&s);
}
void settings_get_cams(char *out,size_t cap) {
    if(!cap) return;
    portENTER_CRITICAL(&mux);
    size_t n=strnlen(cams,sizeof cams); if(n>=cap) n=cap-1;
    memcpy(out,cams,n); out[n]=0;
    portEXIT_CRITICAL(&mux);
}
bool settings_set_cams(const char *csv) {
    if(!csv||strlen(csv)>=sizeof cams) return false;
    char c[544]; memset(c,0,sizeof c); memcpy(c,csv,strlen(csv));
    portENTER_CRITICAL(&mux); memcpy(cams,c,sizeof c); portEXIT_CRITICAL(&mux);
    nvs_handle_t h; if(nvs_open("viewer",NVS_READWRITE,&h)!=ESP_OK) return false;
    bool ok=nvs_set_str(h,"cams",c)==ESP_OK&&nvs_commit(h)==ESP_OK;
    nvs_close(h); return ok;
}
