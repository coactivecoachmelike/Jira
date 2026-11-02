#!/usr/bin/env python3
"""Sprint sunumunu 16:9 (1920x1080) dört slayt olarak üretir.

1. Sprint'te Odaklandığımız İşler — işler Xurrent Talep No'ya göre gruplanır; her grup
   iş birimi diliyle tek tema adı, kategori etiketi (User Story / Bug / Task / Proje işi),
   kaç GitHub işinden oluştuğu ve önceki sprintten aktarıldıysa belirteçle gösterilir.
2. Metriklerimiz — planlanan vs. tamamlanan (distinct Xurrent talebi + Xurrent no'suz ek
   talepler), statü dağılımı (Xurrent talebi | GitHub işi), iş tipi kırılımı, sprint
   ortasında gelen iş oranı (AddedMidSprint), altyapısal işler oranı (TB), lead time.
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
# Metrik slaytlarındaki iş tipi kırılımı: User Story + Bug birlikte, Proje ayrı; Task gösterilmez
TYPE_BUCKETS = [("Geliştirme taleplerimiz", {"User Story", "Bug"}), ("Proje işleri", {"Proje işi"})]
DEV_KINDS = TYPE_BUCKETS[0][1]
PROJ_KINDS = TYPE_BUCKETS[1][1]
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


def sk(s) -> str:
    """Statü karşılaştırma anahtarı: dil bağımsız küçük harf (In Progress → in progress,
    Tamamlandı → tamamlandi)."""
    return str(s or "").strip().lower().replace("\u0307", "").replace("ı", "i")


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


def classify(it: dict, project_field: str, task_kw: list[str], project_labels=()) -> str:
    """İş tipi: GitHub issue type varsa o; yoksa başlık/alan kuralları.
    Sıra: Bug > Proje işi (proje alanı dolu) > Task > User Story."""
    t = (it.get("issueType") or "").lower()
    title = tr_lower(it["raw_title"])
    if t == "bug" or "[bug]" in title:
        return "Bug"
    if (it["fields"].get(project_field) or "").strip() or (project_labels and has_label(it, project_labels)):
        return "Proje işi"
    if t in ("task", "görev"):
        return "Task"
    if t in ("user story", "story", "feature"):
        return "User Story"
    if any(re.search(k, title) for k in task_kw):
        return "Task"
    return "User Story"


PROJ_SPLIT_RE = re.compile(r"\s+[-–|]\s+")


def project_name(r: dict, project_field: str, parent_field: str, projmap: dict) -> tuple[str, str]:
    """Proje işinin proje adı: DD Project Name → başlığın ön eki ("One Click Auction - ...")
    → Parent issue başlığı → talebin tema adı. temalar.json'daki "_projects" eşlemesi
    (#issue_no veya türetilen ad → iş birimi diliyle proje adı) en son uygulanır.
    Döner: (gösterilecek ad, türetilen ham ad)."""
    num = "#" + (r.get("url") or "").rsplit("/", 1)[-1]
    dd = str(r["fields"].get(project_field) or "").strip()
    if dd:
        derived = dd
    else:
        title = XNO_RE.sub("", display_title(r["raw_title"]))
        parts = PROJ_SPLIT_RE.split(title)
        parent = str(r["fields"].get(parent_field) or "").strip()
        if len(parts) > 1 and len(parts[0]) <= 40:
            derived = parts[0].strip()
        elif parent and not parent.lower().startswith("http"):
            derived = parent
        else:
            derived = r.get("theme") or title
    return projmap.get(num) or projmap.get(derived) or derived, derived


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
    if sk(it["status"]) in finished and d and s0:
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


def story_block(sentence: str, done: float, total: float, cap: str, nums: list, warm: bool = False) -> str:
    """Cümleyle anlatılan oran: üstte cümle, büyük yüzde + açıklaması, altta sayılar."""
    p = pct(done, total)
    cells = "".join(f'<div><span>{esc(l)}</span><b>{fmt(v)}<small>{esc(u)}</small></b></div>' for l, v, u in nums)
    return f"""
<div class="comp story">
  <div class="clabel">{sentence}</div>
  <div class="chero">%{p:.0f}<span class="hcap">{esc(cap)}</span></div>
  <div class="bar{' warm' if warm else ''}"><i style="width:{min(p, 100):.2f}%"></i></div>
  <div class="cnums" style="grid-template-columns:repeat({len(nums)},1fr)">{cells}</div>
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


def multi_table(rows: list[dict], cols: list[dict], label_fr: float = 1.25, total: dict | None = None) -> str:
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
    if total:
        cells = "".join(
            f'<div></div><div class="dv">'
            + (f'{fmt(total[c["done"]])}<span>/{fmt(total[c["key"]])}</span>' if c.get("done") else fmt(total[c["key"]]))
            + '</div>' for c in cols)
        out.append(f'<div class="drow total" style="grid-template-columns:{tmpl}">'
                   f'<div class="dl">{esc(total.get("label", "Toplam"))}</div>{cells}</div>')
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


def group_li(g: dict, hexes: dict, origin_label: str, show_cat: bool = True) -> str:
    n = len(g["items"])
    carry = ""
    if g["carry"]:
        carry = (f' <span class="carry" title="{esc(origin_label)}: {esc(", ".join(g["carry"]))}">'
                 f'↪ {esc(", ".join(g["carry"]))}</span>')
    tip = "\n".join(f'{i["status"]} · {display_title(i["raw_title"])}' for i in g["items"])
    xno = f'<span class="xno">#{esc(g["xno"])}</span>' if g["xno"] else ""
    if g.get("part_of"):
        xno = f'<span class="part" title="Talebin yalnızca bu GitHub işi">↳ {esc(g["part_of"])}</span>' + xno
    return (f'<li class="{"carried" if g["carry"] else ""}" title="{esc(tip)}">'
            f'<span class="dot" style="--c:{hexes.get(g["status"], NO_STATUS_COLOR)}"></span>'
            f'<span class="t">'
            + (f'<span class="cat {CAT_CLASS[g["kind"]]}">{esc(g["kind"])}</span> ' if show_cat else "")
            + f'{esc(g["name"])}{carry}{xno}</span>'
            + (f'<span class="cnt">{n} talep</span>' if n > 1 else "") + '</li>')


def grouped_list(groups: list[dict], hexes: dict, origin_label: str, empty: str) -> str:
    """Liste maddelerini iş tipine göre Proje → User Story → Task → Bug alt başlıklarıyla dizer."""
    out = []
    for kind in KANBAN_KIND_ORDER:
        kg = [g for g in groups if g["kind"] == kind]
        if kg:
            out.append(f'<li class="lgroup"><span class="khead {CAT_CLASS[kind]}">{esc(KIND_TITLE[kind])}'
                       f'<span>{len(kg)}</span></span></li>')
            out += [group_li(g, hexes, origin_label, show_cat=False) for g in kg]
    return "\n".join(out) or f'<li class="empty">{empty}</li>'


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
    lanes = [(t, {sk(x) for x in st}, []) for t, st in LANES]
    for g in groups:
        st = sk(g["status"])
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


# --------------------------------------------------------------------------- sprint karşılaştırması

def sprint_rows_block(entries: list[dict], warm: bool = False) -> str:
    """Her sprint için bir satır: ad, büyük yüzde, çubuk ve açıklama cümlesi."""
    out = []
    for e in entries:
        p = pct(e["num"], e["den"])
        out.append(f'<div class="srow"><div class="sname">{esc(e["label"])}</div>'
                   f'<div class="shero">%{p:.0f}</div>'
                   f'<div class="sbody"><div class="bar{" warm" if warm else ""}"><i style="width:{min(p, 100):.2f}%"></i></div>'
                   f'<div class="stext">{e["text"]}</div></div></div>')
    if len(entries) > 1:
        d = pct(entries[-1]["num"], entries[-1]["den"]) - pct(entries[-2]["num"], entries[-2]["den"])
        out.append(f'<div class="sdelta">{esc(entries[-2]["label"])} → {esc(entries[-1]["label"])}: '
                   f'<b>{"+" if d >= 0 else "−"}{abs(d):.0f} puan</b></div>')
    return '<div class="srows">' + "".join(out) + "</div>"


def grouped_hist(bins: list[dict], keys: list[str], colors: list[str]) -> str:
    mx = max([b[k] for b in bins for k in keys] + [1])
    cols = "".join(
        '<div class="gcol">' + "".join(
            f'<div class="gbar" title="{esc(b["label"])} gün: {fmt(b[k])}" style="height:{b[k] / mx * 82:.2f}%;'
            f'background:{c}"><span>{fmt(b[k]) if b[k] else ""}</span></div>' for k, c in zip(keys, colors))
        + "</div>" for b in bins)
    labels = "".join(f'<div>{esc(b["label"])}</div>' for b in bins)
    return f'<div class="hist"><div class="hplot ghplot">{cols}</div><div class="hx">{labels}</div></div>'


def sprint_compare(a, br_rows, all_groups, snos, start_of, hexes, order, project_list):
    """İşleri sprintlere atar ve karşılaştırma slaytını üretir (altyapısal işler yok).
    Kapsam: işin ilk sprinti (Original Sprint, yoksa Sprint) ile bittiği sprint (Closed tarihinin
    düştüğü sprint; bitmediyse şimdiki Sprint) arasındaki her sprint. Tamamlanma bittiği sprinte,
    aradan gelen etiketi eklendiği sprinte (ilk sprint ile oluşturulma sprintinin büyüğü) yazılır."""
    lo, hi = snos[0] - 30, snos[-1] + 30

    def sprint_of(d):
        if not d:
            return None
        for n in range(lo, hi):
            s, e = start_of(f"Sprint {n}"), start_of(f"Sprint {n + 1}")
            if s and e and s <= d < e:
                return n
        return None

    for r in br_rows:
        sc = sprint_no(r["sprint"])
        so = sprint_no(r["origin"]) or sc
        ds = sprint_of(parse_ts(r.get("closed"))) if r["done"] else None
        end = ds if ds else sc
        begin = min(so, end)
        r["br_scope"] = [n for n in snos if begin <= n <= end]
        r["br_done"] = end if r["done"] else None
        cs = sprint_of(parse_ts(r.get("created")))
        r["br_mid"] = max(so, cs or so) if r["mid"] else None

    per = []
    for n in snos:
        sc_ = [r for r in br_rows if n in r["br_scope"]]
        per.append({
            "n": n, "label": f"Sprint {n}", "scope": sc_,
            "done": [r for r in sc_ if r["br_done"] == n],
            "mid": [r for r in sc_ if r["br_mid"] == n],
            "carry": [r for r in sc_ if r["br_done"] != n and max(r["br_scope"]) > n],
            "open": [r for r in sc_ if r["br_done"] != n and max(r["br_scope"]) == n],
            "lead": [r["lead"] for r in sc_ if r["br_done"] == n and r["lead"] is not None
                     and r["kind"] in DEV_KINDS]})

    comp = sprint_rows_block([{"label": s["label"], "num": len(s["done"]), "den": len(s["scope"]),
                               "text": f'Toplam <b>{len(s["scope"])}</b> iş içinden <b>{len(s["done"])}</b> iş tamamlandı'}
                              for s in per])
    midb = sprint_rows_block([{"label": s["label"], "num": len(s["mid"]), "den": len(s["scope"]) - len(s["mid"]),
                               "text": f'İlk planlanan <b>{len(s["scope"]) - len(s["mid"])}</b> işe '
                                       f'<b>{len(s["mid"])}</b> iş eklendi'} for s in per], warm=True)
    carry_entries = [{"label": f'{s["label"]} → Sprint {s["n"] + 1}', "num": len(s["carry"]), "den": len(s["scope"]),
                      "text": f'<b>{len(s["carry"])}</b> iş sonraki sprinte aktarıldı'} for s in per[:-1]]
    last = per[-1]
    carry_entries.append({"label": f'{last["label"]} sonu', "num": len(last["open"]), "den": len(last["scope"]),
                          "text": f'<b>{len(last["open"])}</b> iş devam ediyor'})
    carryb = sprint_rows_block(carry_entries, warm=True).replace('<div class="sdelta">', '<div class="sdelta" hidden>')

    def theme_names(key, color):
        out = []
        for s in per:
            seen = []
            for r in s[key]:
                if r["theme"] not in seen:
                    seen.append(r["theme"])
            out += [f'<li><span class="dot" style="--c:{color}"></span>{esc(nm)} '
                    f'<span class="kmeta">{esc(s["label"])}</span></li>' for nm in seen]
        return "".join(out)

    type_rows = []
    for lbl, kinds in TYPE_BUCKETS:
        row = {"label": lbl}
        for s in per:
            mem = [r for r in s["scope"] if r["kind"] in kinds]
            row[f"c{s['n']}"] = len(mem)
            row[f"d{s['n']}"] = sum(1 for r in mem if r["br_done"] == s["n"])
        type_rows.append(row)
    types = multi_table(type_rows, [{"key": f"c{s['n']}", "done": f"d{s['n']}", "head": s["label"]} for s in per],
                        label_fr=3.4)
    projects = project_list(br_rows, "Bu sprintlerde proje işi yok")

    # Tamamlanan talepler: talep (Xurrent) tüm işleri bitince, son işin bittiği sprintte sayılır
    req_rows = []
    if not a.ignore_xurrent:
        row = {"label": "Xurrent talebi"}
        for s in per:
            row[f"c{s['n']}"] = sum(1 for g in all_groups if g["xno"] and g["done"]
                                    and max(i["br_done"] or 0 for i in g["items"]) == s["n"])
        req_rows.append(row)
    row = {"label": "Ek talep" if not a.ignore_xurrent else "Talep"}
    for s in per:
        row[f"c{s['n']}"] = sum(1 for r in s["done"] if not r["xno"])
    req_rows.append(row)
    total = {"label": "Toplam", **{f"c{s['n']}": sum(rw[f"c{s['n']}"] for rw in req_rows) for s in per}}
    reqs = multi_table(req_rows, [{"key": f"c{s['n']}", "head": s["label"]} for s in per], label_fr=1.3, total=total)

    stats = "".join(
        f'<div class="lsrow"><span class="sname"><i style="background:{c}"></i>{esc(s["label"])}</span>'
        f'<span>Biten iş<b>{len(s["lead"])}</b></span>'
        f'<span>Medyan<b>{fmt(round(statistics.median(s["lead"]), 1)) if s["lead"] else "—"}</b><small>gün</small></span>'
        f'<span>Ortalama<b>{fmt(round(statistics.mean(s["lead"]), 1)) if s["lead"] else "—"}</b><small>gün</small></span></div>'
        for s, c in zip(per, BR_COLORS))
    bins = []
    for lo_, hi_, label in LEAD_BINS:
        b = {"label": label}
        for s in per:
            b[f"c{s['n']}"] = sum(1 for x in s["lead"] if x >= lo_ and (hi_ is None or x < hi_))
        bins.append(b)
    hist = grouped_hist(bins, [f"c{s['n']}" for s in per], BR_COLORS[:len(per)])

    rng = f"{per[0]['label']} – {per[-1]['label']}" if len(per) > 2 else " & ".join(s["label"] for s in per)
    team = a.team or ""
    sub = " · ".join(x for x in [team, "Business review", rng, dt.date.today().strftime("%d.%m.%Y")] if x)
    html = f"""
<div class="frame"><section class="slide" aria-label="Sprint karşılaştırması">
  <header>
    <div><h1>Metriklerimiz <span>- {esc(rng)}</span></h1><div class="sub">{esc(sub)}</div></div>
  </header>
  <div class="grid">
    <div class="panel"><div class="ptitle">Tamamlanma oranı</div>{comp}
      <div class="pfoot">İş adedi · iş, bittiği sprintte tamamlanmış sayılır (Closed tarihi)</div></div>
    <div class="panel"><div class="ptitle">Planlanan ve aradan gelen işler</div>{midb}
      <ul class="names">{theme_names("mid", "var(--warm)")}</ul>
      <div class="pfoot">Aradan gelen = "{esc(a.mid_label[0])}" etiketli işler · oran = aradan gelen ÷ ilk planlanan</div></div>
    <div class="panel"><div class="ptitle">Sonraki sprinte aktarılan işler</div>{carryb}
      <ul class="names">{theme_names("carry", "var(--warm)")}</ul>
      <div class="pfoot">Sprintin kapsamında olup o sprintte bitmeyen işler</div></div>
    <div class="panel"><div class="ptitle">İş tipi kırılımı</div>
      <div class="pkey"><span style="--k:var(--plan)">Planlanan</span><span style="--k:var(--ink)">Tamamlanan</span></div>
      {types}
      <ul class="names one">{projects}</ul>
      <div class="pfoot">Geliştirme taleplerimiz = User Story + Bug · altta proje adları (iki sprint toplamı)</div></div>
    <div class="panel"><div class="ptitle">Tamamlanan talepler</div>{reqs}
      <div class="pfoot">Talep adedi · Xurrent talebi tüm işleri bitince, son işin bittiği sprintte sayılır · ek talep = Xurrent no'suz iş</div></div>
    <div class="panel"><div class="ptitle">Lead time · Geliştirme işleri (gün)</div>
      <div class="lsub">Sprint başlangıcı → Closed · takvim günü · User Story + Bug · Done ve Released işler, bittiği sprinte göre</div>
      <div class="lsrows">{stats}</div>{hist}</div>
  </div>
</section></div>
"""
    card = ("Sprint karşılaştırması", [
        f"{esc(rng)} birlikte verildiği için ana slaytlar son sprint ({esc(per[-1]['label'])}) için, "
        "karşılaştırma slaytı tüm sprintler için üretildi; bu slaytta altyapısal işler yer almaz.",
        "Bir iş, ilk sprintinden (Original Sprint, yoksa Sprint) bittiği sprinte kadar her sprintin "
        "kapsamındadır; bittiği sprint Closed tarihinin düştüğü sprinttir.",
        "Aradan gelen iş, etiketin eklendiği sprintte (ilk sprint ile oluşturulma tarihinin sprinti, hangisi sonraysa) sayılır."])
    summary = [{"sprint": s["label"], "kapsam": len(s["scope"]), "tamamlanan": len(s["done"]),
                "tamamlanma_pct": round(pct(len(s["done"]), len(s["scope"]))), "aradan_gelen": len(s["mid"]),
                "sonraki_sprinte_aktarilan": len(s["carry"]), "acik_kalan": len(s["open"]),
                "lead_gelistirme_medyan": round(statistics.median(s["lead"]), 1) if s["lead"] else None}
               for s in per]
    return html, card, summary


BR_COLORS = ["#9AA9BD", "var(--ink)", "var(--accent)", "var(--warm)"]


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
    # Backlog statüsündeki ve RetroAction etiketli işler hiçbir slayta/metriğe girmez
    ex_st = {sk(x) for x in a.exclude_status}
    excluded = [i for i in items if sk(i["fields"].get(a.status_field)) in ex_st
                or has_label(i, a.exclude_label)]
    items = [i for i in items if i not in excluded]
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
        r["kind"] = classify(r, a.project_field, task_kw, a.project_label)
        r["lead"] = lead_days(r, start_of, {sk(s) for s in a.lead_status})
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

    # Birden fazla sprint verilirse: ana slaytlar son sprint için, karşılaştırma slaytı hepsi için
    br_rows = rows
    snos = sorted({sprint_no(r["sprint"]) for r in rows} - {None})
    multi = len(snos) > 1 and not a.single_sprint
    if multi:
        rows = [r for r in rows if sprint_no(r["sprint"]) == snos[-1]]
        if not a.sprint_start:
            warnings.append("Birden fazla sprint var ama --sprint-start verilmedi; karşılaştırma slaytı üretilmedi")

    themes = {}
    if a.themes and os.path.exists(a.themes):
        with open(a.themes, encoding="utf-8") as fh:
            themes = json.load(fh)
    order = mc["order"]
    all_groups = group_items(br_rows, order, themes)
    for g in all_groups:
        for i in g["items"]:
            i["theme"] = g["name"]
    groups = group_items(rows, order, themes)
    projmap = themes.get("_projects") or {}
    raw_projects: dict[str, list[str]] = {}
    for r in br_rows:
        r["project"] = None
        if r["kind"] == "Proje işi":
            r["project"], raw = project_name(r, a.project_field, a.parent_field, projmap)
            raw_projects.setdefault(raw, []).append(r["title"])
    if a.dump_themes:
        dump = {g["key"]: {"name": g["name"], "titles": [display_title(i["raw_title"]) for i in g["items"]]}
                for g in all_groups}
        dump["_projects"] = {k: {"name": projmap.get(k, k), "titles": v} for k, v in raw_projects.items()}
        with open(a.dump_themes, "w", encoding="utf-8") as fh:
            json.dump(dump, fh, ensure_ascii=False, indent=2)

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
    # Toplam, iş tipi kırılımıyla aynı birim: GitHub işi adedi
    all_done = sum(r["done"] for r in rows)
    if a.ignore_xurrent:
        comp = completion_block("Toplam", all_done, len(rows), "iş")
    else:
        comp = (mini_comp("Toplam iş", all_done, len(rows), "iş")
                + mini_comp("Xurrent talepleri <small>(distinct)</small>", x_done, len(xg), "talep")
                + mini_comp("Ek talepler <small>(Xurrent no'suz)</small>", e_done_n, len(ek), "talep"))
    comp_note = ("İş adedi üzerinden; Xurrent Talep No bu takımda kullanılmıyor"
                 if a.ignore_xurrent else
                 "Toplam = iş adedi (iş tipi kırılımıyla aynı) · Xurrent talebi, tüm işleri "
                 "bitince tamamlanır · ek talep = Xurrent no'suz iş")
    status_rows = []
    for o in used_opts:
        mem = [r for r in rows if r["status"] == o["name"]]
        status_rows.append({"label": o["name"] + (" ✓" if o["done"] else ""), "color": o["hex"],
                            "x": sum(1 for g in xg if g["status"] == o["name"]),
                            "c": len(mem), "e": sum(r["est"] for r in mem)})
    type_rows = []
    for lbl, kinds in TYPE_BUCKETS:
        mem = [r for r in rows if r["kind"] in kinds]
        type_rows.append({"label": lbl, "c": len(mem), "cd": sum(1 for r in mem if r["done"]),
                          "e": sum(r["est"] for r in mem), "ed": sum(r["est"] for r in mem if r["done"])})
    n_task = sum(1 for r in rows if r["kind"] == "Task")
    projects = collections.Counter(r["fields"].get(a.project_field) for r in rows if r["kind"] == "Proje işi")
    proj_txt = " · ".join(f"{esc(n)} <b>{c}</b>" for n, c in projects.most_common()) or "—"

    mid = [r for r in rows if r["mid"]]
    plan = [r for r in rows if not r["mid"]]
    midp = (ratio_block("İş adedine göre", len(mid), len(plan), "Sprint ortasında gelen",
                        "İlk planlanan", "iş")
            + ratio_block(f"{est_label}'a göre", sum(r["est"] for r in mid), sum(r["est"] for r in plan),
                          "Sprint ortasında gelen", "İlk planlanan", unit))
    def name_list(rs):
        gs = group_items(rs, order, themes)
        return "".join(f'<li><span class="dot" style="--c:{hexes.get(g["status"], NO_STATUS_COLOR)}"></span>'
                       f'{esc(g["name"])}</li>' for g in gs)
    mid_list = name_list(mid)
    def project_list(rs, empty="Bu sprintte proje işi yok"):
        by: dict[str, list] = {}
        for r in rs:
            if r["kind"] == "Proje işi":
                by.setdefault(r["project"], []).append(r)
        out = []
        for name, its in sorted(by.items(), key=lambda kv: (-len(kv[1]), tr_lower(kv[0]))):
            st = min((i["status"] for i in its), key=lambda s: order.get(s, 999))
            d = sum(1 for i in its if i["done"])
            out.append(f'<li><span class="dot" style="--c:{hexes.get(st, NO_STATUS_COLOR)}"></span>{esc(name)} '
                       f'<span class="kmeta">{len(its)} iş · {d} tamamlandı</span></li>')
        return "".join(out) or f'<li class="none">{esc(empty)}</li>'
    proj_list = project_list(rows)
    tb = [r for r in rows if r["tb"]]
    tb_list = name_list(tb)
    tbp = (ratio_block("İş adedine göre", len(tb), len(rows), "Altyapısal iş", "Toplam", "iş")
           + ratio_block(f"{est_label}'a göre", sum(r["est"] for r in tb), sum(r["est"] for r in rows),
                         "Altyapısal iş", "Toplam", unit))

    def lead_block(rs, empty="Sprint başlangıç tarihi / Closed verisi yok."):
        closed = [r for r in rs if r["lead"] is not None]
        bins = []
        for lo, hi, label in LEAD_BINS:
            bins.append({"label": label,
                         "c": sum(1 for r in closed if r["lead"] >= lo and (hi is None or r["lead"] < hi))})
        while len(bins) > 4 and not bins[-1]["c"]:
            bins.pop()
        leads = [r["lead"] for r in closed]
        fin = {sk(x) for x in a.lead_status}
        no_close = [(r.get("url") or "").rsplit("/", 1)[-1] for r in rs
                    if sk(r["status"]) in fin and r["lead"] is None]
        missing = (f" · Closed tarihi olmadığı için hariç: {', '.join('#' + n for n in no_close)}"
                   if no_close else "")
        if leads:
            stats = (f'<div><span>Biten iş</span><b>{len(leads)}</b></div>'
                     f'<div><span>Medyan</span><b>{fmt(round(statistics.median(leads), 1))}<small>gün</small></b></div>'
                     f'<div><span>Ortalama</span><b>{fmt(round(statistics.mean(leads), 1))}<small>gün</small></b></div>'
                     f'<div><span>En uzun</span><b>{fmt(round(max(leads), 1))}<small>gün</small></b></div>')
            hg = histogram(bins, "c", "var(--ink)")
        else:
            stats, hg = "", f'<div class="emptymsg">{esc(empty if a.sprint_start else "Sprint başlangıç tarihi verilmedi.")}</div>'
        return stats, hg, missing, leads, no_close
    lead_stats, hist, lead_missing, leads, no_close = lead_block(rows)
    dl_stats, dl_hist, dl_missing, _, _ = lead_block([r for r in rows if r["kind"] in DEV_KINDS],
                                                     "Bu sprintte biten geliştirme işi yok.")
    pl_stats, pl_hist, pl_missing, _, _ = lead_block([r for r in rows if r["kind"] in PROJ_KINDS],
                                                     "Bu sprintte biten proje işi yok.")

    # ---- Özet slaytı: yalnız adet bazlı, sadeleştirilmiş
    sum_comp = story_block(f"Son durumda toplam <b>{fmt(len(rows))}</b> iş içinden <b>{fmt(all_done)}</b> iş tamamlandı",
                           all_done, len(rows), "tamamlanma oranı",
                           [("Toplam", len(rows), "iş"), ("Tamamlanan", all_done, "iş"),
                            ("Kalan", len(rows) - all_done, "iş")])
    if a.ignore_xurrent:
        sum_status_rows = [{"label": r["label"], "color": r["color"], "c": r["c"]} for r in status_rows]
        sum_status_cols = [{"key": "c", "head": ""}]
        sum_status_total = {"label": "Toplam", "c": len(rows)}
    else:
        # yalnız Xurrent talepleri; talep, işleri içinde en geride kalanın statüsünde
        sum_status_rows = [{"label": r["label"], "color": r["color"], "x": r["x"]} for r in status_rows]
        sum_status_cols = [{"key": "x", "head": ""}]
        sum_status_total = {"label": "Toplam", "x": len(xg)}
    sum_status_rows = [r for r in sum_status_rows if any(r[c["key"]] for c in sum_status_cols)]
    sum_status = multi_table(sum_status_rows, sum_status_cols, total=sum_status_total)
    sum_types = multi_table(type_rows, [{"key": "c", "done": "cd", "head": "İş adedi"}], label_fr=2.4)
    sum_mid = story_block(f"İlk planlanan <b>{fmt(len(plan))}</b> işe sprint içinde <b>{fmt(len(mid))}</b> iş eklendi",
                          len(mid), len(plan), "aradan giren iş",
                          [("İlk planlanan", len(plan), "iş"), ("Aradan gelen", len(mid), "iş")], warm=True)
    sum_tb = ratio_block("İş adedine göre", len(tb), len(rows), "Altyapısal iş", "Toplam", "iş")
    sum_status_note = ("İş adedi · tüm işler" if a.ignore_xurrent else
                       "Talep adedi · talep, işleri içinde en geride kalanın statüsünde sayılır")
    status_title = "Statü dağılımı"
    sum_comp_note = ("İş adedi üzerinden; Xurrent Talep No bu takımda kullanılmıyor"
                     if a.ignore_xurrent else "İş adedi üzerinden (iş tipi kırılımıyla aynı birim)")
    types_note = (f"Geliştirme taleplerimiz = User Story + Bug · Proje = {esc(a.project_field)} dolu ya da "
                  f"\"{esc(a.project_label[0])}\" etiketli işler; altında proje adları"
                  + (f" · toplantı/analiz gibi {n_task} task bu kırılıma dahil değil" if n_task else ""))

    # ---- Sprint karşılaştırma slaytı (business review): birden fazla sprint verildiğinde
    br_slide, br_card, br_summary = "", None, []
    if multi and a.sprint_start:
        br_slide, br_card, br_summary = sprint_compare(a, br_rows, all_groups, snos, start_of, hexes, order, project_list)

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
    # Gruplama slayt 1 ile aynı (Xurrent Talep No). Talebin tüm işleri kriterdeyse tema adıyla,
    # yalnız bir kısmı kriterdeyse o işler GitHub başlığıyla ve "↳ talep adı" notuyla listelenir.
    def key_of(r):
        return r.get("url") or id(r)

    def subset_entries(ids, gs=None):
        out = []
        for g in (groups if gs is None else gs):
            sel = [i for i in g["items"] if key_of(i) in ids]
            if not sel:
                continue
            if len(sel) == len(g["items"]):
                out.append(g)
            else:
                for i in sel:
                    out.append(dict(g, items=[i], name=display_title(i["raw_title"]), kind=i["kind"],
                                    status=i["status"], carry=[i["origin"]] if i["carried"] else [],
                                    part_of=g["name"]))
        return out
    ready_st = {sk(s) for s in a.ready_status}
    ready_rows = [r for r in rows if sk(r["status"]) in ready_st and has_label(r, a.ready_label)]
    ready_groups = subset_entries({key_of(r) for r in ready_rows})
    # Canlıya alınanlar = kanbandaki "Canlıda" kolonunun statüleri (etiketten bağımsız)
    live_st = {sk(x) for x in dict(LANES)["Canlıda"]}
    released = [r for r in rows if sk(r["status"]) in live_st]
    rel_groups = subset_entries({key_of(r) for r in released})
    ready_html = grouped_list(ready_groups, hexes, a.origin_field, "Bu kriterde iş yok.")
    released_html = grouped_list(rel_groups, hexes, a.origin_field, "Yok.")
    rel_status = ", ".join(sorted({r["status"] for r in released})) or "—"
    lbls = " / ".join(f'"{esc(x)}"' for x in a.ready_label[:2])
    ready_note = (f'Canlıya hazır (release bekleyen): statü {", ".join(a.ready_status)} + {lbls} etiketi · '
                  f'Canlıya alınan: {esc(rel_status)} statüsündeki tüm işler (kanbandaki "Canlıda" kolonu) · '
                  f'↳ = talebin yalnızca bu GitHub işi kriterde')

    # ---- Slayt 6: hesaplama varsayımları
    def nums(rs):
        return ", ".join("#" + (r.get("url") or "").rsplit("/", 1)[-1] for r in rs) or "—"
    done_lbl = ", ".join(o["name"] for o in mc["options"] if o["done"] and o["count"]) or "Done, Released"
    starts = ", ".join(a.sprint_start or []) or "verilmedi"
    lanes_txt = " · ".join(
        f"{t}: {', '.join(o['name'] for o in used_opts if sk(o['name']) in {sk(x) for x in st})}"
        for t, st in LANES if any(sk(o["name"]) in {sk(x) for x in st} for o in used_opts))
    xurrent_txt = (["Bu takımda Xurrent Talep No kullanılmıyor; tüm sayımlar GitHub işi adedi üzerinden.",
                    "Aynı talebin BE/FE/SAP/Boomi gibi parçaları tek madde olarak gösterilir, yanında \"N talep\" yazar."]
                   if a.ignore_xurrent else
                   ["<b>Xurrent talebi</b>: aynı Xurrent Talep No'ya sahip GitHub işleri tek talep sayılır "
                    "(BE/FE/App/Web parçaları); talep, tüm işleri bitince tamamlanmış sayılır.",
                    "<b>Ek talep</b>: Xurrent Talep No'su boş GitHub işi; her biri ayrı sayılır.",
                    "<b>İş</b>: panodaki her GitHub issue'su. Tamamlanma oranı, aradan gelen iş, altyapısal işler "
                    "ve iş tipi kırılımı bu birimle hesaplanır; bu yüzden talep sayısından büyük olabilir.",
                    "Statü dağılımı yalnız Xurrent taleplerini sayar; talep, işleri içinde "
                    "<b>en geride kalanın</b> statüsünde sayılır."])
    cards = [
        ("Kapsam", [f"Sprint: {esc(', '.join(sorted({r['sprint'] for r in rows} - {''})) or '—')} · "
                    f"{len(rows)} iş değerlendirildi.",
                    f"Hariç tutulanlar: statüsü <b>{esc(', '.join(a.exclude_status))}</b> olan ve "
                    f"<b>{esc(', '.join(a.exclude_label))}</b> etiketli işler — bu sprintte: {nums(excluded)}.",
                    "Tüm slayt ve metrikler bu kapsam üzerinden hesaplanır."]),
        ("Sayım birimleri", xurrent_txt),
        ("Statüler ve kolonlar", [f"Tamamlandı sayılan statüler: <b>{esc(done_lbl)}</b> (lejantta ✓).",
                                  f"Kanban kolonları — {esc(lanes_txt)}.",
                                  "Bir talebin rengi/kolonu, işleri içinde en geride kalanın statüsüdür."]),
        ("İş tipi", ["Başlıkta [BUG] → <b>Bug</b>; DD Project Name dolu → <b>Proje işi</b>; "
                     "toplantı, analiz/inceleme, deployment, kurulum, operasyonel işler → <b>Task</b>; "
                     "diğerleri → <b>User Story</b>.",
                     "Metrik slaytlarında <b>Geliştirme taleplerimiz</b> = User Story + Bug; <b>Proje işleri</b> "
                     "ayrı gösterilir ve altında listelenir; Task'lar bu kırılıma dahil edilmez.",
                     "GitHub'ta issue type sütunu dışa aktarılırsa doğrudan o kullanılır."]),
        ("Etiket bazlı metrikler", [f"Aradan giren iş oranı = \"{esc(a.mid_label[0])}\" etiketli işler ÷ "
                                    "etiketsiz (ilk planlanan) işler.",
                                    f"Altyapısal işler payı = \"{esc(a.tb_label[0])}\" etiketli işler ÷ toplam iş.",
                                    "Etiket karşılaştırmasında büyük/küçük harf, boşluk, - ve _ yok sayılır."]),
        ("Lead time", ["Özet slaytında iki ayrı kutu: <b>geliştirme işleri</b> (User Story + Bug) ve "
                       "<b>proje işleri</b>; iki sprinti birleştiren slaytlarda yalnız geliştirme işleri. Task'lar dahil değil.",
                       "Yalnız <b>Done ve Released</b> işler; süre <b>takvim günü</b> olarak sprint "
                       "başlangıcından Closed tarihine kadar ölçülür.",
                       f"Başlangıç: Original Sprint doluysa o sprintin, değilse işin sprintinin başlangıcı. "
                       f"Tanımlı başlangıç: {esc(starts)}; diğer sprintler {a.sprint_days} günlük sprint varsayımıyla bulunur.",
                       f"Closed tarihi boş olduğu için hariç: {esc(', '.join('#' + n for n in no_close) or '—')}."]),
        ("Canlıya çıkış", [f"Release bekleyen = statü {esc(', '.join(a.ready_status))} + "
                           f"{esc(' / '.join(a.ready_label[:2]))} etiketi.",
                           "Canlıya alınan = kanbandaki \"Canlıda\" kolonundaki tüm işler (etiketten bağımsız).",
                           "Talebin yalnız bir kısmı canlıdaysa o işler GitHub başlığıyla ve ↳ notuyla listelenir."]),
        ("Diğer", [f"Original Estimate boş olan {missing} iş 0 sayılır (yalnız Takıma özel slaytı etkiler).",
                   "Önceki sprintten aktarılan: Original Sprint, işin sprintinden farklıysa (↪ Sprint N).",
                   "Madde adları, GitHub başlıklarından iş birimi diline çevrilmiş tema adlarıdır; "
                   "teknik ayrıntı için Xurrent / GitHub numarasına bakılabilir."]),
    ]
    if br_card:
        cards.insert(-1, br_card)
    if warnings:
        cards.append(("Veri uyarıları", [esc(w) for w in warnings]))
    assumptions = "".join(
        f'<div class="acard"><div class="atitle">{t}</div><ul>{"".join(f"<li>{b}</li>" for b in bl)}</ul></div>'
        for t, bl in cards)

    proj = data.get("project") or {}
    sprints = sorted({str(r["fields"].get(a.sprint_field) or "") for r in rows} - {""})
    subtitle = " · ".join(x for x in [a.team or proj.get("title"), ", ".join(sprints),
                                      dt.date.today().strftime("%d.%m.%Y")] if x)
    focus_key = (f'<li><span class="cat story">Kategori</span>&nbsp;= iş tipi grubu</li>'
                 f'<li><span class="kmeta">N talep</span>&nbsp;= GitHub işi adedi (birden fazlaysa)</li>'
                 f'<li><span class="carry">↪ Sprint N</span>&nbsp;= o sprintten aktarıldı</li>')
    # ---- Retro sinyalleri (Claude içgörü yazarken kullanır) ve Retro İçgörüleri slaytı
    if a.signals:
        cur_no = max((sprint_no(r["sprint"]) or 0) for r in rows) if rows else 0
        opn = [r for r in rows if not r["done"]]
        by_st = {}
        for r in opn:
            d = by_st.setdefault(r["status"], {"is": 0, "oe": 0.0})
            d["is"] += 1; d["oe"] += r["est"]
        by_p = {}
        for r in rows:
            for pp in r["assignees"] or ["Atanmamış"]:
                d = by_p.setdefault(pp, {"toplam": 0, "acik": 0, "acik_oe": 0.0})
                d["toplam"] += 1
                if not r["done"]:
                    d["acik"] += 1; d["acik_oe"] += r["est"]
        old_open = [{"no": "#" + (r.get("url") or "").rsplit("/", 1)[-1], "tema": r.get("theme"), "statu": r["status"],
                     "ilk_sprint": r["origin"]} for r in opn
                    if r["origin"] and sprint_no(r["origin"]) and cur_no - sprint_no(r["origin"]) >= 2]
        projs = {}
        for r in rows:
            if r["kind"] == "Proje işi":
                d = projs.setdefault(r["project"], {"is": 0, "tamamlanan": 0, "oe": 0.0, "statuler": []})
                d["is"] += 1; d["tamamlanan"] += r["done"]; d["oe"] += r["est"]; d["statuler"].append(r["status"])
        def med(kinds):
            xs = [r["lead"] for r in rows if r["lead"] is not None and r["kind"] in kinds]
            return {"biten": len(xs), "medyan": round(statistics.median(xs), 1) if xs else None,
                    "en_uzun": round(max(xs), 1) if xs else None}
        sig = {
            "sprint": ", ".join(sprints), "toplam_is": len(rows), "tamamlanan_is": all_done,
            "tamamlanma_pct": round(pct(all_done, len(rows))),
            "oe_toplam": sum(r["est"] for r in rows), "oe_tamamlanan": sum(r["est"] for r in rows if r["done"]),
            "oe_bos_is": missing,
            "acik_isler_statu": by_st, "kisi_yuku": by_p,
            "aradan_gelen": {"is": len(mid), "ilk_planlanan": len(plan)},
            "altyapisal": {"is": len(tb), "toplam": len(rows)},
            "proje": projs, "iki_sprintten_uzun_acik": old_open,
            "release_bekleyen": len(ready_groups),
            "lead_gelistirme": med(DEV_KINDS), "lead_proje": med(PROJ_KINDS),
            "closed_tarihi_eksik": ["#" + n for n in no_close],
            "veri_uyarilari": warnings, "sprint_karsilastirma": br_summary,
        }
        with open(a.signals, "w", encoding="utf-8") as fh:
            json.dump(sig, fh, ensure_ascii=False, indent=2)
    retro = ""
    if a.insights and os.path.exists(a.insights):
        with open(a.insights, encoding="utf-8") as fh:
            ins = json.load(fh)
        tone_lbl = {"risk": "Dikkat", "good": "İyi giden", "action": "Öneri", "data": "Veri kalitesi"}
        cards_html = "".join(
            f'<div class="rcard {esc(c.get("tone", "risk"))}"><div class="rtag">{esc(tone_lbl.get(c.get("tone", "risk"), ""))}</div>'
            f'<div class="rhead">{esc(c["title"])}</div>'
            + (f'<p>{c["text"]}</p>' if c.get("text") else "")
            + ("<ul>" + "".join(f"<li>{b}</li>" for b in c.get("bullets", [])) + "</ul>" if c.get("bullets") else "")
            + "</div>" for c in ins.get("cards", []))
        qs = "".join(f"<li>{q}</li>" for q in ins.get("questions", []))
        retro = f"""
<div class="frame"><section class="slide" aria-label="Retro İçgörüleri">
  <header>
    <div><h1>Retro <span>İçgörüleri</span></h1><div class="sub">{esc(ins.get("subtitle") or subtitle)}</div></div>
  </header>
  <div class="rlayout">
    <div class="agrid rgridc">{cards_html}</div>
    {f'<div class="rq"><div class="rqt">Retroda konuşalım</div><ol>{qs}</ol></div>' if qs else ''}
  </div>
</section></div>
"""

    # ---- Çok sprintli veri: odaklandığımız işler ve özet metriklerin toplam versiyonları
    br_focus = br_sum = br_release = ""
    if multi:
        rng = " & ".join(f"Sprint {n}" for n in snos) if len(snos) <= 2 else f"Sprint {snos[0]} – Sprint {snos[-1]}"
        bsub = " · ".join(x for x in [a.team or proj.get("title"), rng, dt.date.today().strftime("%d.%m.%Y")] if x)
        br_focus = f"""
<div class="frame"><section class="slide" aria-label="Sprint'te Odaklandığımız İşler - {esc(rng)}">
  <header>
    <div><h1>Sprint'te Odaklandığımız İşler <span>- {esc(rng)}</span></h1>
      <div class="sub">{esc(bsub)} · {len(all_groups)} talep ({len(br_rows)} GitHub işi)</div></div>
    <ul class="legend">{legend}<li class="sep"></li>{focus_key}</ul>
  </header>
  {kanban(all_groups, hexes, order)}
</section></div>
"""
        b_mid = [r for r in br_rows if r["mid"]]
        b_plan = [r for r in br_rows if not r["mid"]]
        b_done = sum(r["done"] for r in br_rows)
        b_midblk = story_block(f"İlk planlanan <b>{fmt(len(b_plan))}</b> işe sprintler içinde <b>{fmt(len(b_mid))}</b> iş eklendi",
                               len(b_mid), len(b_plan), "aradan giren iş",
                               [("İlk planlanan", len(b_plan), "iş"), ("Aradan gelen", len(b_mid), "iş")], warm=True)
        b_comp = story_block(f"Son durumda toplam <b>{fmt(len(br_rows))}</b> iş içinden <b>{fmt(b_done)}</b> iş tamamlandı",
                             b_done, len(br_rows), "tamamlanma oranı",
                             [("Toplam", len(br_rows), "iş"), ("Tamamlanan", b_done, "iş"),
                              ("Kalan", len(br_rows) - b_done, "iş")])
        bxg = [g for g in all_groups if g["xno"]]
        if a.ignore_xurrent:
            b_st = [{"label": o["name"] + (" ✓" if o["done"] else ""), "color": o["hex"],
                     "x": sum(1 for r in br_rows if r["status"] == o["name"])} for o in used_opts]
            b_tot = len(br_rows)
        else:
            b_st = [{"label": o["name"] + (" ✓" if o["done"] else ""), "color": o["hex"],
                     "x": sum(1 for g in bxg if g["status"] == o["name"])} for o in used_opts]
            b_tot = len(bxg)
        b_status = multi_table([r for r in b_st if r["x"]], [{"key": "x", "head": ""}],
                               total={"label": "Toplam", "x": b_tot})
        b_types_rows = []
        for lbl, kinds in TYPE_BUCKETS:
            mem = [r for r in br_rows if r["kind"] in kinds]
            b_types_rows.append({"label": lbl, "c": len(mem), "cd": sum(1 for r in mem if r["done"])})
        b_ntask = sum(1 for r in br_rows if r["kind"] == "Task")
        b_types_note = ("Geliştirme taleplerimiz = User Story + Bug · altında proje adları"
                        + (f" · {b_ntask} task bu kırılıma dahil değil" if b_ntask else ""))
        b_lstats, b_hist, b_missing, _, _ = lead_block([r for r in br_rows if r["kind"] in DEV_KINDS])
        b_ready = [r for r in br_rows if sk(r["status"]) in ready_st and has_label(r, a.ready_label)]
        b_ready_g = subset_entries({key_of(r) for r in b_ready}, all_groups)
        b_rel = [r for r in br_rows if sk(r["status"]) in live_st]
        b_rel_g = subset_entries({key_of(r) for r in b_rel}, all_groups)
        b_wide = len(b_rel_g) > 12
        br_release = f"""
<div class="frame"><section class="slide" aria-label="Canlıya Çıkmaya Hazır Olan İşler - {esc(rng)}">
  <header>
    <div><h1>Canlıya Çıkmaya Hazır Olan İşler <span>- {esc(rng)}</span></h1><div class="sub">{esc(bsub)} · iki sprintin toplamı</div></div>
    <ul class="legend">{focus_key}</ul>
  </header>
  <div class="rgrid" style="grid-template-columns:{"minmax(0,1fr) minmax(0,1.9fr)" if b_wide else "1fr 1fr"}">
    <div class="rsec"><div class="rtitle">Canlıya çıkmaya hazır <small>(release bekleyen)</small> <b>{len(b_ready_g)}</b></div>
      <ul class="list" style="--cols:1;--max:30">
{grouped_list(b_ready_g, hexes, a.origin_field, "Bu kriterde iş yok.")}
      </ul></div>
    <div class="rsec done"><div class="rtitle">{esc(rng)} içinde canlıya alınanlar <b>{len(b_rel_g)}</b> <small>({esc(", ".join(sorted({r["status"] for r in b_rel})) or "—")})</small></div>
      <ul class="list" style="--cols:{2 if b_wide else 1};--max:22">
{grouped_list(b_rel_g, hexes, a.origin_field, "Yok.")}
      </ul></div>
  </div>
  <div class="rnote">{ready_note}</div>
</section></div>
"""
        br_sum = f"""
<div class="frame"><section class="slide" aria-label="Metriklerimiz - Özet {esc(rng)}">
  <header>
    <div><h1>Metriklerimiz <span>- Özet · {esc(rng)}</span></h1><div class="sub">{esc(bsub)} · iki sprintin toplamı</div></div>
    <ul class="legend">{legend}</ul>
  </header>
  <div class="grid">
    <div class="panel"><div class="ptitle">Planlanan ve aradan gelen işler</div><div class="comps r one">{b_midblk}</div>
      <ul class="names">{name_list(b_mid)}</ul>
      <div class="pfoot">Aradan gelen = "{esc(a.mid_label[0])}" etiketli işler · oran = aradan gelen ÷ ilk planlanan</div></div>
    <div class="panel"><div class="ptitle">Tamamlanma durumu</div><div class="comps r one">{b_comp}</div>
      <div class="pfoot">İş adedi · iki sprintteki tüm işler, bugünkü statüsüne göre</div></div>
    <div class="panel"><div class="ptitle">Statü dağılımı</div>{b_status}
      <div class="pfoot">{"İş adedi" if a.ignore_xurrent else "Talep adedi · talep, işleri içinde en geride kalanın statüsünde sayılır"}</div></div>
    <div class="panel"><div class="ptitle">İş tipi kırılımı</div>
      <div class="pkey"><span style="--k:var(--plan)">Planlanan</span><span style="--k:var(--ink)">Tamamlanan</span></div>
      {multi_table(b_types_rows, [{"key": "c", "done": "cd", "head": "İş adedi"}])}
      <ul class="names one">{project_list(br_rows, "Bu sprintlerde proje işi yok")}</ul>
      <div class="pfoot">{b_types_note}</div></div>
    <div class="panel span2"><div class="ptitle">Lead time · Geliştirme işleri (Sprint başlangıcı → Closed, gün)</div>
      <div class="lsub">Takvim günü kullanılarak hesaplanmıştır · User Story + Bug · Done ve Released işler · başlangıç: Original Sprint, yoksa Sprint · sprint = {a.sprint_days} gün{b_missing}</div>
      <div class="lstats">{b_lstats}</div>{b_hist}</div>
  </div>
</section></div>
"""

    page = TEMPLATE.format(
        subtitle=esc(subtitle), legend=legend, focus_key=focus_key, focus=focus,
        n_groups=len(groups), n_items=len(rows), n_carry=n_carry,
        comp=comp, comp_note=comp_note,
        status=sum_status, status_title=status_title, sum_comp_note=sum_comp_note,
        types=multi_table(type_rows, [{"key": "c", "done": "cd", "head": "İş adedi"},
                                      {"key": "e", "done": "ed", "head": est_label}], label_fr=3.2),
        projects=proj_txt, pfield=esc(a.project_field), midp=midp, tbp=tbp,
        mid_label=esc(a.mid_label[0]), tb_label=esc(a.tb_label[0]), mid_list=mid_list, tb_list=tb_list,
        lead_stats=lead_stats, hist=hist, sprint_days=a.sprint_days, lead_missing=lead_missing, oe=oe, est_label=esc(est_label),
        status_oe=multi_table(status_rows, [{"key": "e", "head": est_label}]),
        people=multi_table(people_rows, [{"key": "c", "done": "cd", "head": "İş adedi"},
                                         {"key": "e", "done": "ed", "head": est_label}], 1.6),
        assumptions=assumptions,
        sum_comp=sum_comp, sum_status=sum_status, sum_status_note=sum_status_note, sum_types=sum_types,
        sum_mid=sum_mid, sum_tb=sum_tb, dl_stats=dl_stats, dl_hist=dl_hist, dl_missing=dl_missing,
        pl_stats=pl_stats, pl_hist=pl_hist, pl_missing=pl_missing, proj_list=proj_list, types_note=types_note, br_slide=br_slide, br_focus=br_focus, br_sum=br_sum, br_release=br_release, retro=retro,
        ready=ready_html, ready_note=ready_note, n_ready=len(ready_groups),
        released=released_html, n_released=len(rel_groups),
        rel_cols=2 if len(rel_groups) > 12 else 1,
        rgrid="minmax(0,1fr) minmax(0,1.9fr)" if len(rel_groups) > 12 else "1fr 1fr", rel_status=esc(rel_status),
    )
    with open(a.output, "w", encoding="utf-8") as fh:
        fh.write(page)

    if excluded:
        print(f"  hariç tutulan (Backlog / RetroAction): " + ", ".join(
            "#" + (i.get("url") or "").rsplit("/", 1)[-1] for i in excluded))
    print(f"{a.output} yazıldı: {len(rows)} GitHub işi → {len(groups)} tema "
          f"({len(xg)} Xurrent talebi, {len(ek)} ek iş)")
    print(f"  Xurrent: {sum(g['done'] for g in xg)}/{len(xg)} tamam · ek: {sum(r['done'] for r in ek)}/{len(ek)}")
    print(f"  sprint ortası: {len(mid)}/{len(plan)} iş, {fmt(sum(r['est'] for r in mid))}/"
          f"{fmt(sum(r['est'] for r in plan))} OE · altyapısal: {len(tb)}/{len(rows)} iş, "
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
.comps.r .chero {{ font-size:64px; }}
.comps.r .cnums b {{ font-size:24px; }}
.bar.warm i {{ background:var(--warm); }}
.cnums {{ display:grid; grid-template-columns:repeat(3,1fr); gap:8px; margin-top:12px; }}
.cnums.two {{ grid-template-columns:1fr 1fr; }}
.cnums div {{ display:flex; flex-direction:column; border-top:1px solid var(--rule); padding-top:6px; }}
.cnums span {{ font-size:14px; color:var(--muted); }}
.cnums b {{ font-size:28px; font-variant-numeric:tabular-nums; white-space:nowrap; }}
.cnums small, .lstats small {{ font-size:14px; font-weight:400; color:var(--muted); margin-left:3px; }}
.chero .hcap {{ font-size:20px; font-weight:600; letter-spacing:0; color:var(--muted); margin-left:12px; }}
.story .clabel {{ font-weight:500; font-size:19px; line-height:1.35; }}
.story .clabel b {{ font-weight:800; color:var(--ink); }}
.names.one {{ columns:1; }}
.panel.span2 {{ grid-column:span 2; }}
.rlayout {{ flex:1; min-height:0; display:grid; grid-template-columns:minmax(0,1fr) 440px; gap:22px; }}
.rlayout .agrid {{ columns:2; }}
.rcard {{ break-inside:avoid; border:1px solid var(--rule); border-top:5px solid var(--c, var(--ink)); border-radius:12px;
  padding:12px 18px; margin-bottom:16px; font-size:var(--af); line-height:1.4; color:var(--ink-2); }}
.rcard.risk {{ --c:#E5484D; }} .rcard.good {{ --c:#2EA043; }} .rcard.action {{ --c:#1F8FA0; }} .rcard.data {{ --c:#8B95A5; }}
.rtag {{ font-size:calc(var(--af) * .7); font-weight:800; letter-spacing:.08em; text-transform:uppercase; color:var(--c); }}
.rhead {{ font-size:calc(var(--af) * 1.1); font-weight:800; color:var(--ink); margin:2px 0 4px; }}
.rcard p {{ margin:0 0 4px; }} .rcard ul {{ margin:0; padding-left:1.1em; }} .rcard li {{ margin:2px 0; }}
.rcard b {{ color:var(--ink); }}
.rq {{ background:#F4F6F9; border-radius:14px; padding:20px 24px; font-size:19px; line-height:1.45; color:var(--ink-2); overflow:hidden; }}
.rqt {{ font-size:15px; font-weight:800; letter-spacing:.06em; text-transform:uppercase; color:var(--muted); margin-bottom:8px; }}
.rq ol {{ margin:0; padding-left:1.2em; }} .rq li {{ margin:0 0 10px; }}
.grid.g7 {{ grid-template-columns:repeat(12, minmax(0,1fr)); }}
.grid.g7 > .panel {{ grid-column:span 4; }}
.grid.g7 > .panel:nth-child(n+4) {{ grid-column:span 3; }}
.grid.g7 .lt .lstats {{ grid-template-columns:repeat(2,1fr); gap:4px 10px; }}
.grid.g7 .lt .lstats b {{ font-size:20px; }}
.grid.g7 .lt .lsub {{ margin-top:-4px; }}
.srows {{ display:flex; flex-direction:column; justify-content:space-evenly; flex:0 0 auto; gap:6px; }}
.srow {{ display:grid; grid-template-columns:150px 108px minmax(0,1fr); align-items:center; column-gap:12px;
  border-top:1px solid var(--rule); padding:8px 0; }}
.srow:first-child {{ border-top:0; }}
.sname {{ font-size:17px; font-weight:600; color:var(--ink-2); display:flex; align-items:center; gap:7px; }}
.sname i {{ width:12px; height:12px; border-radius:3px; display:inline-block; }}
.shero {{ font-size:44px; font-weight:800; letter-spacing:-.03em; font-variant-numeric:tabular-nums; line-height:1; }}
.sbody .bar {{ margin:2px 0 6px; height:12px; }}
.stext {{ font-size:16px; color:var(--ink-2); }}
.stext b {{ color:var(--ink); }}
.sdelta {{ font-size:16px; color:var(--muted); padding-top:6px; }}
.sdelta b {{ color:var(--ink); }}
.lsrows {{ display:flex; flex-direction:column; gap:4px; margin-bottom:6px; }}
.lsrow {{ display:grid; grid-template-columns:120px 0.8fr 1fr 1fr; align-items:baseline; font-size:13px; color:var(--muted);
  border-top:1px solid var(--rule); padding-top:4px; white-space:nowrap; }}
.lsrow .sname {{ font-size:16px; }}
.lsrow b {{ font-size:20px; color:var(--ink); margin-left:6px; font-variant-numeric:tabular-nums; }}
.lsrow small {{ margin-left:3px; }}
.ghplot .gcol {{ flex:1; display:flex; align-items:flex-end; gap:3px; height:100%; }}
.gbar {{ flex:1; position:relative; border-radius:4px 4px 0 0; min-height:0; }}
.gbar span {{ position:absolute; top:-20px; left:0; right:0; text-align:center; font-size:13px; font-weight:600; color:var(--ink); }}
.names li.none {{ color:var(--muted); }}
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
.list li.lgroup {{ border:0; padding:calc(var(--fs) * .6) 0 calc(var(--fs) * .15); break-after:avoid; }}
.list li.lgroup .khead {{ width:100%; font-size:calc(var(--fs) * .62); }}
.comps.one {{ grid-template-columns:1fr; }}
.drow.total {{ border-top:2px solid var(--ink-2); font-weight:700; }}
.drow.total .dl {{ color:var(--ink); }}
.part {{ font-size:.62em; color:var(--muted); margin-left:.5em; }}
.agrid {{ flex:1; min-height:0; columns:3; column-gap:22px; --af:17px; overflow:hidden; }}
.acard {{ break-inside:avoid; border:1px solid var(--rule); border-radius:12px; padding:14px 18px; margin-bottom:16px;
  font-size:var(--af); line-height:1.4; color:var(--ink-2); }}
.atitle {{ font-size:calc(var(--af) * .82); font-weight:800; letter-spacing:.06em; text-transform:uppercase;
  color:var(--accent); margin-bottom:6px; }}
.acard ul {{ margin:0; padding-left:1.1em; }}
.acard li {{ margin:3px 0; }}
.acard b {{ color:var(--ink); }}
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
{br_focus}

<div class="frame"><section class="slide" aria-label="Metriklerimiz - Özet">
  <header>
    <div><h1>Metriklerimiz <span>- Özet</span></h1><div class="sub">{subtitle}</div></div>
    <ul class="legend">{legend}</ul>
  </header>
  <div class="grid g7">
    <div class="panel"><div class="ptitle">Planlanan ve aradan gelen işler</div><div class="comps r one">{sum_mid}</div>
      <ul class="names">{mid_list}</ul>
      <div class="pfoot">Aradan gelen = "{mid_label}" etiketli işler · oran = aradan gelen ÷ ilk planlanan</div></div>
    <div class="panel"><div class="ptitle">Tamamlanma durumu</div><div class="comps r one">{sum_comp}</div>
      <div class="pfoot">{sum_comp_note}</div></div>
    <div class="panel"><div class="ptitle">{status_title}</div>{sum_status}
      <div class="pfoot">{sum_status_note}</div></div>
    <div class="panel"><div class="ptitle">İş tipi kırılımı</div>
      <div class="pkey"><span style="--k:var(--plan)">Planlanan</span><span style="--k:var(--ink)">Tamamlanan</span></div>
      {sum_types}
      <ul class="names one">{proj_list}</ul>
      <div class="pfoot">{types_note}</div></div>
    <div class="panel"><div class="ptitle">Altyapısal işler</div><div class="comps r one">{sum_tb}</div>
      <ul class="names">{tb_list}</ul>
      <div class="pfoot">"{tb_label}" etiketli işler ÷ toplam iş</div></div>
    <div class="panel lt"><div class="ptitle">Lead time · Geliştirme işleri</div>
      <div class="lsub">Sprint başlangıcı → Closed, takvim günü · User Story + Bug · Done ve Released{dl_missing}</div>
      <div class="lstats">{dl_stats}</div>{dl_hist}</div>
    <div class="panel lt"><div class="ptitle">Lead time · Proje işleri</div>
      <div class="lsub">Sprint başlangıcı → Closed, takvim günü · proje işleri · Done ve Released{pl_missing}</div>
      <div class="lstats">{pl_stats}</div>{pl_hist}</div>
  </div>
</section></div>

{br_sum}
{br_slide}
<div class="frame"><section class="slide" aria-label="Metriklerimiz">
  <header>
    <div><h1>Metriklerimiz</h1><div class="sub">{subtitle}</div></div>
    <ul class="legend">{legend}</ul>
  </header>
  <div class="grid">
    <div class="panel"><div class="ptitle">Planlanan vs. tamamlanan</div><div class="minis">{comp}</div>
      <div class="pfoot">{comp_note}</div></div>
    <div class="panel"><div class="ptitle">{status_title}</div>{status}
      <div class="pfoot">{sum_status_note}</div></div>
    <div class="panel"><div class="ptitle">İş tipi kırılımı</div>
      <div class="pkey"><span style="--k:var(--plan)">Planlanan</span><span style="--k:var(--ink)">Tamamlanan</span></div>
      {types}
      <ul class="names one">{proj_list}</ul>
      <div class="pfoot">{types_note}</div></div>
    <div class="panel"><div class="ptitle">Sprint ortasında gelen iş</div><div class="comps r">{midp}</div>
      <ul class="names">{mid_list}</ul>
      <div class="pfoot">"{mid_label}" etiketli işler ÷ ilk planlanan işler (etiketsizler)</div></div>
    <div class="panel"><div class="ptitle">Altyapısal işler</div><div class="comps r">{tbp}</div>
      <ul class="names">{tb_list}</ul>
      <div class="pfoot">"{tb_label}" etiketli işler ÷ toplam iş</div></div>
    <div class="panel"><div class="ptitle">Lead time (Sprint başlangıcı → Closed, gün)</div>
      <div class="lsub">Takvim günü kullanılarak hesaplanmıştır · iş adedi üzerinden · Done ve Released işler · başlangıç: Original Sprint, yoksa Sprint · sprint = {sprint_days} gün{lead_missing}</div>
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
  <div class="rgrid" style="grid-template-columns:{rgrid}">
    <div class="rsec"><div class="rtitle">Canlıya çıkmaya hazır <small>(release bekleyen)</small> <b>{n_ready}</b></div>
      <ul class="list" style="--cols:1;--max:30">
{ready}
      </ul></div>
    <div class="rsec done"><div class="rtitle">Bu sprintte canlıya alınanlar <b>{n_released}</b> <small>({rel_status})</small></div>
      <ul class="list" style="--cols:{rel_cols};--max:22">
{released}
      </ul></div>
  </div>
  <div class="rnote">{ready_note}</div>
</section></div>
{br_release}
{retro}
<div class="frame"><section class="slide" aria-label="Hesaplama Varsayımları">
  <header>
    <div><h1>Hesaplama <span>Varsayımları</span></h1><div class="sub">{subtitle} · bu sunumdaki metrikler nasıl hesaplandı?</div></div>
  </header>
  <div class="agrid">{assumptions}</div>
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
  document.querySelectorAll('.agrid').forEach(g => {{
    let af = 18;
    const over = () => g.scrollHeight > g.clientHeight + 1 || g.scrollWidth > g.clientWidth + 1;
    g.style.setProperty('--af', af + 'px');
    while (over() && af > 11) {{ af -= 0.5; g.style.setProperty('--af', af + 'px'); }}
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
    ap.add_argument("--exclude-status", action="append", default=None,
                    help="Tüm slayt/metriklerden hariç tutulan statüler (varsayılan Backlog)")
    ap.add_argument("--exclude-label", action="append", default=None,
                    help="Tüm slayt/metriklerden hariç tutulan etiketler (varsayılan RetroAction)")
    ap.add_argument("--origin-field", default="Original Sprint",
                    help="Doluysa iş önceki sprintten aktarılmış sayılır")
    ap.add_argument("--project-field", default="DD Project Name", help="Doluysa 'Proje işi'")
    ap.add_argument("--task-keyword", action="append",
                    help="Başlıkta geçerse Task sayılır (regex, tekrarlanabilir; varsayılanı değiştirir)")
    ap.add_argument("--mid-label", action="append", default=None, help="Sprint ortasında gelen iş etiketi")
    ap.add_argument("--tb-label", action="append", default=None, help="Altyapısal iş etiketi (TB)")
    ap.add_argument("--project-label", action="append", default=None,
                    help="Proje işi etiketi (DD Project Name boşsa bu etiket de proje işi sayılır)")
    ap.add_argument("--parent-field", default="Parent issue", help="Üst iş (parent) sütunu")
    ap.add_argument("--single-sprint", action="store_true",
                    help="Birden fazla sprint olsa da tek sunum üret (karşılaştırma slaytı yok)")
    ap.add_argument("--ready-label", action="append", default=None, help="Canlıya hazır etiketi")
    ap.add_argument("--ready-status", action="append", default=None, help="Canlıya hazır statüsü")
    ap.add_argument("--themes", help="Tema adları JSON'u (grup anahtarı → ad)")
    ap.add_argument("--signals", help="Retro sinyallerini (metrik özeti) bu JSON'a yaz")
    ap.add_argument("--insights", help="Claude'un yazdığı retro içgörüleri JSON'u → Retro İçgörüleri slaytı")
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
    a.project_label = a.project_label or ["Project", "Proje"]
    a.ready_label = a.ready_label or ["Ready For Release", "WaitingRelease", "Waiting For Release"]
    a.ready_status = a.ready_status or ["Done"]
    a.lead_status = a.lead_status or ["Done", "Released"]
    a.exclude_status = a.exclude_status or ["Backlog"]
    a.exclude_label = a.exclude_label or ["RetroAction"]
    build(a)


if __name__ == "__main__":
    main()
