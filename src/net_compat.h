/*
 * net_compat.h - camada minima de portabilidade (Linux/macOS <-> Windows).
 *
 * O codigo original usava apenas a API POSIX de sockets. Para que o mesmo
 * fonte compile tanto em Linux (gcc/clang) quanto em Windows (MinGW-w64 +
 * Winsock2), as diferencas foram isoladas aqui:
 *   - tipo do descritor de socket, fechamento e codigos de erro;
 *   - inicializacao da biblioteca de rede (WSAStartup / SIGPIPE);
 *   - relogio monotonico de alta resolucao e sleep;
 *   - tempo de CPU consumido pelo processo (usuario + sistema).
 */
#ifndef NET_COMPAT_H
#define NET_COMPAT_H

#include <stdio.h>
#include <stdint.h>
#include <string.h>

#ifdef _WIN32
/* ------------------------------------------------------------- Windows */
#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0600
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>

typedef SOCKET socket_t;
#define INVALID_SOCK INVALID_SOCKET
#define SOCK_EINTR   WSAEINTR

#ifndef MSG_NOSIGNAL
#define MSG_NOSIGNAL 0          /* Windows nao gera SIGPIPE */
#endif
#ifndef SHUT_RDWR
#define SHUT_RDWR SD_BOTH
#endif
#ifndef SHUT_WR
#define SHUT_WR SD_SEND
#endif

static inline int net_init(void) {
    WSADATA wsa;
    return WSAStartup(MAKEWORD(2, 2), &wsa) == 0 ? 0 : -1;
}

static inline void net_cleanup(void) { WSACleanup(); }

static inline int sock_close(socket_t s) { return closesocket(s); }

static inline int sock_errno(void) { return WSAGetLastError(); }

static inline void sock_perror(const char *msg) {
    fprintf(stderr, "%s: erro Winsock %d\n", msg, WSAGetLastError());
}

static inline long sock_send(socket_t s, const char *buf, size_t len, int flags) {
    if (len > 0x7fffffff) len = 0x7fffffff;
    return (long)send(s, buf, (int)len, flags);
}

static inline long sock_recv(socket_t s, char *buf, size_t len, int flags) {
    if (len > 0x7fffffff) len = 0x7fffffff;
    return (long)recv(s, buf, (int)len, flags);
}

/* Relogio monotonico em segundos (QueryPerformanceCounter, ~100 ns). */
static inline double now_seconds(void) {
    static LARGE_INTEGER freq;
    LARGE_INTEGER t;
    if (freq.QuadPart == 0)
        QueryPerformanceFrequency(&freq);
    QueryPerformanceCounter(&t);
    return (double)t.QuadPart / (double)freq.QuadPart;
}

static inline void sleep_seconds(double s) {
    if (s > 0)
        Sleep((DWORD)(s * 1000.0 + 0.5));
}

/* Tempo de CPU do processo (usuario e sistema), em segundos. */
static inline void process_cpu_seconds(double *user, double *sys) {
    FILETIME c, e, k, u;
    if (GetProcessTimes(GetCurrentProcess(), &c, &e, &k, &u)) {
        ULARGE_INTEGER ku, uu;
        ku.LowPart = k.dwLowDateTime; ku.HighPart = k.dwHighDateTime;
        uu.LowPart = u.dwLowDateTime; uu.HighPart = u.dwHighDateTime;
        *sys  = (double)ku.QuadPart / 1e7;   /* unidades de 100 ns */
        *user = (double)uu.QuadPart / 1e7;
    } else {
        *user = *sys = 0.0;
    }
}

#else
/* --------------------------------------------------------------- POSIX */
#include <errno.h>
#include <signal.h>
#include <time.h>
#include <unistd.h>
#include <sys/socket.h>
#include <sys/resource.h>
#include <sys/time.h>
#include <arpa/inet.h>
#include <netinet/in.h>

typedef int socket_t;
#define INVALID_SOCK (-1)
#define SOCK_EINTR   EINTR

#ifndef MSG_NOSIGNAL
#define MSG_NOSIGNAL 0          /* macOS: usamos signal(SIGPIPE, SIG_IGN) */
#endif

static inline int net_init(void) {
    signal(SIGPIPE, SIG_IGN);
    return 0;
}

static inline void net_cleanup(void) { }

static inline int sock_close(socket_t s) { return close(s); }

static inline int sock_errno(void) { return errno; }

static inline void sock_perror(const char *msg) { perror(msg); }

static inline long sock_send(socket_t s, const char *buf, size_t len, int flags) {
    return (long)send(s, buf, len, flags);
}

static inline long sock_recv(socket_t s, char *buf, size_t len, int flags) {
    return (long)recv(s, buf, len, flags);
}

static inline double now_seconds(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec / 1e9;
}

static inline void sleep_seconds(double s) {
    struct timespec ts;
    if (s <= 0)
        return;
    ts.tv_sec = (time_t)s;
    ts.tv_nsec = (long)((s - (double)ts.tv_sec) * 1e9);
    while (nanosleep(&ts, &ts) == -1 && errno == EINTR)
        ;
}

static inline void process_cpu_seconds(double *user, double *sys) {
    struct rusage ru;
    if (getrusage(RUSAGE_SELF, &ru) == 0) {
        *user = (double)ru.ru_utime.tv_sec + ru.ru_utime.tv_usec / 1e6;
        *sys  = (double)ru.ru_stime.tv_sec + ru.ru_stime.tv_usec / 1e6;
    } else {
        *user = *sys = 0.0;
    }
}
#endif

/* Envia todos os bytes, mesmo se send() enviar parcialmente. */
static inline int send_all(socket_t fd, const char *data, size_t len) {
    size_t sent = 0;

    while (sent < len) {
        long n = sock_send(fd, data + sent, len - sent, MSG_NOSIGNAL);

        if (n < 0) {
            if (sock_errno() == SOCK_EINTR)
                continue;
            return -1;
        }

        if (n == 0)
            return -1;

        sent += (size_t)n;
    }

    return 0;
}

#endif /* NET_COMPAT_H */
