/*
 * chat_server.c - servidor de chat TCP multithread (uma thread por cliente)
 *                 com modo de avaliacao de vazao ("--bench").
 *
 * Modo chat (comportamento original):
 *     ./chat_server <porta>
 *
 * Modo benchmark (medicao da vazao agregada clientes -> servidor):
 *     ./chat_server <porta> --bench <n_clientes> <aquecimento_s> <medicao_s>
 *                   [--mode bulk|line] [--bind <IPv4>]
 *
 *   1. Aceita exatamente <n_clientes> conexoes; cada uma e atendida por uma
 *      thread propria, como no chat original.
 *   2. Cada cliente envia seu nome (1a linha), como no protocolo original.
 *      Quando todos estao registrados o servidor envia "GO\n" a todos,
 *      liberando a transmissao simultanea.
 *   3. Apos <aquecimento_s> segundos (descartados), o servidor tira uma
 *      "fotografia" dos contadores de bytes de todas as threads (B0, t0);
 *      apos mais <medicao_s> segundos tira outra (B1, t1).
 *      Vazao agregada = (B1 - B0) * 8 / (t1 - t0) / 10^6  [Mbps].
 *   4. Continua drenando ate todos os clientes encerrarem (EOF), confere se
 *      todos permaneceram ativos durante toda a janela e imprime o resultado
 *      em uma linha "RESULT ..." (lida pelo script de experimentos).
 *
 *   --mode bulk : (padrao) recv() com buffer de 128 KiB -> mede a capacidade
 *                 maxima do caminho TCP + servidor.
 *   --mode line : le 1 byte por recv() e monta linhas, exatamente como o
 *                 read_line() do chat original -> mede o custo do protocolo
 *                 de chat original (sem o broadcast).
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdatomic.h>
#include <pthread.h>

#include "net_compat.h"

#define BUFFER_SIZE 1024
#define MAX_CLIENTS 100
#define BENCH_RECV_BUFFER (128 * 1024)

typedef struct Client {
    socket_t fd;
    char name[32];
    struct Client *next;
} Client;

Client *clients = NULL;
int num_clients = 0;

pthread_mutex_t clients_mutex = PTHREAD_MUTEX_INITIALIZER;

/* Le uma linha terminada por '\n' (1 byte por chamada a recv). */
long read_line(socket_t fd, char *buffer, size_t capacity) {
    size_t pos = 0;

    while (pos < capacity - 1) {
        char c;
        long n = sock_recv(fd, &c, 1, 0);

        if (n == 0)
            return pos == 0 ? 0 : (long)pos;

        if (n < 0) {
            if (sock_errno() == SOCK_EINTR)
                continue;
            return -1;
        }

        if (c == '\n')
            break;

        if (c != '\r')
            buffer[pos++] = c;
    }

    buffer[pos] = '\0';
    return (long)pos;
}

/* ===================================================================== */
/*                         MODO CHAT (original)                          */
/* ===================================================================== */

/* Distribui uma mensagem aos demais clientes. */
void broadcast(const char *message, Client *sender) {
    pthread_mutex_lock(&clients_mutex);

    Client *current = clients;

    while (current != NULL) {
        if (current != sender) {
            if (send_all(current->fd, message,
                         strlen(message)) == -1) {
                fprintf(stderr,
                        "Falha ao enviar para %s.\n",
                        current->name);
            }
        }

        current = current->next;
    }

    pthread_mutex_unlock(&clients_mutex);
}

void *handle_client(void *arg) {
    Client *client = arg;
    char buffer[BUFFER_SIZE];
    char message[BUFFER_SIZE + 64];
    int registered = 0;

    /* A primeira linha recebida e o nome do usuario. */
    long n = read_line(client->fd, buffer, sizeof(buffer));

    if (n <= 0 || buffer[0] == '\0') {
        sock_close(client->fd);
        free(client);
        return NULL;
    }

    snprintf(client->name, sizeof(client->name),
             "%.31s", buffer);

    pthread_mutex_lock(&clients_mutex);

    if (num_clients >= MAX_CLIENTS) {
        pthread_mutex_unlock(&clients_mutex);

        const char *error = "Servidor cheio.\n";
        send_all(client->fd, error, strlen(error));
        sock_close(client->fd);
        free(client);
        return NULL;
    }

    client->next = clients;
    clients = client;
    num_clients++;
    registered = 1;

    pthread_mutex_unlock(&clients_mutex);

    snprintf(message, sizeof(message),
             "*** %s entrou no chat. ***\n",
             client->name);
    broadcast(message, client);

    printf("Usuario conectado: %s\n", client->name);
    fflush(stdout);

    while ((n = read_line(client->fd, buffer,
                          sizeof(buffer))) > 0) {
        if (strcmp(buffer, "/quit") == 0)
            break;

        snprintf(message, sizeof(message),
                 "%s: %s\n", client->name, buffer);

        broadcast(message, client);
    }

    if (n == -1)
        sock_perror("recv");

    if (registered) {
        pthread_mutex_lock(&clients_mutex);

        Client **current = &clients;

        while (*current != NULL && *current != client)
            current = &(*current)->next;

        if (*current == client) {
            *current = client->next;
            num_clients--;
        }

        pthread_mutex_unlock(&clients_mutex);

        snprintf(message, sizeof(message),
                 "*** %s saiu do chat. ***\n",
                 client->name);
        broadcast(message, NULL);
    }

    printf("Usuario desconectado: %s\n", client->name);
    fflush(stdout);

    sock_close(client->fd);
    free(client);

    return NULL;
}

static void run_chat(socket_t server_fd) {
    while (1) {
        struct sockaddr_in client_addr;
        socklen_t addr_len = sizeof(client_addr);

        socket_t client_fd = accept(server_fd,
                                    (struct sockaddr *)&client_addr,
                                    &addr_len);

        if (client_fd == INVALID_SOCK) {
            if (sock_errno() == SOCK_EINTR)
                continue;

            sock_perror("accept");
            continue;
        }

        Client *client = malloc(sizeof(Client));

        if (client == NULL) {
            perror("malloc");
            sock_close(client_fd);
            continue;
        }

        client->fd = client_fd;
        client->name[0] = '\0';
        client->next = NULL;

        pthread_t tid;

        if (pthread_create(&tid, NULL,
                           handle_client, client) != 0) {
            fprintf(stderr, "Erro ao criar thread.\n");
            sock_close(client_fd);
            free(client);
            continue;
        }

        pthread_detach(tid);
    }
}

/* ===================================================================== */
/*                         MODO BENCHMARK                                */
/* ===================================================================== */

enum { MODE_BULK = 0, MODE_LINE = 1 };

/*
 * Estado de cada thread de recepcao. Cada contador ocupa sua propria linha
 * de cache (64 bytes) para evitar "false sharing" entre threads: cada thread
 * e a UNICA escritora do seu contador; a thread principal apenas le.
 */
typedef struct {
    _Alignas(64) _Atomic uint64_t bytes;   /* bytes recebidos (acumulado) */
    _Atomic double end_time;                /* instante em que recebeu EOF */
    socket_t fd;
    int index;
    int mode;
    char name[32];
} BenchWorker;

static BenchWorker workers[MAX_CLIENTS];
static int bench_ready = 0;
static pthread_mutex_t bench_mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t bench_cond = PTHREAD_COND_INITIALIZER;

static void *bench_worker(void *arg) {
    BenchWorker *w = arg;
    char name[BUFFER_SIZE];
    uint64_t local = 0;

    /* Handshake identico ao chat: primeira linha = nome. */
    long n = read_line(w->fd, name, sizeof(name));
    snprintf(w->name, sizeof(w->name), "%.31s", n > 0 ? name : "?");

    pthread_mutex_lock(&bench_mutex);
    bench_ready++;
    pthread_cond_signal(&bench_cond);
    pthread_mutex_unlock(&bench_mutex);

    if (n <= 0) {
        atomic_store_explicit(&w->end_time, now_seconds(), memory_order_relaxed);
        return NULL;
    }

    if (w->mode == MODE_BULK) {
        char *buf = malloc(BENCH_RECV_BUFFER);
        if (buf == NULL) {
            perror("malloc");
            atomic_store_explicit(&w->end_time, now_seconds(), memory_order_relaxed);
            return NULL;
        }

        for (;;) {
            n = sock_recv(w->fd, buf, BENCH_RECV_BUFFER, 0);
            if (n > 0) {
                local += (uint64_t)n;
                atomic_store_explicit(&w->bytes, local, memory_order_relaxed);
            } else if (n < 0 && sock_errno() == SOCK_EINTR) {
                continue;
            } else {
                break;          /* 0 = EOF (cliente terminou); <0 = erro */
            }
        }
        free(buf);
    } else {
        /* Mesmo caminho do chat original: 1 recv() por byte + formatacao
         * da mensagem "<nome>: <texto>\n" (o broadcast e omitido). */
        char line[BUFFER_SIZE];
        char message[BUFFER_SIZE + 64];
        size_t pos = 0;
        volatile size_t sink = 0;

        for (;;) {
            char c;
            n = sock_recv(w->fd, &c, 1, 0);
            if (n == 1) {
                local++;
                atomic_store_explicit(&w->bytes, local, memory_order_relaxed);
                if (c == '\n' || pos == sizeof(line) - 1) {
                    line[pos] = '\0';
                    sink += (size_t)snprintf(message, sizeof(message),
                                             "%s: %s\n", w->name, line);
                    pos = 0;
                } else if (c != '\r') {
                    line[pos++] = c;
                }
            } else if (n < 0 && sock_errno() == SOCK_EINTR) {
                continue;
            } else {
                break;
            }
        }
        (void)sink;
    }

    atomic_store_explicit(&w->end_time, now_seconds(), memory_order_relaxed);
    return NULL;
}

static uint64_t snapshot(int n, uint64_t *per_client) {
    uint64_t total = 0;
    for (int i = 0; i < n; i++) {
        per_client[i] = atomic_load_explicit(&workers[i].bytes,
                                             memory_order_relaxed);
        total += per_client[i];
    }
    return total;
}

static int run_bench(socket_t server_fd, int n_clients, double warmup,
                     double window, int mode) {
    pthread_t tids[MAX_CLIENTS];
    uint64_t c0[MAX_CLIENTS], c1[MAX_CLIENTS], cend[MAX_CLIENTS];

    printf("BENCH aguardando %d clientes (modo %s, aquecimento %.1f s, "
           "medicao %.1f s)\n", n_clients, mode == MODE_BULK ? "bulk" : "line",
           warmup, window);
    fflush(stdout);

    /* 1. Aceita as N conexoes, uma thread por cliente. */
    for (int i = 0; i < n_clients; i++) {
        socket_t fd = accept(server_fd, NULL, NULL);
        if (fd == INVALID_SOCK) {
            if (sock_errno() == SOCK_EINTR) { i--; continue; }
            sock_perror("accept");
            return EXIT_FAILURE;
        }
        workers[i].fd = fd;
        workers[i].index = i;
        workers[i].mode = mode;
        atomic_store(&workers[i].bytes, 0);
        atomic_store(&workers[i].end_time, 0.0);

        if (pthread_create(&tids[i], NULL, bench_worker, &workers[i]) != 0) {
            fprintf(stderr, "Erro ao criar thread.\n");
            return EXIT_FAILURE;
        }
    }

    /* 2. Espera todos se identificarem e libera a transmissao. */
    pthread_mutex_lock(&bench_mutex);
    while (bench_ready < n_clients)
        pthread_cond_wait(&bench_cond, &bench_mutex);
    pthread_mutex_unlock(&bench_mutex);

    double t_go = now_seconds();
    for (int i = 0; i < n_clients; i++)
        send_all(workers[i].fd, "GO\n", 3);

    /* 3. Aquecimento (descartado) e janela de medicao. */
    sleep_seconds(warmup);

    double u0, s0, u1, s1;
    process_cpu_seconds(&u0, &s0);
    double t0 = now_seconds();
    uint64_t b0 = snapshot(n_clients, c0);

    sleep_seconds(window);

    uint64_t b1 = snapshot(n_clients, c1);
    double t1 = now_seconds();
    process_cpu_seconds(&u1, &s1);

    /* 4. Drena ate todos encerrarem e confere a validade da rodada. */
    for (int i = 0; i < n_clients; i++)
        pthread_join(tids[i], NULL);

    uint64_t btotal = snapshot(n_clients, cend);
    int valid = 1;
    for (int i = 0; i < n_clients; i++) {
        double end = atomic_load(&workers[i].end_time);
        if (end < t1 || c1[i] == c0[i]) {
            valid = 0;
            fprintf(stderr, "AVISO: cliente %d (%s) nao transmitiu durante "
                    "toda a janela de medicao.\n", i, workers[i].name);
        }
        sock_close(workers[i].fd);
    }

    double elapsed = t1 - t0;
    double mbps = (double)(b1 - b0) * 8.0 / elapsed / 1e6;
    double cpu = (u1 - u0) + (s1 - s0);

    printf("RESULT clients=%d mode=%s warmup=%.3f window=%.6f "
           "bytes_window=%llu mbps=%.3f bytes_total=%llu "
           "cpu_user=%.3f cpu_sys=%.3f cpu_cores=%.3f "
           "since_go=%.6f valid=%d\n",
           n_clients, mode == MODE_BULK ? "bulk" : "line", t0 - t_go,
           elapsed, (unsigned long long)(b1 - b0), mbps,
           (unsigned long long)btotal, u1 - u0, s1 - s0, cpu / elapsed,
           t1 - t_go, valid);

    for (int i = 0; i < n_clients; i++) {
        printf("CLIENT index=%d name=%s bytes_window=%llu mbps=%.3f "
               "bytes_total=%llu\n", i, workers[i].name,
               (unsigned long long)(c1[i] - c0[i]),
               (double)(c1[i] - c0[i]) * 8.0 / elapsed / 1e6,
               (unsigned long long)cend[i]);
    }
    fflush(stdout);

    return valid ? EXIT_SUCCESS : 2;
}

/* ===================================================================== */

static void usage(const char *prog) {
    fprintf(stderr,
            "Uso:\n"
            "  %s <porta> [--bind IPv4]\n"
            "  %s <porta> --bench <n_clientes> <aquecimento_s> <medicao_s>"
            " [--mode bulk|line] [--bind IPv4]\n", prog, prog);
}

int main(int argc, char *argv[]) {
    socket_t server_fd;
    struct sockaddr_in server_addr;
    const char *bind_ip = NULL;
    int bench = 0, n_clients = 0, mode = MODE_BULK;
    double warmup = 0, window = 0;

    if (argc < 2) {
        usage(argv[0]);
        return EXIT_FAILURE;
    }

    int port = atoi(argv[1]);

    for (int i = 2; i < argc; i++) {
        if (strcmp(argv[i], "--bench") == 0 && i + 3 < argc) {
            bench = 1;
            n_clients = atoi(argv[++i]);
            warmup = atof(argv[++i]);
            window = atof(argv[++i]);
        } else if (strcmp(argv[i], "--mode") == 0 && i + 1 < argc) {
            i++;
            if (strcmp(argv[i], "line") == 0)
                mode = MODE_LINE;
            else if (strcmp(argv[i], "bulk") == 0)
                mode = MODE_BULK;
            else { usage(argv[0]); return EXIT_FAILURE; }
        } else if (strcmp(argv[i], "--bind") == 0 && i + 1 < argc) {
            bind_ip = argv[++i];
        } else {
            usage(argv[0]);
            return EXIT_FAILURE;
        }
    }

    if (bench && (n_clients < 1 || n_clients > MAX_CLIENTS ||
                  warmup < 0 || window <= 0)) {
        fprintf(stderr, "Parametros de benchmark invalidos.\n");
        return EXIT_FAILURE;
    }

    if (net_init() != 0) {
        fprintf(stderr, "Falha ao inicializar a rede.\n");
        return EXIT_FAILURE;
    }

    server_fd = socket(AF_INET, SOCK_STREAM, 0);

    if (server_fd == INVALID_SOCK) {
        sock_perror("socket");
        return EXIT_FAILURE;
    }

    int opt = 1;
    setsockopt(server_fd, SOL_SOCKET, SO_REUSEADDR,
               (const char *)&opt, sizeof(opt));

    memset(&server_addr, 0, sizeof(server_addr));
    server_addr.sin_family = AF_INET;
    server_addr.sin_addr.s_addr = htonl(INADDR_ANY);
    server_addr.sin_port = htons(port);

    if (bind_ip != NULL &&
        inet_pton(AF_INET, bind_ip, &server_addr.sin_addr) != 1) {
        fprintf(stderr, "Endereco IPv4 invalido.\n");
        return EXIT_FAILURE;
    }

    if (bind(server_fd, (struct sockaddr *)&server_addr,
             sizeof(server_addr)) == -1) {
        sock_perror("bind");
        sock_close(server_fd);
        return EXIT_FAILURE;
    }

    if (listen(server_fd, MAX_CLIENTS) == -1) {
        sock_perror("listen");
        sock_close(server_fd);
        return EXIT_FAILURE;
    }

    int rc = EXIT_SUCCESS;

    if (bench) {
        rc = run_bench(server_fd, n_clients, warmup, window, mode);
    } else {
        printf("Chat TCP ativo na porta %d.\n", port);
        fflush(stdout);
        run_chat(server_fd);
    }

    sock_close(server_fd);
    pthread_mutex_destroy(&clients_mutex);
    net_cleanup();

    return rc;
}
