#!/usr/bin/env python3
"""Sprint sunumunu 16:9 (1920x1080) dört slayt olarak üretir.

1. Sprint'te Odaklandığımız İşler — işler Xurrent Talep No'ya göre gruplanır; her grup
   iş birimi diliyle tek tema adı, kategori etiketi (User Story / Bug / Task / Proje işi),
   kaç GitHub işinden oluştuğu ve önceki sprintten aktarıldıysa belirteçle gösterilir.
2. Metriklerimiz — planlanan vs. tamamlanan (distinct Xurrent talebi + Xurrent no'suz ek
   talepler), statü dağılımı (Xurrent talebi | GitHub işi), iş tipi kırılımı, sprint
   ortasında gelen iş oranı (AddedMidSprint), teknik borç oranı (TB), lead time.
3. Metriklerimiz (Takıma özel) — Original Estimate bazında planlanan vs. tamamlanan,
   statü dağılımı (OE) ve kişi bazında planlanan vs. tamamlanan.
4. Canlıya Çıkmaya Hazır Olan İşler — Done statüsünde ve "Ready For Release" etiketli
   işler, Xurrent Talep No'ya göre gruplanmış tema adlarıyla.

Tema adları: --dump-themes ile taslak JSON üretilir, iş birimi diliyle düzenlenip
--themes ile verilir. PNG için: node export_png.mjs slaytlar.html slayt
"""
from __future__ import annotations

import argparse
import collections
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
CAT_CLASS = {"User Story": "story", "Proje işi": "proj", "Bug": "bug", "Task": "task"}
# Düzenli ifade; başlığın küçük harfli halinde aranır
DEFAULT_TASK_KEYWORDS = [r"işlemler(i)?\b", "toplantı", "takvim", "kurulum", "incele", "migration",
                         "dependabot", "destek süreci", "deployment", "task", "dönüşleri",
                         "modelbuilder", "dwh"]
LEAD_BINS = [(0, 1, "<1 gün"), (1, 3, "1–3"), (3, 7, "3–7"), (7, 14, "7–14"),
             (14, 21, "14–21"), (21, 30, "21–30"), (30, None, "30+")]
XNO_RE = re.compile(r"^\s*\+?\s*(\d{6,})\s*-\s*")
# Aynı işin katman/platform versiyonlarını ayıran son ekler (tema adından atılır)
ROLE_SUFFIX_RE = re.compile(
    r"[\s\-–|]+(jarvis\s+be|jarvis\s+inceleme\s+taskı|be\s+servisi|be|fe|mobil|web|app|ios|android)\s*$",
    re.I)


# --------------------------------------------------------------------------- yardımcılar

def tr_lower(s: str) -> str:
    return s.replace("İ", "i").replace("I", "ı").lower()


def norm_label(s: str) -> str:
    return re.sub(r"[\s_\-]+", "", tr_lower(s))


def has_label(it: dict, wanted: list[str]) -> bool:
    w = {norm_label(x) for x in wanted}
    return any(norm_label(l) in w for l in it.get("labels", []))


def base_title(t: str) -> str:
    """Talep no, '+', [BUG], '- 4h' ve BE/FE gibi son ekleri atılmış sade başlık."""
    t = XNO_RE.sub("", t)
    t = re.sub(r"^\s*\+\s*", "", t)
    t = re.sub(r"\[bug\]:?\s*", "", t, flags=re.I)
    t = re.sub(r"\s+-\s*\d+\s*h\s*$", "", t, flags=re.I)
    prev = None
    while prev != t:
        prev, t = t, ROLE_SUFFIX_RE.sub("", t).strip()
    return re.sub(r"\s+", " ", t).strip(" -–|")


def display_title(t: str) -> str:
    t = XNO_RE.sub("", t)
    t = re.sub(r"^\s*\+\s*", "", t)
    return re.sub(r"\[BUG\]:\s*", "[BUG] ", re.sub(r"\s+", " ", t)).strip()


def classify(it: dict, project_field: str, task_kw: list[str]) -> str:
    """İş tipi: GitHub issue type varsa o; yoksa başlık/alan kuralları.
    Sıra: Bug > Proje işi (proje alanı dolu) > Task > User Story."""
    t = (it.get("issueType") or "").lower()
    title = tr_lower(it["raw_title"])
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


def sprint_starts(specs: list[str], days: int) -> callable:
    """'Sprint 4=2026-10-05' tanımlarından sprint adı → başlangıç tarihi çözücü.
    Tanımı olmayan sprint, numarası ve en yakın tanımlı sprint üzerinden sprint
    uzunluğuyla hesaplanır (Sprint n = çapa + (n - çapa_n) × gün)."""
    known: dict[int, dt.datetime] = {}
    for sp in specs:
        name, _, date = sp.partition("=")
        m = re.search(r"(\d+)", name)
        if not m or not date:
            sys.exit(f"--sprint-start biçimi 'Sprint 4=2026-10-05' olmalı: {sp}")
        known[int(m.group(1))] = dt.datetime.fromisoformat(date.strip()).replace(tzinfo=dt.timezone.utc)

    def start(name: str):
        m = re.search(r"(\d+)", name or "")
        if not m or not known:
            return None
        n = int(m.group(1))
        if n in known:
            return known[n]
        anchor = min(known, key=lambda k: abs(k - n))
        return known[anchor] + dt.timedelta(days=(n - anchor) * days)
    return start


def sprint_label(v) -> str:
    """'3', 3.0, 'Sprint 3' → 'Sprint 3'; diğer değerler olduğu gibi."""
    v = re.sub(r"\.0$", "", str(v or "").strip())
    return f"Sprint {v}" if v.isdigit() else v


def sprint_no(v):
    m = re.search(r"(\d+)", v or "")
    return int(m.group(1)) if m else None


def lead_days(it: dict, start, finished: set):
    """Biten işin lead time'ı: Original Sprint doluysa o sprintin, değilse işin
    sprintinin başlangıcından Closed tarihine kadar geçen gün."""
    d = parse_ts(it.get("closed"))
    s0 = start(it["origin"] or it["sprint"])
    if tr_lower(it["status"]) in finished and d and s0:
        return max((d - s0).total_seconds() / 86400, 0.0)
    return None


def pct(done: float, total: float) -> float:
    return done / total * 100 if total else 0.0


# --------------------------------------------------------------------------- gruplama

def group_items(rows: list[dict], order: dict, themes: dict) -> list[dict]:
    """Xurrent Talep No'ya göre; no'suz işler aynı sade başlığı paylaşıyorsa (FE/BE) birleşir."""
    groups: dict[str, dict] = {}
    for r in rows:
        if r["xno"]:
            key = r["xno"]
        else:
            key = ("bug:" if r["kind"] == "Bug" else "") + tr_lower(base_title(r["raw_title"]))
        g = groups.setdefault(key, {"key": key, "xno": r["xno"], "items": []})
        g["items"].append(r)
    # temalar.json'da aynı ad verilen talep no'suz gruplar tek madde olur (ör. BE + FE, SAP + Boomi)
    merged: dict[str, dict] = {}
    for g in groups.values():
        its = g["items"]
        default = min((base_title(i["raw_title"]) for i in its), key=len) or its[0]["title"]
        th = themes.get(g["key"])
        g["name"] = (th.get("name") if isinstance(th, dict) else th) or default
        mkey = g["xno"] or ("name:" + tr_lower(g["name"]) if th else g["key"])
        if mkey in merged:
            merged[mkey]["items"].extend(its)
        else:
            merged[mkey] = g
    out = []
    for g in merged.values():
        its = g["items"]
        kinds = collections.Counter(i["kind"] for i in its)
        g["kind"] = sorted(kinds, key=lambda k: (-kinds[k], TYPES.index(k)))[0]
        g["status"] = min((i["status"] for i in its), key=lambda s: order.get(s, 999))
        g["done"] = all(i["done"] for i in its)
        g["carry"] = sorted({i["origin"] for i in its if i["carried"]})
        g["est"] = sum(i["est"] for i in its)
        out.append(g)
    return sorted(out, key=lambda g: (order.get(g["status"], 999), tr_lower(g["name"])))


# --------------------------------------------------------------------------- bileşenler

def completion_block(label: str, done: float, total: float, unit: str, note: str = "",
                     labels=("Planlanan", "Tamamlanan", "Kalan")) -> str:
    p = pct(done, total)
    u = f"<small>{esc(unit)}</small>" if unit else ""
    return f"""
<div class="comp">
  <div class="clabel">{label}</div>
  <div class="chero">%{p:.0f}</div>
  <div class="bar"><i style="width:{min(p, 100):.2f}%"></i></div>
  <div class="cnums">
    <div><span>{labels[0]}</span><b>{fmt(total)}{u}</b></div>
    <div><span>{labels[1]}</span><b>{fmt(done)}{u}</b></div>
    <div><span>{labels[2]}</span><b>{fmt(total - done)}{u}</b></div>
  </div>
  <div class="cnote">{note or '&nbsp;'}</div>
</div>"""


def ratio_block(label: str, num: float, den: float, num_lbl: str, den_lbl: str, unit: str) -> str:
    p = pct(num, den)
    u = f"<small>{esc(unit)}</small>" if unit else ""
    return f"""
<div class="comp">
  <div class="clabel">{esc(label)}</div>
  <div class="chero">%{p:.0f}</div>
  <div class="bar warm"><i style="width:{min(p, 100):.2f}%"></i></div>
  <div class="cnums two">
    <div><span>{esc(num_lbl)}</span><b>{fmt(num)}{u}</b></div>
    <div><span>{esc(den_lbl)}</span><b>{fmt(den)}{u}</b></div>
  </div>
</div>"""


def mini_comp(label: str, done: float, total: float, unit: str, note: str = "") -> str:
    p = pct(done, total)
    u = f" {esc(unit)}" if unit else ""
    return f"""
<div class="mini">
  <div class="mtop"><span class="mlabel">{label}</span><span class="mhero">%{p:.0f}</span></div>
  <div class="bar"><i style="width:{min(p, 100):.2f}%"></i></div>
  <div class="mnums"><span>Tamamlanan <b>{fmt(done)}</b>{u}</span><span>Planlanan <b>{fmt(total)}</b>{u}</span>
    <span>Kalan <b>{fmt(total - done)}</b>{u}</span></div>
  {f'<div class="cnote">{note}</div>' if note else ''}
</div>"""


def multi_table(rows: list[dict], cols: list[dict], label_fr: float = 1.25) -> str:
    """Satır başına bir etiket + her ölçü için çubuk ve değer. Her ölçü kendi ölçeğinde.
    cols: {"key": toplam anahtarı, "done": tamamlanan anahtarı|None, "head": başlık}"""
    tmpl = f"minmax(0,{label_fr}fr) " + " ".join("minmax(0,1fr) 88px" for _ in cols)
    head = "<span></span>" + "".join(f"<span>{esc(c['head'])}</span><span></span>" for c in cols)
    out = [f'<div class="dhead" style="grid-template-columns:{tmpl}">{head}</div>']
    maxes = [max([r[c["key"]] for r in rows] + [1]) for c in cols]
    for r in rows:
        dot = f'<span class="dot" style="--c:{r["color"]}"></span>' if r.get("color") else ""
        cells = []
        for c, mx in zip(cols, maxes):
            tot = r[c["key"]]
            if c.get("done"):
                dn = r[c["done"]]
                bar = (f'<div class="tr" style="width:{tot / mx * 100:.2f}%"></div>'
                       f'<div class="fi" style="width:{dn / mx * 100:.2f}%"></div>')
                val = f'{fmt(dn)}<span>/{fmt(tot)}</span>'
            else:
                bar = f'<div class="fi" style="width:{tot / mx * 100:.2f}%;background:{r.get("color") or "var(--ink)"}"></div>'
                val = fmt(tot)
            cells.append(f'<div class="db" title="{esc(r["label"])} · {esc(c["head"])}: {fmt(tot)}">{bar}</div>'
                         f'<div class="dv">{val}</div>')
        out.append(f'<div class="drow" style="grid-template-columns:{tmpl}">'
                   f'<div class="dl" title="{esc(r["label"])}">{dot}{esc(r["label"])}</div>{"".join(cells)}</div>')
    return '<div class="dual">' + "".join(out) + "</div>"


def histogram(bins: list[dict], key: str, color: str) -> str:
    mx = max([b[key] for b in bins] + [1])
    cols = "".join(
        f'<div class="hcol" title="{esc(b["label"])} gün: {fmt(b[key])}">'
        f'<div class="hval">{fmt(b[key]) if b[key] else ""}</div>'
        f'<div class="hbar" style="height:{b[key] / mx * 82:.2f}%;background:{color}"></div></div>'
        for b in bins)
    labels = "".join(f'<div>{esc(b["label"])}</div>' for b in bins)
    return f'<div class="hist"><div class="hplot">{cols}</div><div class="hx">{labels}</div></div>'


def group_li(g: dict, hexes: dict, origin_label: str) -> str:
    n = len(g["items"])
    carry = ""
    if g["carry"]:
        carry = (f' <span class="carry" title="{esc(origin_label)}: {esc(", ".join(g["carry"]))}">'
                 f'↪ Aktarıldı: {esc(", ".join(g["carry"]))}</span>')
    tip = "\n".join(f'{i["status"]} · {display_title(i["raw_title"])}' for i in g["items"])
    xno = f'<span class="xno">#{esc(g["xno"])}</span>' if g["xno"] else ""
    return (f'<li class="{"carried" if g["carry"] else ""}" title="{esc(tip)}">'
            f'<span class="dot" style="--c:{hexes.get(g["status"], NO_STATUS_COLOR)}"></span>'
            f'<span class="t"><span class="cat {CAT_CLASS[g["kind"]]}">{esc(g["kind"])}</span> '
            f'{esc(g["name"])}{carry}{xno}</span>'
            f'<span class="cnt">{n} talep</span></li>')


# Kanban kolonları: (başlık, statüler). Bilinmeyen statü "Geliştirmede" kolonuna düşer.
LANES = [("Yapılacak", ["backlog", "todo", "to do", "yapılacak"]),
         ("Geliştirmede", ["in progress", "geliştirmede", "devam ediyor", "review", "in review",
                           "code review", "blocked", "engelli"]),
         ("Test", ["test", "qa", "uat"]),
         ("Tamamlandı", ["done", "tamamlandı", "closed", "completed", "kapandı"]),
         ("Canlıda", ["released", "canlıda", "yayında"])]


KANBAN_KIND_ORDER = ["Proje işi", "User Story", "Task", "Bug"]
KIND_TITLE = {"Proje işi": "Proje", "User Story": "User Story", "Task": "Task", "Bug": "Bug"}


def kanban_card(g: dict, hexes: dict, show_status: bool) -> str:
    """Kanban kolonundaki tek madde: statü renginde bullet + kategori + tema adı."""
    n = len(g["items"])
    color = hexes.get(g["status"], NO_STATUS_COLOR)
    tip = "\n".join(f'{i["status"]} · {display_title(i["raw_title"])}' for i in g["items"])
    carry = (f' <span class="carry" title="Önceki sprintten aktarıldı">↪ {esc(", ".join(g["carry"]))}</span>'
             if g["carry"] else "")
    meta = []
    if show_status:
        meta.append(esc(g["status"]))
    if n > 1:
        meta.append(f"{n} talep")
    meta_html = f' <span class="kmeta">{" · ".join(meta)}</span>' if meta else ""
    return (f'<div class="kitem{" carried" if g["carry"] else ""}" title="{esc(tip)}">'
            f'<span class="dot" style="--c:{color}"></span>'
            f'<span class="kt">{esc(g["name"])}{carry}{meta_html}</span></div>')


def kanban(groups: list[dict], hexes: dict, order: dict) -> str:
    lanes = [(t, set(st), []) for t, st in LANES]
    for g in groups:
        st = tr_lower(g["status"])
        lane = next((l for l in lanes if st in l[1]), lanes[1])
        lane[2].append(g)
    # Kolon genişliği madde + alt başlık sayısıyla orantılı; geniş kolon kendi içinde sütunlara bölünür
    # Kolon genişliği içerdiği metin miktarıyla orantılı (madde adları + alt başlıklar),
    # böylece tüm kolonlar aynı yazı boyutunda benzer doluluğa ulaşır
    def weight(gs):
        if not gs:
            return 110
        chars = sum(len(g["name"]) + 22 for g in gs) + 30 * len({g["kind"] for g in gs})
        return max(chars, 240)
    subs = [weight(l[2]) for l in lanes]
    out = []
    for (title, sts, gs), sub in zip(lanes, subs):
        # Tür grupları sabit sırada: Proje işi → User Story → Task → Bug
        gs.sort(key=lambda g: (KANBAN_KIND_ORDER.index(g["kind"]), order.get(g["status"], 999),
                               not g["carry"], tr_lower(g["name"])))
        present = {g["status"] for g in gs}
        color = next((hexes[s] for s in sorted(present, key=lambda s: order.get(s, 999))), NO_STATUS_COLOR)
        items = sum(len(g["items"]) for g in gs)
        parts = []
        for kind in KANBAN_KIND_ORDER:
            kg = [g for g in gs if g["kind"] == kind]
            if kg:
                parts.append(f'<div class="kgroup"><div class="khead {CAT_CLASS[kind]}">{esc(KIND_TITLE[kind])}'
                             f'<span>{len(kg)}</span></div>'
                             + "".join(kanban_card(g, hexes, len(present) > 1) for g in kg) + '</div>')
        cards = "".join(parts) or '<div class="lempty">—</div>'
        out.append(f'<div class="lane"><div class="lhead" style="--c:{color}"><span>{esc(title)}</span>'
                   f'<b>{len(gs)}</b><small>{items} iş</small></div>'
                   f'<div class="cards">{cards}</div></div>')
    return (f'<div class="board" style="grid-template-columns:{" ".join(f"minmax({260 if l[2] else 150}px,{w}fr)" for w, l in zip(subs, lanes))}">'
            + "".join(out) + "</div>")


# --------------------------------------------------------------------------- build

def load(a) -> tuple[dict, list[dict], list[str]]:
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
    return data, items, warnings


def build(a) -> None:
    data, items, warnings = load(a)
    for it in items:
        it["raw_title"] = it["title"]
        it["title"] = display_title(it["title"])
        it["fields"][COUNT_FIELD] = 1

    done_names = a.done_status or DEFAULT_DONE
    est_field = pick_estimate_field(data, a.estimate_field, a.estimate_field_id)
    if not est_field:
        warnings.append(f"'{a.estimate_field}' alanı veride yok; estimate değerleri 0 görünür")
    mc = build_model(data, items, COUNT_FIELD, done_names, a.closed_as_done)
    task_kw = [tr_lower(k) for k in (a.task_keyword or DEFAULT_TASK_KEYWORDS)]

    rows = mc["rows"]
    cols = data.get("columns")
    has_xfield = a.xurrent_field in cols if cols else any(a.xurrent_field in r["fields"] for r in rows)
    has_origin = a.origin_field in cols if cols else any(a.origin_field in r["fields"] for r in rows)
    start_of = sprint_starts(a.sprint_start or [], a.sprint_days)
    title_fallback = []
    for r in rows:
        v = r["fields"].get(est_field) if est_field else None
        r["est"] = float(v) if isinstance(v, (int, float)) else 0.0
        xf = r["fields"].get(a.xurrent_field)
        m = XNO_RE.match(r["raw_title"])
        if a.ignore_xurrent:
            r["xno"] = None
        elif xf not in (None, ""):
            r["xno"] = re.sub(r"\.0$", "", str(xf)).strip()
        else:
            # Sütun boşsa başlıktaki numara; sütun varsa bunu veri eksiği olarak raporla
            r["xno"] = m.group(1) if m else None
            if m and has_xfield:
                title_fallback.append(f'#{(r.get("url") or "").rsplit("/", 1)[-1]} → {m.group(1)}')
        r["origin"] = sprint_label(r["fields"].get(a.origin_field))
        r["sprint"] = sprint_label(r["fields"].get(a.sprint_field))
        # Original Sprint, işin şimdiki sprintinden farklıysa önceki sprintten aktarılmıştır
        r["carried"] = bool(r["origin"]) and sprint_no(r["origin"]) != sprint_no(r["sprint"])
        r["kind"] = classify(r, a.project_field, task_kw)
        r["lead"] = lead_days(r, start_of, {tr_lower(s) for s in a.lead_status})
        r["mid"] = has_label(r, a.mid_label)
        r["tb"] = has_label(r, a.tb_label)
    if not has_xfield:
        warnings.append(f"'{a.xurrent_field}' sütunu yok; talep no başlıktaki numaradan alındı")
    if title_fallback:
        warnings.append(f"'{a.xurrent_field}' boş ama başlıkta numara olan işler başlıktaki numarayla "
                        f"gruplandı: {', '.join(title_fallback)}")
    if not a.sprint_start:
        warnings.append("--sprint-start verilmedi; lead time hesaplanamadı")
    if not has_origin:
        warnings.append(f"'{a.origin_field}' sütunu yok; aktarılan iş belirteci gösterilemedi")

    themes = {}
    if a.themes and os.path.exists(a.themes):
        with open(a.themes, encoding="utf-8") as fh:
            themes = json.load(fh)
    order = mc["order"]
    groups = group_items(rows, order, themes)
    if a.dump_themes:
        with open(a.dump_themes, "w", encoding="utf-8") as fh:
            json.dump({g["key"]: {"name": g["name"], "titles": [display_title(i["raw_title"]) for i in g["items"]]}
                       for g in groups}, fh, ensure_ascii=False, indent=2)

    est_label, unit = a.estimate_label, a.unit
    hexes = {o["name"]: o["hex"] for o in mc["options"]}
    used_opts = [o for o in mc["options"] if o["count"]]
    legend = "".join(
        f'<li><span class="dot" style="--c:{o["hex"]}"></span>{esc(o["name"])}'
        f'{" ✓" if o["done"] else ""}</li>' for o in used_opts)

    # ---- Slayt 1: odaklandığımız işler
    focus = kanban(groups, hexes, order)
    n_carry = sum(1 for g in groups if g["carry"])

    # ---- Slayt 2: metrikler
    xg = [g for g in groups if g["xno"]]
    ek = [r for r in rows if not r["xno"]]
    x_done, e_done_n = sum(g["done"] for g in xg), sum(r["done"] for r in ek)
    if a.ignore_xurrent:
        comp = completion_block("GitHub talepleri", e_done_n, len(ek), "talep")
    else:
        comp = (mini_comp("Toplam <small>(Xurrent + ek)</small>", x_done + e_done_n, len(xg) + len(ek), "talep")
                + mini_comp("Xurrent talepleri <small>(distinct)</small>", x_done, len(xg), "talep")
                + mini_comp("Ek talepler <small>(Xurrent no'suz)</small>", e_done_n, len(ek), "talep"))
    comp_note = ("GitHub işi (talep) adedi üzerinden; Xurrent Talep No bu takımda kullanılmıyor"
                 if a.ignore_xurrent else
                 "Xurrent talebi, tüm GitHub işleri bitince tamamlanır · ek talep = Xurrent no'suz GitHub işi")
    status_rows = []
    for o in used_opts:
        mem = [r for r in rows if r["status"] == o["name"]]
        status_rows.append({"label": o["name"] + (" ✓" if o["done"] else ""), "color": o["hex"],
                            "x": sum(1 for g in xg if g["status"] == o["name"]),
                            "c": len(mem), "e": sum(r["est"] for r in mem)})
    type_rows = []
    for t in TYPES:
        mem = [r for r in rows if r["kind"] == t]
        type_rows.append({"label": t, "c": len(mem), "cd": sum(1 for r in mem if r["done"]),
                          "e": sum(r["est"] for r in mem), "ed": sum(r["est"] for r in mem if r["done"])})
    projects = collections.Counter(r["fields"].get(a.project_field) for r in rows if r["kind"] == "Proje işi")
    proj_txt = " · ".join(f"{esc(n)} <b>{c}</b>" for n, c in projects.most_common()) or "—"

    mid = [r for r in rows if r["mid"]]
    plan = [r for r in rows if not r["mid"]]
    midp = (ratio_block("GitHub iş adedine göre", len(mid), len(plan), "Sprint ortasında gelen",
                        "İlk planlanan", "iş")
            + ratio_block(f"{est_label}'a göre", sum(r["est"] for r in mid), sum(r["est"] for r in plan),
                          "Sprint ortasında gelen", "İlk planlanan", unit))
    def name_list(rs):
        gs = group_items(rs, order, themes)
        return "".join(f'<li><span class="dot" style="--c:{hexes.get(g["status"], NO_STATUS_COLOR)}"></span>'
                       f'{esc(g["name"])}</li>' for g in gs)
    mid_list = name_list(mid)
    tb = [r for r in rows if r["tb"]]
    tb_list = name_list(tb)
    tbp = (ratio_block("GitHub iş adedine göre", len(tb), len(rows), "Teknik borç", "Toplam", "iş")
           + ratio_block(f"{est_label}'a göre", sum(r["est"] for r in tb), sum(r["est"] for r in rows),
                         "Teknik borç", "Toplam", unit))

    closed = [r for r in rows if r["lead"] is not None]
    bins = []
    for lo, hi, label in LEAD_BINS:
        bins.append({"label": label,
                     "c": sum(1 for r in closed if r["lead"] >= lo and (hi is None or r["lead"] < hi))})
    while len(bins) > 4 and not bins[-1]["c"]:
        bins.pop()
    leads = [r["lead"] for r in closed]
    fin = {tr_lower(x) for x in a.lead_status}
    no_close = [(r.get("url") or "").rsplit("/", 1)[-1] for r in rows
                if tr_lower(r["status"]) in fin and r["lead"] is None]
    lead_missing = (f" · Closed tarihi olmadığı için hariç: {', '.join('#' + n for n in no_close)}"
                    if no_close else "")
    if leads:
        lead_stats = (f'<div><span>Biten GitHub işi</span><b>{len(leads)}</b></div>'
                      f'<div><span>Medyan</span><b>{fmt(round(statistics.median(leads), 1))}<small>gün</small></b></div>'
                      f'<div><span>Ortalama</span><b>{fmt(round(statistics.mean(leads), 1))}<small>gün</small></b></div>'
                      f'<div><span>En uzun</span><b>{fmt(round(max(leads), 1))}<small>gün</small></b></div>')
        hist = histogram(bins, "c", "var(--ink)")
    else:
        lead_stats, hist = "", '<div class="emptymsg">Sprint başlangıç tarihi / Closed verisi yok.</div>'

    # ---- Slayt 3: takıma özel (Original Estimate)
    xe = [r for r in rows if r["xno"]]
    missing = sum(1 for r in rows if not r["est"])
    oe = (mini_comp("Toplam", sum(r["est"] for r in rows if r["done"]), sum(r["est"] for r in rows), unit,
                    f"{missing} işin {esc(est_label)}'ı boş (0 sayıldı)" if missing else "")
          + ("" if a.ignore_xurrent else
             mini_comp("Xurrent talepleri", sum(r["est"] for r in xe if r["done"]), sum(r["est"] for r in xe), unit)
             + mini_comp("Ek talepler <small>(Xurrent no'suz)</small>", sum(r["est"] for r in ek if r["done"]),
                         sum(r["est"] for r in ek), unit)))
    people = {}
    for r in rows:
        for p in r["assignees"] or ["Atanmamış"]:
            d = people.setdefault(p, {"label": p, "c": 0, "cd": 0, "e": 0.0, "ed": 0.0})
            d["c"] += 1
            d["e"] += r["est"]
            if r["done"]:
                d["cd"] += 1
                d["ed"] += r["est"]
    people_rows = sorted(people.values(), key=lambda d: (-d["e"], -d["c"], d["label"]))

    # ---- Slayt 4: canlıya çıkmaya hazır
    ready_st = {tr_lower(s) for s in a.ready_status}
    ready_rows = [r for r in rows if tr_lower(r["status"]) in ready_st and has_label(r, a.ready_label)]
    ready_groups = group_items(ready_rows, order, themes)
    released = [r for r in rows if tr_lower(r["status"]) not in ready_st and r["done"]
                and has_label(r, a.ready_label)]
    ready_html = "\n".join(group_li(g, hexes, a.origin_field) for g in ready_groups) \
        or '<li class="empty">Bu kriterde iş yok.</li>'
    rel_groups = group_items(released, order, themes)
    released_html = "\n".join(group_li(g, hexes, a.origin_field) for g in rel_groups) \
        or '<li class="empty">Yok.</li>'
    rel_status = ", ".join(sorted({r["status"] for r in released})) or "—"
    ready_note = (f'Canlıya hazır: statü {", ".join(a.ready_status)} + "{esc(a.ready_label[0])}" etiketi · '
                  f'Canlıya alınan: "{esc(a.ready_label[0])}" etiketli ve {esc(rel_status)} statüsünde')

    proj = data.get("project") or {}
    sprints = sorted({str(r["fields"].get(a.sprint_field) or "") for r in rows} - {""})
    subtitle = " · ".join(x for x in [a.team or proj.get("title"), ", ".join(sprints),
                                      dt.date.today().strftime("%d.%m.%Y")] if x)
    focus_key = (f'<li><span class="cat story">Kategori</span>&nbsp;= iş tipi grubu</li>'
                 f'<li><span class="kmeta">N talep</span>&nbsp;= GitHub işi adedi (birden fazlaysa)</li>'
                 f'<li><span class="carry">↪ Sprint N</span>&nbsp;= o sprintten aktarıldı</li>')
    page = TEMPLATE.format(
        subtitle=esc(subtitle), legend=legend, focus_key=focus_key, focus=focus,
        n_groups=len(groups), n_items=len(rows), n_carry=n_carry,
        comp=comp, comp_note=comp_note,
        status=multi_table(status_rows, ([] if a.ignore_xurrent else [{"key": "x", "head": "Xurrent talebi"}])
                           + [{"key": "c", "head": "GitHub işi"}]),
        types=multi_table(type_rows, [{"key": "c", "done": "cd", "head": "GitHub işi"},
                                      {"key": "e", "done": "ed", "head": est_label}]),
        projects=proj_txt, pfield=esc(a.project_field), midp=midp, tbp=tbp,
        mid_label=esc(a.mid_label[0]), tb_label=esc(a.tb_label[0]), mid_list=mid_list, tb_list=tb_list,
        lead_stats=lead_stats, hist=hist, sprint_days=a.sprint_days, lead_missing=lead_missing, oe=oe, est_label=esc(est_label),
        status_oe=multi_table(status_rows, [{"key": "e", "head": est_label}]),
        people=multi_table(people_rows, [{"key": "c", "done": "cd", "head": "GitHub işi"},
                                         {"key": "e", "done": "ed", "head": est_label}], 1.6),
        ready=ready_html, ready_note=ready_note, n_ready=len(ready_groups),
        released=released_html, n_released=len(rel_groups), rel_status=esc(rel_status),
    )
    with open(a.output, "w", encoding="utf-8") as fh:
        fh.write(page)

    print(f"{a.output} yazıldı: {len(rows)} GitHub işi → {len(groups)} tema "
          f"({len(xg)} Xurrent talebi, {len(ek)} ek iş)")
    print(f"  Xurrent: {sum(g['done'] for g in xg)}/{len(xg)} tamam · ek: {sum(r['done'] for r in ek)}/{len(ek)}")
    print(f"  sprint ortası: {len(mid)}/{len(plan)} iş, {fmt(sum(r['est'] for r in mid))}/"
          f"{fmt(sum(r['est'] for r in plan))} OE · teknik borç: {len(tb)}/{len(rows)} iş, "
          f"{fmt(sum(r['est'] for r in tb))}/{fmt(sum(r['est'] for r in rows))} OE")
    print(f"  canlıya hazır: {len(ready_rows)} iş / {len(ready_groups)} tema · aktarılan tema: {n_carry}")
    if leads:
        print(f"  lead time: {len(leads)} kapanan iş, medyan {statistics.median(leads):.1f} gün")
    for w in warnings:
        print("uyarı:", w, file=sys.stderr)


TEMPLATE = """<!doctype html>
<html lang="tr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sprint Sunumu</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;800&display=swap" rel="stylesheet">
<style>
:root {{ --bg:#E9EDF2; --slide:#FFFFFF; --ink:#0B1F3A; --ink-2:#3B4A5E; --muted:#6B7788;
  --rule:#DCE6EE; --accent:#1FA3B5; --warm:#E07B39; --track:#E8EDF2; --plan:#C9D5E1; --s:1; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink);
  font-family:"Plus Jakarta Sans", Inter, system-ui, -apple-system, "Segoe UI", sans-serif; }}
.deck {{ display:flex; flex-direction:column; align-items:center; gap:24px; padding:24px 0; }}
.frame {{ width:calc(1920px * var(--s)); height:calc(1080px * var(--s)); overflow:hidden;
  box-shadow:0 2px 12px rgba(11,31,58,.12); }}
.slide {{ width:1920px; height:1080px; background:var(--slide); padding:44px 60px 36px;
  transform:scale(var(--s)); transform-origin:0 0; display:flex; flex-direction:column; }}
header {{ display:flex; align-items:flex-end; justify-content:space-between; gap:32px; margin-bottom:22px; }}
h1 {{ white-space:nowrap; font-size:56px; font-weight:800; letter-spacing:-.02em; margin:0; line-height:1.05; }}
h1 span {{ color:var(--accent); font-weight:600; }}
.sub {{ color:var(--muted); font-size:19px; margin-top:10px; white-space:nowrap; }}
.legend {{ list-style:none; margin:0; padding:12px 18px; display:flex; flex-wrap:wrap; gap:8px 22px; align-items:center;
  border:1px solid var(--rule); border-radius:12px; font-size:17px; color:var(--ink-2); justify-content:flex-end; min-width:0; flex:0 1 auto; }}
.legend li {{ display:flex; align-items:center; }}
.legend .sep {{ width:1px; height:22px; background:var(--rule); }}
.dot {{ display:inline-block; width:13px; height:13px; border-radius:50%; background:var(--c); margin-right:9px; flex:none; }}
/* paneller */
.grid {{ flex:1; min-height:0; display:grid; grid-template-columns:repeat(3, minmax(0,1fr));
  grid-template-rows:minmax(0,1fr) minmax(0,1fr); gap:18px; }}
.grid.one {{ grid-template-rows:minmax(0,1fr); }}
.grid.one .dual {{ justify-content:flex-start; }}
.grid.one .drow {{ max-height:92px; font-size:20px; }}
.grid.one .db {{ height:18px; }}
.panel {{ border:1px solid var(--rule); border-radius:14px; padding:18px 22px; display:flex; flex-direction:column; min-height:0; }}
.ptitle {{ font-size:16px; font-weight:600; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); margin-bottom:10px; }}
.pfoot {{ font-size:14px; color:var(--muted); margin-top:auto; padding-top:10px; line-height:1.45; }}
.pfoot b {{ color:var(--ink); }}
.comps {{ display:grid; grid-template-columns:1fr 1fr; gap:22px; flex:1; }}
.comp {{ display:flex; flex-direction:column; justify-content:center; }}
.clabel {{ font-size:17px; color:var(--ink-2); font-weight:600; }}
.clabel small {{ font-weight:400; color:var(--muted); font-size:14px; }}
.chero {{ font-size:92px; font-weight:800; line-height:1.05; letter-spacing:-.03em; font-variant-numeric:tabular-nums; }}
.bar {{ height:14px; border-radius:7px; background:var(--track); overflow:hidden; margin:10px 0 6px; }}
.bar i {{ display:block; height:100%; background:var(--accent); border-radius:6px; }}
.comps.r .chero {{ font-size:72px; }}
.comps.r .cnums b {{ font-size:24px; }}
.bar.warm i {{ background:var(--warm); }}
.cnums {{ display:grid; grid-template-columns:repeat(3,1fr); gap:8px; margin-top:12px; }}
.cnums.two {{ grid-template-columns:1fr 1fr; }}
.cnums div {{ display:flex; flex-direction:column; border-top:1px solid var(--rule); padding-top:6px; }}
.cnums span {{ font-size:14px; color:var(--muted); }}
.cnums b {{ font-size:28px; font-variant-numeric:tabular-nums; white-space:nowrap; }}
.cnums small, .lstats small {{ font-size:14px; font-weight:400; color:var(--muted); margin-left:3px; }}
.cnote {{ font-size:14px; color:var(--muted); margin-top:8px; }}
.minis {{ display:flex; flex-direction:column; justify-content:space-evenly; flex:1; gap:18px; }}
.mini .mtop {{ display:flex; align-items:baseline; justify-content:space-between; }}
.mlabel {{ font-size:20px; font-weight:600; color:var(--ink-2); }}
.mlabel small {{ font-size:14px; font-weight:400; color:var(--muted); }}
.mhero {{ font-size:64px; font-weight:800; letter-spacing:-.03em; font-variant-numeric:tabular-nums; line-height:1; }}
.mini .bar {{ margin:10px 0 10px; }}
.grid:not(.one) .minis {{ gap:6px; }}
.grid:not(.one) .mhero {{ font-size:44px; }}
.grid:not(.one) .mlabel {{ font-size:17px; }}
.grid:not(.one) .mini .bar {{ margin:6px 0 6px; height:12px; }}
.grid:not(.one) .mnums {{ font-size:14px; gap:16px; }}
.grid:not(.one) .mnums b {{ font-size:17px; }}
.mnums {{ display:flex; gap:22px; font-size:16px; color:var(--muted); }}
.mnums b {{ color:var(--ink); font-size:20px; font-variant-numeric:tabular-nums; }}
.dual {{ display:flex; flex-direction:column; flex:1; justify-content:space-evenly; }}
.dhead, .drow {{ display:grid; column-gap:10px; align-items:center; }}
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
.lstats {{ display:grid; grid-template-columns:repeat(4,1fr); gap:10px; margin-bottom:10px; }}
.lstats div {{ display:flex; flex-direction:column; border-top:1px solid var(--rule); padding-top:5px; }}
.lstats span {{ font-size:13px; color:var(--muted); }}
.lstats b {{ font-size:24px; font-variant-numeric:tabular-nums; white-space:nowrap; }}
.lsub {{ font-size:13px; color:var(--muted); margin:-6px 0 8px; }}
.hist {{ display:flex; flex-direction:column; min-height:0; flex:1; }}
.hplot {{ flex:1; min-height:80px; display:flex; align-items:flex-end; gap:6px; border-bottom:1px solid var(--plan); }}
.hcol {{ flex:1; height:100%; display:flex; flex-direction:column; justify-content:flex-end; }}
.hval {{ text-align:center; font-size:15px; font-weight:600; font-variant-numeric:tabular-nums; margin-bottom:3px; }}
.hbar {{ border-radius:4px 4px 0 0; }}
.hx {{ display:flex; gap:6px; font-size:13px; color:var(--muted); margin-top:5px; }}
.hx div {{ flex:1; text-align:center; white-space:nowrap; }}
.emptymsg {{ margin:auto; font-size:20px; color:var(--ink-2); }}
/* listeler */
.list {{ flex:1; min-height:0; list-style:none; margin:0; padding:0; columns:var(--cols, 3); column-gap:56px;
  column-fill:auto; column-rule:1px solid var(--accent); --fs:22px; overflow:hidden; }}
.list li {{ break-inside:avoid; display:flex; align-items:flex-start; gap:2px; padding:calc(var(--fs) * .45) 0;
  border-bottom:1px solid var(--rule); font-size:var(--fs); line-height:1.3; font-weight:500; }}
.list .dot {{ width:calc(var(--fs) * .55); height:calc(var(--fs) * .55); margin-top:calc(var(--fs) * .38); }}
.list .t {{ flex:1; }}
.list li.empty {{ color:var(--muted); border:0; }}
.cat {{ display:inline-block; font-size:.62em; font-weight:700; letter-spacing:.04em; text-transform:uppercase;
  padding:.15em .5em; border-radius:5px; vertical-align:.12em; margin-right:.2em; white-space:nowrap;
  background:#EDF1F6; color:#3B4A5E; }}
.cat.story {{ background:#E3EEFB; color:#1D4E89; }}
.cat.proj {{ background:#DDF3F5; color:#0F6470; }}
.cat.bug {{ background:#FCE4E2; color:#A3261C; }}
.cat.task {{ background:#ECEDEF; color:#4A5363; }}
.carry {{ display:inline-block; font-size:.62em; font-weight:700; padding:.15em .55em; border-radius:999px;
  vertical-align:.12em; margin-left:.35em; white-space:nowrap; background:#FFD666; color:#5C3B00; border:1px solid #F0A800; }}
.list li.carried {{ background:linear-gradient(90deg, rgba(255,214,102,.28), transparent 70%); }}
.xno {{ font-size:.6em; color:var(--muted); margin-left:.5em; white-space:nowrap; font-variant-numeric:tabular-nums; }}
.cnt {{ font-size:calc(var(--fs) * .66); color:var(--ink-2); border:1px solid var(--rule); border-radius:999px;
  padding:1px 9px; margin:2px 0 0 10px; white-space:nowrap; font-variant-numeric:tabular-nums; }}
.legend .cnt {{ font-size:14px; margin:0; }}
.legend .cat, .legend .carry {{ font-size:12px; vertical-align:0; margin:0; }}
.names {{ list-style:none; margin:10px 0 0; padding:8px 0 0; border-top:1px solid var(--rule);
  columns:2; column-gap:18px; font-size:14px; color:var(--ink-2); }}
.names li {{ display:block; break-inside:avoid; padding:2px 0; line-height:1.35; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
.names .dot {{ width:9px; height:9px; margin:0 7px 0 0; vertical-align:0; }}
.rgrid {{ flex:1; min-height:0; display:grid; grid-template-columns:1fr 1fr; gap:40px; }}
.rsec {{ display:flex; flex-direction:column; min-height:0; }}
.rsec .list {{ column-rule:none; }}
.rtitle {{ font-size:22px; font-weight:700; padding-bottom:10px; border-bottom:3px solid var(--accent); margin-bottom:4px; }}
.rtitle b {{ display:inline-block; min-width:34px; text-align:center; background:var(--accent); color:#fff;
  border-radius:999px; padding:0 10px; margin-left:8px; font-size:18px; }}
.rtitle small {{ font-weight:400; color:var(--muted); font-size:16px; }}
.rsec.done .rtitle {{ border-color:var(--plan); color:var(--ink-2); }}
.rsec.done .rtitle b {{ background:var(--plan); color:var(--ink); }}
/* kanban */
.board {{ flex:1; min-height:0; display:grid; gap:16px; --kf:17px; }}
.lane {{ display:flex; flex-direction:column; min-height:0; background:#F7F9FB; border-radius:14px; padding:12px 14px; }}
.lhead {{ display:flex; flex-wrap:wrap; row-gap:2px; align-items:baseline; gap:10px; overflow:hidden; padding:4px 4px 10px; margin-bottom:10px;
  border-bottom:4px solid var(--c); font-size:21px; font-weight:700; }}
.lhead b {{ font-size:15px; background:var(--c); color:#fff; border-radius:999px; padding:1px 10px; }}
.lhead small {{ margin-left:auto; font-size:13px; font-weight:500; color:var(--muted); white-space:nowrap; }}
.cards {{ flex:1; min-height:0; overflow:hidden; column-width:230px; column-gap:12px; column-fill:balance; }}
.cards .kitem {{ break-inside:avoid; }}
.kitem {{ display:flex; align-items:flex-start; gap:2px; padding:calc(var(--kf) * .42) 2px;
  border-bottom:1px solid var(--rule); font-size:var(--kf); line-height:1.3; font-weight:500; }}
.kitem .dot {{ width:calc(var(--kf) * .55); height:calc(var(--kf) * .55); margin-top:calc(var(--kf) * .38); }}
.kitem .kt {{ flex:1; }}
.kitem .cat, .kitem .carry {{ font-size:.62em; vertical-align:.12em; }}
.kitem.carried {{ background:linear-gradient(90deg, rgba(255,214,102,.35), transparent 85%); border-radius:4px; }}
.kgroup {{ margin-bottom:calc(var(--kf) * .7); }}
.khead {{ display:flex; align-items:center; gap:8px; font-size:calc(var(--kf) * .72); font-weight:800;
  letter-spacing:.06em; text-transform:uppercase; padding:3px 8px; border-radius:6px; margin:2px 0 2px;
  break-after:avoid; background:#EDF1F6; color:#3B4A5E; }}
.khead span {{ font-weight:600; opacity:.75; }}
.khead.story {{ background:#E3EEFB; color:#1D4E89; }}
.khead.proj {{ background:#DDF3F5; color:#0F6470; }}
.khead.bug {{ background:#FCE4E2; color:#A3261C; }}
.khead.task {{ background:#ECEDEF; color:#4A5363; }}
.kmeta {{ font-size:.7em; color:var(--muted); white-space:nowrap; }}
.lempty {{ color:var(--muted); text-align:center; padding:20px 0; }}
.rnote {{ font-size:16px; color:var(--muted); margin-top:14px; }}
@media print {{
  @page {{ size:1920px 1080px; margin:0; }}
  body {{ background:none; }} .deck {{ padding:0; gap:0; }} .frame {{ box-shadow:none; break-after:page; }}
}}
</style>
</head>
<body>
<div class="deck">

<div class="frame"><section class="slide" aria-label="Sprint'te Odaklandığımız İşler">
  <header>
    <div><h1>Sprint'te Odaklandığımız İşler</h1>
      <div class="sub">{subtitle} · {n_groups} talep ({n_items} GitHub işi)</div></div>
    <ul class="legend">{legend}<li class="sep"></li>{focus_key}</ul>
  </header>
  {focus}
</section></div>

<div class="frame"><section class="slide" aria-label="Metriklerimiz">
  <header>
    <div><h1>Metriklerimiz</h1><div class="sub">{subtitle}</div></div>
    <ul class="legend">{legend}</ul>
  </header>
  <div class="grid">
    <div class="panel"><div class="ptitle">Planlanan vs. tamamlanan</div><div class="minis">{comp}</div>
      <div class="pfoot">{comp_note}</div></div>
    <div class="panel"><div class="ptitle">Statü dağılımı</div>{status}</div>
    <div class="panel"><div class="ptitle">İş tipi kırılımı</div>
      <div class="pkey"><span style="--k:var(--plan)">Planlanan</span><span style="--k:var(--ink)">Tamamlanan</span></div>
      {types}
      <div class="pfoot">Proje işleri ({pfield}): {projects}</div></div>
    <div class="panel"><div class="ptitle">Sprint ortasında gelen iş</div><div class="comps r">{midp}</div>
      <ul class="names">{mid_list}</ul>
      <div class="pfoot">"{mid_label}" etiketli işler ÷ ilk planlanan işler (etiketsizler)</div></div>
    <div class="panel"><div class="ptitle">Teknik borç payı</div><div class="comps r">{tbp}</div>
      <ul class="names">{tb_list}</ul>
      <div class="pfoot">"{tb_label}" etiketli işler ÷ toplam iş</div></div>
    <div class="panel"><div class="ptitle">Lead time (Sprint başlangıcı → Closed, gün)</div>
      <div class="lsub">GitHub'taki talep (iş) sayısı üzerinden · Done ve Released işler · başlangıç: Original Sprint, yoksa Sprint · sprint = {sprint_days} gün{lead_missing}</div>
      <div class="lstats">{lead_stats}</div>{hist}</div>
  </div>
</section></div>

<div class="frame"><section class="slide" aria-label="Metriklerimiz (Takıma özel)">
  <header>
    <div><h1>Metriklerimiz <span>(Takıma özel)</span></h1><div class="sub">{subtitle} · {est_label} bazında</div></div>
    <ul class="legend">{legend}</ul>
  </header>
  <div class="grid one">
    <div class="panel"><div class="ptitle">Planlanan vs. tamamlanan · {est_label}</div><div class="minis">{oe}</div></div>
    <div class="panel"><div class="ptitle">Statü dağılımı · {est_label}</div>{status_oe}</div>
    <div class="panel"><div class="ptitle">Kişi bazında planlanan vs. tamamlanan</div>
      <div class="pkey"><span style="--k:var(--plan)">Planlanan</span><span style="--k:var(--ink)">Tamamlanan</span></div>
      {people}</div>
  </div>
</section></div>

<div class="frame"><section class="slide" aria-label="Canlıya Çıkmaya Hazır Olan İşler">
  <header>
    <div><h1>Canlıya Çıkmaya Hazır Olan İşler</h1><div class="sub">{subtitle}</div></div>
    <ul class="legend">{focus_key}</ul>
  </header>
  <div class="rgrid">
    <div class="rsec"><div class="rtitle">Canlıya çıkmaya hazır <b>{n_ready}</b></div>
      <ul class="list" style="--cols:1;--max:30">
{ready}
      </ul></div>
    <div class="rsec done"><div class="rtitle">Bu sprintte canlıya alınanlar <b>{n_released}</b> <small>({rel_status})</small></div>
      <ul class="list" style="--cols:1;--max:22">
{released}
      </ul></div>
  </div>
  <div class="rnote">{ready_note}</div>
</section></div>

</div>
<script>
(() => {{
  // Her listeyi slayta sığana kadar küçült (az madde varsa büyük kalır), sonra kolonları dengele
  document.querySelectorAll('.list').forEach(list => {{
    let fs = parseFloat(getComputedStyle(list).getPropertyValue('--max')) || 22;
    const fits = () => list.scrollWidth <= list.clientWidth + 1 && list.scrollHeight <= list.clientHeight + 1;
    list.style.setProperty('--fs', fs + 'px');
    while (!fits() && fs > 11) {{ fs -= 0.5; list.style.setProperty('--fs', fs + 'px'); }}
    list.style.columnFill = 'balance';
    if (!fits()) list.style.columnFill = 'auto';
    list.dataset.fitted = fs;
  }});
  // Kanban: en kalabalık kolon sığana kadar kart yazısını küçült
  document.querySelectorAll('.board').forEach(b => {{
    let kf = 18;
    const over = () => [...b.querySelectorAll('.cards')].some(c => c.scrollHeight > c.clientHeight + 1 || c.scrollWidth > c.clientWidth + 1);
    b.style.setProperty('--kf', kf + 'px');
    while (over() && kf > 10) {{ kf -= 0.5; b.style.setProperty('--kf', kf + 'px'); }}
    b.dataset.fitted = kf;
  }});
  document.body.dataset.fitted = [...document.querySelectorAll('.list, .board')].map(l => l.dataset.fitted).join('/');
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
    ap.add_argument("--sprint-field", default="Sprint")
    ap.add_argument("--sprint-start", action="append",
                    help="Sprint başlangıcı, ör. 'Sprint 4=2026-10-05' (tekrarlanabilir; eksikler --sprint-days ile türetilir)")
    ap.add_argument("--sprint-days", type=int, default=14, help="Sprint uzunluğu (gün)")
    ap.add_argument("--lead-status", action="append", default=None,
                    help="Lead time'da biten sayılan statüler (varsayılan Done, Released)")
    ap.add_argument("--estimate-field", default="Original Estimate")
    ap.add_argument("--estimate-field-id", type=int, default=None)
    ap.add_argument("--estimate-label", default="Original Estimate")
    ap.add_argument("--xurrent-field", default="Xurrent Talep No",
                    help="Talep no alanı; yoksa başlıktaki baştaki numara kullanılır")
    ap.add_argument("--ignore-xurrent", action="store_true",
                    help="Xurrent Talep No'yu yok say; metrikler yalnız GitHub işi adedi üzerinden")
    ap.add_argument("--origin-field", default="Original Sprint",
                    help="Doluysa iş önceki sprintten aktarılmış sayılır")
    ap.add_argument("--project-field", default="DD Project Name", help="Doluysa 'Proje işi'")
    ap.add_argument("--task-keyword", action="append",
                    help="Başlıkta geçerse Task sayılır (regex, tekrarlanabilir; varsayılanı değiştirir)")
    ap.add_argument("--mid-label", action="append", default=None, help="Sprint ortasında gelen iş etiketi")
    ap.add_argument("--tb-label", action="append", default=None, help="Teknik borç etiketi")
    ap.add_argument("--ready-label", action="append", default=None, help="Canlıya hazır etiketi")
    ap.add_argument("--ready-status", action="append", default=None, help="Canlıya hazır statüsü")
    ap.add_argument("--themes", help="Tema adları JSON'u (grup anahtarı → ad)")
    ap.add_argument("--dump-themes", help="Grup anahtarlarını ve varsayılan adları bu JSON'a yaz")
    ap.add_argument("--team", help="Alt başlıkta proje adı yerine gösterilecek takım adı")
    ap.add_argument("--done-status", action="append")
    ap.add_argument("--closed-as-done", action="store_true")
    ap.add_argument("--include-archived", action="store_true")
    ap.add_argument("--unit", default="", help="Original Estimate birimi (ör. 'sa', 'gün', 'SP')")
    ap.add_argument("-o", "--output", default="sprint-slaytlar.html")
    a = ap.parse_args()
    a.mid_label = a.mid_label or ["AddedMidSprint"]
    a.tb_label = a.tb_label or ["TB", "Teknik Borç", "Tech Debt"]
    a.ready_label = a.ready_label or ["Ready For Release"]
    a.ready_status = a.ready_status or ["Done"]
    a.lead_status = a.lead_status or ["Done", "Released"]
    build(a)


if __name__ == "__main__":
    main()
