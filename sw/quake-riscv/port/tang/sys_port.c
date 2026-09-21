#include <quakedef.h>
#include <quakembd.h>

#include <stdio.h>
#include <string.h>

#define DEFAULT_MEM_SIZE (8 * 1024 * 1024)
#define DEFAULT_BASEDIR "."

qboolean isDedicated;
cvar_t sys_linerefresh = {"sys_linerefresh", "0"};

#ifdef WINQUAKE_LOGGING_EXTERNAL
#define DEFAULT_LOG_BUFFER_SIZE 1024

void _Sys_Printf(const char *fmt, ...) {
  va_list argptr;
  char text[DEFAULT_LOG_BUFFER_SIZE];
  unsigned char *p;

  va_start(argptr, fmt);
  vsprintf(text, fmt, argptr);
  va_end(argptr);

  if (strlen(text) > DEFAULT_LOG_BUFFER_SIZE) {
    qembd_error("memory overwrite in Sys_Printf");
    return;
  }

  for (p = (unsigned char *)text; *p; p++) {
    *p &= 0x7f;
    if ((*p > 128 || *p < 32) && *p != 10 && *p != 13 && *p != 9)
      fprintf(stdout, "[%02x]", *p);
    else
      putc(*p, stdout);
  }
  fflush(stdout);
}
#endif

void Sys_Error(char *error, ...) {
  va_list argptr;
  char string[1024];

  va_start(argptr, error);
  vsprintf(string, error, argptr);
  va_end(argptr);
  fprintf(stderr, "Error: %s\n", string);
  Host_Shutdown();
  exit(1);
}

void Sys_Quit(void) {
  Host_Shutdown();
  exit(0);
}

double Sys_FloatTime(void) { return qembd_get_us_time() / 1000000.0; }

char *Sys_ConsoleInput(void) { return NULL; }

void Sys_Sleep(void) {}

void Sys_SendKeyEvents(void) {
  key_event_t e;
  while (qembd_dequeue_key_event(&e) == 0)
    Key_Event(e.keycode, e.state == 1);
}

void Sys_HighFPPrecision(void) {}
void Sys_LowFPPrecision(void) {}
void Sys_MakeCodeWriteable(unsigned long startaddr, unsigned long length) {
  (void)startaddr;
  (void)length;
}

int qembd_main(int c, char **v) {
  float time, oldtime, newtime;
  quakeparms_t parms = {0};
  int j;
  int max_frames = 0;
  int frames = 0;

  COM_InitArgv(c, v);
  parms.argc = com_argc;
  parms.argv = com_argv;

  parms.memsize = DEFAULT_MEM_SIZE;
  j = COM_CheckParm("-mem");
  if (j)
    parms.memsize = (int)(Q_atof(com_argv[j + 1]) * 1024 * 1024);
  j = COM_CheckParm("-frames");
  if (j && j + 1 < com_argc)
    max_frames = atoi(com_argv[j + 1]);

  parms.membase = qembd_allocmain(parms.memsize);
  if (!parms.membase) {
    qembd_error("Memory cannot be allocated");
    return -1;
  }

  parms.basedir = DEFAULT_BASEDIR;
  Host_Init(&parms);
  /* quake.rc may be missing on a partial tree; still honor +commands. */
  Cbuf_InsertText("stuffcmds\n");
  qembd_info("QuakEMBD - Based on WinQuake %0.3f", VERSION);
  fflush(stdout);

  oldtime = (float)Sys_FloatTime() - 0.1f;
  while (1) {
    newtime = (float)Sys_FloatTime();
    time = newtime - oldtime;

    if (time > sys_ticrate.value * 2)
      oldtime = newtime;
    else
      oldtime += time;

    Host_Frame(time);
    if (max_frames > 0 && ++frames >= max_frames)
      Sys_Quit();
  }
}
