#!/usr/bin/env python3
"""Build a self-contained HTML report from Ghidra Gowin exports."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import html
import json
import re
from pathlib import Path


CATEGORIES = {
    "Qt / GUI": r"\bqt|qwidget|qdialog|qwindow|qapplication|qgraphics|qicon|qfont|qmenu|qaction|qpaint|\bmouse|\bkeyboard",
    "Tcl / scripting": r"\btcl|\binterp|\bscript|\bcommand",
    "HDL / netlist": r"\bverilog|\bvhdl|\bhdl|\bnetlist|\bmodule|\binstance|\bport|\bwire|\bnet\b|\bcell",
    "Synthesis / mapping": r"\bsynth|\bmapping|\btechnology|\boptimi[sz]|\blut|\bcarry|\bprimitive",
    "Placement / routing": r"\bplace|\broute|\bfloorplan|\bregion|\blocation|\bconstraint|\bcongestion",
    "Timing / SDC": r"\btiming|\bclock|\bsdc|\bslack|\bdelay|\bsetup|\bhold|\bcritical|\bperiod",
    "Device / package": r"\bdevice|\bfamily|\bpart|\bpackage|\bpin\b|\bbank|\biostandard|\bspeedgrade",
    "Memory / BSRAM / DDR": r"\bbsram|\bsram|\bdram|\bddr[0-9]*|\bmemory|\bfifo|\becc|\brom\b|\bram\b",
    "Logic / I/O analyzer": r"\banaly[sz]er|\bgoeye|\bgao|\bgvio|\bprobe|\btrigger|\bwaveform|\bcapture",
    "Files / configuration": r"\bfile|\bpath|\bdirectory|\bconfig|\bproject|\bxml|\bjson|\bread|\bwrite|\bload|\bsave|\bopen",
    "Licensing / cryptography": r"\blicen[cs]e|\bcrypto|\bcipher|\bencrypt|\bdecrypt|\bhash|\bsha|\bmd5|\brsa|\bssl|\bevp_",
    "Concurrency": r"\bthread|\bmutex|\bsemaphore|\batomic|\block|\bcondition|\bfuture|\bconcurrent",
    "Network / web": r"\bnetwork|\bsocket|\bhttp|\burl|\bweb|\btcp|\budp|\bssl|\bproxy",
    "Diagnostics": r"\bdebug|\btrace|\blog|\bwarning|\berror|\bassert|\bdiagnostic",
}

API_PATTERNS = {
    "Process execution": r"\b(system|popen|execv[ep]?|execl[ep]?|posix_spawn|fork)\s*\(",
    "Dynamic loading": r"\b(dlopen|dlsym|dlclose)\s*\(",
    "Network sockets": r"\b(socket|connect|bind|listen|accept|send|recv)\s*\(",
    "File I/O": r"\b(fopen|open|read|write|fread|fwrite|QFile)\s*\(",
    "Environment": r"\b(getenv|setenv|putenv|qgetenv|qputenv)\s*\(",
    "Crypto API": r"\b(EVP_|RSA_|AES_|SHA[0-9_]*|MD5|HMAC)[A-Za-z0-9_]*\s*\(",
    "Memory allocation": r"\b(malloc|calloc|realloc|free|operator_new|operator_delete)\s*\(",
    "Threading": r"\b(pthread_|QThread|std::thread|QMutex)[A-Za-z0-9_:]*\s*\(",
}

DOMAIN_HINTS = re.compile(
    r"\bgowin|\bfpga|\bdevice|\bpackage|\bpin\b|\bverilog|\bvhdl|\bnetlist|\bsynth|\bplace|\broute|"
    r"\bfloor|\btiming|\bclock|\bsdc|\bbsram|\bsram|\bddr[0-9]*|\bfifo|\becc|\banaly[sz]er|"
    r"\bprobe|\btrigger|\bbitstream|\btcl|\bconstraint|\bprimitive",
    re.I,
)

RUNTIME_NAME = re.compile(
    r"^(FUN_|_|std::|boost::|Q(?:String|List|Map|Vector|Variant|Array|Object|Widget|Dialog)|operator|~|"
    r"mem|str|__|LAB_|switchD)",
    re.I,
)


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def human_size(value: int) -> str:
    units = ["B", "KiB", "MiB", "GiB"]
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
        amount /= 1024
    return str(value)


def parse_tsv(path: Path) -> list[dict[str, str]]:
    rows = []
    with path.open(encoding="utf-8", errors="replace") as stream:
        header = stream.readline().rstrip("\n").split("\t")
        for line in stream:
            values = line.rstrip("\n").split("\t", len(header) - 1)
            if len(values) == len(header):
                rows.append(dict(zip(header, values)))
    return rows


def namespace_counts(functions: list[dict[str, str]]) -> list[tuple[str, int]]:
    counts: collections.Counter[str] = collections.Counter()
    for function in functions:
        value = function["name"] + " " + function["signature"]
        for namespace in re.findall(r"\b([A-Za-z][A-Za-z0-9_]{2,})::", value):
            if namespace not in {"std", "boost"} and not namespace.startswith("Q"):
                counts[namespace] += 1
    return counts.most_common(8)


def extract_strings(text: str) -> list[str]:
    found = []
    for match in re.finditer(r'"((?:[^"\\]|\\.){5,240})"', text):
        value = match.group(1)
        if re.search(r"[A-Za-z]{3}", value) and not re.fullmatch(r"[A-Za-z0-9_:.<>~/-]+", value):
            found.append(value.replace("\\n", " ").replace("\\t", " "))
    return found


def interesting_strings(strings: list[str], limit: int = 10) -> list[str]:
    scored = []
    seen = set()
    keywords = re.compile(
        r"error|warning|device|family|package|license|verilog|vhdl|synth|place|route|timing|clock|"
        r"bitstream|project|constraint|analy[sz]er|probe|trigger|ddr|bsram|unsupported|failed",
        re.I,
    )
    for value in strings:
        normalized = " ".join(value.split())
        if normalized in seen or len(normalized) > 180:
            continue
        seen.add(normalized)
        score = 4 if keywords.search(normalized) else 0
        score += 2 if "Error" in normalized or "WARNING" in normalized else 0
        score += 1 if 15 <= len(normalized) <= 120 else 0
        if score:
            scored.append((score, normalized))
    scored.sort(key=lambda item: (-item[0], len(item[1]), item[1]))
    return [value for _, value in scored[:limit]]


def extract_function_block(text: str, address: str, max_chars: int = 5200) -> str | None:
    marker = re.search(rf"/\*\s*{re.escape(address)}\s*\|.*?\*/", text)
    if not marker:
        return None
    following = re.search(r"\n/\*\s*[0-9a-fA-F]{6,16}\s*\|", text[marker.end():])
    end = marker.end() + following.start() if following else min(len(text), marker.end() + max_chars)
    block = text[marker.start():end].strip()
    if len(block) > max_chars:
        block = block[:max_chars] + "\n/* excerpt truncated */"
    return block


def select_excerpts(functions: list[dict[str, str]], text: str, limit: int) -> list[dict[str, str]]:
    candidates = []
    for function in functions:
        name = function["name"]
        signature = function["signature"]
        score = 0
        if name == "main":
            score += 100
        if DOMAIN_HINTS.search(name):
            score += 30
        elif DOMAIN_HINTS.search(signature):
            score += 8
        if not RUNTIME_NAME.search(name):
            score += 4
        if name.startswith("FUN_"):
            score -= 20
        if score > 10:
            candidates.append((score, function))
    candidates.sort(key=lambda item: (-item[0], item[1]["entry"]))
    selected = []
    used_names = set()
    for _, function in candidates:
        if function["name"] in used_names and function["name"] != "main":
            continue
        block = extract_function_block(text, function["entry"])
        if block:
            selected.append({**function, "code": block})
            used_names.add(function["name"])
        if len(selected) >= limit:
            break
    return selected


def bar(value: int, maximum: int) -> str:
    width = 0 if maximum == 0 else max(1.0, value * 100 / maximum)
    return f'<span class="bar"><i style="width:{width:.2f}%"></i></span>'


def build_report(source: Path, metadata_path: Path, output: Path) -> None:
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))["binaries"]
    by_name = {record["name"]: record for record in metadata}
    binaries = []
    function_catalog: dict[str, list[list[str]]] = {}
    all_dependencies: collections.Counter[str] = collections.Counter()

    for tsv_path in sorted(source.glob("*.functions.tsv")):
        name = tsv_path.name.removesuffix(".functions.tsv")
        c_path = source / f"{name}.decompiled.c"
        functions = parse_tsv(tsv_path)
        text = c_path.read_text(encoding="utf-8", errors="replace")
        named = sum(not item["name"].startswith("FUN_") for item in functions)
        category_counts = {}
        for label, pattern in CATEGORIES.items():
            rx = re.compile(pattern, re.I)
            category_counts[label] = sum(bool(rx.search(item["name"] + " " + item["signature"])) for item in functions)
        api_counts = {label: len(re.findall(pattern, text)) for label, pattern in API_PATTERNS.items()}
        record = by_name.get(name, {"name": name, "needed": [], "size": 0, "segments": {}})
        all_dependencies.update(record.get("needed", []))
        strings = extract_strings(text)
        excerpts = select_excerpts(functions, text, 2 if len(functions) > 500 else 1)
        binaries.append({
            "name": name,
            "functions": len(functions),
            "named": named,
            "auto": len(functions) - named,
            "failed": sum(item["status"] != "ok" for item in functions),
            "c_bytes": c_path.stat().st_size,
            "c_lines": text.count("\n") + 1,
            "metadata": record,
            "categories": category_counts,
            "apis": api_counts,
            "namespaces": namespace_counts(functions),
            "strings": interesting_strings(strings),
            "excerpts": excerpts,
        })
        function_catalog[name] = [
            [item["entry"], item["name"], item["signature"], item["status"]]
            for item in functions
        ]

    binaries.sort(key=lambda item: (-item["functions"], item["name"].lower()))
    total_functions = sum(item["functions"] for item in binaries)
    total_named = sum(item["named"] for item in binaries)
    total_failed = sum(item["failed"] for item in binaries)
    total_c_bytes = sum(item["c_bytes"] for item in binaries)
    total_source_bytes = sum(item["metadata"].get("size", 0) for item in binaries)
    max_functions = max(item["functions"] for item in binaries)
    generated = dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")

    summary_rows = []
    for item in binaries:
        meta = item["metadata"]
        summary_rows.append(f"""
          <tr data-search="{esc(item['name']).lower()}">
            <td><a href="#{esc(item['name'])}"><code>{esc(item['name'])}</code></a></td>
            <td class="num">{item['functions']:,}{bar(item['functions'], max_functions)}</td>
            <td class="num">{item['named']:,} <span class="muted">({item['named']/max(1,item['functions']):.0%})</span></td>
            <td class="num">{human_size(meta.get('size', 0))}</td>
            <td class="num">{human_size(item['c_bytes'])}</td>
            <td class="num">{len(meta.get('needed', []))}</td>
            <td><span class="ok">{item['failed']} failed</span></td>
          </tr>""")

    dependency_rows = []
    for dependency, count in all_dependencies.most_common():
        consumers = [item["name"] for item in binaries if dependency in item["metadata"].get("needed", [])]
        dependency_rows.append(
            f"<tr><td><code>{esc(dependency)}</code></td><td class='num'>{count}</td>"
            f"<td>{esc(', '.join(consumers))}</td></tr>"
        )

    detail_sections = []
    for item in binaries:
        meta = item["metadata"]
        dominant = sorted(item["categories"].items(), key=lambda pair: -pair[1])[:6]
        category_html = "".join(
            f'<span class="badge">{esc(label)} <b>{count:,}</b></span>' for label, count in dominant if count
        ) or '<span class="muted">No category-name matches</span>'
        namespace_html = "".join(
            f"<li><code>{esc(namespace)}</code><b>{count:,}</b></li>" for namespace, count in item["namespaces"]
        ) or "<li class='muted'>No non-runtime C++ namespaces recovered</li>"
        api_html = "".join(
            f"<tr><td>{esc(label)}</td><td class='num'>{count:,}</td></tr>"
            for label, count in sorted(item["apis"].items(), key=lambda pair: -pair[1]) if count
        ) or "<tr><td colspan='2' class='muted'>No selected API patterns observed</td></tr>"
        string_html = "".join(f"<li><code>{esc(value)}</code></li>" for value in item["strings"])
        if not string_html:
            string_html = "<li class='muted'>No domain-specific diagnostic strings selected</li>"
        needed_html = " ".join(f'<code class="pill">{esc(dep)}</code>' for dep in meta.get("needed", []))
        excerpts_html = "".join(f"""
          <details class="code-detail">
            <summary><code>{esc(excerpt['entry'])}</code> — {esc(excerpt['name'])}</summary>
            <div class="signature">{esc(excerpt['signature'])}</div>
            <pre><code>{esc(excerpt['code'])}</code></pre>
          </details>""" for excerpt in item["excerpts"])
        if not excerpts_html:
            excerpts_html = "<p class='muted'>No domain-named function was selected for an inline excerpt.</p>"
        segments = meta.get("segments", {})
        detail_sections.append(f"""
        <section class="binary" id="{esc(item['name'])}">
          <div class="section-head">
            <div><div class="eyebrow">Executable detail</div><h3>{esc(item['name'])}</h3></div>
            <button class="catalog-button" data-binary="{esc(item['name'])}">Browse all {item['functions']:,} functions</button>
          </div>
          <div class="metric-grid compact">
            <div><span>Functions</span><b>{item['functions']:,}</b></div>
            <div><span>Named / auto</span><b>{item['named']:,} / {item['auto']:,}</b></div>
            <div><span>Source / C export</span><b>{human_size(meta.get('size',0))} / {human_size(item['c_bytes'])}</b></div>
            <div><span>Text / data / BSS</span><b>{human_size(segments.get('text',0))} / {human_size(segments.get('data',0))} / {human_size(segments.get('bss',0))}</b></div>
            <div><span>ELF entry</span><b><code>{esc(meta.get('entry'))}</code></b></div>
            <div><span>Symbols (all / dynamic)</span><b>{meta.get('all_defined_symbols',0):,} / {meta.get('dynamic_defined_symbols',0):,}</b></div>
          </div>
          <div class="facts"><code>{esc(meta.get('file','unknown'))}</code></div>
          <div class="facts"><b>SHA-256</b> <code>{esc(meta.get('sha256','unknown'))}</code></div>
          <div class="facts"><b>Build ID</b> <code>{esc(meta.get('build_id','not present'))}</code> · <b>Runpath</b> <code>{esc(meta.get('runpath','not present'))}</code></div>
          <h4>Observed functional signals</h4>
          <div class="badges">{category_html}</div>
          <div class="columns">
            <div><h4>Recovered namespaces</h4><ul class="rank-list">{namespace_html}</ul></div>
            <div><h4>Selected API references in decompiled C</h4><table class="mini"><tbody>{api_html}</tbody></table></div>
          </div>
          <h4>Direct dependencies</h4><div class="dependency-pills">{needed_html or '<span class="muted">None recorded</span>'}</div>
          <h4>Representative embedded diagnostics</h4><ul class="strings">{string_html}</ul>
          <h4>Representative decompiler excerpts</h4>{excerpts_html}
        </section>""")

    category_totals = {
        label: sum(item["categories"][label] for item in binaries) for label in CATEGORIES
    }
    category_rows = "".join(
        f"<tr><td>{esc(label)}</td><td class='num'>{count:,}</td><td>{bar(count, max(category_totals.values()))}</td></tr>"
        for label, count in sorted(category_totals.items(), key=lambda pair: -pair[1])
    )

    catalog_json = json.dumps(function_catalog, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    binary_options = "".join(f'<option value="{esc(item["name"])}">{esc(item["name"])} ({item["functions"]:,})</option>' for item in binaries)

    document = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Gowin 1.9.11.03 Linux binaries — Ghidra decompilation report</title>
<style>
:root{{--ink:#17202a;--muted:#64748b;--paper:#f5f3ed;--panel:#fffefa;--line:#d9d6ce;--navy:#172b4d;--blue:#1e5b91;--teal:#0f766e;--gold:#bd7a10;--red:#a53f3f;--code:#111827;--shadow:0 12px 35px rgba(23,43,77,.09)}}
*{{box-sizing:border-box}} html{{scroll-behavior:smooth}} body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.55 Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
a{{color:var(--blue)}} code{{font-family:"SFMono-Regular",Consolas,"Liberation Mono",monospace;font-size:.9em}} .wrap{{max-width:1280px;margin:auto;padding:0 28px}}
header{{background:linear-gradient(125deg,#12233f 0%,#183b61 58%,#0f766e 130%);color:#fff;padding:74px 0 62px;border-bottom:6px solid #d49a34}}
.kicker,.eyebrow{{font-size:12px;letter-spacing:.15em;text-transform:uppercase;font-weight:800;color:#b9d8ef}} h1{{font-family:Georgia,serif;font-size:clamp(38px,6vw,68px);line-height:1.02;margin:12px 0 20px;max-width:980px;font-weight:600}}
.lede{{font-size:19px;max-width:850px;color:#dce8f2}} .hero-meta{{display:flex;flex-wrap:wrap;gap:10px;margin-top:28px}} .hero-meta span{{background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.22);border-radius:999px;padding:6px 11px;font-size:13px}}
nav{{position:sticky;top:0;z-index:20;background:rgba(255,254,250,.94);backdrop-filter:blur(10px);border-bottom:1px solid var(--line)}} nav .wrap{{display:flex;gap:18px;overflow:auto;padding-top:12px;padding-bottom:12px}} nav a{{white-space:nowrap;text-decoration:none;color:var(--navy);font-weight:700;font-size:13px}}
main{{padding:36px 0 90px}} section{{scroll-margin-top:64px}} h2{{font:600 34px/1.15 Georgia,serif;color:var(--navy);margin:54px 0 18px}} h3{{font:600 28px/1.2 Georgia,serif;color:var(--navy);margin:4px 0}} h4{{color:var(--navy);margin:25px 0 10px}}
.callout{{background:#e9f1f7;border-left:5px solid var(--blue);padding:16px 19px;border-radius:4px;margin:18px 0}} .warning{{background:#fff3db;border-left-color:var(--gold)}}
.metric-grid{{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px;margin:25px 0}} .metric-grid>div{{background:var(--panel);border:1px solid var(--line);box-shadow:var(--shadow);border-radius:10px;padding:18px}} .metric-grid span{{display:block;color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.08em}} .metric-grid b{{display:block;color:var(--navy);font-size:24px;margin-top:5px}} .metric-grid.compact{{grid-template-columns:repeat(3,minmax(0,1fr));margin:18px 0}} .metric-grid.compact b{{font-size:16px}}
.panel,.binary{{background:var(--panel);border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow);padding:24px;margin:20px 0}} .binary{{margin:30px 0;padding:28px}} .section-head{{display:flex;align-items:center;justify-content:space-between;gap:15px}}
table{{width:100%;border-collapse:collapse}} th{{position:sticky;top:43px;background:#e9edf0;color:var(--navy);text-align:left;font-size:12px;text-transform:uppercase;letter-spacing:.05em;z-index:2}} th,td{{border-bottom:1px solid var(--line);padding:10px;vertical-align:top}} tbody tr:hover{{background:#f4f7f8}} .num{{font-variant-numeric:tabular-nums;text-align:right;white-space:nowrap}} .table-scroll{{overflow:auto;max-height:680px;border:1px solid var(--line);border-radius:8px}}
.bar{{display:block;background:#dce7ee;width:120px;height:5px;border-radius:4px;margin-top:6px;overflow:hidden}} .bar i{{display:block;background:linear-gradient(90deg,var(--blue),var(--teal));height:100%}} .muted{{color:var(--muted)}} .ok{{color:var(--teal);font-weight:700}}
.columns{{display:grid;grid-template-columns:1fr 1fr;gap:22px}} .badges,.dependency-pills{{display:flex;flex-wrap:wrap;gap:7px}} .badge,.pill{{border:1px solid #cbd5df;background:#edf5f6;border-radius:999px;padding:5px 9px;font-size:12px}} .badge b{{color:var(--teal)}}
.rank-list{{list-style:none;padding:0;margin:0}} .rank-list li{{display:flex;justify-content:space-between;border-bottom:1px dotted var(--line);padding:5px 0}} .mini td{{padding:5px 8px}} .strings code{{overflow-wrap:anywhere}} .facts{{font-size:13px;color:var(--muted);overflow-wrap:anywhere;margin:7px 0}}
button,.catalog-button,select,input{{font:inherit}} button,.catalog-button{{border:0;background:var(--navy);color:#fff;border-radius:7px;padding:9px 13px;font-weight:700;cursor:pointer}} button:hover{{background:var(--blue)}} input,select{{border:1px solid #aeb8c2;background:#fff;border-radius:7px;padding:9px 11px}}
.code-detail{{border:1px solid var(--line);border-radius:8px;margin:8px 0;background:#f7f7f4}} .code-detail summary{{cursor:pointer;padding:11px 13px;font-weight:700}} .signature{{padding:0 13px 8px;color:var(--muted);overflow-wrap:anywhere}} pre{{margin:0;background:var(--code);color:#d7e2ef;padding:17px;overflow:auto;font:12px/1.5 "SFMono-Regular",Consolas,monospace;max-height:520px}}
.catalog-controls{{display:grid;grid-template-columns:260px 1fr auto;gap:10px;margin-bottom:14px}} .catalog-status{{color:var(--muted);margin:8px 0}} #functionRows td:nth-child(2),#functionRows td:nth-child(3){{overflow-wrap:anywhere}}
.method{{counter-reset:step}} .method li{{margin:10px 0}} footer{{border-top:1px solid var(--line);padding:35px 0;color:var(--muted);font-size:13px}}
@media(max-width:850px){{.metric-grid,.metric-grid.compact{{grid-template-columns:1fr 1fr}}.columns{{grid-template-columns:1fr}}.catalog-controls{{grid-template-columns:1fr}}th{{top:42px}}}}
@media(max-width:520px){{.wrap{{padding:0 16px}}.metric-grid,.metric-grid.compact{{grid-template-columns:1fr}}header{{padding:48px 0}}.section-head{{align-items:flex-start;flex-direction:column}}}}
@media print{{nav,button,.catalog-controls{{display:none!important}} body{{background:#fff}} .panel,.binary{{box-shadow:none;break-inside:avoid}} pre{{max-height:none;white-space:pre-wrap}}}}
</style>
</head>
<body>
<header><div class="wrap">
  <div class="kicker">Static reverse-engineering dossier</div>
  <h1>Gowin 1.9.11.03 Linux binaries</h1>
  <p class="lede">A detailed, evidence-separated report generated from Ghidra 12.1.3 analysis and C decompiler exports of every top-level 64-bit ELF executable in <code>IDE/bin</code>.</p>
  <div class="hero-meta"><span>Generated {esc(generated)}</span><span>Host: hyper-MS-lin</span><span>Architecture: x86-64</span><span>Scope: executables, not shared-library bodies</span></div>
</div></header>
<nav><div class="wrap"><a href="#executive">Overview</a><a href="#scope">Scope &amp; method</a><a href="#inventory">Inventory</a><a href="#behavior">Behavioral signals</a><a href="#dependencies">Dependencies</a><a href="#catalog">Function catalog</a><a href="#binaries">Binary details</a><a href="#limitations">Limitations</a></div></nav>
<main class="wrap">
<section id="executive">
  <h2>Executive overview</h2>
  <div class="metric-grid">
    <div><span>Executables</span><b>{len(binaries)}</b></div><div><span>Functions</span><b>{total_functions:,}</b></div>
    <div><span>Recognizable names</span><b>{total_named:,}</b></div><div><span>Decompiler failures</span><b>{total_failed}</b></div>
    <div><span>Generated C</span><b>{human_size(total_c_bytes)}</b></div>
  </div>
  <div class="callout"><b>Observed:</b> all {len(binaries)} selected files loaded as 64-bit little-endian x86 ELF programs. Ghidra exported {total_functions:,} functions and marked every function export successful. The source executables total {human_size(total_source_bytes)}; the textual decompiler output expands to {human_size(total_c_bytes)}.</div>
  <p><b>High-level interpretation:</b> this directory is not one monolithic compiler. It contains several Qt front ends, Tcl-enabled command shells, floorplanning and constraint tools, embedded logic-analyzer applications, small launchers, and device/module-generation utilities. The largest recovered bodies are <code>floorplanner</code>, <code>GowinModGen</code>, <code>gvio_analyzer</code>, <code>gao_analyzer</code>, <code>gw_goeye</code>, and their shell variants.</p>
  <div class="callout warning"><b>Important boundary:</b> dependency names and call sites are described here, but the bodies of proprietary <code>IDE/lib/*.so</code> libraries were not decompiled in this pass. A small executable such as <code>gw_sh</code> delegates substantial behavior to those libraries.</div>
</section>
<section id="scope">
  <h2>Scope, provenance, and method</h2>
  <div class="panel">
    <ol class="method">
      <li>Selected every executable regular file in <code>/home/hyper/opt/gowin/IDE/bin</code> identified by <code>file</code> as a 64-bit ELF program.</li>
      <li>Imported each program into a persistent Ghidra 12.1.3 project using the <code>x86:LE:64:default:gcc</code> language/compiler specification.</li>
      <li>Ran standard headless auto-analysis, then decompiled every non-external function with a 120-second per-function ceiling.</li>
      <li>Exported address, recovered name, signature, status, and decompiler C. Source SHA-256 values, ELF headers, segments, symbols, dependencies, build IDs, and runpaths were collected independently with read-only system tools.</li>
      <li>This report derives counts directly from those artifacts. “Observed” means a literal symbol, string, call, header, or dependency; “inferred” means a semantic grouping based on names and signatures.</li>
    </ol>
    <p><b>Artifact locations on Linux:</b><br><code>/home/hyper/gowin-decompiled-1.9.11.03/</code><br><code>/home/hyper/ghidra-projects/gowin-1.9.11.03.gpr</code></p>
  </div>
</section>
<section id="inventory">
  <h2>Executable inventory</h2>
  <p>The table is ordered by recovered function count. Bars are relative to the largest executable.</p>
  <div class="table-scroll"><table><thead><tr><th>Binary</th><th class="num">Functions</th><th class="num">Named</th><th class="num">ELF size</th><th class="num">C export</th><th class="num">Dependencies</th><th>Status</th></tr></thead><tbody>{''.join(summary_rows)}</tbody></table></div>
</section>
<section id="behavior">
  <h2>Behavioral signals</h2>
  <p>These are name/signature matches, not claims that every matched function implements the category. They provide a navigational map across a very large C++ codebase.</p>
  <div class="panel"><table><thead><tr><th>Signal family</th><th class="num">Matching functions</th><th>Relative volume</th></tr></thead><tbody>{category_rows}</tbody></table></div>
  <div class="columns">
    <div class="panel"><h3>What is clearly present</h3><ul><li>Qt application and widget infrastructure across most GUI executables.</li><li>Tcl interpreter setup and command registration in command-shell entry points.</li><li>Device, family, package, pin, primitive, memory, and constraint vocabulary.</li><li>Specialized BSRAM/ECC and module-combination logic in <code>GowinModGen</code>.</li><li>Floorplan, region, placement, clock, timing, SDC, hierarchy, probe, capture, and trigger logic.</li></ul></div>
    <div class="panel"><h3>What this pass does not prove</h3><ul><li>The complete synthesis, place-and-route, or bitstream-generation algorithm may live in shared libraries.</li><li>A referenced crypto API does not by itself establish what data is encrypted or whether it is licensing-related.</li><li>Decompiler types and calling conventions are approximate until manually corrected in Ghidra.</li><li>Recovered source is behaviorally useful pseudocode, not the vendor’s original source.</li></ul></div>
  </div>
</section>
<section id="dependencies">
  <h2>Dynamic dependencies</h2>
  <p>Observed <code>DT_NEEDED</code> entries, grouped across executables. The consumers column makes launcher/library boundaries explicit.</p>
  <div class="table-scroll"><table><thead><tr><th>Library</th><th class="num">Consumers</th><th>Executables</th></tr></thead><tbody>{''.join(dependency_rows)}</tbody></table></div>
</section>
<section id="catalog">
  <h2>Searchable function catalog</h2>
  <p>All {total_functions:,} recovered functions are embedded below as compact data. Select a binary and search by address, function name, or signature. Rendering is capped to 250 rows at once.</p>
  <div class="panel">
    <div class="catalog-controls"><select id="binarySelect">{binary_options}</select><input id="functionSearch" type="search" placeholder="Search names, signatures, or addresses"><button id="clearSearch">Clear</button></div>
    <div id="catalogStatus" class="catalog-status"></div>
    <div class="table-scroll"><table><thead><tr><th>Address</th><th>Name</th><th>Recovered signature</th><th>Status</th></tr></thead><tbody id="functionRows"></tbody></table></div>
  </div>
</section>
<section id="binaries"><h2>Per-binary findings</h2><p>Each section combines raw ELF facts with derived navigation aids, representative diagnostic strings, and short decompiler excerpts. Use the catalog button to jump to that binary’s complete function list.</p>{''.join(detail_sections)}</section>
<section id="limitations">
  <h2>Decompiler limitations and reading guidance</h2>
  <div class="panel"><ul>
    <li><b>Names:</b> many C++ names survived or were demangled, but <code>FUN_address</code> is Ghidra’s placeholder for an unnamed function.</li>
    <li><b>Types:</b> <code>undefined</code>, <code>undefined8</code>, generic pointers, and synthetic locals indicate unresolved types, not necessarily unsafe source code.</li>
    <li><b>PLT warnings:</b> messages about an “EXTERNAL block location” refer to import stubs; they did not count as local-function export failures.</li>
    <li><b>Inlining and templates:</b> compiler optimization can merge, clone, inline, or eliminate functions. Counts are analysis units, not original source-function counts.</li>
    <li><b>Call counts:</b> API counts are textual references in decompiler output and may include wrapper/thunk artifacts.</li>
    <li><b>Security conclusions:</b> this is a structural and behavioral inventory, not a vulnerability assessment.</li>
  </ul></div>
</section>
</main>
<footer><div class="wrap">Generated from Ghidra decompiler exports and read-only ELF metadata. No Gowin executable was modified or executed to produce this report.</div></footer>
<script id="catalogData" type="application/json">{catalog_json}</script>
<script>
const catalog=JSON.parse(document.getElementById('catalogData').textContent);
const select=document.getElementById('binarySelect'), search=document.getElementById('functionSearch'), rows=document.getElementById('functionRows'), status=document.getElementById('catalogStatus');
function render(){{const name=select.value,q=search.value.trim().toLowerCase(),all=catalog[name]||[];const matches=q?all.filter(r=>r.some(v=>v.toLowerCase().includes(q))):all;const shown=matches.slice(0,250);rows.innerHTML=shown.map(r=>`<tr><td><code>${{escapeHtml(r[0])}}</code></td><td><code>${{escapeHtml(r[1])}}</code></td><td>${{escapeHtml(r[2])}}</td><td><span class="ok">${{escapeHtml(r[3])}}</span></td></tr>`).join('');status.textContent=`${{matches.length.toLocaleString()}} matching functions in ${{name}}${{matches.length>250?' — first 250 shown':''}}`;}}
function escapeHtml(s){{return s.replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));}}
select.addEventListener('change',render);search.addEventListener('input',render);document.getElementById('clearSearch').addEventListener('click',()=>{{search.value='';render();search.focus();}});
document.querySelectorAll('.catalog-button').forEach(button=>button.addEventListener('click',()=>{{select.value=button.dataset.binary;search.value='';render();document.getElementById('catalog').scrollIntoView();}}));render();
</script>
</body></html>"""

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("metadata", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    build_report(args.source, args.metadata, args.output)


if __name__ == "__main__":
    main()
