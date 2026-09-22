/* pwrite/fsync for the trace writer. MinGW declares them and does not ship them. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <io.h>
#include <stdint.h>
#include <string.h>
#include <sys/types.h>

ssize_t pwrite(int fd, const void *buf, size_t count, off_t offset)
{
    HANDLE handle = (HANDLE)_get_osfhandle(fd);
    OVERLAPPED ov;
    DWORD wrote = 0;

    if (handle == INVALID_HANDLE_VALUE)
        return -1;
    memset(&ov, 0, sizeof(ov));
    ov.Offset = (DWORD)offset;
    ov.OffsetHigh = (DWORD)((uint64_t)offset >> 32);
    if (!WriteFile(handle, buf, (DWORD)count, &wrote, &ov))
        return -1;
    return (ssize_t)wrote;
}

int fsync(int fd)
{
    HANDLE handle = (HANDLE)_get_osfhandle(fd);
    if (handle == INVALID_HANDLE_VALUE || !FlushFileBuffers(handle))
        return -1;
    return 0;
}
