#!/usr/bin/env node
/*
 * Gera RELATORIO.docx a partir de RELATORIO.md.
 *
 *   npm install docx@9 marked@15
 *   node scripts/build_docx.js
 *
 * Os graficos usam os PNGs de results/figures/ (versoes rasterizadas dos SVGs).
 */
const fs = require("fs");
const path = require("path");
const { marked } = require("marked");
const JSZip = require("jszip");      // dependencia do pacote docx
const {
  Document, Packer, Paragraph, TextRun, ExternalHyperlink, ImageRun, Table,
  TableRow, TableCell, WidthType, ShadingType, BorderStyle, AlignmentType,
  HeadingLevel, LevelFormat, Footer, PageNumber, Math: OMath, MathRun,
  MathFraction, MathSubScript, MathSuperScript, VerticalAlign,
} = require("docx");

const ROOT = path.resolve(__dirname, "..");
const SRC = path.join(ROOT, "RELATORIO.md");
const OUT = path.join(ROOT, "RELATORIO.docx");
const REPO = "https://github.com/calderar0/redes-sockets-tcp";
const TITLE = "Capacidade máxima de transmissão de um chat TCP multithread";
const AUTHOR = "Felipe Soares Calderaro";
const KICKER = "RELATÓRIO TÉCNICO · REDES DE COMPUTADORES";

const FONT = "Calibri";
const MONO = "Consolas";
const BLUE = "2A78D6";
const LINK = "1A5FB4";
const PAGE_W = 11906;          // A4 em DXA
const MARGIN = 1134;           // 2 cm
const CONTENT_W = PAGE_W - 2 * MARGIN;
const BORDER = { style: BorderStyle.SINGLE, size: 4, color: "C8C8C8" };
const CELL_BORDERS = { top: BORDER, bottom: BORDER, left: BORDER, right: BORDER };

const unescape = (s) => s
  .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"')
  .replace(/&#39;/g, "'").replace(/&amp;/g, "&");

// ------------------------------------------------------------ pre-processamento
function preprocess(md) {
  md = md.replace(/^# .*\n/, "");                                  // titulo -> capa
  md = md.replace(/\$\$[\s\S]*?\$\$/g, "@@FORMULA@@");
  md = md.replace(/\[(se[cç][aã]o \d+)\]\(#[^)]*\)/g, "$1");
  md = md.replace(/(?<!!)\[([^\]]+)\]\((?!https?:\/\/|#)([^)]+)\)/g,
                  (_, t, u) => `[${t}](${REPO}/blob/main/${u})`);
  md = md.replace(/(!\[[^\]]*\]\(results\/figures\/[^)]+)\.svg\)/g, "$1.png)");
  return md;
}

// ------------------------------------------------------------ inline
function inline(tokens, fmt = {}) {
  const out = [];
  for (const t of tokens || []) {
    switch (t.type) {
      case "strong":
        out.push(...inline(t.tokens, { ...fmt, bold: true })); break;
      case "em":
        out.push(...inline(t.tokens, { ...fmt, italics: true })); break;
      case "codespan":
        out.push(new TextRun({ text: unescape(t.text), font: MONO,
                               size: fmt.size ? fmt.size - 2 : 18, bold: fmt.bold,
                               shading: { type: ShadingType.CLEAR, fill: "F1F1F1", color: "auto" } }));
        break;
      case "link":
        out.push(new ExternalHyperlink({
          link: t.href,
          children: inline(t.tokens, { ...fmt, link: true }),
        }));
        break;
      case "br":
        out.push(new TextRun({ break: 1 })); break;
      case "text":
      case "escape":
        if (t.tokens && t.tokens.length) out.push(...inline(t.tokens, fmt));
        else out.push(run(unescape(t.text), fmt));
        break;
      default:
        if (t.text) out.push(run(unescape(t.text), fmt));
    }
  }
  return out;
}

function run(text, fmt) {
  return new TextRun({
    text, bold: fmt.bold, italics: fmt.italics, size: fmt.size,
    color: fmt.link ? LINK : fmt.color, font: fmt.font,
    underline: fmt.link ? {} : undefined,
  });
}

// ------------------------------------------------------------ blocos
function formula() {
  const sub = (b, s) => new MathSubScript({ children: [new MathRun(b)], subScript: [new MathRun(s)] });
  return new Paragraph({
    alignment: AlignmentType.CENTER, spacing: { before: 160, after: 200 },
    children: [new OMath({ children: [
      new MathRun("Throughput agregado (Mbps) = "),
      new MathFraction({
        numerator: [new MathRun("("), sub("B", "1"), new MathRun(" − "), sub("B", "0"),
                    new MathRun(") × 8")],
        denominator: [new MathRun("("), sub("t", "1"), new MathRun(" − "), sub("t", "0"),
                      new MathRun(") × "),
                      new MathSuperScript({ children: [new MathRun("10")],
                                            superScript: [new MathRun("6")] })],
      }),
    ] })],
  });
}

function image(tok) {
  const file = path.join(ROOT, tok.href);
  const w = 600, h = Math.round(600 * 430 / 760);
  return new Paragraph({
    alignment: AlignmentType.CENTER, spacing: { before: 120, after: 200 },
    children: [new ImageRun({
      type: "png", data: fs.readFileSync(file), transformation: { width: w, height: h },
      altText: { title: tok.text, description: tok.text, name: path.basename(file) },
    })],
  });
}

function paragraph(tok) {
  if (tok.text.trim() === "@@FORMULA@@") return [formula()];
  const toks = tok.tokens.filter((t) => !(t.type === "text" && !t.text.trim()));
  if (toks.length === 1 && toks[0].type === "image") return [image(toks[0])];
  return [new Paragraph({ spacing: { after: 120 }, children: inline(tok.tokens) })];
}

function textLen(cell) { return unescape(cell.text).length; }

function table(tok) {
  const ncols = tok.header.length;
  const wide = ncols >= 9;
  const size = wide ? 14 : 16;
  // Larguras proporcionais ao conteudo (limitadas), somando CONTENT_W.
  const weights = tok.header.map((h, i) => {
    const lens = [textLen(h) * (wide ? 0.55 : 1), ...tok.rows.map((r) => textLen(r[i]))];
    const m = Math.max(...lens);
    return Math.min(Math.max(m, wide ? 6 : 8), 70);
  });
  const total = weights.reduce((a, b) => a + b, 0);
  const widths = weights.map((w) => Math.floor((w / total) * CONTENT_W));
  widths[widths.length - 1] += CONTENT_W - widths.reduce((a, b) => a + b, 0);
  const align = (i) => (tok.align[i] === "right" ? AlignmentType.RIGHT
                       : tok.align[i] === "center" ? AlignmentType.CENTER : AlignmentType.LEFT);
  const keep = tok.rows.length <= 16;

  const mkRow = (cells, header, last) => new TableRow({
    tableHeader: header, cantSplit: true,
    children: cells.map((c, i) => new TableCell({
      width: { size: widths[i], type: WidthType.DXA },
      borders: CELL_BORDERS,
      verticalAlign: VerticalAlign.CENTER,
      margins: { top: 30, bottom: 30, left: wide ? 50 : 80, right: wide ? 50 : 80 },
      shading: header ? { type: ShadingType.CLEAR, fill: "EEF3FB", color: "auto" } : undefined,
      children: [new Paragraph({
        alignment: header ? (align(i) === AlignmentType.LEFT ? AlignmentType.LEFT : align(i)) : align(i),
        keepNext: keep && !last,
        spacing: { before: 0, after: 0 },
        children: inline(c.tokens, { size, bold: header || undefined }),
      })],
    })),
  });

  const rows = [mkRow(tok.header, true, tok.rows.length === 0),
                ...tok.rows.map((r, k) => mkRow(r, false, k === tok.rows.length - 1))];
  return [
    new Table({ width: { size: CONTENT_W, type: WidthType.DXA }, columnWidths: widths, rows }),
    new Paragraph({ spacing: { after: 120 }, children: [] }),
  ];
}

let listInstance = 0;
function list(tok) {
  const inst = ++listInstance;
  const out = [];
  for (const item of tok.items) {
    const children = [];
    for (const t of item.tokens) {
      if (t.type === "text" || t.type === "paragraph") children.push(...inline(t.tokens || [t]));
    }
    out.push(new Paragraph({
      numbering: { reference: tok.ordered ? "numbers" : "bullets", level: 0, instance: inst },
      spacing: { after: 60 }, children,
    }));
  }
  out.push(new Paragraph({ spacing: { after: 60 }, children: [] }));
  return out;
}

function code(tok) {
  const lines = tok.text.split("\n");
  return lines.map((l, i) => new Paragraph({
    keepNext: i < lines.length - 1, keepLines: true,
    spacing: { before: i === 0 ? 80 : 0, after: i === lines.length - 1 ? 160 : 0, line: 260 },
    shading: { type: ShadingType.CLEAR, fill: "F5F5F5", color: "auto" },
    indent: { left: 120, right: 120 },
    children: [new TextRun({ text: l || " ", font: MONO, size: 16 })],
  }));
}

function blockquote(tok) {
  return tok.tokens.filter((t) => t.type === "paragraph").map((p) => new Paragraph({
    spacing: { before: 120, after: 160 },
    border: { left: { style: BorderStyle.SINGLE, size: 18, color: BLUE, space: 8 } },
    shading: { type: ShadingType.CLEAR, fill: "F4F8FD", color: "auto" },
    indent: { left: 200 },
    children: inline(p.tokens),
  }));
}

function blocks(tokens) {
  const out = [];
  for (const t of tokens) {
    switch (t.type) {
      case "heading":
        out.push(new Paragraph({
          heading: t.depth <= 2 ? HeadingLevel.HEADING_1 : HeadingLevel.HEADING_2,
          children: inline(t.tokens),
        }));
        break;
      case "paragraph": out.push(...paragraph(t)); break;
      case "table": out.push(...table(t)); break;
      case "list": out.push(...list(t)); break;
      case "code": out.push(...code(t)); break;
      case "blockquote": out.push(...blockquote(t)); break;
      default: break;            // hr, space, html
    }
  }
  return out;
}

// ------------------------------------------------------------ documento
function cover() {
  const today = new Date().toLocaleDateString("pt-BR");
  return [
    new Paragraph({ spacing: { after: 60 },
      children: [new TextRun({ text: KICKER, bold: true, color: BLUE, size: 18 })] }),
    new Paragraph({ heading: HeadingLevel.TITLE, children: [new TextRun(TITLE)] }),
    new Paragraph({
      spacing: { after: 240 },
      border: { bottom: { style: BorderStyle.SINGLE, size: 12, color: BLUE, space: 6 } },
      children: [
        new TextRun({ text: `${AUTHOR} · ${today} · `, color: "444444", size: 19 }),
        new ExternalHyperlink({ link: REPO, children: [
          new TextRun({ text: REPO, color: LINK, underline: {}, size: 19 })] }),
      ],
    }),
  ];
}

function main() {
  const md = preprocess(fs.readFileSync(SRC, "utf8"));
  const body = blocks(marked.lexer(md));

  const doc = new Document({
    creator: AUTHOR, lastModifiedBy: AUTHOR, title: TITLE,
    subject: "Vazão agregada de um chat TCP com 1 a 10 clientes",
    styles: {
      default: { document: { run: { font: FONT, size: 21 }, paragraph: { spacing: { line: 276 } } } },
      paragraphStyles: [
        { id: "Title", name: "Title", basedOn: "Normal", next: "Normal",
          run: { font: FONT, size: 44, bold: true, color: "111111" },
          paragraph: { spacing: { before: 0, after: 80 }, keepNext: true } },
        { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
          run: { font: FONT, size: 30, bold: true, color: "111111" },
          paragraph: { spacing: { before: 360, after: 120 }, keepNext: true, outlineLevel: 0 } },
        { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
          run: { font: FONT, size: 24, bold: true, color: "111111" },
          paragraph: { spacing: { before: 240, after: 100 }, keepNext: true, outlineLevel: 1 } },
      ],
    },
    numbering: {
      config: [
        { reference: "bullets", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•",
          alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 540, hanging: 270 } } } }] },
        { reference: "numbers", levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.",
          alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 540, hanging: 300 } } } }] },
      ],
    },
    sections: [{
      properties: {
        page: { size: { width: PAGE_W, height: 16838 },
                margin: { top: MARGIN, bottom: MARGIN, left: MARGIN, right: MARGIN } },
      },
      footers: {
        default: new Footer({ children: [new Paragraph({
          alignment: AlignmentType.CENTER,
          children: [new TextRun({ children: [PageNumber.CURRENT, " / ", PageNumber.TOTAL_PAGES],
                                   size: 17, color: "666666" })],
        })] }),
      },
      children: [...cover(), ...body],
    }],
  });

  Packer.toBuffer(doc).then(async (raw) => {
    // Remove as entradas de pasta do ZIP: o Word recusa arquivos com elas
    // quando aberto por servicos de coedicao na nuvem.
    const zip = await JSZip.loadAsync(raw);
    for (const name of Object.keys(zip.files))
      if (zip.files[name].dir) delete zip.files[name];
    const buf = await zip.generateAsync({ type: "nodebuffer", compression: "DEFLATE" });
    fs.writeFileSync(OUT, buf);
    console.log(`${OUT}: ${buf.length} bytes`);
  });
}

main();
