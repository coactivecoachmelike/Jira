#!/usr/bin/env python3
"""Geliştirmeler raporunu 16:9 (1920x1080) iki slayt olarak üretir.

Slayt 1: Metrikler — grafikler iki versiyonlu (İş adedi | Original Estimate):
         tamamlanma oranı, statü dağılımı, kişi bazında planlanan vs. tamamlanan,
         iş tipi kırılımı (User Story / Proje işi / Bug / Task); lead time histogramı yalnız adet.
Slayt 2: Maddeler — statü renkli bullet'lı iş listesi (yazı boyutu sığana kadar küçülür).

Veri kaynağı seçenekleri build_report.py ile aynıdır. PNG için:
  node export_png.mjs slaytlar.html slaytlar   ->  slaytlar-1.png, slaytlar-2.png
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_report import (COUNT_FIELD, DEFAULT_DONE, NO_STATUS_COLOR,  # noqa: E402
                          apply_filter, build_model, esc, fetch_project, fmt, load_export,
                          normalize, pick_estimate_field)

TYPES = ["User Story", "Proje işi", "Bug", "Task"]
# Düzenli ifade; başlığın küçük harfli halinde aranır
DEFAULT_TASK_KEYWORDS = [r"işlemler(i)?\b", "toplantı", "takvim", "kurulum", "incele", "migration",
                         "dependabot", "destek süreci", "deployment", "task", "dönüşleri",
                         "modelbuilder", "dwh"]
LEAD_BINS = [(0, 1, "<1 gün"), (1, 3, "1–3"), (3, 7, "3–7"), (7, 14, "7–14"),
             (14, 21, "14–21"), (21, 30, "21–30"), (30, None, "30+")]


# --------------------------------------------------------------------------- hesap

def tr_lower(s: str) -> str:
    return s.replace("İ", "i").replace("I", "ı").lower()


def classify(it: dict, project_field: str, task_kw: list[str]) -> str:
    """İş tipi: GitHub issue type varsa o; yoksa başlık/alan kuralları.
    Sıra: Bug > Proje işi (proje alanı dolu) > Task > User Story."""
    t = (it.get("issueType") or "").lower()
    title = tr_lower(it["title"])
    if t == "bug" or "[bug]" in title:
        return "Bug"
    if (it["fields"].get(project_field) or "").strip():
        return "Proje işi"
    if t in ("task", "görev"):
        return "Task"
    if t in ("user story", "story", "feature"):
        return "User Story"
    if any(re.search(k, title) for k in task_kw):
        return "Task"
    return "User Story"


def parse_ts(s):
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def lead_days(it: dict):
    c, d = parse_ts(it.get("created")), parse_ts(it.get("closed"))
    if c and d and d >= c:
        return (d - c).total_seconds() / 86400
    return None


def pct(done: float, total: float) -> float:
    return done / total * 100 if total else 0.0


# --------------------------------------------------------------------------- bileşenler

def completion_block(label: str, done: float, total: float, unit: str, note: str = "") -> str:
    p = pct(done, total)
    u = f"<small>{esc(unit)}</small>" if unit else ""
    return f"""
<div class="comp">
  <div class="clabel">{esc(label)}</div>
  <div class="chero">%{p:.0f}</div>
  <div class="bar"><i style="width:{min(p, 100):.2f}%"></i></div>
  <div class="cnums">
    <div><span>Planlanan</span><b>{fmt(total)}{u}</b></div>
    <div><span>Tamamlanan</span><b>{fmt(done)}{u}</b></div>
    <div><span>Kalan</span><b>{fmt(total - done)}{u}</b></div>
  </div>
  <div class="cnote">{note or '&nbsp;'}</div>
</div>"""


def dual_table(rows: list[dict], with_done: bool, est_label: str) -> str:
    """Her satır: etiket | adet çubuğu + değer | estimate çubuğu + değer.
    İki ölçü ayrı ölçekte (yan yana iki küçük grafik), satırlar hizalı."""
    cmax = max([r["c"] for r in rows] + [1])
    emax = max([r["e"] for r in rows] + [1])

    def cell(total, done, mx, color, tip):
        if with_done:
            bar = (f'<div class="tr" style="width:{total / mx * 100:.2f}%"></div>'
                   f'<div class="fi" style="width:{done / mx * 100:.2f}%"></div>')
            val = f'{fmt(done)}<span>/{fmt(total)}</span>'
        else:
            bar = f'<div class="fi" style="width:{total / mx * 100:.2f}%;background:{color}"></div>'
            val = fmt(total)
        return f'<div class="db" title="{esc(tip)}">{bar}</div><div class="dv">{val}</div>'

    out = [f'<div class="dhead"><span></span><span>İş adedi</span><span></span>'
           f'<span>{esc(est_label)}</span><span></span></div>']
    for r in rows:
        dot = f'<span class="dot" style="--c:{r["color"]}"></span>' if r.get("color") else ""
        out.append(
            f'<div class="drow"><div class="dl" title="{esc(r["label"])}">{dot}{esc(r["label"])}</div>'
            + cell(r["c"], r.get("cd", 0), cmax, r.get("color"), f'{r["label"]}: {fmt(r["c"])} iş')
            + cell(r["e"], r.get("ed", 0), emax, r.get("color"), f'{r["label"]}: {fmt(r["e"])} {est_label}')
            + "</div>")
    return '<div class="dual">' + "".join(out) + "</div>"


def histogram(bins: list[dict], key: str, title: str, color: str) -> str:
    mx = max([b[key] for b in bins] + [1])
    cols = "".join(
        f'<div class="hcol" title="{esc(b["label"])} gün: {fmt(b[key])}">'
        f'<div class="hval">{fmt(b[key]) if b[key] else ""}</div>'
        f'<div class="hbar" style="height:{b[key] / mx * 82:.2f}%;background:{color}"></div></div>'
        for b in bins)
    labels = "".join(f'<div>{esc(b["label"])}</div>' for b in bins)
    return (f'<div class="hist"><div class="htitle">{esc(title)}</div>'
            f'<div class="hplot">{cols}</div><div class="hx">{labels}</div></div>')


# --------------------------------------------------------------------------- build

def build(a) -> None:
    if a.from_json:
        with open(a.from_json, encoding="utf-8") as fh:
            data = json.load(fh)
    elif a.from_export:
        data = load_export(a.from_export, a.status_field)
    else:
        data = normalize(fetch_project(a.org, a.project, a.view or None), a.status_field)

    items = [i for i in data["items"] if a.include_archived or not i.get("archived")]
    view = data.get("view") or {}
    expr = a.filter if a.filter is not None else (view.get("filter") or "")
    expr = f"{expr} {a.extra_filter}".strip()
    items, warnings = apply_filter(items, expr, a.me)
    if a.clean_titles:
        for it in items:
            it["title"] = re.sub(r"^\s*\d{6,}\s*-\s*", "", it["title"])
            it["title"] = re.sub(r"\[BUG\]:\s*", "[BUG] ", it["title"]).strip()

    done_names = a.done_status or DEFAULT_DONE
    est_field = pick_estimate_field(data, a.estimate_field, a.estimate_field_id)
    if not est_field:
        warnings.append(f"'{a.estimate_field}' alanı veride yok; estimate grafikleri 0 görünür")
    for it in items:
        it["fields"][COUNT_FIELD] = 1
    mc = build_model(data, items, COUNT_FIELD, done_names, a.closed_as_done)
    task_kw = [tr_lower(k) for k in (a.task_keyword or DEFAULT_TASK_KEYWORDS)]

    rows = mc["rows"]
    for r in rows:
        v = r["fields"].get(est_field) if est_field else None
        r["est"] = float(v) if isinstance(v, (int, float)) else 0.0
        r["kind"] = classify(r, a.project_field, task_kw)
        r["lead"] = lead_days(r)
    est_label = a.estimate_label
    unit = a.unit

    # Planlanan vs tamamlanan
    e_total = sum(r["est"] for r in rows)
    e_done = sum(r["est"] for r in rows if r["done"])
    missing = sum(1 for r in rows if not r["est"])
    comp = (completion_block("İş adedi", mc["count_done"], mc["count"], "iş")
            + completion_block(est_label, e_done, e_total, unit,
                               f"{missing} işin estimate'i boş (0 sayıldı)" if missing else ""))

    # Statü
    status_rows = []
    for o in mc["options"]:
        if not o["count"]:
            continue
        mem = [r for r in rows if r["status"] == o["name"]]
        status_rows.append({"label": o["name"] + (" ✓" if o["done"] else ""), "color": o["hex"],
                            "c": len(mem), "e": sum(r["est"] for r in mem)})

    # Kişi
    people = {}
    for r in rows:
        for p in r["assignees"] or ["Atanmamış"]:
            d = people.setdefault(p, {"label": p, "c": 0, "cd": 0, "e": 0.0, "ed": 0.0})
            d["c"] += 1
            d["e"] += r["est"]
            if r["done"]:
                d["cd"] += 1
                d["ed"] += r["est"]
    people_rows = sorted(people.values(), key=lambda d: (-d["c"], -d["e"], d["label"]))

    # İş tipi
    type_rows = []
    for t in TYPES:
        mem = [r for r in rows if r["kind"] == t]
        type_rows.append({"label": t, "c": len(mem), "cd": sum(1 for r in mem if r["done"]),
                          "e": sum(r["est"] for r in mem), "ed": sum(r["est"] for r in mem if r["done"])})
    projects = {}
    for r in rows:
        if r["kind"] == "Proje işi":
            n = r["fields"].get(a.project_field)
            projects[n] = projects.get(n, 0) + 1
    proj_txt = " · ".join(f"{esc(n)} <b>{c}</b>" for n, c in sorted(projects.items(), key=lambda x: -x[1]))

    # Lead time
    closed = [r for r in rows if r["lead"] is not None]
    bins = []
    for lo, hi, label in LEAD_BINS:
        mem = [r for r in closed if r["lead"] >= lo and (hi is None or r["lead"] < hi)]
        bins.append({"label": label, "c": len(mem), "e": sum(r["est"] for r in mem)})
    while len(bins) > 4 and not bins[-1]["c"]:
        bins.pop()
    leads = [r["lead"] for r in closed]
    if leads:
        lead_stats = (f'<div><span>Kapanan iş</span><b>{len(leads)}</b></div>'
                      f'<div><span>Medyan</span><b>{fmt(round(statistics.median(leads), 1))}<small>gün</small></b></div>'
                      f'<div><span>Ortalama</span><b>{fmt(round(statistics.mean(leads), 1))}<small>gün</small></b></div>'
                      f'<div><span>En uzun</span><b>{fmt(round(max(leads), 1))}<small>gün</small></b></div>')
        hists = histogram(bins, "c", "İş adedi", "var(--ink)")
    else:
        lead_stats, hists = "", '<div class="emptymsg">Created / Closed verisi yok.</div>'

    legend = "".join(
        f'<li><span class="dot" style="--c:{o["hex"]}"></span>{esc(o["name"])}'
        f'{" ✓" if o["done"] else ""}<span class="muted">{o["count"]}</span></li>'
        for o in mc["options"] if o["count"])

    # Slayt 2
    hexes = {o["name"]: o["hex"] for o in mc["options"]}
    lis = []
    for r in sorted(rows, key=lambda r: (mc["order"].get(r["status"], 999), r["title"].lower())):
        badge = f'<span class="est">{fmt(r["est"])}{(" " + esc(unit)) if unit else ""}</span>' if r["est"] else ""
        lis.append(f'<li><span class="dot" style="--c:{hexes.get(r["status"], NO_STATUS_COLOR)}"></span>'
                   f'<span class="t">{esc(r["title"])}</span>{badge}</li>')

    proj = data.get("project") or {}
    subtitle = " · ".join(x for x in [proj.get("title"), view.get("name"),
                                      dt.date.today().strftime("%d.%m.%Y")] if x)
    page = TEMPLATE.format(
        title=esc(a.title), subtitle=esc(subtitle), legend=legend, comp=comp,
        status=dual_table(status_rows, False, est_label),
        people=dual_table(people_rows, True, est_label),
        types=dual_table(type_rows, True, est_label), projects=proj_txt or "—",
        lead_stats=lead_stats, hists=hists, items="\n".join(lis), n=len(rows),
        est_label=esc(est_label), pfield=esc(a.project_field),
    )
    with open(a.output, "w", encoding="utf-8") as fh:
        fh.write(page)

    print(f"{a.output} yazıldı: {len(rows)} iş")
    print(f"  adet: %{pct(mc['count_done'], mc['count']):.0f} ({mc['count_done']}/{mc['count']}) · "
          f"estimate: %{pct(e_done, e_total):.0f} ({fmt(e_done)}/{fmt(e_total)}), {missing} iş boş")
    print("  tip: " + ", ".join(f"{t['label']} {t['c']} ({fmt(t['e'])})" for t in type_rows))
    for t in TYPES:
        print(f"    {t}: " + " | ".join(r["title"][:50] for r in rows if r["kind"] == t))
    if leads:
        print(f"  lead time: {len(leads)} kapanan iş, medyan {statistics.median(leads):.1f} gün · "
              + ", ".join(f"{b['label']}: {b['c']}" for b in bins))
    for w in warnings:
        print("uyarı:", w, file=sys.stderr)


TEMPLATE = """<!doctype html>
<html lang="tr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} Slaytları</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;800&display=swap" rel="stylesheet">
<style>
:root {{ --bg:#E9EDF2; --slide:#FFFFFF; --ink:#0B1F3A; --ink-2:#3B4A5E; --muted:#6B7788;
  --rule:#DCE6EE; --accent:#1FA3B5; --track:#E8EDF2; --plan:#C9D5E1; --s:1; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink);
  font-family:"Plus Jakarta Sans", Inter, system-ui, -apple-system, "Segoe UI", sans-serif; }}
.deck {{ display:flex; flex-direction:column; align-items:center; gap:24px; padding:24px 0; }}
.frame {{ width:calc(1920px * var(--s)); height:calc(1080px * var(--s)); overflow:hidden;
  box-shadow:0 2px 12px rgba(11,31,58,.12); }}
.slide {{ width:1920px; height:1080px; background:var(--slide); padding:44px 60px 36px;
  transform:scale(var(--s)); transform-origin:0 0; display:flex; flex-direction:column; }}
header {{ display:flex; align-items:flex-end; justify-content:space-between; gap:32px; margin-bottom:22px; }}
h1 {{ white-space:nowrap; font-size:58px; font-weight:800; letter-spacing:-.02em; margin:0; line-height:1; }}
h1 span {{ color:var(--accent); font-weight:600; }}
.sub {{ color:var(--muted); font-size:19px; margin-top:10px; white-space:nowrap; }}
.legend {{ list-style:none; margin:0; padding:12px 18px; display:flex; flex-wrap:wrap; gap:6px 24px;
  border:1px solid var(--rule); border-radius:12px; font-size:18px; color:var(--ink-2); justify-content:flex-end; min-width:0; flex:0 1 auto; }}
.legend li {{ display:flex; align-items:center; }}
.legend .muted {{ margin-left:8px; color:var(--muted); font-variant-numeric:tabular-nums; }}
.dot {{ display:inline-block; width:13px; height:13px; border-radius:50%; background:var(--c); margin-right:9px; flex:none; }}
/* metrikler */
.grid {{ flex:1; min-height:0; display:grid; grid-template-columns:repeat(3, minmax(0,1fr));
  grid-template-rows:minmax(0,1fr) minmax(0,1fr); gap:18px; }}
.panel {{ border:1px solid var(--rule); border-radius:14px; padding:18px 22px; display:flex; flex-direction:column; min-height:0; }}
.panel.wide {{ grid-column:span 2; }}
.ptitle {{ font-size:16px; font-weight:600; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); margin-bottom:10px; }}
.pfoot {{ font-size:14px; color:var(--muted); margin-top:auto; padding-top:10px; line-height:1.45; }}
.pfoot b {{ color:var(--ink); }}
.comps {{ display:grid; grid-template-columns:1fr 1fr; gap:22px; flex:1; }}
.comp {{ display:flex; flex-direction:column; }}
.clabel {{ font-size:17px; color:var(--ink-2); font-weight:600; }}
.chero {{ font-size:84px; font-weight:800; line-height:1.05; letter-spacing:-.03em; font-variant-numeric:tabular-nums; }}
.bar {{ height:14px; border-radius:7px; background:var(--track); overflow:hidden; margin:10px 0 auto; }}
.bar i {{ display:block; height:100%; background:var(--accent); border-radius:6px; }}
.cnums {{ display:grid; grid-template-columns:repeat(3,1fr); gap:8px; }}
.cnums div {{ display:flex; flex-direction:column; border-top:1px solid var(--rule); padding-top:6px; }}
.cnums span {{ font-size:14px; color:var(--muted); }}
.cnums b {{ font-size:28px; font-variant-numeric:tabular-nums; white-space:nowrap; }}
.cnums small, .lstats small {{ font-size:14px; font-weight:400; color:var(--muted); margin-left:3px; }}
.cnote {{ font-size:14px; color:var(--muted); margin-top:8px; }}
.dual {{ display:flex; flex-direction:column; flex:1; justify-content:space-evenly; }}
.dhead, .drow {{ display:grid; grid-template-columns:minmax(0,1.25fr) minmax(0,1fr) 64px minmax(0,1fr) 88px;
  column-gap:10px; align-items:center; }}
.dhead {{ font-size:14px; color:var(--muted); padding-bottom:5px; }}
.drow {{ border-top:1px solid var(--rule); padding:6px 0; font-size:18px; flex:1; max-height:64px; }}
.dl {{ overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--ink-2); display:flex; align-items:center; }}
.db {{ position:relative; height:14px; }}
.tr, .fi {{ position:absolute; left:0; top:0; height:100%; border-radius:0 4px 4px 0; }}
.tr {{ background:var(--plan); }} .fi {{ background:var(--ink); }}
.dv {{ text-align:right; font-weight:600; font-variant-numeric:tabular-nums; white-space:nowrap; }}
.dv span {{ color:var(--muted); font-weight:400; }}
.pkey {{ display:flex; gap:18px; font-size:14px; color:var(--ink-2); margin:-2px 0 8px; }}
.pkey span::before {{ content:""; display:inline-block; width:14px; height:9px; border-radius:2px; margin-right:6px; background:var(--k); }}
.lead {{ display:grid; grid-template-columns:170px 1fr; gap:32px; flex:1; min-height:0; }}
.lstats {{ display:flex; flex-direction:column; gap:8px; }}
.lstats div {{ display:flex; flex-direction:column; border-top:1px solid var(--rule); padding-top:5px; }}
.lstats span {{ font-size:14px; color:var(--muted); }}
.lstats b {{ font-size:28px; font-variant-numeric:tabular-nums; }}
.hist {{ display:flex; flex-direction:column; min-height:0; }}
.htitle {{ font-size:15px; color:var(--ink-2); font-weight:600; margin-bottom:4px; }}
.hplot {{ flex:1; min-height:100px; display:flex; align-items:flex-end; gap:8px; border-bottom:1px solid var(--plan); }}
.hcol {{ flex:1; height:100%; display:flex; flex-direction:column; justify-content:flex-end; }}
.hval {{ text-align:center; font-size:16px; font-weight:600; font-variant-numeric:tabular-nums; margin-bottom:4px; }}
.hbar {{ border-radius:4px 4px 0 0; }}
.hx {{ display:flex; gap:8px; font-size:14px; color:var(--muted); margin-top:6px; }}
.hx div {{ flex:1; text-align:center; white-space:nowrap; }}
.hunit {{ font-size:13px; color:var(--muted); text-align:right; margin-top:2px; }}
.emptymsg {{ margin:auto; font-size:20px; color:var(--ink-2); }}
/* maddeler */
.list {{ flex:1; min-height:0; list-style:none; margin:0; padding:0; columns:3; column-gap:56px;
  column-fill:auto; column-rule:1px solid var(--accent); --fs:22px; overflow:hidden; }}
.list li {{ break-inside:avoid; display:flex; align-items:flex-start; gap:2px; padding:calc(var(--fs) * .42) 0;
  border-bottom:1px solid var(--rule); font-size:var(--fs); line-height:1.28; font-weight:500; }}
.list .dot {{ width:calc(var(--fs) * .55); height:calc(var(--fs) * .55); margin-top:calc(var(--fs) * .38); }}
.list .t {{ flex:1; }}
.est {{ font-size:calc(var(--fs) * .7); color:var(--muted); border:1px solid var(--rule); border-radius:999px;
  padding:1px 8px; margin:2px 0 0 8px; white-space:nowrap; }}
@media print {{
  @page {{ size:1920px 1080px; margin:0; }}
  body {{ background:none; }} .deck {{ padding:0; gap:0; }} .frame {{ box-shadow:none; break-after:page; }}
}}
</style>
</head>
<body>
<div class="deck">
<div class="frame"><section class="slide" id="s1" aria-label="Metrikler">
  <header>
    <div><h1>{title} <span>· Metrikler</span></h1><div class="sub">{subtitle}</div></div>
    <ul class="legend" aria-label="Statü lejantı">{legend}</ul>
  </header>
  <div class="grid">
    <div class="panel"><div class="ptitle">Planlanan vs. tamamlanan</div>
      <div class="comps">{comp}</div></div>
    <div class="panel"><div class="ptitle">Statü dağılımı</div>{status}</div>
    <div class="panel"><div class="ptitle">Kişi bazında</div>
      <div class="pkey"><span style="--k:var(--plan)">Planlanan</span><span style="--k:var(--ink)">Tamamlanan</span></div>
      {people}</div>
    <div class="panel"><div class="ptitle">İş tipi kırılımı</div>
      <div class="pkey"><span style="--k:var(--plan)">Planlanan</span><span style="--k:var(--ink)">Tamamlanan</span></div>
      {types}
      <div class="pfoot">Proje işleri ({pfield}): {projects}<br>
      Bug = başlıkta [BUG] · Task = operasyonel işler · diğerleri User Story</div></div>
    <div class="panel wide"><div class="ptitle">Lead time histogramı (Created → Closed, gün)</div>
      <div class="lead"><div class="lstats">{lead_stats}</div>{hists}</div></div>
  </div>
</section></div>

<div class="frame"><section class="slide" id="s2" aria-label="Maddeler">
  <header>
    <div><h1>{title}</h1><div class="sub">{subtitle} · {n} iş · ✓ = tamamlandı · rozet = {est_label}</div></div>
    <ul class="legend" aria-label="Statü lejantı">{legend}</ul>
  </header>
  <ul class="list">
{items}
  </ul>
</section></div>
</div>
<script>
(() => {{
  // Maddeler slaytına sığana kadar yazı boyutunu küçült
  const list = document.querySelector('.list');
  let fs = 22;
  const fits = () => list.scrollWidth <= list.clientWidth + 1 && list.scrollHeight <= list.clientHeight + 1;
  list.style.setProperty('--fs', fs + 'px');
  while (!fits() && fs > 11) {{ fs -= 0.5; list.style.setProperty('--fs', fs + 'px'); }}
  // Sığdıysa kolonları dengele; dengeli hali taşarsa geri al
  list.style.columnFill = 'balance';
  if (!fits()) list.style.columnFill = 'auto';
  document.body.dataset.fitted = fs;
  const scale = () => document.documentElement.style.setProperty('--s',
    Math.min(1, (innerWidth - 32) / 1920).toFixed(4));
  scale(); addEventListener('resize', scale);
}})();
</script>
</body>
</html>
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--org", default="borusanotomotiv")
    ap.add_argument("--project", type=int, default=34)
    ap.add_argument("--view", type=int, default=11)
    ap.add_argument("--from-json")
    ap.add_argument("--from-export", help="GitHub Projects'ten indirilen TSV/CSV")
    ap.add_argument("--filter")
    ap.add_argument("--extra-filter", default="")
    ap.add_argument("--me")
    ap.add_argument("--status-field", default="Status")
    ap.add_argument("--estimate-field", default="Original Estimate")
    ap.add_argument("--estimate-field-id", type=int, default=None)
    ap.add_argument("--estimate-label", default="Original Estimate", help="Grafiklerde estimate başlığı")
    ap.add_argument("--project-field", default="DD Project Name",
                    help="Doluysa işi 'Proje işi' sayan alan")
    ap.add_argument("--task-keyword", action="append",
                    help="Başlıkta geçerse Task sayılır (tekrarlanabilir; varsayılan listeyi değiştirir)")
    ap.add_argument("--done-status", action="append")
    ap.add_argument("--closed-as-done", action="store_true")
    ap.add_argument("--include-archived", action="store_true")
    ap.add_argument("--clean-titles", action="store_true")
    ap.add_argument("--title", default="Geliştirmeler")
    ap.add_argument("--unit", default="", help="Original Estimate birimi (ör. 'sa', 'gün', 'SP')")
    ap.add_argument("-o", "--output", default="gelistirmeler-slaytlar.html")
    build(ap.parse_args())


if __name__ == "__main__":
    main()
