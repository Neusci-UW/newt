/* A stand-in for amcam's API (char strings, as on Linux/macOS), for testing the wrapper. */
#include <string.h>
#include <stdlib.h>
#include <pthread.h>
#include <unistd.h>
typedef struct { char displayname[64]; char id[64]; const void* model; } AmcamDeviceV2;
typedef void (*CB)(unsigned, void*);
typedef struct { int open; unsigned esize; CB cb; void* ctx; int run; pthread_t t; unsigned frame; } Cam;
static Cam cam;
static const int W[3] = {3584, 1792, 896}, Hh[3] = {2748, 1374, 684};
unsigned Amcam_EnumV2(AmcamDeviceV2* a) { strcpy(a[0].displayname, "MU1000"); strcpy(a[0].id, "tp-1-2-1234"); a[0].model = 0; return 1; }
void* Amcam_Open(const char* id) { if (!id || strcmp(id, "tp-1-2-1234")) return 0; cam.open = 1; cam.esize = 0; return &cam; }
void Amcam_Close(void* h) { cam.open = 0; }
static void* loop(void* _) { while (cam.run) { usleep(20000); cam.frame++; cam.cb(0x0004, cam.ctx); } return 0; }
long Amcam_StartPullModeWithCallback(void* h, CB cb, void* ctx) { cam.cb = cb; cam.ctx = ctx; cam.run = 1; pthread_create(&cam.t, 0, loop, 0); return 0; }
long Amcam_Stop(void* h) { if (cam.run) { cam.run = 0; pthread_join(cam.t, 0); } return 0; }
long Amcam_get_ResolutionNumber(void* h) { return 3; }
long Amcam_get_Resolution(void* h, unsigned i, int* w, int* hh) { if (i > 2) return -1; *w = W[i]; *hh = Hh[i]; return 0; }
long Amcam_get_eSize(void* h, unsigned* i) { *i = cam.esize; return 0; }
long Amcam_put_eSize(void* h, unsigned i) { if (i > 2 || cam.run) return -1; cam.esize = i; return 0; }
long Amcam_put_AutoExpoEnable(void* h, int on) { return 0; }
long Amcam_PullImage(void* h, void* data, int bits, unsigned* w, unsigned* hh) {
    if (bits != 24) return -1;
    int ww = W[cam.esize], h2 = Hh[cam.esize], stride = ((ww * 24 + 31) / 32) * 4;
    unsigned char* p = data;
    for (int y = 0; y < h2; y++) for (int x = 0; x < ww; x++) { unsigned char* q = p + y * stride + x * 3; q[0] = (unsigned char)y; q[1] = (unsigned char)x; q[2] = (unsigned char)cam.frame; }
    *w = ww; *hh = h2; return 0;
}
