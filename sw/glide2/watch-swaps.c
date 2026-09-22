/* Stop a Wine process group once a Glide trace contains enough swaps.
 *
 * Python cannot scan the trace as fast as MiniGL writes it. This reads
 * the file as it grows and signals the process group on the Nth write to
 * SST_swapbufferCMD (register 0x128), or when the time or size cap hits.
 */
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define HDR 64
#define ENT 24
#define SWAP_ADDR 0x128u
#define MAGIC 0x564F4F44u

static void stop_group(pid_t leader) {
    if (kill(-leader, SIGTERM) != 0)
        kill(leader, SIGTERM);
}

int main(int argc, char **argv) {
    const char *path;
    int want;
    int limit_s;
    pid_t leader;
    unsigned long cap;
    int fd = -1;
    unsigned char buf[1 << 20];
    unsigned char hold[ENT];
    size_t held = 0;
    int saw_hdr = 0;
    int swaps = 0;
    unsigned long long bytes = 0;
    int ticks = 0;
    time_t start = time(NULL);

    if (argc != 6) {
        fprintf(stderr, "usage: watch-swaps TRACE SWAPS SECONDS LEADER MAX_BYTES\n");
        return 2;
    }
    path = argv[1];
    want = atoi(argv[2]);
    limit_s = atoi(argv[3]);
    leader = (pid_t)atoi(argv[4]);
    cap = strtoul(argv[5], NULL, 10);

    for (;;) {
        if (fd < 0) {
            fd = open(path, O_RDONLY);
            if (fd < 0) {
                if (ticks > limit_s * 20 || kill(leader, 0) != 0) {
                    printf("no trace swaps=0\n");
                    fflush(stdout);
                    if (kill(leader, 0) == 0)
                        stop_group(leader);
                    return 0;
                }
                usleep(50000);
                ticks++;
                continue;
            }
        }
        ssize_t n = read(fd, buf, sizeof buf);
        if (n < 0) {
            if (errno == EINTR)
                continue;
            perror("read");
            stop_group(leader);
            return 1;
        }
        if (n == 0) {
            if (time(NULL) - start >= limit_s || kill(leader, 0) != 0) {
                printf("stop swaps=%d bytes=%llu\n", swaps, bytes);
                fflush(stdout);
                if (kill(leader, 0) == 0)
                    stop_group(leader);
                return 0;
            }
            usleep(50000);
            ticks++;
            continue;
        }
        const unsigned char *p = buf;
        size_t left = (size_t)n;
        bytes += (unsigned long long)n;
        if (!saw_hdr) {
            unsigned char hdr[HDR];
            size_t got = 0;
            if (held) {
                size_t take = held > HDR ? HDR : held;
                memcpy(hdr, hold, take);
                got = take;
                held = 0;
            }
            while (got < HDR && left) {
                hdr[got++] = *p++;
                left--;
            }
            if (got < HDR) {
                memcpy(hold, hdr, got);
                held = got;
                continue;
            }
            uint32_t magic;
            memcpy(&magic, hdr, 4);
            if (magic != MAGIC) {
                printf("bad magic %08x\n", magic);
                stop_group(leader);
                return 1;
            }
            saw_hdr = 1;
        }
        if (held && left) {
            size_t need = ENT - held;
            if (left < need) {
                memcpy(hold + held, p, left);
                held += left;
                left = 0;
            } else {
                memcpy(hold + held, p, need);
                p += need;
                left -= need;
                held = ENT;
            }
        }
        if (held == ENT) {
            uint8_t cmd = hold[8];
            uint32_t addr;
            memcpy(&addr, hold + 12, 4);
            held = 0;
            if (cmd == 0 && addr == SWAP_ADDR) {
                swaps++;
                printf("swap %d at %llu\n", swaps, bytes);
                fflush(stdout);
                if (swaps >= want) {
                    stop_group(leader);
                    printf("swaps=%d\n", swaps);
                    return 0;
                }
            }
        }
        while (left >= ENT) {
            uint8_t cmd = p[8];
            uint32_t addr;
            memcpy(&addr, p + 12, 4);
            p += ENT;
            left -= ENT;
            if (cmd == 0 && addr == SWAP_ADDR) {
                swaps++;
                printf("swap %d at %llu\n", swaps, bytes);
                fflush(stdout);
                if (swaps >= want) {
                    stop_group(leader);
                    printf("swaps=%d\n", swaps);
                    return 0;
                }
            }
        }
        if (left) {
            memcpy(hold, p, left);
            held = left;
        }
        if (bytes > cap) {
            printf("cap swaps=%d bytes=%llu\n", swaps, bytes);
            fflush(stdout);
            stop_group(leader);
            return 0;
        }
    }
}
