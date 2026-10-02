#!/usr/bin/env python3
"""Geliştirmeler raporunu 16:9 (1920x1080) iki slayt olarak üretir.

Slayt 1: Metrikler — iş adedi ve Original Estimate bazında planlanan vs. tamamlanan,
         statü dağılımı ve kişi bazında ilerleme.
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
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_report import (COUNT_FIELD, DEFAULT_DONE, NO_STATUS, NO_STATUS_COLOR,  # noqa: E402
                          apply_filter, build_model, esc, fetch_project, fmt, load_export,
                          normalize, pick_estimate_field)


def pct(m: dict) -> float:
    return m["completed"] / m["planned"] * 100 if m["planned"] else 0.0


def metric_panel(title: str, m: dict | None, unit: str, empty_msg: str) -> str:
    if m is None:
        return (f'<div class="panel empty"><div class="ptitle">{esc(title)}</div>'
                f'<div class="emptymsg">{empty_msg}</div></div>')
    u = f" {unit}" if unit else ""
    used = [o for o in m["options"] if o["estimate"] > 0]
    total = sum(o["estimate"] for o in used) or 1
    segs = "".join(f'<div class="seg" style="flex:{o["estimate"]};--c:{o["hex"]}"></div>' for o in used)
    table = "".join(
        f'<tr><td><span class="dot" style="--c:{o["hex"]}"></span>{esc(o["name"])}{" ✓" if o["done"] else ""}</td>'
        f'<td class="n">{fmt(o["estimate"])}<small>{esc(u)}</small></td>'
        f'<td class="n m">%{o["estimate"] / total * 100:.0f}</td></tr>' for o in used)
    note = ""
    if m.get("missing_estimate") and m.get("is_est"):
        note = f'<div class="pnote">{m["missing_estimate"]} işin tahmini boş (0 sayıldı)</div>'
    return f"""
<div class="panel">
  <div class="ptitle">{esc(title)}</div>
  <div class="prow">
    <div class="hero"><span class="hv">%{pct(m):.0f}</span><span class="hl">tamamlandı</span></div>
    <div class="kpis">
      <div><span class="kl">Planlanan</span><span class="kv">{fmt(m["planned"])}<small>{esc(u)}</small></span></div>
      <div><span class="kl">Tamamlanan</span><span class="kv">{fmt(m["completed"])}<small>{esc(u)}</small></span></div>
      <div><span class="kl">Kalan</span><span class="kv">{fmt(m["planned"] - m["completed"])}<small>{esc(u)}</small></span></div>
    </div>
  </div>
  <div class="bar"><i style="width:{min(pct(m), 100):.2f}%"></i></div>
  <div class="stitle">Statü dağılımı</div>
  <div class="strip">{segs}</div>
  <table class="stab"><tbody>{table}</tbody></table>
  {note}
</div>"""


def people_panel(mc: dict, me: dict | None, unit: str) -> str:
    est = {p["name"]: p for p in me["people"]} if me else {}
    cmax = max([p["planned"] for p in mc["people"]] + [1])
    emax = max([p["planned"] for p in me["people"]] + [1]) if me else 1
    rows = []
    for p in mc["people"]:
        e = est.get(p["name"])
        ebar = ""
        if me:
            ep, ed = (e["planned"], e["done"]) if e else (0, 0)
            ebar = (f'<div class="pb est"><div class="tr" style="width:{ep / emax * 100:.2f}%"></div>'
                    f'<div class="fi" style="width:{ed / emax * 100:.2f}%"></div></div>'
                    f'<div class="pv">{fmt(ed)}/{fmt(ep)}</div>')
        rows.append(
            f'<div class="person"><div class="pn">{esc(p["name"])}</div>'
            f'<div class="pb"><div class="tr" style="width:{p["planned"] / cmax * 100:.2f}%"></div>'
            f'<div class="fi" style="width:{p["done"] / cmax * 100:.2f}%"></div></div>'
            f'<div class="pv">{fmt(p["done"])}/{fmt(p["planned"])}</div>'
            + (f'<div></div>{ebar}' if me else "") + "</div>")
    u = f" ({unit})" if unit else ""
    key = ('<span style="--k:var(--plan)">Planlanan</span><span style="--k:var(--ink)">Tamamlanan</span>')
    sub = (f"Üst çubuk: iş adedi · alt çubuk: Original Estimate{esc(u)}" if me
           else "İş adedi bazında")
    return f"""
<div class="panel people">
  <div class="ptitle">Kişi bazında planlanan vs. tamamlanan</div>
  <div class="psub">{sub}</div>
  <div class="pkey">{key}</div>
  {''.join(rows)}
</div>"""


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

    done = a.done_status or DEFAULT_DONE
    for it in items:
        it["fields"][COUNT_FIELD] = 1
    mc = build_model(data, items, COUNT_FIELD, done, a.closed_as_done)
    est_field = pick_estimate_field(data, a.estimate_field, a.estimate_field_id)
    me = build_model(data, items, est_field, done, a.closed_as_done) if est_field else None
    if me:
        me["is_est"] = True
    else:
        warnings.append(f"'{a.estimate_field}' alanı veride yok")

    legend = "".join(
        f'<li><span class="dot" style="--c:{o["hex"]}"></span>{esc(o["name"])}'
        f'{" ✓" if o["done"] else ""}<span class="muted">{o["count"]}</span></li>'
        for o in mc["options"] if o["count"])

    rows = sorted(mc["rows"], key=lambda r: (mc["order"].get(r["status"], 999), r["title"].lower()))
    est_vals = {}
    if me:
        est_vals = {(r["title"], r.get("url")): r["estimate"] for r in me["rows"]}
    hexes = {o["name"]: o["hex"] for o in mc["options"]}
    unit = a.unit
    lis = []
    for r in rows:
        badge = ""
        if me:
            v = est_vals.get((r["title"], r.get("url")), 0)
            badge = f'<span class="est">{fmt(v)}{(" " + esc(unit)) if unit else ""}</span>' if v else ""
        lis.append(f'<li><span class="dot" style="--c:{hexes.get(r["status"], NO_STATUS_COLOR)}"></span>'
                   f'<span class="t">{esc(r["title"])}</span>{badge}</li>')

    empty_msg = (f"<b>{esc(a.estimate_field)}</b> verisi yok.<br>"
                 "Proje görünümüne bu alanı sütun olarak ekleyip veriyi yeniden dışa aktarın "
                 "(veya API ile çekin); bu panel otomatik dolar.")
    proj = data.get("project") or {}
    subtitle = " · ".join(x for x in [proj.get("title"), view.get("name"),
                                      dt.date.today().strftime("%d.%m.%Y")] if x)
    page = TEMPLATE.format(
        title=esc(a.title), subtitle=esc(subtitle), legend=legend,
        count_panel=metric_panel("İş adedi bazında", mc, "iş", ""),
        est_panel=metric_panel(f"{a.estimate_field} bazında", me, unit, empty_msg),
        people=people_panel(mc, me, unit), items="\n".join(lis), n=len(rows),
    )
    with open(a.output, "w", encoding="utf-8") as fh:
        fh.write(page)
    est_txt = (f"estimate %{pct(me):.0f} ({fmt(me['completed'])}/{fmt(me['planned'])})"
               if me else "estimate verisi yok")
    print(f"{a.output} yazıldı: {len(rows)} iş · adet %{pct(mc):.0f} "
          f"({mc['count_done']}/{mc['count']}) · {est_txt}")
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
.slide {{ width:1920px; height:1080px; background:var(--slide); padding:64px 88px 56px;
  transform:scale(var(--s)); transform-origin:0 0; display:flex; flex-direction:column; }}
header {{ display:flex; align-items:flex-end; flex-wrap:nowrap; justify-content:space-between; gap:32px; margin-bottom:36px; }}
h1 {{ white-space:nowrap; font-size:72px; font-weight:800; letter-spacing:-.02em; margin:0; line-height:1; }}
h1 span {{ color:var(--accent); font-weight:600; }}
.sub {{ color:var(--muted); font-size:20px; margin-top:12px; }}
.legend {{ list-style:none; margin:0; padding:14px 20px; display:flex; flex-wrap:wrap; gap:8px 26px;
  border:1px solid var(--rule); border-radius:12px; font-size:19px; color:var(--ink-2); flex:0 1 auto; justify-content:flex-end; }}
.legend li {{ display:flex; align-items:center; }}
.legend .muted {{ margin-left:8px; color:var(--muted); font-variant-numeric:tabular-nums; }}
.dot {{ display:inline-block; width:14px; height:14px; border-radius:50%; background:var(--c); margin-right:10px; flex:none; }}
/* metrikler */
.grid {{ flex:1; display:grid; grid-template-columns:1fr 1fr 640px; gap:28px; min-height:0; }}
.panel {{ border:1px solid var(--rule); border-radius:16px; padding:30px 32px; display:flex; flex-direction:column; }}
.ptitle {{ font-size:22px; font-weight:600; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); }}
.prow {{ display:flex; flex-direction:column; gap:22px; margin-top:22px; }}
.hero {{ display:flex; align-items:baseline; gap:14px; }}
.hv {{ font-size:120px; font-weight:800; line-height:1; letter-spacing:-.03em; font-variant-numeric:tabular-nums; }}
.hl {{ font-size:26px; color:var(--ink-2); }}
.kpis {{ display:grid; grid-template-columns:repeat(3,1fr); gap:14px; }}
.kpis > div {{ border-top:2px solid var(--rule); padding-top:10px; display:flex; flex-direction:column; }}
.kl {{ font-size:18px; color:var(--muted); }}
.kv {{ font-size:44px; font-weight:800; font-variant-numeric:tabular-nums; }}
.kv small {{ font-size:20px; font-weight:500; color:var(--muted); }}
.bar {{ height:18px; border-radius:9px; background:var(--track); overflow:hidden; margin-top:26px; }}
.bar i {{ display:block; height:100%; background:var(--accent); border-radius:9px; }}
.stitle {{ font-size:18px; color:var(--muted); margin-top:34px; margin-bottom:10px; }}
.strip {{ display:flex; gap:3px; height:30px; }}
.seg {{ background:var(--c); min-width:4px; }}
.seg:first-child {{ border-radius:6px 0 0 6px; }} .seg:last-child {{ border-radius:0 6px 6px 0; }}
.stab {{ width:100%; border-collapse:collapse; margin-top:14px; font-size:19px; }}
.stab td {{ padding:7px 0; border-top:1px solid var(--rule); color:var(--ink); }}
.stab .dot {{ width:12px; height:12px; vertical-align:0; }}
.stab .n {{ text-align:right; font-weight:600; font-variant-numeric:tabular-nums; width:90px; }}
.stab .n small {{ font-weight:400; color:var(--muted); font-size:15px; }}
.stab .m {{ color:var(--muted); font-weight:400; width:70px; }}
.pnote {{ font-size:16px; color:var(--muted); margin-top:12px; }}
.panel.empty {{ background:repeating-linear-gradient(135deg, transparent 0 14px, rgba(11,31,58,.03) 14px 28px); }}
.emptymsg {{ margin:auto 0; font-size:24px; line-height:1.45; color:var(--ink-2); }}
.psub {{ font-size:17px; color:var(--muted); margin-top:8px; }}
.pkey {{ display:flex; gap:22px; font-size:17px; color:var(--ink-2); margin:16px 0 8px; }}
.pkey span::before {{ content:""; display:inline-block; width:16px; height:10px; border-radius:3px; margin-right:8px; background:var(--k); }}
.person {{ display:grid; grid-template-columns:170px 1fr 92px; column-gap:14px; row-gap:6px; align-items:center;
  padding:12px 0; border-top:1px solid var(--rule); }}
.pn {{ font-size:19px; color:var(--ink-2); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
.pb {{ position:relative; height:16px; }}
.pb.est {{ height:10px; margin-top:-2px; }}
.tr, .fi {{ position:absolute; left:0; top:0; height:100%; border-radius:0 4px 4px 0; }}
.tr {{ background:var(--plan); }}
.fi {{ background:var(--ink); }}
.pb.est .tr {{ background:#D6EEF1; }} .pb.est .fi {{ background:var(--accent); }}
.pv {{ text-align:right; font-size:18px; color:var(--muted); font-variant-numeric:tabular-nums; }}
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
    {count_panel}
    {est_panel}
    {people}
  </div>
</section></div>

<div class="frame"><section class="slide" id="s2" aria-label="Maddeler">
  <header>
    <div><h1>{title}</h1><div class="sub">{subtitle} · {n} iş · ✓ = tamamlandı</div></div>
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
