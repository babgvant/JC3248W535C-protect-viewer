#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "multipart.h"
#include "viewer_state.h"
static unsigned frames;
static void emit(void *ctx,const uint8_t *p,size_t n) {assert(n==8);assert(p[0]==255);frames++;}
static void parse(const char *prefix,size_t chunk) {
    uint8_t buffer[JPEG_LIMIT]; multipart_t p;
    assert(mp_init(&p,"multipart/x-mixed-replace; boundary=frame",buffer,emit,NULL));
    const char *part="--frame\r\nContent-Type: image/jpeg\r\nContent-Length: 8\r\n\r\n\xff\xd8test\xff\xd9\r\n";
    if(prefix) mp_feed(&p,(const uint8_t *)prefix,strlen(prefix));
    size_t n=strlen(part); unsigned before=frames;
    for(size_t i=0;i<n;i+=chunk) mp_feed(&p,(const uint8_t *)part+i,n-i<chunk?n-i:chunk);
    assert(frames==before+1);
}
int main(void) {
    for(size_t chunk=1;chunk<150;chunk++) parse(NULL,chunk);
    parse("--frame\r\nContent-Length: 99999999999999999999999\r\n\r\nbad\r\n",1);
    parse("--frame\r\nContent-Length: -1\r\n\r\n",1);
    parse("--frame\r\nContent-Length: 8\r\nContent-Type: image/jpeg\r\n\r\nBADBYTES\r\n",1);
    char longline[2000];memset(longline,'x',sizeof longline);longline[1998]='\n';longline[1999]=0;parse(longline,1);
    viewer_state_t s={.count=3,.generation=1};
    for(int i=0;i<10000;i++) {uint32_t old=s.generation;assert(viewer_switch(&s,1));assert(!viewer_accept(&s,old));assert(viewer_accept(&s,s.generation));}
    s.selected=0;viewer_switch(&s,-1);assert(s.selected==2);
    s.off=true;assert(!viewer_accept(&s,s.generation));
    s.count=1;assert(!viewer_switch(&s,1));s.count=0;assert(!viewer_switch(&s,1));
    assert(viewer_gesture(-60,10,200)==1);assert(viewer_gesture(60,10,200)==-1);
    assert(!viewer_gesture(5,2,100));assert(!viewer_gesture(60,40,200));assert(!viewer_gesture(80,0,900));
    uint8_t card[JPEG_LIMIT];FILE *file=fopen("firmware/main/testcard.jpg","rb");assert(file);
    size_t size=fread(card,1,sizeof card,file);fclose(file);
    assert(jpeg_profile_supported(card,size));
    size_t sof=0;for(size_t i=0;i+1<size;i++) if(card[i]==255&&card[i+1]==192) {sof=i;break;}
    assert(sof);card[sof+1]=194;assert(!jpeg_profile_supported(card,size));card[sof+1]=192;
    card[sof+11]=0x11;assert(!jpeg_profile_supported(card,size));
    puts("firmware parser and switching tests passed");
}
