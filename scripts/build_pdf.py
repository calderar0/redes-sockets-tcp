#!/usr/bin/env python3
"""
Gera RELATORIO.pdf a partir de RELATORIO.md (HTML + Chrome/Edge headless).

Requisitos: pip install markdown pypdf ; Google Chrome ou Microsoft Edge.
    python scripts/build_pdf.py
"""
import datetime as dt
import os
import re
import shutil
import subprocess
import sys
import tempfile

import markdown
from pypdf import PdfReader, PdfWriter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "RELATORIO.md")
OUT = os.path.join(ROOT, "RELATORIO.pdf")
REPO = "https://github.com/calderar0/redes-sockets-tcp"
TITLE = "Capacidade máxima de transmissão de um chat TCP multithread"
AUTHOR = "Felipe Soares Calderaro"

BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "google-chrome", "chromium", "chromium-browser", "microsoft-edge",
]

FORMULA = """
<div class="formula">
  <span>Throughput agregado (Mbps)</span> <span>=</span>
  <span class="frac"><span class="num">(B<sub>1</sub> − B<sub>0</sub>) × 8</span>
  <span class="den">(t<sub>1</sub> − t<sub>0</sub>) × 10<sup>6</sup></span></span>
</div>
"""

CSS = """
@page { size: A4; margin: 18mm 16mm 18mm 16mm;
  @bottom-center { content: counter(page) " / " counter(pages);
                   font: 9pt 'Segoe UI', Arial, sans-serif; color: #666; } }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { font: 10.2pt/1.45 'Segoe UI', Calibri, Arial, sans-serif; color: #111; }
.cover { border-bottom: 2px solid #2a78d6; padding-bottom: 8px; margin-bottom: 14px; }
.cover .kicker { font-size: 9pt; letter-spacing: .08em; text-transform: uppercase; color: #2a78d6; font-weight: 600; }
.cover h1 { font-size: 20pt; margin: 4px 0 6px; border: 0; }
.cover .meta { font-size: 9.5pt; color: #444; }
h1 { font-size: 17pt; margin: 0 0 6px; }
h2 { font-size: 13.5pt; margin: 20px 0 6px; padding-bottom: 3px; border-bottom: 1px solid #ccc;
     break-after: avoid; }
h3 { font-size: 11.5pt; margin: 14px 0 4px; break-after: avoid; }
p, li { orphans: 3; widows: 3; }
a { color: #1a5fb4; text-decoration: none; word-break: break-all; }
table { border-collapse: collapse; width: 100%; margin: 6px 0 10px; font-size: 8.4pt; break-inside: avoid;
        }
tr { break-inside: avoid; }
th, td { border: 1px solid #d0d0d0; padding: 3px 5px; vertical-align: top; }
th { background: #eef3fb; font-weight: 600; }
tbody tr:nth-child(even) td { background: #fafafa; }
table.wide { font-size: 7.4pt; }
table.wide th, table.wide td { padding: 2px 3px; }
code { font: 8.6pt Consolas, 'Courier New', monospace; background: #f3f3f3; padding: 0 2px;
       border-radius: 2px; }
pre { background: #f5f5f5; border: 1px solid #e0e0e0; border-radius: 4px; padding: 6px 8px;
      white-space: pre-wrap; word-break: break-all; break-inside: avoid; }
pre code { background: none; padding: 0; font-size: 8.2pt; }
blockquote { margin: 8px 0; padding: 4px 10px; border-left: 3px solid #2a78d6;
             background: #f4f8fd; color: #333; }
img { display: block; width: 100%; max-width: 150mm; margin: 4px auto 8px; break-inside: avoid; }
hr { border: 0; border-top: 1px solid #ddd; margin: 14px 0; }
.formula { text-align: center; margin: 10px 0 12px; font-size: 11pt; }
.formula > span { vertical-align: middle; margin: 0 4px; }
.frac { display: inline-block; vertical-align: middle; text-align: center; }
.frac .num { display: block; border-bottom: 1px solid #111; padding: 0 6px 2px; }
.frac .den { display: block; padding: 2px 6px 0; }
.pagebreak { break-before: page; }
"""


def find_browser():
    for b in BROWSERS:
        p = b if os.path.isabs(b) else shutil.which(b)
        if p and os.path.exists(p):
            return p
    sys.exit("Chrome/Edge nao encontrado.")


def preprocess(md):
    # Titulo vira capa propria.
    md = re.sub(r"^# .*\n", "", md, count=1)
    # Formula LaTeX -> HTML (sem MathJax no PDF).
    md = re.sub(r"\$\$.*?\$\$", lambda m: FORMULA, md, flags=re.S)
    # Ancoras internas -> texto; links relativos -> GitHub.
    md = re.sub(r"\[(se[cç][aã]o \d+)\]\(#[^)]*\)", r"\1", md)
    md = re.sub(r"(?<!!)\[([^\]]+)\]\((?!https?://|#)([^)]+)\)",
                lambda m: f"[{m.group(1)}]({REPO}/blob/main/{m.group(2)})", md)
    # Secoes longas comecam em pagina nova para nao quebrar tabelas/graficos.
    for sec in ("## 7. Limites",):
        md = md.replace(sec, '<div class="pagebreak"></div>\n\n' + sec)
    return md


def to_html(md):
    body = markdown.markdown(md, extensions=["tables", "fenced_code", "sane_lists"])
    # Tabelas com muitas colunas usam fonte menor.
    body = re.sub(r"<table>(\s*<thead>\s*<tr>(?:\s*<th[^>]*>.*?</th>){9,})",
                  r'<table class="wide">\1', body, flags=re.S)
    today = dt.date.today().strftime("%d/%m/%Y")
    cover = (f'<div class="cover"><div class="kicker">Relatório técnico · Redes de Computadores'
             f'</div><h1>{TITLE}</h1><div class="meta">{AUTHOR} · {today} · '
             f'<a href="{REPO}">{REPO}</a></div></div>')
    return (f"<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'>"
            f"<title>{TITLE}</title><style>{CSS}</style></head>"
            f"<body>{cover}{body}</body></html>")


def main():
    with open(SRC, encoding="utf-8") as f:
        html = to_html(preprocess(f.read()))

    # O HTML fica na raiz do repositorio para resolver os caminhos das figuras.
    html_path = os.path.join(ROOT, ".relatorio_build.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    tmp_pdf = os.path.join(tempfile.mkdtemp(), "raw.pdf")
    profile = tempfile.mkdtemp()
    try:
        subprocess.run([find_browser(), "--headless=new", "--disable-gpu",
                        "--no-pdf-header-footer", f"--user-data-dir={profile}",
                        "--allow-file-access-from-files",
                        f"--print-to-pdf={tmp_pdf}",
                        "file:///" + html_path.replace("\\", "/")],
                       check=True, capture_output=True, timeout=120)
    finally:
        os.remove(html_path)
        shutil.rmtree(profile, ignore_errors=True)

    # Metadados do documento: somente titulo, autor e assunto; sem
    # identificacao de ferramenta (Creator/Producer) e sem XMP.
    reader = PdfReader(tmp_pdf)
    writer = PdfWriter()
    writer.append(reader)
    writer._info.get_object().clear()
    writer.add_metadata({"/Title": TITLE, "/Author": AUTHOR,
                         "/Subject": "Vazão agregada de um chat TCP com 1 a 10 clientes"})
    root = writer._root_object
    for key in ("/Metadata",):
        if key in root:
            del root[key]
    with open(OUT, "wb") as f:
        writer.write(f)
    print(f"{OUT}: {len(reader.pages)} paginas")


if __name__ == "__main__":
    main()
