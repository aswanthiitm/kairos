#!/usr/bin/env python3
"""Render a KAIRÓS markdown document to a print-ready PDF."""
import html, os, re, subprocess, sys

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
FONTDIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "app", "static", "fonts")


def inline(t):
    t = html.escape(t)
    t = re.sub(r'`([^`]+)`', r'<code>\1</code>', t)
    t = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', t)
    t = re.sub(r'(?<![\w*])\*([^*\n]+)\*(?![\w*])', r'<em>\1</em>', t)
    t = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', t)
    return t


def convert(src, dst, title):
    lines, out, i = open(src, encoding="utf8").read().split("\n"), [], 0
    while i < len(lines):
        l = lines[i]
        if l.startswith("```"):
            b = []; i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                b.append(html.escape(lines[i])); i += 1
            out.append("<pre>%s</pre>" % "\n".join(b)); i += 1; continue
        if l.startswith("|") and i + 1 < len(lines) and re.match(r'^\|[\s:|-]+\|$', lines[i+1]):
            hdr = [c.strip() for c in l.strip("|").split("|")]; i += 2; rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip("|").split("|")]); i += 1
            out.append("<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table>" % (
                "".join("<th>%s</th>" % inline(c) for c in hdr),
                "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % inline(c) for c in r)
                        for r in rows))); continue
        m = re.match(r'^(#{1,4}) (.*)', l)
        if m:
            n = len(m.group(1)); out.append("<h%d>%s</h%d>" % (n, inline(m.group(2)), n)); i += 1; continue
        if l.strip() in ("---", "***"): out.append("<hr>"); i += 1; continue
        if l.startswith("> "):
            q = []
            while i < len(lines) and lines[i].startswith("> "):
                q.append(lines[i][2:]); i += 1
            out.append("<blockquote>%s</blockquote>" % inline(" ".join(q))); continue
        if re.match(r'^\d+\. ', l) or l.startswith("- "):
            tag = "ol" if re.match(r'^\d+\. ', l) else "ul"; items = []
            while i < len(lines) and (re.match(r'^\d+\. ', lines[i]) or lines[i].startswith("- ")
                                      or lines[i].startswith("  ")):
                if lines[i].startswith("  ") and items: items[-1] += " " + lines[i].strip()
                else: items.append(re.sub(r'^(\d+\. |- )', '', lines[i]))
                i += 1
            out.append("<%s>%s</%s>" % (tag, "".join("<li>%s</li>" % inline(x) for x in items), tag))
            continue
        if l.strip():
            p = [l]; i += 1
            while i < len(lines) and lines[i].strip() and not re.match(r'^(#|\||```|---|> |\d+\. |- )', lines[i]):
                p.append(lines[i]); i += 1
            out.append("<p>%s</p>" % inline(" ".join(p))); continue
        i += 1

    faces = "".join(
        "@font-face{font-family:Nohemi;font-weight:%d;src:url('file://%s/Nohemi-%s.woff2') format('woff2')}"
        % (w, FONTDIR, n) for w, n in ((400, "Regular"), (500, "Medium"),
                                       (600, "SemiBold"), (700, "Bold")))
    css = faces + """
@page{size:A4;margin:16mm 15mm 17mm}
*{box-sizing:border-box}
body{font-family:"Source Serif 4",Georgia,serif;font-size:9.7pt;line-height:1.52;color:#191B1A;margin:0}
h1{font-family:Nohemi,Helvetica,sans-serif;font-size:23pt;line-height:1.1;margin:0 0 5pt;
  letter-spacing:.04em;font-weight:700;border-bottom:2.5pt solid #191B1A;padding-bottom:8pt}
h2{font-family:Nohemi,Helvetica,sans-serif;font-size:13pt;font-weight:600;margin:18pt 0 5pt;
  padding-bottom:3pt;border-bottom:.6pt solid #C9C5BC;page-break-after:avoid}
h3{font-family:Nohemi,Helvetica,sans-serif;font-size:10.6pt;font-weight:600;margin:12pt 0 3pt;
  color:#8A4A32;page-break-after:avoid}
h4{font-family:"JetBrains Mono",monospace;font-size:8pt;letter-spacing:.1em;text-transform:uppercase;
  color:#7C837F;margin:10pt 0 3pt}
p{margin:0 0 6pt;orphans:3;widows:3}
strong{font-weight:600}
a{color:#8A4A32;text-decoration:none}
code{font-family:"JetBrains Mono",Menlo,monospace;font-size:8.3pt;background:#EDEBE6;
  border:.5pt solid #DBD7CF;padding:0 3px}
pre{font-family:"JetBrains Mono",Menlo,monospace;font-size:7.9pt;line-height:1.45;background:#F2F0EC;
  border:.6pt solid #C9C5BC;border-left:2.5pt solid #8A4A32;padding:7pt 9pt;margin:6pt 0;
  white-space:pre-wrap;page-break-inside:avoid}
blockquote{margin:7pt 0;padding:7pt 11pt;background:#EFE4DF;border-left:2.5pt solid #8A4A32;
  font-style:italic;page-break-inside:avoid}
table{width:100%;border-collapse:collapse;margin:6pt 0;font-family:Nohemi,Helvetica,sans-serif;
  font-size:8.3pt;page-break-inside:avoid}
th{text-align:left;background:#E3E0DA;border-bottom:.8pt solid #B0ABA1;padding:4pt 6pt;
  font-weight:600;font-size:7.4pt;text-transform:uppercase;letter-spacing:.06em;color:#4B514E}
td{border-bottom:.5pt solid #DBD7CF;padding:4pt 6pt;vertical-align:top;line-height:1.38}
ul,ol{margin:0 0 6pt 15pt;padding:0}
li{margin-bottom:3pt}
hr{border:0;border-top:.6pt solid #C9C5BC;margin:12pt 0}
"""
    doc = ('<!doctype html><meta charset="utf-8"><title>%s</title>'
           '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
           'family=JetBrains+Mono:wght@400;500&family=Source+Serif+4:ital,opsz,wght@0,8..60,400;'
           '0,8..60,600;1,8..60,400&display=swap"><style>%s</style>%s'
           % (html.escape(title), css, "\n".join(out)))
    tmp = "/tmp/_kairos_doc.html"
    open(tmp, "w", encoding="utf8").write(doc)
    subprocess.run([CHROME, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                    "--print-to-pdf=" + dst, "--virtual-time-budget=9000",
                    "file://" + tmp], capture_output=True)
    return os.path.getsize(dst)


if __name__ == "__main__":
    src, dst, title = sys.argv[1], sys.argv[2], sys.argv[3]
    print("%s -> %s  (%.0f KB)" % (src, dst, convert(src, dst, title) / 1024))
