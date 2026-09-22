/* Included ahead of the Glide sources when they are cross-built with MinGW.
 * The trace harness registers SIGQUIT, which win32 headers do not define. */
#ifndef SIGHUP
#define SIGHUP 1
#endif
#ifndef SIGQUIT
#define SIGQUIT 3
#endif
