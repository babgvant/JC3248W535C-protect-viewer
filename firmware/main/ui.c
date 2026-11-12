#include "ui.h"
#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "esp_log.h"
#include "font.h"
#include "settings.h"

#define W 480
#define H 320
#define BLACK 0x0000
#define WHITE 0xffff
#define BAR 0x2104
#define DARK 0x18e3
#define KEY 0x39e7
#define GREEN 0x0480
#define RED 0xa000
#define CYAN 0x07ff
#define YELLOW 0xffe0
#define MAX_APS 20
#define ROWS 5

typedef enum {UI_OFF,UI_MAIN,UI_SCAN,UI_PASS} ui_mode_t;
static SemaphoreHandle_t mu;
static ui_mode_t mode;
static uint32_t version=1;
static void (*wifi_changed)(void);
static wifi_ap_record_t aps[MAX_APS];
static unsigned ap_count,scroll;
static bool scanning,scan_failed;
static char pick[33],pass[65];
static bool shift,symbols;

static const char *pages[3][4]={
    {"1234567890","qwertyuiop","asdfghjkl-","zxcvbnm._@"},
    {"1234567890","QWERTYUIOP","ASDFGHJKL-","ZXCVBNM._@"},
    {"!@#$%^&*()","-_=+[]{};:","'\"\\|/?<>,.","~`"},
};
#define KB_Y 76
#define KEY_W 48
#define KEY_H 44

void ui_init(void (*cb)(void)) {mu=xSemaphoreCreateMutex();wifi_changed=cb;}
bool ui_active(void) {return mode!=UI_OFF;}
uint32_t ui_version(void) {return version;}
void ui_open(void) {xSemaphoreTake(mu,portMAX_DELAY);mode=UI_MAIN;version++;xSemaphoreGive(mu);}

bool ui_gear_hit(int x,int y,int lw) {return x>=lw-40&&y<36;}
void ui_draw_gear(uint16_t *c,int lw) {
    const int cx=lw-20,cy=16;
    for(int dy=-9;dy<=9;dy++) for(int dx=-9;dx<=9;dx++) {
        int r=dx*dx+dy*dy;
        if(r<=49&&r>=16) overlay_rect(c,cx+dx,cy+dy,1,1,WHITE);
    }
    static const int tooth[8][2]={{0,-10},{0,10},{-10,0},{10,0},{-7,-7},{7,-7},{-7,7},{7,7}};
    for(int i=0;i<8;i++) overlay_rect(c,cx+tooth[i][0]-2,cy+tooth[i][1]-2,4,4,WHITE);
}

static void button(uint16_t *c,int x,int y,int w,int h,const char *label,uint16_t fill) {
    overlay_rect(c,x,y,w,h,WHITE);overlay_rect(c,x+1,y+1,w-2,h-2,fill);
    int tw=(int)strlen(label)*8-2;
    overlay_text_cased(c,x+(w-tw)/2,y+(h-10)/2,label,WHITE,CYAN);
}
static void title(uint16_t *c,const char *left,const char *right) {
    overlay_rect(c,0,0,W,32,BAR);
    overlay_text(c,8,11,left,WHITE);
    if(right) overlay_text(c,W-8-(int)strlen(right)*8,11,right,YELLOW);
}
static bool in(int x,int y,int bx,int by,int bw,int bh) {return x>=bx&&x<bx+bw&&y>=by&&y<by+bh;}

static void scan_task(void *arg) {
    static wifi_ap_record_t tmp[MAX_APS];
    wifi_scan_config_t sc={.show_hidden=false};
    uint16_t n=MAX_APS; bool ok=esp_wifi_scan_start(&sc,true)==ESP_OK&&esp_wifi_scan_get_ap_records(&n,tmp)==ESP_OK;
    xSemaphoreTake(mu,portMAX_DELAY);
    ap_count=0;
    for(unsigned i=0;ok&&i<n;i++) {
        if(!tmp[i].ssid[0]) continue;
        bool dup=false;
        for(unsigned k=0;k<ap_count;k++) if(!strcmp((char *)aps[k].ssid,(char *)tmp[i].ssid)) dup=true;
        if(!dup) aps[ap_count++]=tmp[i];
    }
    scan_failed=!ok;scanning=false;version++;
    xSemaphoreGive(mu);
    vTaskDelete(NULL);
}
static void start_scan(void) { // mu held
    mode=UI_SCAN;version++;
    if(scanning) return;
    scanning=true;scan_failed=false;ap_count=0;scroll=0;
    if(xTaskCreate(scan_task,"scan",4096,NULL,3,NULL)!=pdPASS) {scanning=false;scan_failed=true;}
}

static void draw_main(uint16_t *c) {
    title(c,"SETTINGS",NULL);
    settings_t s;settings_get(&s);
    char line[96];
    wifi_ap_record_t ap;bool assoc=esp_wifi_sta_get_ap_info(&ap)==ESP_OK;
    esp_netif_ip_info_t ip={0};
    esp_netif_t *netif=esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
    bool have_ip=netif&&esp_netif_get_ip_info(netif,&ip)==ESP_OK&&ip.ip.addr;
    snprintf(line,sizeof line,"WIFI: %.40s",s.ssid);overlay_text_cased(c,12,46,line,WHITE,CYAN);
    if(assoc) snprintf(line,sizeof line,"STATUS: CONNECTED  RSSI %d",ap.rssi);
    else snprintf(line,sizeof line,"STATUS: NOT CONNECTED");
    overlay_text(c,12,76,line,assoc?0x07e0:YELLOW);
    if(have_ip) snprintf(line,sizeof line,"IP: " IPSTR,IP2STR(&ip.ip)); else snprintf(line,sizeof line,"IP: -");
    overlay_text(c,12,106,line,WHITE);
    if(have_ip) snprintf(line,sizeof line,"WEB: http://" IPSTR "/",IP2STR(&ip.ip)); else snprintf(line,sizeof line,"WEB: -");
    overlay_text_cased(c,12,136,line,WHITE,CYAN);
    snprintf(line,sizeof line,"ENCODER: %.30s:%u",s.host,(unsigned)s.port);overlay_text_cased(c,12,166,line,WHITE,CYAN);
    overlay_text(c,12,196,"SET ENCODER IP/PORT/KEY ON THE WEB PAGE",YELLOW);
    button(c,20,236,200,64,"SCAN WIFI",GREEN);
    button(c,260,236,200,64,"CLOSE",KEY);
}
static void draw_scan(uint16_t *c) {
    title(c,"WIFI NETWORKS","RESCAN");
    settings_t s;settings_get(&s);
    if(scanning) overlay_text(c,150,140,"SCANNING...",WHITE);
    else if(scan_failed) overlay_text(c,100,140,"SCAN FAILED - TAP RESCAN",YELLOW);
    else if(!ap_count) overlay_text(c,150,140,"NONE FOUND",YELLOW);
    for(unsigned i=0;i<ROWS&&scroll+i<ap_count;i++) {
        const wifi_ap_record_t *ap=&aps[scroll+i];
        int y=36+40*i;
        bool current=!strcmp((const char *)ap->ssid,s.ssid);
        overlay_rect(c,0,y,W,38,current?GREEN:DARK);
        char line[48];
        snprintf(line,sizeof line,"%.30s",(const char *)ap->ssid);overlay_text_cased(c,8,y+14,line,WHITE,CYAN);
        snprintf(line,sizeof line,"%d",ap->rssi);overlay_text(c,350,y+14,line,WHITE);
        overlay_text(c,410,y+14,ap->authmode==WIFI_AUTH_OPEN?"OPEN":"LOCK",ap->authmode==WIFI_AUTH_OPEN?YELLOW:WHITE);
    }
    button(c,0,250,150,60,"UP",KEY);
    button(c,165,250,150,60,"DOWN",KEY);
    button(c,330,250,150,60,"BACK",KEY);
}
static void draw_pass(uint16_t *c) {
    char line[64];snprintf(line,sizeof line,"JOIN %.34s",pick);
    overlay_rect(c,0,0,W,32,BAR);overlay_text_cased(c,8,11,line,WHITE,CYAN);
    overlay_text(c,W-8-48,11,"CANCEL",YELLOW);
    overlay_rect(c,4,38,W-8,32,WHITE);overlay_rect(c,5,39,W-10,30,BLACK);
    char shown[66];snprintf(shown,sizeof shown,"%s_",pass);
    // Show the tail when longer than the box.
    size_t n=strlen(shown);const char *p=n>56?shown+(n-56):shown;
    overlay_text_cased(c,10,48,p,WHITE,CYAN);
    int page=symbols?2:shift?1:0;
    for(int r=0;r<4;r++) for(int k=0;pages[page][r][k];k++) {
        char s[2]={pages[page][r][k],0};
        button(c,k*KEY_W+1,KB_Y+r*KEY_H+1,KEY_W-2,KEY_H-2,s,KEY);
    }
    int y=KB_Y+4*KEY_H+1,h=KEY_H-2;
    button(c,1,y,94,h,"SHIFT",shift&&!symbols?GREEN:KEY);
    button(c,97,y,94,h,symbols?"ABC":"123",symbols?GREEN:KEY);
    button(c,193,y,142,h,"SPACE",KEY);
    button(c,337,y,70,h,"DEL",RED);
    button(c,409,y,70,h,"OK",GREEN);
}
void ui_draw(uint16_t *c) {
    xSemaphoreTake(mu,portMAX_DELAY);
    overlay_rect(c,0,0,W,H,BLACK);
    switch(mode) {
    case UI_MAIN: draw_main(c);break;
    case UI_SCAN: draw_scan(c);break;
    case UI_PASS: draw_pass(c);break;
    default: break;
    }
    xSemaphoreGive(mu);
}

static bool tap_scan(int x,int y) { // returns true when Wi-Fi settings changed
    if(in(x,y,0,0,W,32)) {if(x>=400) start_scan();return false;}
    if(y>=250) {
        if(x<150) {scroll=scroll>=ROWS?scroll-ROWS:0;}
        else if(x>=165&&x<315) {if(scroll+ROWS<ap_count) scroll+=ROWS;}
        else if(x>=330) mode=UI_MAIN;
        return false;
    }
    if(y>=36&&y<236) {
        unsigned idx=scroll+(y-36)/40;
        if(idx>=ap_count) return false;
        snprintf(pick,sizeof pick,"%s",(const char *)aps[idx].ssid);
        if(aps[idx].authmode==WIFI_AUTH_OPEN) {mode=UI_MAIN;return settings_set_wifi(pick,"");}
        pass[0]=0;shift=symbols=false;mode=UI_PASS;
    }
    return false;
}
static bool tap_pass(int x,int y) {
    if(y<32) {if(x>=W-60) mode=UI_SCAN;return false;}
    size_t n=strlen(pass);
    if(y>=KB_Y&&y<KB_Y+4*KEY_H) {
        int r=(y-KB_Y)/KEY_H,k=x/KEY_W;
        const char *row=pages[symbols?2:shift?1:0][r];
        if(k<(int)strlen(row)&&n<sizeof pass-1) {pass[n]=row[k];pass[n+1]=0;if(shift&&!symbols) shift=false;}
        return false;
    }
    if(y>=KB_Y+4*KEY_H&&y<KB_Y+5*KEY_H) {
        if(x<96) {symbols=false;shift=!shift;}
        else if(x<192) symbols=!symbols;
        else if(x<336) {if(n<sizeof pass-1) {pass[n]=' ';pass[n+1]=0;}}
        else if(x<408) {if(n) pass[n-1]=0;}
        else if(settings_set_wifi(pick,pass)) {mode=UI_MAIN;return true;}
    }
    return false;
}
void ui_tap(int x,int y) {
    bool changed=false;
    xSemaphoreTake(mu,portMAX_DELAY);
    switch(mode) {
    case UI_MAIN:
        if(in(x,y,20,236,200,64)) start_scan();
        else if(in(x,y,260,236,200,64)) mode=UI_OFF;
        break;
    case UI_SCAN: changed=tap_scan(x,y);break;
    case UI_PASS: changed=tap_pass(x,y);break;
    default: break;
    }
    version++;
    xSemaphoreGive(mu);
    if(changed&&wifi_changed) wifi_changed();
}
