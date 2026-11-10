# redes-sockets-tcp — capacidade máxima de transmissão do chat TCP

Aplicação de chat TCP cliente/servidor em C (uma thread por cliente), modificada para medir a
**vazão agregada máxima dos clientes para o servidor** com 1 a 10 clientes simultâneos.

- Relatório técnico com os resultados: **[RELATORIO.docx](RELATORIO.docx)** e
  [RELATORIO.pdf](RELATORIO.pdf) (fonte: [RELATORIO.md](RELATORIO.md))
- Servidor: [`src/chat_server.c`](src/chat_server.c)
- Cliente: [`src/chat_client.c`](src/chat_client.c)
- Portabilidade Linux/Windows: [`src/net_compat.h`](src/net_compat.h)
- Experimentos: [`scripts/run_experiments.py`](scripts/run_experiments.py)
- Análise, tabelas e gráficos: [`scripts/analyze.py`](scripts/analyze.py)
- Dados brutos: [`results/raw/`](results/raw/)

O modo chat original continua funcionando. O modo de medição é ativado por `--bench`.

## Compilação

Requisitos: `gcc` (ou `clang`) com pthreads. No Windows, use MinGW-w64 com o modelo de threads
POSIX (testado com MinGW-w64 GCC 16.1.0); a Winsock2 (`-lws2_32`) é ligada automaticamente.

```bash
make            # Linux/macOS
```

```bash
mingw32-make    # Windows (MinGW-w64)
```

Os binários ficam em `bin/`. Sem `make`, compile assim:

```bash
gcc -Wall -Wextra -O2 -std=gnu11 src/chat_server.c -o bin/chat_server -pthread
gcc -Wall -Wextra -O2 -std=gnu11 src/chat_client.c -o bin/chat_client -pthread
```

No Windows, acrescente `-lws2_32` ao final dos dois comandos.

## Execução — modo chat (original)

```bash
./bin/chat_server 5000
```

```bash
./bin/chat_client 127.0.0.1 5000
```

## Execução — modo de medição de vazão

Servidor: aceita exatamente `N` clientes, libera todos ao mesmo tempo (`GO`), descarta o
aquecimento e mede a vazão agregada durante a janela de medição.

```text
chat_server <porta> --bench <N> <aquecimento_s> <medicao_s> [--mode bulk|line] [--bind IPv4]
```

Cliente: após o `GO`, envia blocos de `<bloco_bytes>` sem parar durante `<duracao_s>` segundos.

```text
chat_client <IP> <porta> --bench <duracao_s> <bloco_bytes> [nome]
```

Exemplo manual com 3 clientes (aquecimento de 2 s, medição de 10 s, blocos de 64 KiB):

```bash
./bin/chat_server 5000 --bench 3 2 10 --bind 127.0.0.1 &
for i in 1 2 3; do ./bin/chat_client 127.0.0.1 5000 --bench 14 65536 c$i & done; wait
```

A duração dos clientes deve ser **maior** que `aquecimento + medição` (aqui, 14 s > 12 s).
Assim, todos continuam transmitindo durante toda a janela. O servidor imprime uma linha
`RESULT ... mbps=<vazão agregada> ... valid=1` e uma linha `CLIENT ...` por conexão.

Para dois computadores, inicie o servidor com `--bind 0.0.0.0` (ou sem `--bind`) e passe o IP
do servidor aos clientes.

## Reproduzindo os experimentos do relatório

Use Python 3.8+ (somente biblioteca padrão):

```bash
python scripts/run_experiments.py --tag main_bulk_64k --clients 1-10 --reps 5 --warmup 2 --window 10 --block 65536 --mode bulk
```

```bash
python scripts/run_experiments.py --tag line_mode --clients 1-10 --reps 3 --warmup 2 --window 5 --block 65536 --mode line
```

```bash
for b in 1024 4096 16384 65536 262144; do python scripts/run_experiments.py --tag block_$b --clients 1,10 --reps 3 --warmup 2 --window 5 --block $b; done
```

```bash
python scripts/analyze.py
```

`analyze.py` gera `results/tabelas.md`, `results/summary_main.csv` e os gráficos SVG em
`results/figures/`.

Para gerar o PDF do relatório a partir do `RELATORIO.md`, são necessários Chrome ou Edge e os
pacotes `markdown` e `pypdf`:

```bash
pip install markdown pypdf
```

```bash
python scripts/build_pdf.py
```

Para gerar a versão Word (`RELATORIO.docx`), são necessários Node.js e os pacotes `docx` e
`marked`. Os gráficos entram como os PNGs de `results/figures/`.

```bash
npm install docx@9 marked@15
```

```bash
node scripts/build_docx.js
```
