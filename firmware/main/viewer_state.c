#include "viewer_state.h"
#include <stdlib.h>
bool viewer_switch(viewer_state_t *s,int d) {
    if(!s->count || s->count==1) return false;
    s->selected=(s->selected+s->count+(d>0?1:-1))%s->count; s->generation++; return true;
}
bool viewer_accept(const viewer_state_t *s,uint32_t g) {return s->count && !s->off && s->generation==g;}
int viewer_gesture(int dx,int dy,unsigned ms) {
    return ms<=800 && abs(dx)>=55 && abs(dx)>2*abs(dy) ? (dx<0?1:-1):0;
}
