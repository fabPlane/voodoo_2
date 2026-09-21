#include <ctype.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include <ftdi.h>

static volatile sig_atomic_t running = 1;

static void stop_monitor(int signal_number) {
    (void)signal_number;
    running = 0;
}

static void usage(const char *argv0) {
    fprintf(stderr,
            "usage: %s [--hex] [--interface a|b] [--baud N] [--serial PREFIX]\n"
            "defaults: interface B, requested baud 500000, serial 2025041420\n",
            argv0);
}

int main(int argc, char **argv) {
    int show_hex = 0;
    int baud = 500000;
    enum ftdi_interface interface = INTERFACE_B;
    const char *serial = "2025041420";

    for (int i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--help") || !strcmp(argv[i], "-h")) {
            usage(argv[0]);
            return 0;
        } else if (!strcmp(argv[i], "--hex")) {
            show_hex = 1;
        } else if (!strcmp(argv[i], "--interface") && i + 1 < argc) {
            const char *value = argv[++i];
            if (!strcmp(value, "a")) interface = INTERFACE_A;
            else if (!strcmp(value, "b")) interface = INTERFACE_B;
            else { usage(argv[0]); return 2; }
        } else if (!strcmp(argv[i], "--baud") && i + 1 < argc) {
            baud = atoi(argv[++i]);
        } else if (!strcmp(argv[i], "--serial") && i + 1 < argc) {
            serial = argv[++i];
        } else {
            usage(argv[0]);
            return 2;
        }
    }

    signal(SIGINT, stop_monitor);
    signal(SIGTERM, stop_monitor);

    struct ftdi_context *ftdi = ftdi_new();
    if (!ftdi) {
        fprintf(stderr, "could not allocate libftdi context\n");
        return 3;
    }
    ftdi_set_interface(ftdi, interface);
    if (ftdi_usb_open_desc(ftdi, 0x0403, 0x6010, NULL, serial) < 0) {
        fprintf(stderr, "open failed: %s\n", ftdi_get_error_string(ftdi));
        ftdi_free(ftdi);
        return 4;
    }

    /* The Console debugger firmware exposes a 4x divisor convention: asking
       FTDI for 500 kbaud samples the FPGA/BL616 wire running at 2 Mbaud. */
    if (ftdi_set_baudrate(ftdi, baud) < 0 ||
        ftdi_set_line_property(ftdi, BITS_8, STOP_BIT_2, NONE) < 0) {
        fprintf(stderr, "serial setup failed: %s\n", ftdi_get_error_string(ftdi));
        ftdi_usb_close(ftdi);
        ftdi_free(ftdi);
        return 5;
    }
    ftdi_set_latency_timer(ftdi, 2);
    ftdi_tcioflush(ftdi);

    fprintf(stderr,
            "Voodoo diagnostic UART: FTDI interface %c, request %d baud, 8N2.\n"
            "Hold the case MODE button while loading the core; Control-C stops.\n",
            interface == INTERFACE_A ? 'A' : 'B', baud);

    unsigned char buffer[1024];
    while (running) {
        int count = ftdi_read_data(ftdi, buffer, sizeof(buffer));
        if (count < 0) {
            fprintf(stderr, "\nread failed: %s\n", ftdi_get_error_string(ftdi));
            break;
        }
        if (count == 0) {
            usleep(5000);
            continue;
        }
        for (int i = 0; i < count; ++i) {
            unsigned char value = buffer[i];
            if (show_hex) {
                printf("%02x%c", value, ((i + 1) % 32) ? ' ' : '\n');
            } else if (value == '\n' || value == '\r' || value == '\t' || isprint(value)) {
                putchar(value);
            } else {
                printf("<%02x>", value);
            }
        }
        fflush(stdout);
    }

    ftdi_usb_close(ftdi);
    ftdi_free(ftdi);
    return 0;
}
