#!/usr/bin/env python3
"""
Consolida os CSVs de results/raw/ em tabelas (Markdown/CSV) e graficos SVG.
Somente biblioteca padrao - os SVGs sao gerados diretamente.

    python scripts/analyze.py
"""
import csv
import glob
import math
import os
import statistics as st
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "results", "raw")
OUT = os.path.join(ROOT, "results")
FIG = os.path.join(OUT, "figures")

# t de Student bicaudal 95% por graus de liberdade
T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447,
       7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228}

# Paleta (slots categoricos 1-3 da paleta de referencia validada, modo claro)
C = {"s1": "#2a78d6", "s2": "#eb6834", "s3": "#1baf7a",
     "surface": "#fcfcfb", "ink": "#0b0b0b", "ink2": "#52514e",
     "grid": "#e4e3df", "axis": "#b9b8b2"}
IDLE_CPU = 28.3   # CPU do sistema medida por 10 s sem teste (navegador e apps abertos)
FONT = "font-family='Segoe UI, Helvetica, Arial, sans-serif'"


def load(tag):
    path = os.path.join(RAW, f"{tag}.csv")
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def summarize(rows, key="clients"):
    groups = defaultdict(list)
    for r in rows:
        groups[int(r[key])].append(r)
    out = []
    for k in sorted(groups):
        g = groups[k]
        v = [float(r["mbps"]) for r in g]
        n = len(v)
        mean = st.mean(v)
        sd = st.stdev(v) if n > 1 else 0.0
        ci = T95.get(n - 1, 1.96) * sd / math.sqrt(n) if n > 1 else 0.0
        out.append({
            key: k, "n": n, "mean": mean, "sd": sd, "ci95": ci,
            "cv_pct": 100 * sd / mean if mean else 0.0,
            "min": min(v), "max": max(v), "median": st.median(v),
            "per_client": mean / k if key == "clients" else mean,
            "srv_cpu": st.mean(float(r["server_cpu_cores"]) for r in g),
            "sys_cpu": st.mean(float(r["system_cpu_pct"]) for r in g),
            "jain": st.mean(float(r["jain_index"]) for r in g),
            "gb_window": st.mean(int(r["bytes_window"]) for r in g) / 1e9,
            "valid": sum(int(r["valid"]) for r in g),
            "match": sum(int(r["bytes_match"]) for r in g),
            "power": ",".join(sorted({r["power"] for r in g})),
        })
    return out


def fmt(x, d=1):
    s = f"{x:,.{d}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")   # pt-BR


# ------------------------------------------------------------------ SVG
class Chart:
    def __init__(self, title, subtitle, xlabel, ylabel, w=760, h=430):
        self.w, self.h = w, h
        self.l, self.r, self.t, self.b = 84, 24, 86, 58
        self.title, self.subtitle = title, subtitle
        self.xlabel, self.ylabel = xlabel, ylabel
        self.parts = []

    def setup(self, xmin, xmax, ymin, ymax, logy=False, xticks=None,
              yticks=None, xfmt=str, yfmt=None, xpad=0.5):
        self.xmin, self.xmax = xmin - xpad, xmax + xpad
        self.logy = logy
        self.ymin, self.ymax = ymin, ymax
        self.pw = self.w - self.l - self.r
        self.ph = self.h - self.t - self.b
        yfmt = yfmt or (lambda v: fmt(v, 0))
        p = self.parts
        for v in yticks:
            y = self.Y(v)
            p.append(f"<line x1='{self.l}' x2='{self.w - self.r}' y1='{y:.1f}' "
                     f"y2='{y:.1f}' stroke='{C['grid']}' stroke-width='1'/>")
            p.append(f"<text x='{self.l - 8}' y='{y + 4:.1f}' text-anchor='end' "
                     f"font-size='12' fill='{C['ink2']}' {FONT}>{yfmt(v)}</text>")
        base = self.h - self.b
        p.append(f"<line x1='{self.l}' x2='{self.w - self.r}' y1='{base}' y2='{base}' "
                 f"stroke='{C['axis']}' stroke-width='1'/>")
        for v in xticks:
            x = self.X(v)
            p.append(f"<text x='{x:.1f}' y='{base + 20}' text-anchor='middle' "
                     f"font-size='12' fill='{C['ink2']}' {FONT}>{xfmt(v)}</text>")
        p.append(f"<text x='{self.l + self.pw / 2}' y='{self.h - 14}' text-anchor='middle' "
                 f"font-size='13' fill='{C['ink2']}' {FONT}>{self.xlabel}</text>")
        p.append(f"<text transform='translate(20,{self.t + self.ph / 2}) rotate(-90)' "
                 f"text-anchor='middle' font-size='13' fill='{C['ink2']}' {FONT}>"
                 f"{self.ylabel}</text>")

    def X(self, v):
        return self.l + (v - self.xmin) / (self.xmax - self.xmin) * self.pw

    def Y(self, v):
        if self.logy:
            a, b, v = math.log10(self.ymin), math.log10(self.ymax), math.log10(v)
        else:
            a, b = self.ymin, self.ymax
        return self.t + self.ph - (v - a) / (b - a) * self.ph

    def bars(self, xs, ys, color, lo=None, hi=None, tips=None, width=0.62):
        bw = width * self.pw / (self.xmax - self.xmin)
        base = self.Y(self.ymin)
        for i, (x, y) in enumerate(zip(xs, ys)):
            cx, top = self.X(x), self.Y(y)
            h = base - top
            r = min(4, h / 2)
            # barra com cantos superiores arredondados (4px), ancorada na base
            d = (f"M{cx - bw / 2:.1f},{base:.1f} V{top + r:.1f} "
                 f"Q{cx - bw / 2:.1f},{top:.1f} {cx - bw / 2 + r:.1f},{top:.1f} "
                 f"H{cx + bw / 2 - r:.1f} Q{cx + bw / 2:.1f},{top:.1f} "
                 f"{cx + bw / 2:.1f},{top + r:.1f} V{base:.1f} Z")
            tip = f"<title>{tips[i]}</title>" if tips else ""
            self.parts.append(f"<path d='{d}' fill='{color}'>{tip}</path>")
            if lo is not None:
                y1, y2 = self.Y(lo[i]), self.Y(hi[i])
                self.parts.append(
                    f"<g stroke='{C['ink']}' stroke-width='1.5'>"
                    f"<line x1='{cx:.1f}' x2='{cx:.1f}' y1='{y1:.1f}' y2='{y2:.1f}'/>"
                    f"<line x1='{cx - 6:.1f}' x2='{cx + 6:.1f}' y1='{y1:.1f}' y2='{y1:.1f}'/>"
                    f"<line x1='{cx - 6:.1f}' x2='{cx + 6:.1f}' y1='{y2:.1f}' y2='{y2:.1f}'/>"
                    f"</g>")

    def line(self, xs, ys, color, tips=None, lo=None, hi=None):
        pts = " ".join(f"{self.X(x):.1f},{self.Y(y):.1f}" for x, y in zip(xs, ys))
        self.parts.append(f"<polyline points='{pts}' fill='none' stroke='{color}' "
                          f"stroke-width='2' stroke-linejoin='round'/>")
        for i, (x, y) in enumerate(zip(xs, ys)):
            if lo is not None:
                y1, y2 = self.Y(lo[i]), self.Y(hi[i])
                self.parts.append(
                    f"<line x1='{self.X(x):.1f}' x2='{self.X(x):.1f}' y1='{y1:.1f}' "
                    f"y2='{y2:.1f}' stroke='{color}' stroke-width='1.5'/>")
            tip = f"<title>{tips[i]}</title>" if tips else ""
            self.parts.append(
                f"<circle cx='{self.X(x):.1f}' cy='{self.Y(y):.1f}' r='4.5' fill='{color}' "
                f"stroke='{C['surface']}' stroke-width='2'>{tip}</circle>")

    def refline(self, y, text):
        yy = self.Y(y)
        self.parts.append(f"<line x1='{self.l}' x2='{self.w - self.r}' y1='{yy:.1f}' "
                          f"y2='{yy:.1f}' stroke='{C['ink2']}' stroke-width='1' "
                          f"stroke-dasharray='4 4'/>")
        self.parts.append(f"<text x='{self.w - self.r}' y='{yy + 16:.1f}' text-anchor='end' "
                          f"font-size='12' fill='{C['ink2']}' {FONT}>{text}</text>")

    def label(self, x, y, text, dy=-10, anchor="middle", color=None, size=12,
              weight="600"):
        self.parts.append(
            f"<text x='{self.X(x):.1f}' y='{self.Y(y) + dy:.1f}' text-anchor='{anchor}' "
            f"font-size='{size}' font-weight='{weight}' fill='{color or C['ink']}' "
            f"{FONT}>{text}</text>")

    def legend(self, items):
        x = self.l
        y = 66
        for name, color in items:
            self.parts.append(f"<rect x='{x}' y='{y - 9}' width='12' height='12' rx='3' "
                              f"fill='{color}'/>")
            self.parts.append(f"<text x='{x + 18}' y='{y + 1}' font-size='12' "
                              f"fill='{C['ink2']}' {FONT}>{name}</text>")
            x += 26 + 7.2 * len(name)

    def save(self, name):
        os.makedirs(FIG, exist_ok=True)
        head = (f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {self.w} {self.h}' "
                f"width='{self.w}' height='{self.h}' role='img' aria-label='{self.title}'>"
                f"<rect width='100%' height='100%' fill='{C['surface']}'/>"
                f"<text x='{self.l}' y='26' font-size='16' font-weight='600' "
                f"fill='{C['ink']}' {FONT}>{self.title}</text>"
                f"<text x='{self.l}' y='44' font-size='12' fill='{C['ink2']}' {FONT}>"
                f"{self.subtitle}</text>")
        with open(os.path.join(FIG, name), "w", encoding="utf-8") as f:
            f.write(head + "".join(self.parts) + "</svg>\n")


def nice_max(v, steps=(1, 2, 2.5, 5, 10)):
    mag = 10 ** math.floor(math.log10(v))
    for s in steps:
        if s * mag >= v:
            return s * mag
    return 10 * mag


# ------------------------------------------------------------------ main
def main():
    md = []
    main_rows = load("main_bulk_64k")
    line_rows = load("line_mode")
    S = summarize(main_rows)
    L = summarize(line_rows)

    # ---- tabela principal
    if S:
        with open(os.path.join(OUT, "summary_main.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(S[0]))
            w.writeheader()
            w.writerows(S)
        md.append("### Tabela principal (bulk, bloco 64 KiB)\n")
        md.append("| Clientes | Máximo (Mbps) | Média (Mbps) | Desvio-padrão | IC 95% (±) | CV (%) "
                  "| Mínimo (Mbps) | Média por cliente (Mbps) | CPU do sistema (%) "
                  "| CPU do servidor (núcleos) | Índice de Jain | Válidas |")
        md.append("|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for s in S:
            md.append(f"| {s['clients']} | **{fmt(s['max'])}** | {fmt(s['mean'])} | "
                      f"{fmt(s['sd'])} | {fmt(s['ci95'])} | {fmt(s['cv_pct'])} | "
                      f"{fmt(s['min'])} | {fmt(s['per_client'])} | {fmt(s['sys_cpu'])} | "
                      f"{fmt(s['srv_cpu'], 2)} | {fmt(s['jain'], 3)} | "
                      f"{s['valid']}/{s['n']} |")
        md.append("")

        xs = [s["clients"] for s in S]
        ymax = nice_max(max(s["max"] for s in S) * 1.08)
        step = ymax / 5

        # Fig 0 - minimo exigido: clientes x throughput agregado MAXIMO (Mbps)
        c = Chart("Throughput agregado máximo × clientes simultâneos",
                  "Maior valor entre 5 repetições · bloco 64 KiB · janela de 10 s · loopback",
                  "Clientes simultâneos", "Throughput agregado máximo (Mbps)")
        c.setup(1, 10, 0, ymax, xticks=xs, yticks=[i * step for i in range(6)])
        c.bars(xs, [s["max"] for s in S], C["s1"],
               tips=[f"{s['clients']} clientes: máximo {fmt(s['max'])} Mbps" for s in S])
        for s in S:
            c.label(s["clients"], s["max"], fmt(s["max"], 0), dy=-8, size=11)
        c.save("fig0_vazao_maxima.svg")

        # Fig 1 - throughput agregado medio
        c = Chart("Vazão agregada média recebida pelo servidor",
                  "Média de 5 repetições; barras de erro = mínimo–máximo · "
                  "bloco 64 KiB · janela de 10 s · loopback",
                  "Clientes simultâneos", "Vazão agregada média (Mbps)")
        c.setup(1, 10, 0, ymax, xticks=xs, yticks=[i * step for i in range(6)])
        c.bars(xs, [s["mean"] for s in S], C["s1"],
               lo=[s["min"] for s in S], hi=[s["max"] for s in S],
               tips=[f"{s['clients']} clientes: média {fmt(s['mean'])} Mbps, "
                     f"máx {fmt(s['max'])} Mbps" for s in S])
        peak = max(S, key=lambda s: s["mean"])
        for s in (S[0], peak, S[-1]):
            c.label(s["clients"], s["max"], fmt(s['mean'], 0), dy=-8)
        c.save("fig1_vazao_agregada.svg")

        # Fig 2 - vazao media por cliente
        pmax = nice_max(max(s["per_client"] for s in S) * 1.1)
        c = Chart("Vazão média por cliente",
                  "Vazão agregada média ÷ número de clientes",
                  "Clientes simultâneos", "Vazão por cliente (Mbps)")
        c.setup(1, 10, 0, pmax, xticks=xs, yticks=[i * pmax / 5 for i in range(6)])
        c.line(xs, [s["per_client"] for s in S], C["s1"],
               tips=[f"{s['clients']} clientes: {fmt(s['per_client'])} Mbps/cliente"
                     for s in S])
        for s in (S[0], S[-1]):
            c.label(s["clients"], s["per_client"], fmt(s['per_client'], 0),
                    dy=-12)
        c.save("fig2_vazao_por_cliente.svg")

        # Fig 3 - CPU do sistema
        c = Chart("Utilização total de CPU durante a janela de medição",
                  "16 CPUs lógicas compartilhadas por servidor e clientes (mesma máquina)",
                  "Clientes simultâneos", "CPU do sistema (%)")
        c.setup(1, 10, 0, 100, xticks=xs, yticks=[0, 20, 40, 60, 80, 100])
        c.refline(IDLE_CPU, f"máquina ociosa (sem teste) ≈ {fmt(IDLE_CPU, 0)}%")
        c.line(xs, [s["sys_cpu"] for s in S], C["s1"],
               tips=[f"{s['clients']} clientes: {fmt(s['sys_cpu'])}%" for s in S])
        for s in (S[0], S[-1]):
            c.label(s["clients"], s["sys_cpu"], f"{fmt(s['sys_cpu'], 0)}%", dy=-12)
        c.save("fig3_cpu_sistema.svg")

    # ---- modo line (caminho de leitura original)
    if L:
        md.append("### Modo line (leitura de 1 byte por recv(), como o chat original)\n")
        md.append("| Clientes | Máximo (Mbps) | Média (Mbps) | Desvio-padrão | Média por cliente (Mbps) "
                  "| CPU do sistema (%) | CPU do servidor (núcleos) | Válidas |")
        md.append("|---:|---:|---:|---:|---:|---:|---:|---:|")
        for s in L:
            md.append(f"| {s['clients']} | **{fmt(s['max'])}** | {fmt(s['mean'])} | "
                      f"{fmt(s['sd'])} | {fmt(s['per_client'])} | {fmt(s['sys_cpu'])} | "
                      f"{fmt(s['srv_cpu'], 2)} | {s['valid']}/{s['n']} |")
        md.append("")
        if S:
            xs = [s["clients"] for s in S]
            lo = 10 ** math.floor(math.log10(min(s["min"] for s in L)))
            hi = 10 ** math.ceil(math.log10(max(s["max"] for s in S)))
            ticks = [10 ** e for e in range(int(math.log10(lo)), int(math.log10(hi)) + 1)]
            c = Chart("Leitura em blocos × leitura byte a byte (escala log)",
                      "bulk: recv() de 128 KiB · line: 1 byte por recv(), "
                      "como o read_line() do chat original",
                      "Clientes simultâneos", "Vazão agregada (Mbps, log)")
            c.setup(1, 10, lo, hi, logy=True, xticks=xs, yticks=ticks)
            c.line(xs, [s["mean"] for s in S], C["s1"],
                   tips=[f"bulk, {s['clients']}: {fmt(s['mean'])} Mbps" for s in S])
            c.line([s["clients"] for s in L], [s["mean"] for s in L], C["s2"],
                   tips=[f"line, {s['clients']}: {fmt(s['mean'])} Mbps" for s in L])
            c.label(10, S[-1]["mean"], "bulk", dy=-12, anchor="end")
            c.label(10, L[-1]["mean"], "line", dy=-12, anchor="end")
            c.legend([("bulk (recv 128 KiB)", C["s1"]), ("line (recv 1 byte)", C["s2"])])
            c.save("fig4_bulk_vs_line.svg")

    # ---- tamanho de bloco
    blocks = []
    for path in sorted(glob.glob(os.path.join(RAW, "block_*.csv"))):
        if path.endswith("_per_client.csv"):
            continue
        b = int(os.path.basename(path)[6:-4])
        for s in summarize(load(f"block_{b}")):
            blocks.append({"block": b, **s})
    if blocks:
        blocks.sort(key=lambda r: (r["clients"], r["block"]))
        md.append("### Influência do tamanho do bloco (bulk, janela de 5 s, 3 repetições)\n")
        md.append("| Bloco (bytes) | Clientes | Máximo (Mbps) | Média (Mbps) | Desvio-padrão | CPU do sistema (%) |")
        md.append("|---:|---:|---:|---:|---:|---:|")
        for r in blocks:
            md.append(f"| {r['block']:,}".replace(",", ".") + f" | {r['clients']} | "
                      f"**{fmt(r['max'])}** | {fmt(r['mean'])} | {fmt(r['sd'])} | "
                      f"{fmt(r['sys_cpu'])} |")
        md.append("")
        bs = sorted({r["block"] for r in blocks})
        ymax = nice_max(max(r["max"] for r in blocks) * 1.08)
        c = Chart("Vazão agregada × tamanho do bloco enviado",
                  "Média de 3 repetições · janela de 5 s",
                  "Tamanho do bloco por send() (bytes, escala log₂)",
                  "Vazão agregada (Mbps)")
        lx = [math.log2(b) for b in bs]
        c.setup(lx[0], lx[-1], 0, ymax, xticks=lx,
                yticks=[i * ymax / 5 for i in range(6)],
                xfmt=lambda v: (f"{int(2 ** v) // 1024} KiB"), xpad=0.6)
        for n, col in ((1, C["s1"]), (10, C["s2"])):
            rr = [r for r in blocks if r["clients"] == n]
            c.line([math.log2(r["block"]) for r in rr], [r["mean"] for r in rr], col,
                    tips=[f"{n} cliente(s), bloco {r['block']}: {fmt(r['mean'])} Mbps"
                          for r in rr])
        c.legend([("1 cliente", C["s1"]), ("10 clientes", C["s2"])])
        c.save("fig5_tamanho_bloco.svg")

    with open(os.path.join(OUT, "tabelas.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
