#include "quakedef.h"

/* Host and Tang SW builds: no rv32emu sound syscalls from quake-embedded. */

cvar_t bgmvolume = {"bgmvolume", "1", true};
cvar_t volume = {"volume", "0.7", true};

static sfx_t dummy_sfx;

void S_Init(void) {
  Cvar_RegisterVariable(&volume);
  Cvar_RegisterVariable(&bgmvolume);
}

void S_Startup(void) {}
void S_Shutdown(void) {}
void S_AmbientOff(void) {}
void S_AmbientOn(void) {}
void S_TouchSound(char *sample) { (void)sample; }
void S_ClearBuffer(void) {}
void S_StaticSound(sfx_t *sfx, vec3_t origin, float vol, float attenuation) {
  (void)sfx;
  (void)origin;
  (void)vol;
  (void)attenuation;
}
void S_StartSound(int entnum, int entchannel, sfx_t *sfx, vec3_t origin,
                  float fvol, float attenuation) {
  (void)entnum;
  (void)entchannel;
  (void)sfx;
  (void)origin;
  (void)fvol;
  (void)attenuation;
}
void S_StopSound(int entnum, int entchannel) {
  (void)entnum;
  (void)entchannel;
}
sfx_t *S_PrecacheSound(char *sample) {
  (void)sample;
  return &dummy_sfx;
}
void S_ClearPrecache(void) {}
void S_Update(vec3_t origin, vec3_t v_forward, vec3_t v_right, vec3_t v_up) {
  (void)origin;
  (void)v_forward;
  (void)v_right;
  (void)v_up;
}
void S_StopAllSounds(qboolean clear) { (void)clear; }
void S_BeginPrecaching(void) {}
void S_EndPrecaching(void) {}
void S_ExtraUpdate(void) {}
void S_LocalSound(char *s) { (void)s; }
sfxcache_t *S_LoadSound(sfx_t *s) {
  (void)s;
  return NULL;
}
