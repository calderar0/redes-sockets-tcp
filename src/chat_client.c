/*
 * chat_client.c - cliente de chat TCP com modo de transmissao continua
 *                 ("--bench") para avaliacao de vazao.
 *
 * Modo chat (comportamento original):
 *     ./chat_client <IP> <porta>
 *
 * Modo benchmark:
 *     ./chat_client <IP> <porta> --bench <duracao_s> <bloco_bytes> [nome]
 *
 *   Conecta, envia o nome (protocolo original), espera o "GO\n" do servidor
 *   e transmite blocos de <bloco_bytes> continuamente, sem digitacao, ate
 *   completar <duracao_s> segundos. Depois fecha o sentido de escrita
 *   (shutdown), espera o servidor terminar de ler e imprime uma linha
 *   "RESULT ..." com o total de bytes enviados.
 *
 *   O conteudo do bloco e texto imprimivel com '\n' a cada 128 bytes, ou
 *   seja, o fluxo e uma sequencia de mensagens de chat validas de 128 bytes.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <pthread.h>

#include "net_compat.h"

#define BUFFER_SIZE 1024
#define BENCH_LINE  128

socket_t sockfd;
volatile int closing = 0;   /* evita erro espurio de recv() apos shutdown */
pthread_mutex_t output_mutex = PTHREAD_MUTEX_INITIALIZER;

/* ===================================================================== */
/*                         MODO CHAT (original)                          */
/* ===================================================================== */

void *receive_messages(void *arg) {
    char buffer[BUFFER_SIZE];
    size_t pos = 0;

    (void)arg;

    while (1) {
        char c;
        long n = sock_recv(sockfd, &c, 1, 0);

        if (n == 0) {
            pthread_mutex_lock(&output_mutex);
            printf("\nConexao encerrada pelo servidor.\n");
            fflush(stdout);
            pthread_mutex_unlock(&output_mutex);
            break;
        }

        if (n < 0) {
            if (sock_errno() == SOCK_EINTR)
                continue;

            if (!closing) {
                pthread_mutex_lock(&output_mutex);
                sock_perror("recv");
                pthread_mutex_unlock(&output_mutex);
            }
            break;
        }

        if (c == '\n') {
            buffer[pos] = '\0';

            pthread_mutex_lock(&output_mutex);
            printf("\r%s\n", buffer);
            fflush(stdout);
            pthread_mutex_unlock(&output_mutex);

            pos = 0;
        } else if (c != '\r') {
            if (pos < sizeof(buffer) - 1)
                buffer[pos++] = c;
        }
    }

    return NULL;
}

static int run_chat(void) {
    char name[32];
    char buffer[BUFFER_SIZE];
    pthread_t receiver;

    printf("Seu nome: ");

    if (fgets(name, sizeof(name), stdin) == NULL)
        return EXIT_FAILURE;

    name[strcspn(name, "\r\n")] = '\0';

    if (name[0] == '\0' ||
        strchr(name, '\n') != NULL) {
        fprintf(stderr, "Nome invalido.\n");
        return EXIT_FAILURE;
    }

    if (send_all(sockfd, name, strlen(name)) == -1 ||
        send_all(sockfd, "\n", 1) == -1) {
        sock_perror("send");
        return EXIT_FAILURE;
    }

    if (pthread_create(&receiver, NULL,
                       receive_messages, NULL) != 0) {
        fprintf(stderr, "Erro ao criar thread receptora.\n");
        return EXIT_FAILURE;
    }

    printf("Conectado! Digite /quit para sair.\n");

    while (1) {
        pthread_mutex_lock(&output_mutex);
        printf("> ");
        fflush(stdout);
        pthread_mutex_unlock(&output_mutex);

        if (fgets(buffer, sizeof(buffer), stdin) == NULL)
            break;

        if (strcmp(buffer, "/quit\n") == 0 ||
            strcmp(buffer, "/quit") == 0) {
            send_all(sockfd, "/quit\n", 6);
            break;
        }

        size_t len = strlen(buffer);

        if (len > 0 && buffer[len - 1] != '\n') {
            int c;
            while ((c = getchar()) != '\n' && c != EOF)
                ;
            fprintf(stderr, "Mensagem muito longa.\n");
            continue;
        }

        if (send_all(sockfd, buffer, len) == -1) {
            sock_perror("send");
            break;
        }
    }

    closing = 1;
    shutdown(sockfd, SHUT_RDWR);
    pthread_join(receiver, NULL);

    printf("Cliente encerrado.\n");
    return EXIT_SUCCESS;
}

/* ===================================================================== */
/*                         MODO BENCHMARK                                */
/* ===================================================================== */

static int run_bench(double duration, size_t block_size, const char *name) {
    char line[64];
    size_t pos = 0;

    char *block = malloc(block_size);
    if (block == NULL) {
        perror("malloc");
        return EXIT_FAILURE;
    }

    /* Mensagens de chat de 128 bytes: 127 caracteres + '\n'. */
    for (size_t j = 0; j < block_size; j++)
        block[j] = (j % BENCH_LINE == BENCH_LINE - 1)
                   ? '\n' : (char)('a' + j % 26);

    /* Handshake original: nome terminado por '\n'. */
    if (send_all(sockfd, name, strlen(name)) == -1 ||
        send_all(sockfd, "\n", 1) == -1) {
        sock_perror("send");
        free(block);
        return EXIT_FAILURE;
    }

    /* Aguarda o sinal de largada do servidor. */
    for (;;) {
        char c;
        long n = sock_recv(sockfd, &c, 1, 0);
        if (n == 1) {
            if (c == '\n') break;
            if (pos < sizeof(line) - 1) line[pos++] = c;
        } else if (n < 0 && sock_errno() == SOCK_EINTR) {
            continue;
        } else {
            fprintf(stderr, "Servidor encerrou antes do GO.\n");
            free(block);
            return EXIT_FAILURE;
        }
    }
    line[pos] = '\0';
    if (strcmp(line, "GO") != 0) {
        fprintf(stderr, "Sinal inesperado do servidor: %s\n", line);
        free(block);
        return EXIT_FAILURE;
    }

    /* Transmissao continua durante o intervalo definido. */
    uint64_t sent = 0;
    int failed = 0;
    double t_start = now_seconds();
    double t_end = t_start + duration;
    double t;

    do {
        if (send_all(sockfd, block, block_size) == -1) {
            sock_perror("send");
            failed = 1;
            break;
        }
        sent += block_size;
        t = now_seconds();
    } while (t < t_end);

    double t_stop = now_seconds();

    /* Encerramento ordenado: fim da escrita e espera do EOF do servidor,
     * garantindo que todos os bytes foram consumidos pela aplicacao. */
    shutdown(sockfd, SHUT_WR);
    for (;;) {
        char tmp[256];
        long n = sock_recv(sockfd, tmp, sizeof(tmp), 0);
        if (n > 0) continue;
        if (n < 0 && sock_errno() == SOCK_EINTR) continue;
        break;
    }

    double elapsed = t_stop - t_start;
    printf("RESULT name=%s block=%zu sent=%llu seconds=%.6f mbps=%.3f ok=%d\n",
           name, block_size, (unsigned long long)sent, elapsed,
           (double)sent * 8.0 / elapsed / 1e6, !failed);
    fflush(stdout);

    free(block);
    return failed ? EXIT_FAILURE : EXIT_SUCCESS;
}

/* ===================================================================== */

int main(int argc, char *argv[]) {
    struct sockaddr_in server_addr;
    int bench = 0;
    double duration = 0;
    long long block_size = 0;
    const char *bench_name = "bench";

    if (argc == 3) {
        bench = 0;
    } else if ((argc == 6 || argc == 7) && strcmp(argv[3], "--bench") == 0) {
        bench = 1;
        duration = atof(argv[4]);
        block_size = atoll(argv[5]);
        if (argc == 7)
            bench_name = argv[6];
        if (duration <= 0 || block_size <= 0 || block_size > (1LL << 30)) {
            fprintf(stderr, "Duracao ou tamanho de bloco invalido.\n");
            return EXIT_FAILURE;
        }
    } else {
        fprintf(stderr,
                "Uso:\n"
                "  %s <IP> <porta>\n"
                "  %s <IP> <porta> --bench <duracao_s> <bloco_bytes> [nome]\n",
                argv[0], argv[0]);
        return EXIT_FAILURE;
    }

    int port = atoi(argv[2]);

    if (net_init() != 0) {
        fprintf(stderr, "Falha ao inicializar a rede.\n");
        return EXIT_FAILURE;
    }

    sockfd = socket(AF_INET, SOCK_STREAM, 0);

    if (sockfd == INVALID_SOCK) {
        sock_perror("socket");
        return EXIT_FAILURE;
    }

    memset(&server_addr, 0, sizeof(server_addr));
    server_addr.sin_family = AF_INET;
    server_addr.sin_port = htons(port);

    if (inet_pton(AF_INET, argv[1],
                  &server_addr.sin_addr) != 1) {
        fprintf(stderr, "Endereco IPv4 invalido.\n");
        sock_close(sockfd);
        return EXIT_FAILURE;
    }

    if (connect(sockfd, (struct sockaddr *)&server_addr,
                sizeof(server_addr)) == -1) {
        sock_perror("connect");
        sock_close(sockfd);
        return EXIT_FAILURE;
    }

    int rc = bench ? run_bench(duration, (size_t)block_size, bench_name)
                   : run_chat();

    sock_close(sockfd);
    pthread_mutex_destroy(&output_mutex);
    net_cleanup();

    return rc;
}
