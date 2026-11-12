#include "font.h"
#include <ctype.h>
// Original minimal 3x5 glyphs, packed as five three-bit rows.
static const char glyph[][16]={
  "111101101101111","010110010010111","110001111100111","110001011001110","101101111001001",
  "111100110001110","011100111101111","111001010010010","111101111101111","111101111001110",
  "010101111101101","110101110101110","011100100100011","110101101101110","111100110100111",
  "111100110100100","011100101101011","101101111101101","111010010010111","001001001101010",
  "101101110101101","100100100100111","101111111101101","101111111111101","010101101101010",
  "110101110100100","010101101111011","110101110101101","011100010001110","111010010010010",
  "101101101101111","101101101101010","101101111111101","101101010101101","101101010010010","111001010100111"
};
static uint16_t bits(char c) {
    c=toupper((unsigned char)c);
    int index=c>='0'&&c<='9'?c-'0':c>='A'&&c<='Z'?10+c-'A':-1;
    if(index>=0) {uint16_t b=0;for(int i=0;i<15;i++) b=(b<<1)|(glyph[index][i]=='1');return b;}
    if(c==' ') return 0;
    if(c=='-') return 448;
    if(c=='.') return 2;
    if(c==':') return 1040;
    if(c=='/') return 4680;
    static const struct {char c; const char *rows;} sym[]={
        {'_',"000000000000111"},{'@',"111101111100111"},{'!',"010010010000010"},{'#',"101111101111101"},
        {'$',"011110010011110"},{'%',"101001010100101"},{'^',"010101000000000"},{'&',"010101010101011"},
        {'*',"000101010101000"},{'(',"001010010010001"},{')',"100010010010100"},{'=',"000111000111000"},
        {'+',"000010111010000"},{'[',"011010010010011"},{']',"110010010010110"},{'{',"011010110010011"},
        {'}',"110010011010110"},{';',"000010000010100"},{'\'',"010010000000000"},{'"',"101101000000000"},
        {'\\',"100100010001001"},{'|',"010010010010010"},{'?',"110001010000010"},{'<',"001010100010001"},
        {'>',"100010001010100"},{',',"000000000010100"},{'~',"000011110000000"},{'`',"100010000000000"},
    };
    for(unsigned i=0;i<sizeof sym/sizeof sym[0];i++) if(sym[i].c==c) {
        uint16_t b=0;for(int k=0;k<15;k++) b=(b<<1)|(sym[i].rows[k]=='1');return b;
    }
    return 0;
}
static bool portrait;
void overlay_set_portrait(bool value) {portrait=value;}
void overlay_rect(uint16_t *p,int x,int y,int w,int h,uint16_t color) {
    // Landscape: native pixel (logical y, 479-logical x). Portrait: logical == native (320x480).
    // Store big-endian RGB565 for LCD.
    color=(color>>8)|(color<<8);
    int lw=portrait?320:480,lh=portrait?480:320;
    for(int yy=y;yy<y+h&&yy<lh;yy++) for(int xx=x;xx<x+w&&xx<lw;xx++)
        if(xx>=0&&yy>=0) p[portrait?yy*320+xx:(479-xx)*320+yy]=color;
}
void overlay_text_cased(uint16_t *p,int x,int y,const char *s,uint16_t upper,uint16_t lower) {
    for(;*s&&x<(portrait?310:470);s++,x+=8) {
        uint16_t b=bits(*s),c=islower((unsigned char)*s)?lower:upper;
        for(int row=0;row<5;row++) for(int col=0;col<3;col++)
            if(b&(1<<(14-row*3-col))) overlay_rect(p,x+col*2,y+row*2,2,2,c);
    }
}
void overlay_text(uint16_t *p,int x,int y,const char *s,uint16_t c) {overlay_text_cased(p,x,y,s,c,c);}
