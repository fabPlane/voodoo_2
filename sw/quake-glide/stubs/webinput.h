/*
 * Remote input for headless GLQuake: when QUAKE_INPUT_UDP=<port> is set, key
 * and mouse events arrive as text datagrams on 127.0.0.1:<port> (sent by
 * sw/pcie/voodoo-view.py from the browser). Without it there is no input.
 *
 *   "k <keycode> <1|0>"         Linux/svgalib keycode, press or release
 *   "m <buttons> <dx> <dy>"     svgalib button bits (4 left, 2 mid, 1 right)
 *
 * Only gl_vidlinux.c includes this, so file-static state is shared by the
 * keyboard and mouse stubs.
 */
#ifndef WEBINPUT_STUB_H
#define WEBINPUT_STUB_H
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>

typedef void (*webinput_key_fn)(int scancode, int state);
typedef void (*webinput_mouse_fn)(int button, int dx, int dy);

static int webinput_fd = -2;          /* -2 not opened yet, -1 disabled */
static webinput_key_fn webinput_key;
static webinput_mouse_fn webinput_mouse;
static int webinput_buttons;

static void webinput_open(void)
{
	const char *p = getenv("QUAKE_INPUT_UDP");
	struct sockaddr_in a;
	if (webinput_fd != -2) return;
	webinput_fd = -1;
	if (!p || !*p) return;
	webinput_fd = socket(AF_INET, SOCK_DGRAM, 0);
	memset(&a, 0, sizeof a);
	a.sin_family = AF_INET;
	a.sin_port = htons(atoi(p));
	a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
	if (webinput_fd < 0 || bind(webinput_fd, (struct sockaddr *)&a, sizeof a) < 0) {
		perror("QUAKE_INPUT_UDP");
		if (webinput_fd >= 0) close(webinput_fd);
		webinput_fd = -1;
		return;
	}
	fcntl(webinput_fd, F_SETFL, O_NONBLOCK);
	fprintf(stderr, "[webinput] listening on udp 127.0.0.1:%s\n", p);
}

/* Handles one pending datagram; returns 0 when none is left. */
static int webinput_poll(void)
{
	char b[64];
	int n, x, y, z;
	webinput_open();
	if (webinput_fd < 0) return 0;
	n = recv(webinput_fd, b, sizeof b - 1, 0);
	if (n <= 0) return 0;
	b[n] = 0;
	if (sscanf(b, "k %d %d", &x, &y) == 2) {
		if (webinput_key && x > 0 && x < 128) webinput_key(x, y ? 1 : 0);
	} else if (sscanf(b, "m %d %d %d", &x, &y, &z) == 3) {
		webinput_buttons = x;
		if (webinput_mouse) webinput_mouse(x, y, z);
	}
	return 1;
}
#endif
