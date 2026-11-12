#include "multipart.h"
#include <string.h>
#include <stdlib.h>
#include <ctype.h>
#ifdef _MSC_VER
#define strcasecmp _stricmp
#define strncasecmp _strnicmp
#else
#include <strings.h>
#endif

bool mp_init(multipart_t *p, const char *ct, uint8_t *buf, mp_frame_fn cb, void *ctx) {
    memset(p, 0, sizeof(*p));
    if (!ct || strncasecmp(ct,"multipart/x-mixed-replace",24)) return false;
    const char *b = strstr(ct,"boundary=");
    if (!b) return false;
    b += 9;
    bool quoted = *b == '"'; if (quoted) b++;
    size_t n = strcspn(b, quoted ? "\"\r\n" : "; \r\n");
    if (!n || n > 68) return false;
    p->boundary[0]='-'; p->boundary[1]='-'; memcpy(p->boundary+2,b,n);
    p->buffer=buf; p->emit=cb; p->ctx=ctx;
    return true;
}

static void line(multipart_t *p) {
    size_t n=p->line_len;
    if(n && p->line[n-1]=='\r') p->line[--n]=0;
    if (!strcmp(p->line,p->boundary)) {
        p->state=1; p->has_length=false; p->jpeg=false; p->length=0;
    } else if(p->state==1) {
        if (!n) {
            if(p->has_length && p->jpeg && p->length>=4 && p->length<=JPEG_LIMIT) {
                p->state=2; p->used=0;
            } else {p->rejected++; p->state=0;}
        } else if(!strncasecmp(p->line,"Content-Length:",15)) {
            const char *s=p->line+15; while(*s==' ') s++;
            size_t v=0; bool ok=isdigit((unsigned char)*s) && !p->has_length;
            while(isdigit((unsigned char)*s)) {v=v*10+(*s++-'0'); if(v>JPEG_LIMIT) {ok=false; break;}}
            while(*s==' ' || *s=='\t') s++;
            p->has_length=ok && !*s; p->length=v;
            if(!p->has_length) {p->rejected++; p->state=0;}
        } else if(!strncasecmp(p->line,"Content-Type:",13)) {
            const char *s=p->line+13; while(*s==' ') s++;
            p->jpeg=!strcasecmp(s,"image/jpeg");
        }
    }
    p->line_len=0;
}

void mp_feed(multipart_t *p,const uint8_t *data,size_t n) {
    while(n) {
        if(p->state==2) {
            size_t take=p->length-p->used; if(take>n) take=n;
            memcpy(p->buffer+p->used,data,take); p->used+=take; data+=take; n-=take;
            if(p->used==p->length) {
                if(p->buffer[0]==0xff && p->buffer[1]==0xd8 && p->buffer[p->length-2]==0xff && p->buffer[p->length-1]==0xd9)
                    p->emit(p->ctx,p->buffer,p->length);
                else p->rejected++;
                p->state=0;
            }
        } else {
            char c=*data++; n--;
            if(p->state==3) {if(c=='\n') p->state=0; continue;}
            if(c=='\n') {p->line[p->line_len]=0; line(p);}
            else if(p->line_len<sizeof(p->line)-1) p->line[p->line_len++]=c;
            else {p->state=3; p->line_len=0;}
        }
    }
}

bool jpeg_profile_supported(const uint8_t *p,size_t n) {
    if(n<4||p[0]!=255||p[1]!=216) return false;
    size_t i=2;
    while(i+4<=n) {
        if(p[i++]!=255) return false;
        while(i<n&&p[i]==255) i++;
        if(i+3>n) return false;
        unsigned marker=p[i++],length=((unsigned)p[i]<<8)|p[i+1];
        if(length<2||length>n-i) return false;
        if(marker==0xc0) {
            // Baseline, 8-bit, exact native canvas, YUV 4:2:0 only.
            return length==17 && p[i+2]==8 && p[i+3]==1 && p[i+4]==224 &&
                p[i+5]==1 && p[i+6]==64 && p[i+7]==3 &&
                p[i+9]==0x22 && p[i+12]==0x11 && p[i+15]==0x11;
        }
        if(marker==0xc2||marker==0xda||marker==0xd9) return false;
        i+=length;
    }
    return false;
}
