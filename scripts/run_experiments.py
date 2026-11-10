#!/usr/bin/env python3
"""
Orquestra os experimentos de vazao do chat TCP (somente biblioteca padrao).

Para cada repeticao e cada quantidade de clientes N:
  1. inicia o servidor em modo --bench (aceita N conexoes);
  2. inicia N processos cliente simultaneos em modo --bench;
  3. le a linha RESULT do servidor (vazao agregada na janela de medicao) e as
     linhas RESULT dos clientes (bytes enviados), validando que
     sum(bytes enviados) == bytes recebidos pelo servidor;
  4. registra tudo em CSV.

A ordem das quantidades de clientes e embaralhada em cada repeticao (semente
fixa) para que derivas lentas (temperatura, processos em segundo plano) nao
favorecam sistematicamente nenhum cenario.

Exemplo:
  python scripts/run_experiments.py --tag main --clients 1-10 --reps 5 \
      --warmup 2 --window 10 --block 65536 --mode bulk
"""
import argparse
import csv
import datetime as dt
import os
import platform
import random
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = ".exe" if os.name == "nt" else ""
KV = re.compile(r"(\w+)=(\S+)")


def parse_range(text):
    out = []
    for part in text.split(","):
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


# --------------------------------------------------------------- CPU do SO
def system_cpu_times():
    """Retorna (ocioso, total) acumulados de todas as CPUs, em unidades do SO."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        idle, kern, user = (wintypes.FILETIME() for _ in range(3))
        ctypes.windll.kernel32.GetSystemTimes(
            ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user))

        def ft(f):
            return (f.dwHighDateTime << 32) | f.dwLowDateTime

        # No Windows o tempo de kernel ja inclui o tempo ocioso.
        return ft(idle), ft(kern) + ft(user)
    with open("/proc/stat") as f:
        vals = [int(v) for v in f.readline().split()[1:]]
    idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
    return idle, sum(vals)


def cpu_util(a, b):
    di, dt_ = b[0] - a[0], b[1] - a[1]
    return 100.0 * (1.0 - di / dt_) if dt_ > 0 else float("nan")


def on_ac_power():
    """'ac', 'battery' ou 'unknown' (registrado para documentar o ambiente)."""
    if os.name == "nt":
        import ctypes

        class SPS(ctypes.Structure):
            _fields_ = [("ACLineStatus", ctypes.c_byte),
                        ("BatteryFlag", ctypes.c_byte),
                        ("BatteryLifePercent", ctypes.c_byte),
                        ("SystemStatusFlag", ctypes.c_byte),
                        ("BatteryLifeTime", ctypes.c_ulong),
                        ("BatteryFullLifeTime", ctypes.c_ulong)]
        s = SPS()
        if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)):
            return {1: "ac", 0: "battery"}.get(s.ACLineStatus, "unknown")
    return "unknown"


# ------------------------------------------------------------ uma rodada
def run_once(args, n, port):
    server = os.path.join(ROOT, "bin", "chat_server" + EXE)
    client = os.path.join(ROOT, "bin", "chat_client" + EXE)
    client_duration = args.warmup + args.window + args.tail

    srv = subprocess.Popen(
        [server, str(port), "--bench", str(n), str(args.warmup),
         str(args.window), "--mode", args.mode, "--bind", args.host],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    first = srv.stdout.readline()          # "BENCH aguardando ..." => escutando
    if not first.startswith("BENCH"):
        srv.kill()
        raise RuntimeError("servidor nao iniciou: " + first + srv.stderr.read())

    clients = [subprocess.Popen(
        [client, args.host, str(port), "--bench", str(client_duration),
         str(args.block), f"c{i + 1}"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for i in range(n)]

    # Amostra a CPU total do sistema aproximadamente na janela de medicao.
    time.sleep(args.warmup + 0.2)
    cpu_a = system_cpu_times()
    time.sleep(max(args.window - 0.4, 0.1))
    cpu_b = system_cpu_times()

    timeout = client_duration + 60
    cli_out = []
    for c in clients:
        out, err = c.communicate(timeout=timeout)
        cli_out.append((c.returncode, out, err))
    s_out, s_err = srv.communicate(timeout=timeout)

    result, per_client = None, []
    for line in s_out.splitlines():
        if line.startswith("RESULT"):
            result = dict(KV.findall(line))
        elif line.startswith("CLIENT"):
            per_client.append(dict(KV.findall(line)))
    if result is None:
        raise RuntimeError("sem RESULT do servidor:\n" + s_out + s_err)

    sent_total, client_ok = 0, True
    for rc, out, err in cli_out:
        m = [dict(KV.findall(l)) for l in out.splitlines() if l.startswith("RESULT")]
        if rc != 0 or not m or m[0].get("ok") != "1":
            client_ok = False
        if m:
            sent_total += int(m[0]["sent"])

    rates = [float(c["mbps"]) for c in per_client]
    jain = (sum(rates) ** 2) / (len(rates) * sum(r * r for r in rates)) if rates else 0

    row = {
        "clients": n,
        "mode": result["mode"],
        "block": args.block,
        "warmup_s": float(result["warmup"]),
        "window_s": float(result["window"]),
        "bytes_window": int(result["bytes_window"]),
        "mbps": float(result["mbps"]),
        "server_bytes_total": int(result["bytes_total"]),
        "clients_bytes_sent": sent_total,
        "bytes_match": int(sent_total == int(result["bytes_total"])),
        "server_cpu_cores": float(result["cpu_cores"]),
        "system_cpu_pct": round(cpu_util(cpu_a, cpu_b), 2),
        "per_client_min_mbps": min(rates),
        "per_client_max_mbps": max(rates),
        "jain_index": round(jain, 5),
        "valid": int(result["valid"] == "1" and client_ok
                     and sent_total == int(result["bytes_total"])),
    }
    return row, per_client


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", required=True, help="nome do experimento (arquivo CSV)")
    ap.add_argument("--clients", default="1-10")
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--warmup", type=float, default=2.0)
    ap.add_argument("--window", type=float, default=10.0)
    ap.add_argument("--tail", type=float, default=2.0,
                    help="segundos extras de transmissao apos a janela")
    ap.add_argument("--block", type=int, default=65536)
    ap.add_argument("--mode", choices=["bulk", "line"], default="bulk")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5100)
    ap.add_argument("--pause", type=float, default=2.0,
                    help="pausa entre rodadas (s)")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    counts = parse_range(args.clients)
    out_dir = os.path.join(ROOT, "results", "raw")
    os.makedirs(out_dir, exist_ok=True)
    runs_path = os.path.join(out_dir, f"{args.tag}.csv")
    cli_path = os.path.join(out_dir, f"{args.tag}_per_client.csv")

    rng = random.Random(args.seed)
    run_id = 0
    print(f"# {args.tag}: {platform.platform()} | energia={on_ac_power()} | "
          f"{dt.datetime.now().isoformat(timespec='seconds')}", flush=True)

    with open(runs_path, "w", newline="") as fr, open(cli_path, "w", newline="") as fc:
        wr = wc = None
        for rep in range(1, args.reps + 1):
            order = counts[:]
            rng.shuffle(order)
            for n in order:
                run_id += 1
                port = args.port + (run_id % 50)
                started = dt.datetime.now().isoformat(timespec="seconds")
                row, per_client = run_once(args, n, port)
                row = {"run": run_id, "rep": rep, "started": started,
                       "power": on_ac_power(), **row}
                if wr is None:
                    wr = csv.DictWriter(fr, fieldnames=list(row))
                    wr.writeheader()
                wr.writerow(row)
                fr.flush()
                for c in per_client:
                    crow = {"run": run_id, "rep": rep, "clients": n,
                            "index": c["index"], "name": c["name"],
                            "bytes_window": c["bytes_window"], "mbps": c["mbps"],
                            "bytes_total": c["bytes_total"]}
                    if wc is None:
                        wc = csv.DictWriter(fc, fieldnames=list(crow))
                        wc.writeheader()
                    wc.writerow(crow)
                fc.flush()
                print(f"run {run_id:3d} rep {rep} N={n:2d} "
                      f"{row['mbps']:10.1f} Mbps  cpu_srv={row['server_cpu_cores']:.2f} "
                      f"cpu_sys={row['system_cpu_pct']:5.1f}%  jain={row['jain_index']:.3f} "
                      f"valid={row['valid']} power={row['power']}", flush=True)
                time.sleep(args.pause)
    print(f"# fim {dt.datetime.now().isoformat(timespec='seconds')} -> {runs_path}")


if __name__ == "__main__":
    sys.exit(main())
