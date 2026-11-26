#!/usr/bin/env python3
"""İş birimli Sprint Review sunumu.

Girdi: çeyreklik talep listesi (Excel, ör. "BNEXT Product Talep Listesi" → "Bnext + BO" sayfası)
ve GitHub Projects sprint export'u (TSV/CSV). Talepler GitHub işleriyle 4Me / Xurrent Talep No,
başlıktaki numara, başlık benzerliği ve (varsa) elle verilen eşleştirme dosyasıyla bağlanır.

Slaytlar (16:9):
  1. Q4 Talep Planı: çeyrekte planlanan taleplerin tamamlanma oranı, geçen süreye göre hız,
     sprint bazında planlanan/tamamlanan, talep durum dağılımı, sonradan gelen / plan dışı işler,
     içgörüler (--insights).
  2. Son N sprint: canlıya çıkan ve canlıya hazır talepler.
  3. Son N sprint: devam eden taleplerin statüleri (kanban).
  4. Hesaplama varsayımları ve eşleşme özeti.

PNG için: node export_png.mjs review.html review
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_report import (DEFAULT_STATUS_COLORS, GITHUB_COLORS, NO_STATUS_COLOR,  # noqa: E402
                          STATUS_RANK, esc, fmt)
from build_slides import TEMPLATE, multi_table, pct, sk, story_block, tr_lower  # noqa: E402

DONE = {"done", "released"}
LIVE = {"released"}
SHEET_DONE = {"tamamlandı", "tamamlandi"}
SHEET_CANCEL = {"iptal edildi", "iptal", "cancel", "cancelled"}
# Excel'deki "Sprint Planlama" durumlarının gruplanması (GitHub eşleşmesi olmayan talepler için)
SHEET_GROUPS = [
    ("Sprint'e alındı (GitHub'da iş yok)", {"sprinte alındı", "sprinte alindi"}),
    ("Planlama / netleştirme", {"sprint planlama", "talep netleştirme", "sprint backlog"}),
    ("Tasarım bekleniyor", {"tasarım bekleniyor"}),
    ("Hold / backlog", {"hold", "backlog"}),
    ("Durum girilmemiş", {""}),
]
GROUP_COLORS = {"Tamamlandı": GITHUB_COLORS["GREEN"], "Geliştirme / test sürüyor": GITHUB_COLORS["YELLOW"],
                "Sprint'e alındı (GitHub'da iş yok)": GITHUB_COLORS["ORANGE"],
                "Planlama / netleştirme": GITHUB_COLORS["PURPLE"], "Tasarım bekleniyor": GITHUB_COLORS["PINK"],
                "Hold / backlog": GITHUB_COLORS["GRAY"], "Durum girilmemiş": NO_STATUS_COLOR,
                "Diğer": NO_STATUS_COLOR}
STOP = set("bnext borusan next app apps web fe be bff mobil jarvis hk ve ile için de da bir olarak olan "
           "the in task yapılması geliştirilmesi geliştirme talebi talep eklenmesi gösterilmesi sayfası "
           "alanının alanında edilmesi seo data".split())


def nums(v) -> set[str]:
    if isinstance(v, float):
        v = str(int(v))
    return set(re.findall(r"\d{6,}", str(v or "")))


def toks(s) -> set[str]:
    s = tr_lower(str(s or ""))
    s = re.sub(r"\d{4,}", " ", s)
    s = re.sub(r"[^\wçğıöşü]+", " ", s)
    return {w for w in s.split() if len(w) > 2 and w not in STOP}


def hexof(st: str) -> str:
    return GITHUB_COLORS.get(DEFAULT_STATUS_COLORS.get(sk(st)), NO_STATUS_COLOR)


def rank(st: str) -> float:
    return STATUS_RANK.get(sk(st), 2)


def tr1(x: float) -> str:
    return f"{x:.1f}".replace(".", ",")


def clean(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


# --------------------------------------------------------------------------- veri

def load_plan(path: str, sheet: str, a) -> list[dict]:
    try:
        import openpyxl
    except ImportError:
        sys.exit("openpyxl gerekli: pip install openpyxl")
    import warnings
    warnings.filterwarnings("ignore")
    wb = openpyxl.load_workbook(path, data_only=True)
    if sheet not in wb.sheetnames:
        sys.exit(f"'{sheet}' sayfası yok; sayfalar: {', '.join(wb.sheetnames)}")
    it = wb[sheet].iter_rows(values_only=True)
    head = [clean(h) for h in next(it)]
    col = {}
    for j, h in enumerate(head):
        col.setdefault(h, j)
    need = [a.col_title, a.col_quarter, a.col_sprint, a.col_state, a.col_no]
    miss = [c for c in need if c not in col]
    if miss:
        sys.exit(f"Excel'de sütun bulunamadı: {', '.join(miss)}")

    def g(row, name):
        j = col.get(name)
        return row[j] if j is not None and j < len(row) else None
    a._has_later_col = a.col_later in col
    out = []
    for i, row in enumerate(it, start=2):
        title = clean(g(row, a.col_title))
        if not title:
            continue
        sprint = clean(g(row, a.col_sprint))
        out.append({
            "row": i, "title": title, "quarter": clean(g(row, a.col_quarter)),
            # "Q4/3" ya da "Q4-3 2026" biçimleri
            "sprint": (lambda m: f"Q{m.group(1)}/{m.group(2)}" if m else "")(
                re.fullmatch(r"Q(\d)\s*[-/]\s*(\d+)(\s+\d{4})?", sprint)),
            "state": clean(g(row, a.col_state)),
            "nos": nums(g(row, a.col_no)).union(*(nums(g(row, c)) for c in (a.col_no_extra or []))),
            "out_of_scope": a.col_scope in col and sk(g(row, a.col_scope)) in {"hayır", "hayir", "no"},
            "no": next(iter(sorted(nums(g(row, a.col_no)))), ""),
            "later": sk(g(row, a.col_later)) in {"evet", "yes", "x", "1"} if a.col_later in col else False,
            "type": clean(g(row, a.col_type)), "squad": clean(g(row, a.col_squad)),
        })
    return out


def load_github(path: str, a) -> tuple[list[dict], list[str]]:
    with open(path, encoding="utf-8-sig") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        rows = list(csv.DictReader(fh, delimiter="\t" if sample.count("\t") > sample.count(",") else ","))
    items, excluded = [], []
    ex_st = {sk(x) for x in a.exclude_status}
    ex_lb = {re.sub(r"[\s_-]", "", tr_lower(x)) for x in a.exclude_label}
    for r in rows:
        num = (r.get("URL") or "").rstrip("/").rsplit("/", 1)[-1]
        labels = [l.strip() for l in (r.get("Labels") or "").split(",") if l.strip()]
        status = clean(r.get("Status")) or "Statüsüz"
        if sk(status) in ex_st or {re.sub(r"[\s_-]", "", tr_lower(l)) for l in labels} & ex_lb:
            excluded.append(num)
            continue
        est = clean(r.get(a.estimate_field))
        items.append({
            "num": num, "title": clean(r.get("Title")), "status": status,
            "sprint": clean(r.get("Sprint")), "labels": labels,
            "label_keys": {re.sub(r"[\s_-]", "", tr_lower(l)) for l in labels},
            "xno": nums(r.get(a.xurrent_field)), "tnums": nums(r.get("Title")),
            "parent": clean(r.get("Parent issue")),
            "est": float(est) if re.fullmatch(r"\d+(\.\d+)?", est) else 0.0,
            "bug": "[bug]" in tr_lower(r.get("Title") or ""),
        })
    return items, excluded


def sprint_qmap(items: list[dict], explicit: list[str]) -> dict[str, str]:
    """GitHub sprint adı → 'Q4/3' etiketi. Ad içinde '(2026-Q4/3)' varsa ondan, yoksa numara
    farkından (Sprint 6 ↔ Q4/3 ise Sprint 5 ↔ Q4/2) türetilir."""
    m: dict[str, str] = {}
    offsets = []
    for sp in explicit:
        k, _, v = sp.partition("=")
        m[k.strip()] = v.strip()
    for name in {i["sprint"] for i in items if i["sprint"]}:
        q = re.search(r"Q(\d)/(\d+)", name)
        n = re.search(r"Sprint\s*(\d+)", name)
        if q:
            m.setdefault(name, f"Q{q.group(1)}/{q.group(2)}")
            if n:
                offsets.append((int(n.group(1)) - int(q.group(2)), q.group(1)))
    for k, v in list(m.items()):
        n, q = re.search(r"Sprint\s*(\d+)", k), re.fullmatch(r"Q(\d)/(\d+)", v)
        if n and q:
            offsets.append((int(n.group(1)) - int(q.group(2)), q.group(1)))
    if offsets:
        off, qn = collections.Counter(offsets).most_common(1)[0][0]
        for name in {i["sprint"] for i in items if i["sprint"]}:
            n = re.search(r"Sprint\s*(\d+)", name)
            if name not in m and n:
                m[name] = f"Q{qn}/{int(n.group(1)) - off}"
    return m


def match(plan: list[dict], items: list[dict], overrides: dict, all_nos: set[str] = frozenset()) -> None:
    """Her talebe GitHub işlerini bağlar: talep no (Xurrent alanı veya başlıktaki numara) →
    başlık benzerliği → elle eşleştirme (overrides: {"<4Me no | talep başlığı>": ["#800", ...]})."""
    by_num = {i["num"]: i for i in items}
    for t in plan:
        hits = [i for i in items if t["nos"] & (i["xno"] | i["tnums"])]
        how = "talep no" if hits else ""
        if not hits:
            A = toks(t["title"])
            for i in items:
                if (i["xno"] | i["tnums"]) & all_nos:
                    continue  # numarasıyla başka bir talebe ait iş başlık benzerliğiyle alınmaz
                B = toks(i["title"])
                if not A or not B:
                    continue
                common = len(A & B)
                if (common >= 3 and common / min(len(A), len(B)) >= 0.8) or common / len(A | B) >= 0.6:
                    hits.append(i)
            how = "başlık" if hits else ""
        ov = None
        for key in [t["no"], t["title"], f"row:{t['row']}"]:
            if key and key in overrides:
                ov = overrides[key]
        if ov is not None:
            hits = [by_num[str(x).lstrip("#")] for x in ov if str(x).lstrip("#") in by_num]
            how = "elle" if hits else "elle (eşleşme yok)"
        t["items"], t["how"] = hits, how
        t["manual"] = ov is not None
    # Kardeş işler: eşleşmemiş bir iş, bir talebe bağlı işle neredeyse aynı başlığı taşıyorsa
    # (BE/FE/App/Web parçaları) aynı talebe eklenir
    owner = {}
    for t in plan:
        for i in t["items"]:
            owner.setdefault(i["num"], []).append(t)
    for i in items:
        if i["num"] in owner or (i["xno"] | i["tnums"]) & all_nos:
            continue
        A = toks(i["title"])
        best = None
        for num, ts in owner.items():
            B = toks(by_num[num]["title"])
            if A and B and len(A & B) / len(A | B) >= 0.7:
                best = ts
                break
        for t in best or []:
            if not t.get("manual"):
                t["items"].append(i)
                t["how"] = t["how"] or "kardeş iş"


def talep_status(t: dict) -> None:
    its = t["items"]
    if its:
        st = min((i["status"] for i in its), key=rank)
        t["status"], t["source"] = st, "github"
        t["done"] = all(sk(i["status"]) in DONE for i in its)
        t["live"] = all(sk(i["status"]) in LIVE for i in its)
        t["group"] = "Tamamlandı" if t["done"] else "Geliştirme / test sürüyor"
    else:
        s = sk(t["state"])
        t["source"] = "liste"
        t["done"] = t["live"] = s in {sk(k) for k in SHEET_DONE}
        t["status"] = t["state"] or "—"
        t["group"] = "Tamamlandı" if t["done"] else next(
            (g for g, keys in SHEET_GROUPS if s in {sk(k) for k in keys}), "Diğer")
    # Talep listesi GitHub'dan daha ileri bir durum gösteriyorsa (Tamamlandı) o tercih edilir
    if t["source"] == "github" and sk(t["state"]) in {sk(k) for k in SHEET_DONE} and not t["live"]:
        t["status"], t["source"] = "Tamamlandı (liste)", "liste"
        t["done"] = t["live"] = True
        t["group"] = "Tamamlandı"
    t["ready"] = t["done"] and not t["live"]


# --------------------------------------------------------------------------- html parçaları

def page_assets() -> tuple[str, str]:
    t = TEMPLATE
    css = t[t.index("<style>") + 7:t.index("</style>")].replace("{{", "{").replace("}}", "}")
    js = t[t.index("<script>") + 8:t.index("</script>")].replace("{{", "{").replace("}}", "}")
    return css, js


def item_meta(t: dict, show_status: bool = True) -> str:
    parts = []
    if show_status:
        parts.append(esc(t["status"]))
    if t["items"]:
        n = len(t["items"])
        parts.append(f"{n} GitHub işi" if n > 1 else "#" + esc(t["items"][0]["num"]))
    return f' <span class="kmeta">{" · ".join(parts)}</span>' if parts else ""


def talep_li(t: dict, show_status: bool) -> str:
    tip = "\n".join(f'#{i["num"]} {i["status"]} · {i["title"]}' for i in t["items"]) or f"Listede: {t['state']}"
    later = (' <span class="carry">sonradan geldi</span>' if t["later"] else "") + (
        f' <span class="carry">{esc(t["quarter"] or "önceki çeyrek")}</span>' if t.get("carry") else "")
    return (f'<li title="{esc(tip)}"><span class="dot" style="--c:{hexof(t["status"]) if t["source"] == "github" else GROUP_COLORS.get(t["group"], NO_STATUS_COLOR)}"></span>'
            f'<span class="t">{esc(t["title"])}{later}{item_meta(t, show_status)}</span></li>')


def by_sprint_list(ts: list[dict], sprints: list[str], show_status: bool, empty: str) -> str:
    out = []
    for k, sp in enumerate(sprints):
        g = [t for t in ts if t["sprint"] == sp]
        if g:
            out.append(f'<li class="lgroup"><span class="khead {"story" if k % 2 == 0 else "proj"}">{esc(sp)}'
                       f'<span>{len(g)}</span></span></li>')
            out += [talep_li(t, show_status) for t in sorted(g, key=lambda t: tr_lower(t["title"]))]
    return "\n".join(out) or f'<li class="empty">{esc(empty)}</li>'


REVIEW_LANES = [("Bekliyor", lambda t: t["source"] == "liste" and t["group"] != "Sprint'e alındı (GitHub'da iş yok)",
                 GITHUB_COLORS["GRAY"]),
                ("Başlanmadı", lambda t: t["source"] == "liste" or sk(t["status"]) in {"todo", "to do", "backlog"},
                 GITHUB_COLORS["RED"]),
                ("Geliştirmede", lambda t: sk(t["status"]) in {"in progress", "review", "in review", "code review"},
                 GITHUB_COLORS["YELLOW"]),
                ("Test", lambda t: sk(t["status"]) in {"test", "qa"}, GITHUB_COLORS["ORANGE"]),
                ("UAT", lambda t: sk(t["status"]) == "uat", GITHUB_COLORS["PURPLE"])]


LANE_NOTE = {"Bekliyor": "hold · tasarım · planlama", "Başlanmadı": "Todo · GitHub'da iş yok",
             "Geliştirmede": "In Progress · Review", "UAT": "iş birimi onayı"}


def review_kanban(ts: list[dict], sprints: list[str]) -> str:
    lanes, used = [], set()
    for title, pred, color in REVIEW_LANES:
        g = [t for t in ts if id(t) not in used and pred(t)]
        used |= {id(t) for t in g}
        lanes.append((title, g, color))
    rest = [t for t in ts if id(t) not in used]
    dev = next(k for k, l in enumerate(lanes) if l[0] == "Geliştirmede")
    if rest:
        lanes[dev] = (lanes[dev][0], lanes[dev][1] + rest, lanes[dev][2])
    lanes = lanes[1:] + lanes[:1]  # "Bekliyor" en sağda
    weights = [max(sum(len(t["title"]) + 24 for t in g), 260) if g else 110 for _, g, _ in lanes]
    out = []
    for (title, g, color), w in zip(lanes, weights):
        parts = []
        for k, sp in enumerate(sprints):
            sg = [t for t in g if t["sprint"] == sp]
            if not sg:
                continue
            cards = "".join(
                f'<div class="kitem" title="{esc(chr(10).join(i["num"] + " " + i["status"] + " · " + i["title"] for i in t["items"]) or t["state"])}">'
                f'<span class="dot" style="--c:{hexof(t["status"]) if t["source"] == "github" else GROUP_COLORS.get(t["group"], NO_STATUS_COLOR)}"></span>'
                f'<span class="kt">{esc(t["title"])}'
                + (' <span class="carry">sonradan geldi</span>' if t["later"] else "")
                + (f' <span class="carry">{esc(t["quarter"] or "önceki çeyrek")}</span>' if t.get("carry") else "")
                + item_meta(t, True) + '</span></div>'
                for t in sorted(sg, key=lambda t: (rank(t["status"]), tr_lower(t["title"]))))
            parts.append(f'<div class="kgroup"><div class="khead {"story" if k % 2 == 0 else "proj"}">{esc(sp)}'
                         f'<span>{len(sg)}</span></div>{cards}</div>')
        out.append(f'<div class="lane"><div class="lhead" style="--c:{color}"><span>{esc(title)}</span>'
                   f'<b>{len(g)}</b><small>{esc(LANE_NOTE.get(title, ""))}</small></div>'
                   f'<div class="cards">{"".join(parts) or "<div class=lempty>—</div>"}</div></div>')
    cols = " ".join(f"minmax({260 if g else 150}px,{w}fr)" for w, (_, g, _) in zip(weights, lanes))
    return f'<div class="board" style="grid-template-columns:{cols}">' + "".join(out) + "</div>"


# --------------------------------------------------------------------------- build

def build(a) -> None:
    all_rows = load_plan(a.plan, a.sheet, a)
    q = a.quarter
    q_rows = [t for t in all_rows if t["quarter"] == q]
    out_scope = [t for t in q_rows if t.get("out_of_scope")]
    q_rows = [t for t in q_rows if not t.get("out_of_scope")]
    cancel = {sk(k) for k in SHEET_CANCEL}
    cancelled = [t for t in q_rows if sk(t["state"]) in cancel]
    plan = [t for t in q_rows if t not in cancelled]
    # Başka çeyreğin talebi ama bu çeyreğin sprintine alınmış: devreden talep
    carry = [t for t in all_rows if t["quarter"] != q and t["sprint"].startswith(q + "/")
             and sk(t["state"]) not in cancel]
    items, gh_excluded = load_github(a.export, a)
    qmap = sprint_qmap(items, a.sprint_map or [])
    for i in items:
        i["q"] = qmap.get(i["sprint"], "")

    overrides = {}
    if a.matches and os.path.exists(a.matches):
        with open(a.matches, encoding="utf-8") as fh:
            overrides = json.load(fh)
    later_how = (f'talep listesinde "{a.col_later}" = evet'
                 if getattr(a, "_has_later_col", False) and not a.later_from_diff_only else "")
    if a.later_from_diff_only:
        for t in all_rows:
            t["later"] = False
    if a.previous_plan:
        prev = [p for p in load_plan(a.previous_plan, a.sheet, a) if p["quarter"] == q]
        prev_nos = set().union(*(p["nos"] for p in prev)) if prev else set()
        prev_titles = {tr_lower(p["title"]) for p in prev}
        if a.later_from_diff_only:
            for p in prev:
                p["later"] = False
        prev_later_nos = set().union(*(p["nos"] for p in prev if p["later"])) if prev else set()
        prev_later_titles = {tr_lower(p["title"]) for p in prev if p["later"]}
        prev_toks = [toks(p["title"]) for p in prev]

        def similar(title):
            A = toks(title)
            return any(A and B and len(A & B) / len(A | B) >= 0.6 for B in prev_toks)
        for t in plan:
            known = (t["nos"] & prev_nos) or tr_lower(t["title"]) in prev_titles or similar(t["title"])
            flagged = (t["nos"] & prev_later_nos) or tr_lower(t["title"]) in prev_later_titles
            if flagged or not known:
                t["later"] = True
        later_how = ((later_how + " · ") if later_how else "") + (
            f"{esc(a.previous_label or 'önceki liste')} ile karşılaştırıldığında sonradan {q}'e eklenen talepler"
            if a.later_from_diff_only else
            f"önceki listede sonradan gelen olarak işaretli ya da {esc(a.previous_label or 'önceki listeden')} "
            f"sonra {q}'e eklenen talepler")
    a._later_how = later_how or "—"
    all_nos = set().union(*(t["nos"] for t in all_rows))
    match(plan + carry, items, overrides, all_nos)
    for t in plan + carry:
        t["carry"] = t in carry
        talep_status(t)

    # takvim
    qs = dt.date.fromisoformat(a.quarter_start)
    today = dt.date.fromisoformat(a.today) if a.today else dt.date.today()
    total_days = a.quarter_sprints * a.sprint_days
    elapsed_days = min(max((today - qs).days + 1, 0), total_days)
    cur_k = min(max((today - qs).days // a.sprint_days + 1, 1), a.quarter_sprints)
    qn = re.sub(r"\D", "", q)
    labels = [f"Q{qn}/{k}" for k in range(1, a.quarter_sprints + 1)]
    last = labels[max(cur_k - a.last_sprints, 0):cur_k]
    gh_name = {v: k for k, v in qmap.items()}

    n_plan, n_done = len(plan), sum(t["done"] for t in plan)
    frac = elapsed_days / total_days
    expected = n_plan * frac
    projection = n_done / frac if frac else 0
    remaining_sprints = a.quarter_sprints - cur_k + (1 - ((today - qs).days % a.sprint_days + 1) / a.sprint_days)
    need_per_sprint = (n_plan - n_done) / remaining_sprints if remaining_sprints > 0 else 0
    done_per_sprint = n_done / (elapsed_days / a.sprint_days) if elapsed_days else 0

    # sprint bazında
    sp_rows = []
    for k, lb in enumerate(labels, start=1):
        ts = [t for t in plan if t["sprint"] == lb]
        cs = [t for t in carry if t["sprint"] == lb]
        tag = " (şimdiki)" if k == cur_k else (" (ileri)" if k > cur_k else "")
        sp_rows.append({"label": f"{lb}{tag}",
                        "c": len(ts), "cd": sum(t["done"] for t in ts),
                        "k": len(cs), "kd": sum(t["done"] for t in cs)})
    no_sprint = [t for t in plan if not t["sprint"]]
    sp_rows.append({"label": "Sprint atanmamış", "c": len(no_sprint), "cd": sum(t["done"] for t in no_sprint),
                    "k": 0, "kd": 0})
    sprint_table = multi_table(sp_rows, [{"key": "c", "done": "cd", "head": f"{q} talebi"},
                                         {"key": "k", "done": "kd", "head": "Devreden talep"}], label_fr=2.0)

    # durum dağılımı
    grp = collections.Counter(t["group"] for t in plan)
    order = ["Tamamlandı", "Geliştirme / test sürüyor"] + [g for g, _ in SHEET_GROUPS] + ["Diğer"]
    st_rows = [{"label": g, "color": GROUP_COLORS[g], "x": grp[g]} for g in order if grp[g]]
    status_table = multi_table(st_rows, [{"key": "x", "head": ""}], label_fr=2.2,
                               total={"label": "Toplam", "x": n_plan})

    # sonradan gelen / plan dışı
    later = [t for t in plan if t["later"]]
    matched_nums = {i["num"] for t in plan + carry for i in t["items"]}
    other_q = [t for t in all_rows if t["quarter"] != q]
    last_items = [i for i in items if i["q"] in last]
    outside = [i for i in last_items if i["num"] not in matched_nums]
    mid_keys = {re.sub(r"[\s_-]", "", tr_lower(x)) for x in a.mid_label}

    def bucket(i):
        if any(i["xno"] & t["nos"] or i["tnums"] & t["nos"] for t in other_q) or any(
                i in t["items"] for t in carry):
            return "Önceki çeyrekten"
        if i["bug"]:
            return "Bug düzeltmesi"
        if i["label_keys"] & mid_keys:
            return "Sprint ortası eklenen"
        return "Listede karşılığı yok"
    bk = collections.defaultdict(list)
    for i in outside:
        bk[bucket(i)].append(i)
    out_rows = [{"label": k, "c": len(v), "e": sum(i["est"] for i in v)} for k, v in
                sorted(bk.items(), key=lambda kv: -len(kv[1]))]
    out_table = multi_table(out_rows, [{"key": "c", "head": "İş"}, {"key": "e", "head": "Efor (saat)"}],
                            label_fr=2.6) if out_rows else ""
    out_share = pct(len(outside), len(last_items))
    out_eshare = pct(sum(i["est"] for i in outside), sum(i["est"] for i in last_items))

    insights = {}
    if a.insights and os.path.exists(a.insights):
        with open(a.insights, encoding="utf-8") as fh:
            insights = json.load(fh)

    # son N sprint kapsamı
    scope = [t for t in plan + carry if t["sprint"] in last]
    live = [t for t in scope if t["live"]]
    ready = [t for t in scope if t["ready"]]
    open_ = [t for t in scope if not t["done"]]

    if a.signals:
        sig = {
            "ceyrek": q, "bugun": today.isoformat(), "gecen_gun": elapsed_days, "toplam_gun": total_days,
            "gecen_sure_pct": round(frac * 100), "simdiki_sprint": labels[cur_k - 1],
            "planlanan_talep": n_plan, "iptal": len(cancelled), "tamamlanan_talep": n_done,
            "tamamlanma_pct": round(pct(n_done, n_plan)), "sureye_gore_beklenen": round(expected, 1),
            "bu_hizla_ceyrek_sonu": round(projection, 1), "sprint_basina_tamamlanan": round(done_per_sprint, 1),
            "kalan_icin_sprint_basina_gereken": round(need_per_sprint, 1),
            "sprint_bazinda": sp_rows, "durum_dagilimi": dict(grp),
            "sonradan_gelen": [{"talep": t["title"], "durum": t["status"], "sprint": t["sprint"]} for t in later],
            "devreden_talep": len(carry), "devreden_tamamlanan": sum(t["done"] for t in carry),
            "devreden_son_sprintlerde": [f'{t["sprint"]} {t["title"]} ({t["status"]})' for t in carry if t["sprint"] in last],
            "son_sprintler": last, "son_sprint_isleri": len(last_items),
            "plan_disi_is": len(outside), "plan_disi_pct": round(out_share), "plan_disi_efor_pct": round(out_eshare),
            "plan_disi_kirilim": {k: [f'#{i["num"]} {i["title"]}' for i in v] for k, v in bk.items()},
            "son_sprint_kapsami": len(scope), "canlida": len(live), "canliya_hazir": len(ready), "devam_eden": len(open_),
            "devam_eden_statu": dict(collections.Counter(t["status"] for t in open_)),
            "talep_tipi": dict(collections.Counter(t["type"] for t in plan)),
            "eslesmeyen_son_sprint_talepleri": [f'{t["sprint"]} {t["title"]} ({t["state"]})'
                                               for t in scope if not t["items"]],
        }
        with open(a.signals, "w", encoding="utf-8") as fh:
            json.dump(sig, fh, ensure_ascii=False, indent=2)
    if a.dump_matches:
        with open(a.dump_matches, "w", encoding="utf-8") as fh:
            json.dump({
                "talepler": [{"anahtar": t["no"] or t["title"], "sprint": t["sprint"], "durum": t["state"],
                              "talep": t["title"], "eslesme": t["how"],
                              "github": [f'#{i["num"]} {i["status"]} · {i["title"]}' for i in t["items"]]}
                             for t in plan + carry if t["sprint"] in last or t["items"]],
                "eslesmeyen_github_isleri": [f'#{i["num"]} [{i["q"]}] {i["status"]} · {i["title"]}' for i in outside],
            }, fh, ensure_ascii=False, indent=2)

    # ---------------------------------------------------------------- slaytlar
    team = a.team or ""
    sub = " · ".join(x for x in [team, f"{q} 2026" if not a.year else f"{q} {a.year}",
                                 f"{labels[cur_k - 1]} sonu", today.strftime("%d.%m.%Y")] if x)
    pc = pct(n_done, n_plan)
    comp = f"""
<div class="comp story">
  <div class="cnums" style="grid-template-columns:repeat(3,1fr);margin:0 0 18px">
    <div><span>Planlanan</span><b>{n_plan}<small>talep</small></b></div>
    <div><span>Tamamlanan</span><b>{n_done}<small>talep</small></b></div>
    <div><span>Kalan</span><b>{n_plan - n_done}<small>talep</small></b></div>
  </div>
  <div class="chero">%{pc:.0f}<span class="hcap">tamamlanma</span></div>
  <div class="bar"><i style="width:{min(pc, 100):.2f}%"></i></div>
</div>"""
    diff = n_done - expected
    verdict = ("plana göre <b>ileride</b>" if diff >= 0.5 else "plana göre <b>geride</b>" if diff <= -0.5
               else "plana <b>paralel</b>")
    pace = f"""
<div class="srows">
  <div class="srow"><div class="sname">Geçen süre</div><div class="shero">%{frac * 100:.0f}</div>
    <div class="sbody"><div class="bar warm"><i style="width:{frac * 100:.2f}%"></i></div>
    <div class="stext"><b>{elapsed_days}</b> / {total_days} gün</div></div></div>
  <div class="srow"><div class="sname">Tamamlanan</div><div class="shero">%{pct(n_done, n_plan):.0f}</div>
    <div class="sbody"><div class="bar"><i style="width:{min(pct(n_done, n_plan), 100):.2f}%"></i></div>
    <div class="stext"><b>{n_done}</b> / {n_plan} talep</div></div></div>
</div>
<div class="pace">
  <div>Süreye göre beklenen: <b>~{expected:.0f}</b> talep · gerçekleşen <b>{n_done}</b> → {verdict}
    ({"+" if diff >= 0 else "−"}{abs(diff):.0f} talep)</div>
  <div>Şu ana kadar sprint başına <b>{tr1(done_per_sprint)}</b> talep; kalan {n_plan - n_done} talep için
    sprint başına <b>{tr1(need_per_sprint)}</b> gerekiyor. Bu hızla çeyrek sonunda <b>~{projection:.0f}</b>
    talep (%{pct(projection, n_plan):.0f}).</div>
</div>"""
    later_sorted = sorted(later, key=lambda t: (t["sprint"] or "Q9", tr_lower(t["title"])))
    later_list = "".join(f'<li><span class="dot" style="--c:{hexof(t["status"]) if t["source"] == "github" else GROUP_COLORS.get(t["group"])}"></span>'
                         f'{esc(t["title"])} <span class="kmeta">{esc(t["sprint"] or "—")} · {esc(t["status"])}</span></li>'
                         for t in later_sorted[:9 if len(later) <= 9 else 8]) or '<li class="none">Sonradan gelen talep yok</li>'
    if len(later) > 9:
        later_list += f'<li class="none">… ve {len(later) - 8} talep daha</li>'
    tone_lbl = {"risk": "Dikkat", "good": "İyi giden", "action": "Öneri", "data": "Veri kalitesi"}
    if insights.get("cards"):
        ins_cards = "".join(
            f'<div class="rcard {esc(c.get("tone", "risk"))}"><div class="rtag">{esc(tone_lbl.get(c.get("tone", "risk"), ""))}</div>'
            f'<div class="rhead">{esc(c["title"])}</div>'
            + (f'<p>{c["text"]}</p>' if c.get("text") else "")
            + ("<ul>" + "".join(f"<li>{b}</li>" for b in c.get("bullets", [])) + "</ul>" if c.get("bullets") else "")
            + "</div>" for c in insights["cards"])
    elif insights.get("bullets"):
        ins_cards = "".join(f'<div class="rcard action"><p>{b}</p></div>' for b in insights["bullets"])
    else:
        ins_cards = '<div class="emptymsg">İçgörüler için --insights verilmedi.</div>'
    qs_html = "".join(f"<li>{x}</li>" for x in insights.get("questions", []))

    scope_note = f" ve kapsam dışı ({esc(a.col_scope)} = Hayır) {len(out_scope)} talep" if out_scope else ""
    s1 = f"""
<div class="frame"><section class="slide" aria-label="{esc(q)} Talep Planı">
  <header>
    <div><h1>{esc(q)} Talep Planı <span>- Neredeyiz?</span></h1><div class="sub">{esc(sub)}</div></div>
  </header>
  <div class="grid">
    <div class="panel"><div class="ptitle">Planlanan talepler</div><div class="comps r one">{comp}</div>
      <div class="pfoot">Talep listesi ({esc(a.sheet)}), Quarter = {esc(q)} · iptal edilen {len(cancelled)} talep{scope_note} hariç</div></div>
    <div class="panel"><div class="ptitle">Zamana göre hızımız</div>{pace}
      <div class="pfoot">Çeyrek {qs.strftime("%d.%m.%Y")} başlangıçlı {a.quarter_sprints} sprint × {a.sprint_days} gün</div></div>
    <div class="panel"><div class="ptitle">Talep durumu</div>{status_table}
      <div class="pfoot">Durum: talep listesi ile GitHub'dan hangisi daha ilerideyse o · GitHub'da birden fazla iş varsa en geride olanı</div></div>
    <div class="panel span2"><div class="ptitle">Sprint bazında planlanan vs tamamlanan</div>
      <div class="pkey"><span style="--k:var(--plan)">Planlanan</span><span style="--k:var(--ink)">Tamamlanan</span></div>
      {sprint_table}
      <div class="pfoot">Listedeki Sprint kolonu · {esc(" · ".join(f"{l} = {re.sub(r' - .*', '', gh_name[l])}" for l in labels if l in gh_name))}</div></div>
    <div class="panel"><div class="ptitle">Sonradan gelen talepler</div>
      <div class="later"><b>{len(later)}</b> talep çeyreğe sonradan eklendi
        <ul class="names one">{later_list}</ul></div>
      <div class="pfoot">Sonradan gelen = {a._later_how}</div></div>
  </div>
</section></div>
<div class="frame"><section class="slide" aria-label="{esc(q)} İçgörüleri">
  <header>
    <div><h1>{esc(q)} <span>İçgörüleri</span></h1><div class="sub">{esc(insights.get("subtitle") or sub)}</div></div>
  </header>
  <div class="rlayout"{"" if qs_html else ' style="grid-template-columns:1fr"'}>
    <div class="agrid rgridc">{ins_cards}</div>
    {f'<div class="rq"><div class="rqt">Konuşalım</div><ol>{qs_html}</ol></div>' if qs_html else ''}
  </div>
</section></div>"""

    s2 = f"""
<div class="frame"><section class="slide" aria-label="Canlıya çıkanlar">
  <header>
    <div><h1>Son {len(last)} Sprint <span>- Canlıya Çıkanlar ve Hazır Olanlar</span></h1>
      <div class="sub">{esc(team)} · {esc(" & ".join(last))} kapsamı · {len(scope)} planlanan talep</div></div>
  </header>
  <div class="rgrid" style="grid-template-columns:{"minmax(0,1.6fr) minmax(0,1fr)" if len(live) > 10 else "1fr 1fr"}">
    <div class="rsec"><div class="rtitle">Canlıya çıkan <b>{len(live)}</b> <small>(tüm GitHub işleri Released ya da listede Tamamlandı)</small></div>
      <ul class="list" style="--cols:{2 if len(live) > 10 else 1};--max:24">
{by_sprint_list(live, last, False, "Bu kapsamda canlıya çıkan talep yok.")}
      </ul></div>
    <div class="rsec done"><div class="rtitle">Canlıya hazır <b>{len(ready)}</b> <small>(işleri Done, release bekliyor)</small></div>
      <ul class="list" style="--cols:1;--max:24">
{by_sprint_list(ready, last, False, "Release bekleyen talep yok.")}
      </ul></div>
  </div>
  <div class="rnote">Kapsam: talep listesinde Sprint = {esc(", ".join(last))} olan talepler · kalan {len(open_)} talebin durumu sonraki slaytta ·
    #numara = GitHub işi</div>
</section></div>"""

    s3 = f"""
<div class="frame"><section class="slide" aria-label="Devam eden işler">
  <header>
    <div><h1>Son {len(last)} Sprint <span>- Devam Eden İşler</span></h1>
      <div class="sub">{esc(team)} · {esc(" & ".join(last))} kapsamında tamamlanmamış {len(open_)} talep</div></div>
    <ul class="legend">{"".join(f'<li><span class="dot" style="--c:{hexof(s)}"></span>{s}</li>' for s in ["Todo", "In Progress", "Review", "Test", "UAT"])}
      <li class="sep"></li><li><span class="dot" style="--c:{GROUP_COLORS["Sprint'e alındı (GitHub'da iş yok)"]}"></span>GitHub'da işi yok</li>
      <li><span class="carry">önceki çeyrekten</span>&nbsp;= devreden talep</li></ul>
  </header>
  {review_kanban(open_, last)}
</section></div>"""

    unmatched_scope = [t for t in scope if not t["items"]]
    cards = [
        ("Kapsam", [f"Talep listesi: <b>{esc(a.plan_label or os.path.basename(a.plan))}</b> → \"{esc(a.sheet)}\" sayfası, Quarter = {esc(q)}: "
                    f"{len(q_rows) + len(out_scope)} talep; iptal edilen {len(cancelled)}"
                    + (f" ve kapsam dışı ({esc(a.col_scope)} = Hayır) {len(out_scope)}" if out_scope else "")
                    + f" talep hariç <b>{n_plan}</b> talep.",
                    f"GitHub: {esc(a.export_label or os.path.basename(a.export))} ({len(items)} iş; Backlog / RetroAction hariç: "
                    f"{', '.join('#' + n for n in gh_excluded) or '—'}).",
                    f"Son {len(last)} sprint = {esc(', '.join(last))} (GitHub: {esc(', '.join(re.sub(r' - .*', '', gh_name.get(l, '?')) for l in last))})."]),
        ("Talep ↔ GitHub eşleşmesi", [
            "Önce 4Me / Xurrent Talep No (GitHub'daki Xurrent alanı veya başlıktaki numara), yoksa başlık benzerliği; "
            "gerekirse elle eşleştirme dosyası.",
            f"Eşleşen talep: {sum(1 for t in plan if t['items'])} / {n_plan} "
            f"(no: {sum(1 for t in plan if t['how'] == 'talep no')}, başlık: {sum(1 for t in plan if t['how'] == 'başlık')}, "
            f"elle: {sum(1 for t in plan if t['how'] == 'elle')}).",
            "Bir talebin birden fazla GitHub işi olabilir; talep, işlerinin en geride olanının statüsünü alır."]),
        ("Tamamlandı / canlı tanımı", [
            "Talep durumu: talep listesi ile GitHub'dan hangisi daha ileri durumu gösteriyorsa o (listede \"Tamamlandı\" ise canlıda sayılır).",
            "Tamamlandı: GitHub işlerinin hepsi Done veya Released ya da talep listesinde \"Tamamlandı\".",
            "Canlıya çıkan: işlerin hepsi Released (ya da listede Tamamlandı). Canlıya hazır: hepsi bitmiş, en az biri Done.",
            "GitHub export'unda yalnız son sprintler olduğundan önceki sprintlerin talepleri çoğunlukla listedeki duruma göre sayılır."]),
        ("Hız", [f"Geçen süre = çeyrek başından ({qs.strftime('%d.%m.%Y')}) bugüne takvim günü ÷ "
                 f"{a.quarter_sprints} × {a.sprint_days} gün.",
                 "Süreye göre beklenen = planlanan talep × geçen süre oranı (talepler çeyreğe eşit dağılsaydı).",
                 "Bu hızla çeyrek sonu = tamamlanan ÷ geçen süre oranı."]),
        ("Devreden talepler", [
            f"Quarter'ı {esc(q)} olmayan ama Sprint kolonu {esc(q)}/x olan talepler devreden sayılır ({len(carry)} talep): "
            f"{esc(q)} tamamlanma oranına girmez, son sprint slaytlarında ve sprint tablosunda ayrı gösterilir."]),
        ("Sonradan gelen / plan dışı", [
            f"Sonradan gelen talep: {a._later_how}.",
            f"Plan dışı iş: son {len(last)} sprintteki GitHub işlerinden {esc(q)} talebine bağlanamayanlar; önceki çeyrek "
            "talebine bağlananlar \"devreden\", [BUG] başlıklılar \"hata\", AddedMidSprint etiketliler \"sprint ortası\" sayılır."]),
    ]
    if unmatched_scope:
        cards.append(("GitHub'da işi bulunamayan talepler (son sprintler)",
                      [f"{esc(t['sprint'])} · {esc(t['title'])} ({esc(t['state'])})" for t in unmatched_scope[:14]]
                      + ([f"… ve {len(unmatched_scope) - 14} talep daha"] if len(unmatched_scope) > 14 else [])))
    assumptions = "".join(
        f'<div class="acard"><div class="atitle">{t}</div><ul>{"".join(f"<li>{b}</li>" for b in bl)}</ul></div>'
        for t, bl in cards)
    s4 = f"""
<div class="frame"><section class="slide" aria-label="Hesaplama Varsayımları">
  <header>
    <div><h1>Hesaplama <span>Varsayımları</span></h1><div class="sub">{esc(sub)} · bu sunumdaki rakamlar nasıl hesaplandı?</div></div>
  </header>
  <div class="agrid">{assumptions}</div>
</section></div>"""

    css, js = page_assets()
    extra = """
.pace { font-size:16px; color:var(--ink-2); line-height:1.45; margin-top:8px; display:flex; flex-direction:column; gap:8px; }
.pace b { color:var(--ink); }
.later { font-size:16px; color:var(--ink-2); margin-bottom:6px; }
.later b { color:var(--ink); }
.later .names { margin-top:4px; }
ul.ins { margin:0; padding-left:1.1em; font-size:15.5px; line-height:1.4; color:var(--ink-2); }
ul.ins li { margin:0 0 7px; }
ul.ins b { color:var(--ink); }
"""
    html = f"""<!doctype html>
<html lang="tr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sprint Review</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;800&display=swap" rel="stylesheet">
<style>{css}{extra}</style>
</head>
<body>
<div class="deck">
{s1}
{s2}
{s3}
{s4}
</div>
<script>{js}</script>
</body>
</html>
"""
    with open(a.output, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"{a.output} yazıldı · {q}: {n_done}/{n_plan} talep tamamlandı (%{pct(n_done, n_plan):.0f}), "
          f"geçen süre %{frac * 100:.0f} · son sprintler {', '.join(last)}: {len(scope)} talep, "
          f"canlı {len(live)}, hazır {len(ready)}, devam {len(open_)} · plan dışı iş {len(outside)}/{len(last_items)}")
    if unmatched_scope:
        print(f"uyarı: son sprint kapsamında GitHub işi bulunamayan {len(unmatched_scope)} talep "
              f"(--dump-matches ile gözden geçir, gerekirse --matches ile elle eşle)", file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", required=True, help="Çeyreklik talep listesi (xlsx)")
    ap.add_argument("--sheet", default="Bnext + BO", help="Talep listesindeki sayfa")
    ap.add_argument("--export", required=True, help="GitHub Projects sprint export'u (TSV/CSV)")
    ap.add_argument("--quarter", default="Q4")
    ap.add_argument("--year", default="")
    ap.add_argument("--quarter-start", required=True, help="Çeyreğin ilk sprintinin başlangıcı, ör. 2026-10-05")
    ap.add_argument("--quarter-sprints", type=int, default=7)
    ap.add_argument("--sprint-days", type=int, default=14)
    ap.add_argument("--today", help="Bugünün tarihi (YYYY-MM-DD); varsayılan sistem tarihi")
    ap.add_argument("--last-sprints", type=int, default=2, help="Canlı/kanban slaytları için son N sprint")
    ap.add_argument("--sprint-map", action="append",
                    help="GitHub sprint adı → çeyrek etiketi, ör. 'Sprint 5=Q4/2' (adında Q4/3 yoksa)")
    ap.add_argument("--matches", help="Elle eşleştirme JSON'u: {\"<4Me no | talep başlığı>\": [\"#800\", ...]}")
    ap.add_argument("--dump-matches", help="Eşleşme taslağını bu JSON'a yaz (gözden geçirmek için)")
    ap.add_argument("--signals", help="İçgörü yazmak için metrik özetini bu JSON'a yaz")
    ap.add_argument("--insights", help="Claude'un yazdığı içgörüler: {\"bullets\": [\"...\"]}")
    ap.add_argument("--team", default="Retail")
    ap.add_argument("--previous-plan", help="Önceki talep listesi (xlsx): ondan sonra çeyreğe eklenen talepler sonradan gelen sayılır")
    ap.add_argument("--later-from-diff-only", action="store_true",
                    help="Sonradan gelen = yalnız önceki listeyle fark (\"Sonradan Gelen\" kolonları yok sayılır)")
    ap.add_argument("--previous-label", help="Önceki listenin adı/tarihi, ör. '12.11.2026 listesi'")
    ap.add_argument("--plan-label", help="Varsayımlar slaytında gösterilecek talep listesi dosya adı")
    ap.add_argument("--export-label", help="Varsayımlar slaytında gösterilecek GitHub export dosya adı")
    ap.add_argument("--col-title", default="Talep Başlığı / Konu")
    ap.add_argument("--col-quarter", default="Quarter")
    ap.add_argument("--col-sprint", default="Sprint")
    ap.add_argument("--col-state", default="Sprint Planlama")
    ap.add_argument("--col-no", default="4Me Talep No")
    ap.add_argument("--col-no-extra", action="append",
                    help="Eşleşmede kullanılacak ek numara sütunu (ör. İhale sayfasında 'İş Birimi Talebi')")
    ap.add_argument("--col-scope", default="Kapsamda mı?",
                    help="Varsa 'Hayır' olan talepler çeyrek planı dışında sayılır")
    ap.add_argument("--col-later", default="Sonradan Gelen")
    ap.add_argument("--col-type", default="Talep Tipi")
    ap.add_argument("--col-squad", default="Squad")
    ap.add_argument("--xurrent-field", default="Xurrent Talep No")
    ap.add_argument("--estimate-field", default="Original Estimate")
    ap.add_argument("--mid-label", action="append", default=None)
    ap.add_argument("--exclude-status", action="append", default=None)
    ap.add_argument("--exclude-label", action="append", default=None)
    ap.add_argument("-o", "--output", default="sprint-review.html")
    a = ap.parse_args()
    a.mid_label = a.mid_label or ["AddedMidSprint"]
    a.exclude_status = a.exclude_status or ["Backlog"]
    a.exclude_label = a.exclude_label or ["RetroAction"]
    build(a)


if __name__ == "__main__":
    main()
