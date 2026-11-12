#include "web.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include "freertos/FreeRTOS.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "cJSON.h"
#include "settings.h"

#define RING 6144
static char ring[RING];
static uint32_t total;
static portMUX_TYPE ring_mux=portMUX_INITIALIZER_UNLOCKED;
static vprintf_like_t previous;
static web_hooks_t hooks;

static int log_vprintf(const char *fmt,va_list ap) {
    char line[200]; va_list copy; va_copy(copy,ap);
    int n=vsnprintf(line,sizeof line,fmt,copy); va_end(copy);
    if(n>0) {
        if(n>=(int)sizeof line) {n=sizeof line-1;line[n-1]='\n';}
        portENTER_CRITICAL(&ring_mux);
        for(int i=0;i<n;i++) ring[(total+i)%RING]=line[i];
        total+=n;
        portEXIT_CRITICAL(&ring_mux);
    }
    return previous?previous(fmt,ap):vprintf(fmt,ap);
}
void web_log_init(void) {previous=esp_log_set_vprintf(log_vprintf);}
static size_t log_read(uint32_t since,char *out,uint32_t *next) {
    portENTER_CRITICAL(&ring_mux);
    uint32_t t=total,start=t>RING?t-RING:0;
    if(since<start||since>t) since=start;
    size_t n=t-since;
    for(size_t i=0;i<n;i++) out[i]=ring[(since+i)%RING];
    portEXIT_CRITICAL(&ring_mux);
    *next=t; return n;
}

static const char PAGE[]=
"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
"<title>Protect Viewer</title><style>"
"body{margin:0;background:#1b1e24;color:#d8dee9;font:14px system-ui,sans-serif}"
"header{background:#2b303b;padding:10px 16px;font-size:18px;font-weight:600}"
"main{padding:16px;max-width:900px;margin:auto}"
"form{display:grid;grid-template-columns:90px 1fr;gap:8px;align-items:center;background:#242933;padding:14px;border-radius:6px}"
"input{background:#1b1e24;color:#d8dee9;border:1px solid #444c5c;border-radius:4px;padding:7px;font:inherit}"
"input[type=checkbox]{width:18px;height:18px}"
"button{background:#3b82f6;color:#fff;border:0;border-radius:4px;padding:8px 14px;font:inherit;cursor:pointer}"
"button.g{background:#444c5c}.row{grid-column:1/3;display:flex;gap:10px;align-items:center}"
"h2{font-size:15px;margin:22px 0 8px;display:flex;gap:8px;align-items:center}h2 span{flex:1}"
".cam{display:flex;gap:8px;align-items:center;background:#242933;padding:6px 10px;margin-bottom:4px;border-radius:4px}"
".cam span{flex:1}.cam.off span{opacity:.45}.cam button{padding:3px 10px}"
"#log{background:#0d0f13;border-radius:6px;padding:8px;height:55vh;overflow:auto;font:12px ui-monospace,Consolas,monospace;white-space:pre-wrap}"
"#log div{line-height:1.35}.c31{color:#f87171}.c33{color:#facc15}.c32{color:#4ade80}.c36{color:#67e8f9}#msg,#cm{color:#facc15}"
"</style></head><body><header>Protect Viewer</header><main>"
"<form id='f'><label>Wi-Fi</label><span id='wifi'>-</span>"
"<label>Encoder IP</label><input id='host' placeholder='192.168.1.50' autocomplete='off'>"
"<label>Port</label><input id='port' type='number' min='1' max='65535'>"
"<label>Key</label><input id='token' type='password' placeholder='' autocomplete='off'>"
"<div class='row'><button>Save</button><span id='msg'></span></div></form>"
"<h2><span>Cameras</span><button class='g' id='cr'>Reload</button></h2>"
"<div id='cams'></div><div style='margin-top:8px;display:flex;gap:10px;align-items:center'><button id='cs'>Save cameras</button><span id='cm'></span></div>"
"<h2><span>Logs</span><button class='g' id='pause'>Pause</button><button class='g' id='clear'>Clear</button></h2>"
"<div id='log'></div></main><script>"
"var $=function(i){return document.getElementById(i)},nxt=0,carry='',paused=false,box=$('log');"
"function line(l){var m=/\\x1b\\[[01];(\\d+)m/.exec(l);l=l.replace(/\\x1b\\[[0-9;]*m/g,'');if(!l)return;"
"var d=document.createElement('div');d.textContent=l;if(m)d.className='c'+m[1];box.appendChild(d);"
"while(box.childNodes.length>1500)box.removeChild(box.firstChild)}"
"async function poll(){try{var r=await fetch('/log?since='+nxt),n=+r.headers.get('X-Next'),t=await r.text();"
"if(n<nxt){box.textContent='';carry=''}nxt=n;"
"if(t&&!paused){var near=box.scrollHeight-box.scrollTop-box.clientHeight<40,p=(carry+t).split('\\n');carry=p.pop();p.forEach(line);if(near)box.scrollTop=box.scrollHeight}"
"else if(t)carry+=t}catch(e){}setTimeout(poll,700)}"
"$('pause').onclick=function(){paused=!paused;this.textContent=paused?'Resume':'Pause'};"
"$('clear').onclick=function(){box.textContent=''};"
"fetch('/api/config').then(function(r){return r.json()}).then(function(c){$('host').value=c.host;$('port').value=c.port;"
"$('token').placeholder=c.token_set?'unchanged (set)':'required';$('wifi').textContent=c.ssid+(c.ip?'  ('+c.ip+')':'')});"
"$('f').onsubmit=async function(e){e.preventDefault();$('msg').textContent='Saving...';"
"try{var r=await fetch('/api/config',{method:'POST',body:JSON.stringify({host:$('host').value.trim(),port:+$('port').value,token:$('token').value})});"
"$('msg').textContent=r.ok?'Saved - reconnecting':'Rejected: '+await r.text();if(r.ok){$('token').value='';setTimeout(loadCams,3000)}}catch(x){$('msg').textContent='Failed'}};"
"var cams=[];function draw(){var h=$('cams');h.textContent='';if(!cams.length){h.textContent='No cameras yet - they load once the encoder is reachable.';return}"
"cams.forEach(function(c,i){var d=document.createElement('div');d.className='cam'+(c.enabled?'':' off');"
"var k=document.createElement('input');k.type='checkbox';k.checked=c.enabled;k.onchange=function(){c.enabled=k.checked;draw()};"
"var s=document.createElement('span');s.textContent=c.name+'  ('+c.id+')';d.append(k,s);"
"[['\\u25b2',-1],['\\u25bc',1]].forEach(function(a){var b=document.createElement('button');b.type='button';b.className='g';b.textContent=a[0];"
"b.onclick=function(){var j=i+a[1];if(j<0||j>=cams.length)return;var t=cams[i];cams[i]=cams[j];cams[j]=t;draw()};d.append(b)});h.append(d)})}"
"function loadCams(){fetch('/api/cameras').then(function(r){return r.json()}).then(function(c){cams=c.cameras;draw()})}"
"$('cr').onclick=loadCams;"
"$('cs').onclick=async function(){var ids=cams.filter(function(c){return c.enabled}).map(function(c){return c.id});"
"if(!ids.length){$('cm').textContent='Select at least one camera';return}"
"var r=await fetch('/api/cameras',{method:'POST',body:JSON.stringify({ids:ids})});$('cm').textContent=r.ok?'Saved':'Rejected: '+await r.text();if(r.ok)loadCams()};"
"loadCams();poll();</script></body></html>";

static esp_err_t h_root(httpd_req_t *r) {
    httpd_resp_set_type(r,"text/html");
    return httpd_resp_send(r,PAGE,HTTPD_RESP_USE_STRLEN);
}
static esp_err_t h_cfg_get(httpd_req_t *r) {
    settings_t s; settings_get(&s);
    cJSON *o=cJSON_CreateObject();
    cJSON_AddStringToObject(o,"ssid",s.ssid);cJSON_AddStringToObject(o,"host",s.host);
    cJSON_AddNumberToObject(o,"port",s.port);cJSON_AddBoolToObject(o,"token_set",s.token[0]!=0);
    esp_netif_ip_info_t info={0}; esp_netif_t *nif=esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
    if(nif&&esp_netif_get_ip_info(nif,&info)==ESP_OK&&info.ip.addr) {
        char ip[16]; snprintf(ip,sizeof ip,IPSTR,IP2STR(&info.ip)); cJSON_AddStringToObject(o,"ip",ip);
    }
    char *j=cJSON_PrintUnformatted(o);
    httpd_resp_set_type(r,"application/json");
    esp_err_t e=j?httpd_resp_send(r,j,HTTPD_RESP_USE_STRLEN):httpd_resp_send_500(r);
    free(j);cJSON_Delete(o);return e;
}
static esp_err_t read_body(httpd_req_t *r,char *buf,size_t cap) {
    size_t len=r->content_len;
    if(!len||len>=cap) return ESP_ERR_INVALID_SIZE;
    for(size_t got=0;got<len;) {
        int n=httpd_req_recv(r,buf+got,len-got);
        if(n<=0) return ESP_FAIL;
        got+=n;
    }
    buf[len]=0; return ESP_OK;
}
static esp_err_t h_cfg_post(httpd_req_t *r) {
    char buf[512];
    esp_err_t rb=read_body(r,buf,sizeof buf);
    if(rb==ESP_ERR_INVALID_SIZE) return httpd_resp_send_err(r,HTTPD_400_BAD_REQUEST,"bad length");
    if(rb!=ESP_OK) return rb;
    cJSON *o=cJSON_Parse(buf);
    cJSON *host=cJSON_GetObjectItem(o,"host"),*port=cJSON_GetObjectItem(o,"port"),*token=cJSON_GetObjectItem(o,"token");
    const char *err=NULL;
    if(!cJSON_IsString(host)||!settings_valid_host(host->valuestring)) err="invalid IP/host";
    else if(!cJSON_IsNumber(port)||port->valueint<1||port->valueint>65535) err="invalid port";
    else if(cJSON_IsString(token)&&token->valuestring[0]&&!settings_valid_token(token->valuestring)) err="invalid key";
    else {
        settings_t s; settings_get(&s);
        bool has_token=(cJSON_IsString(token)&&token->valuestring[0])||s.token[0];
        if(!has_token) err="key required";
        else if(!settings_set_gateway(host->valuestring,port->valueint,cJSON_IsString(token)?token->valuestring:NULL)) err="could not save";
    }
    cJSON_Delete(o);
    if(err) return httpd_resp_send_err(r,HTTPD_400_BAD_REQUEST,err);
    ESP_LOGI("web","encoder settings updated");
    if(hooks.gateway_changed) hooks.gateway_changed();
    httpd_resp_set_type(r,"application/json");
    return httpd_resp_sendstr(r,"{\"ok\":true}");
}
static esp_err_t h_cams_get(httpd_req_t *r) {
    web_cam_t list[16]; int n=hooks.list_cameras?hooks.list_cameras(list,16):0;
    cJSON *o=cJSON_CreateObject(),*arr=cJSON_AddArrayToObject(o,"cameras");
    for(int i=0;i<n;i++) {
        cJSON *c=cJSON_CreateObject();
        cJSON_AddStringToObject(c,"id",list[i].id);cJSON_AddStringToObject(c,"name",list[i].name);
        cJSON_AddBoolToObject(c,"enabled",list[i].enabled);cJSON_AddItemToArray(arr,c);
    }
    char *j=cJSON_PrintUnformatted(o);
    httpd_resp_set_type(r,"application/json");
    esp_err_t e=j?httpd_resp_send(r,j,HTTPD_RESP_USE_STRLEN):httpd_resp_send_500(r);
    free(j);cJSON_Delete(o);return e;
}
static esp_err_t h_cams_post(httpd_req_t *r) {
    char buf[1024];
    esp_err_t rb=read_body(r,buf,sizeof buf);
    if(rb==ESP_ERR_INVALID_SIZE) return httpd_resp_send_err(r,HTTPD_400_BAD_REQUEST,"bad length");
    if(rb!=ESP_OK) return rb;
    cJSON *o=cJSON_Parse(buf),*ids=cJSON_GetObjectItem(o,"ids");
    static char sel[16][33]; int n=cJSON_IsArray(ids)?cJSON_GetArraySize(ids):0; const char *err=NULL;
    if(n<1||n>16) err="select 1-16 cameras";
    for(int i=0;!err&&i<n;i++) {
        cJSON *s=cJSON_GetArrayItem(ids,i);
        if(!cJSON_IsString(s)||!s->valuestring[0]||strlen(s->valuestring)>32||
           strspn(s->valuestring,"abcdefghijklmnopqrstuvwxyz0123456789_-")!=strlen(s->valuestring)) err="invalid camera id";
        else memcpy(sel[i],s->valuestring,strlen(s->valuestring)+1);
    }
    cJSON_Delete(o);
    if(err) return httpd_resp_send_err(r,HTTPD_400_BAD_REQUEST,err);
    ESP_LOGI("web","camera selection updated (%d shown)",n);
    if(hooks.apply_cameras) hooks.apply_cameras((const char (*)[33])sel,n);
    httpd_resp_set_type(r,"application/json");
    return httpd_resp_sendstr(r,"{\"ok\":true}");
}
static esp_err_t h_log(httpd_req_t *r) {
    uint32_t since=0,next=0; char q[32],v[16];
    if(httpd_req_get_url_query_str(r,q,sizeof q)==ESP_OK&&httpd_query_key_value(q,"since",v,sizeof v)==ESP_OK) since=strtoul(v,NULL,10);
    char *buf=malloc(RING); if(!buf) return httpd_resp_send_500(r);
    size_t n=log_read(since,buf,&next);
    char hdr[12]; snprintf(hdr,sizeof hdr,"%lu",(unsigned long)next);
    httpd_resp_set_type(r,"text/plain; charset=utf-8");
    httpd_resp_set_hdr(r,"X-Next",hdr);
    httpd_resp_set_hdr(r,"Cache-Control","no-store");
    esp_err_t e=httpd_resp_send(r,buf,n);
    free(buf);return e;
}
void web_start(const web_hooks_t *h) {
    hooks=*h;
    httpd_config_t cfg=HTTPD_DEFAULT_CONFIG();
    cfg.stack_size=8192;cfg.task_priority=2;cfg.max_uri_handlers=8;cfg.lru_purge_enable=true;
    httpd_handle_t srv;
    if(httpd_start(&srv,&cfg)!=ESP_OK) {ESP_LOGW("web","http server failed to start");return;}
    static const httpd_uri_t uris[]={
        {.uri="/",.method=HTTP_GET,.handler=h_root},
        {.uri="/api/config",.method=HTTP_GET,.handler=h_cfg_get},
        {.uri="/api/config",.method=HTTP_POST,.handler=h_cfg_post},
        {.uri="/api/cameras",.method=HTTP_GET,.handler=h_cams_get},
        {.uri="/api/cameras",.method=HTTP_POST,.handler=h_cams_post},
        {.uri="/log",.method=HTTP_GET,.handler=h_log},
    };
    for(unsigned i=0;i<sizeof uris/sizeof uris[0];i++) httpd_register_uri_handler(srv,&uris[i]);
    ESP_LOGI("web","http server started on port %d",cfg.server_port);
}
