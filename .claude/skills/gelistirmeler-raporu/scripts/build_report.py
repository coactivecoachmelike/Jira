#!/usr/bin/env python3
"""GitHub Projects (v2) -> "Geliştirmeler" HTML raporu.

Bir GitHub organizasyon projesindeki item'ları (issue / PR / draft) çeker,
statü renkli bullet'lı bir liste, statü lejantı ve "Original Estimate"
üzerinden planlanan vs. tamamlanan iş grafikleri içeren tek dosyalık bir
HTML raporu üretir. Yalnızca Python standart kütüphanesini kullanır.

Örnekler:
  # Canlı veri (gh CLI veya GITHUB_TOKEN ile)
  python3 build_report.py --org borusanotomotiv --project 34 --view 11 -o gelistirmeler.html

  # Daha önce kaydedilmiş JSON'dan
  python3 build_report.py --from-json data.json -o gelistirmeler.html
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import urllib.request

# GitHub Projects single-select renk adları -> hex (Primer "fg" tonları)
GITHUB_COLORS = {
    "GRAY": "#8C959F",
    "BLUE": "#218BFF",
    "GREEN": "#2DA44E",
    "YELLOW": "#D4A72C",
    "ORANGE": "#FB8F44",
    "RED": "#E5534B",
    "PINK": "#E85AAD",
    "PURPLE": "#A475F9",
}
NO_STATUS = "Statüsüz"
NO_STATUS_COLOR = "#C4CAD2"

DEFAULT_DONE = ["done", "tamamlandı", "tamamlandi", "completed", "closed",
                "kapandı", "kapandi", "canlıda", "canlida", "released", "yayında"]

FIELD_FRAG = "field { ... on ProjectV2FieldCommon { name databaseId } }"
QUERY = """
query($org: String!, $number: Int!, $cursor: String, $view: Int!, $withView: Boolean!) {
  organization(login: $org) {
    projectV2(number: $number) {
      title
      url
      view(number: $view) @include(if: $withView) { name filter layout }
      fields(first: 100) {
        nodes {
          __typename
          ... on ProjectV2FieldCommon { id name databaseId dataType }
          ... on ProjectV2SingleSelectField { options { id name color description } }
        }
      }
      items(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          isArchived
          type
          content {
            __typename
            ... on Issue { title url number state repository { nameWithOwner }
                           assignees(first: 10) { nodes { login name } }
                           labels(first: 20) { nodes { name } } }
            ... on PullRequest { title url number state repository { nameWithOwner }
                                 assignees(first: 10) { nodes { login name } }
                                 labels(first: 20) { nodes { name } } }
            ... on DraftIssue { title assignees(first: 10) { nodes { login name } } }
          }
          fieldValues(first: 50) {
            nodes {
              __typename
              ... on ProjectV2ItemFieldSingleSelectValue { name %(f)s }
              ... on ProjectV2ItemFieldNumberValue { number %(f)s }
              ... on ProjectV2ItemFieldTextValue { text %(f)s }
              ... on ProjectV2ItemFieldDateValue { date %(f)s }
              ... on ProjectV2ItemFieldIterationValue { title %(f)s }
              ... on ProjectV2ItemFieldMilestoneValue { milestone { title } %(f)s }
            }
          }
        }
      }
    }
  }
}
""" % {"f": FIELD_FRAG}


# --------------------------------------------------------------------------- fetch

def graphql(query: str, variables: dict) -> dict:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token and shutil.which("gh"):
        cmd = ["gh", "api", "graphql", "-f", f"query={query}"]
        for k, v in variables.items():
            if v is None:
                continue
            flag = "-F" if isinstance(v, (int, bool)) else "-f"
            val = str(v).lower() if isinstance(v, bool) else str(v)
            cmd += [flag, f"{k}={val}"]
        out = subprocess.run(cmd, capture_output=True, text=True)
        if out.returncode != 0:
            sys.exit(f"gh api graphql hata verdi:\n{out.stderr}\n"
                     "İpucu: `gh auth refresh -s read:project` ile proje okuma yetkisi ekleyin.")
        data = json.loads(out.stdout)
    elif token:
        req = urllib.request.Request(
            "https://api.github.com/graphql",
            data=json.dumps({"query": query, "variables": variables}).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:
            data = json.load(resp)
    else:
        sys.exit("GitHub kimlik bilgisi yok: `gh auth login -s read:project` çalıştırın "
                 "veya GITHUB_TOKEN (read:project kapsamlı) tanımlayın.")
    if data.get("errors"):
        sys.exit("GraphQL hatası: " + json.dumps(data["errors"], ensure_ascii=False, indent=2))
    return data["data"]


def fetch_project(org: str, number: int, view: int | None) -> dict:
    items, cursor, project = [], None, None
    while True:
        data = graphql(QUERY, {"org": org, "number": number, "cursor": cursor,
                               "view": view or 1, "withView": bool(view)})
        project = data["organization"]["projectV2"]
        if project is None:
            sys.exit(f"Proje bulunamadı: {org} #{number}")
        items.extend(project["items"]["nodes"])
        page = project["items"]["pageInfo"]
        if not page["hasNextPage"]:
            break
        cursor = page["endCursor"]
    project["items"] = items
    return project


def normalize(project: dict, status_field: str) -> dict:
    """GraphQL cevabını raporun kullandığı sade JSON yapısına çevirir."""
    fields = [f for f in project["fields"]["nodes"] if f]
    status_opts = []
    for f in fields:
        if f.get("name", "").lower() == status_field.lower() and f.get("options"):
            status_opts = [{"name": o["name"], "color": o["color"],
                            "description": o.get("description") or ""} for o in f["options"]]
    number_fields = [{"name": f["name"], "databaseId": f.get("databaseId")}
                     for f in fields if f.get("dataType") == "NUMBER"]
    out_items = []
    for it in project["items"]:
        c = it.get("content") or {}
        vals = {}
        for fv in it["fieldValues"]["nodes"]:
            if not fv or not fv.get("field"):
                continue
            name = fv["field"]["name"]
            for key in ("name", "number", "text", "date", "title"):
                if key in fv:
                    vals[name] = fv[key]
                    break
            if "milestone" in fv:
                vals[name] = (fv["milestone"] or {}).get("title")
            vals.setdefault("__ids__", {})[name] = fv["field"].get("databaseId")
        ids = vals.pop("__ids__", {})
        out_items.append({
            "title": c.get("title") or "(başlıksız)",
            "url": c.get("url"),
            "number": c.get("number"),
            "repo": (c.get("repository") or {}).get("nameWithOwner"),
            "type": it.get("type"),
            "state": c.get("state") or ("DRAFT" if it.get("type") == "DRAFT_ISSUE" else None),
            "archived": it.get("isArchived", False),
            "assignees": [a["login"] for a in (c.get("assignees") or {}).get("nodes", [])],
            "labels": [l["name"] for l in (c.get("labels") or {}).get("nodes", [])],
            "fields": vals,
            "fieldIds": ids,
        })
    return {
        "project": {"title": project.get("title"), "url": project.get("url")},
        "view": project.get("view"),
        "statusField": status_field,
        "statusOptions": status_opts,
        "numberFields": number_fields,
        "items": out_items,
    }


# --------------------------------------------------------------------------- filter

def _norm(s: str) -> str:
    return re.sub(r"\s+", "-", s.strip().lower())


def _field_value(item: dict, key: str):
    key = _norm(key)
    if key in ("assignee", "assignees"):
        return item["assignees"]
    if key in ("label", "labels"):
        return item["labels"]
    if key == "repo":
        return item["repo"]
    for name, v in item["fields"].items():
        if _norm(name) == key:
            return v
    return None


def _match_value(actual, wanted: str) -> bool:
    if actual is None:
        return False
    if isinstance(actual, list):
        return any(_match_value(a, wanted) for a in actual)
    if isinstance(actual, (int, float)):
        m = re.fullmatch(r"(>=|<=|>|<)?\s*(-?[\d.]+)(?:\.\.(-?[\d.]+))?", wanted)
        if not m:
            return False
        op, a, b = m.groups()
        a = float(a)
        if b is not None:
            return a <= actual <= float(b)
        return {"<": actual < a, ">": actual > a, "<=": actual <= a,
                ">=": actual >= a, None: actual == a}[op]
    return str(actual).strip().lower() == wanted.strip().lower()


def apply_filter(items: list[dict], expr: str, me: str | None = None) -> tuple[list[dict], list[str]]:
    """GitHub Projects filtre sözdiziminin yaygın alt kümesini uygular.

    Desteklenen: is:open|closed|issue|pr|draft, [-]alan:değer1,değer2,
    no:alan, has:alan, sayısal karşılaştırma (>5, 1..3), serbest metin.
    """
    warnings = []
    try:
        tokens = shlex.split(expr or "")
    except ValueError:
        tokens = (expr or "").split()
    result = items
    for tok in tokens:
        neg = tok.startswith("-")
        body = tok[1:] if neg else tok
        if ":" not in body:
            pred = lambda it, t=body.lower(): t in it["title"].lower()
        else:
            key, val = body.split(":", 1)
            k = _norm(key)
            if k == "is":
                v = val.lower()
                preds = {
                    "open": lambda it: it["state"] in ("OPEN", "DRAFT"),
                    "closed": lambda it: it["state"] in ("CLOSED", "MERGED"),
                    "issue": lambda it: it["type"] == "ISSUE",
                    "pr": lambda it: it["type"] == "PULL_REQUEST",
                    "draft": lambda it: it["type"] == "DRAFT_ISSUE",
                    "archived": lambda it: it["archived"],
                }
                if v not in preds:
                    warnings.append(f"desteklenmeyen filtre atlandı: {tok}")
                    continue
                pred = preds[v]
            elif k in ("no", "has"):
                want_empty = k == "no"
                pred = lambda it, f=val, e=want_empty: (
                    (_field_value(it, f) in (None, [], "")) == e)
            else:
                wanted = [w for w in val.split(",") if w]
                if any(w.startswith("@") and w != "@me" for w in wanted):
                    warnings.append(f"desteklenmeyen filtre atlandı: {tok}")
                    continue
                wanted = [me if w == "@me" else w for w in wanted]
                pred = lambda it, f=key, ws=wanted: any(
                    _match_value(_field_value(it, f), w) for w in ws if w)
        result = [it for it in result if bool(pred(it)) != neg]
    return result, warnings


# --------------------------------------------------------------------------- model

def pick_estimate_field(data: dict, name: str | None, field_id: int | None) -> str | None:
    nf = data.get("numberFields", [])
    if field_id:
        for f in nf:
            if f.get("databaseId") == field_id:
                return f["name"]
    if name:
        for f in nf:
            if f["name"].lower() == name.lower():
                return f["name"]
    for f in nf:
        if "estimate" in f["name"].lower() or "tahmin" in f["name"].lower():
            return f["name"]
    return name


def build_model(data: dict, items: list[dict], estimate_field: str | None,
                done_names: list[str], closed_as_done: bool) -> dict:
    status_field = data.get("statusField", "Status")
    options = [dict(o, hex=GITHUB_COLORS.get(o.get("color", "GRAY"), GITHUB_COLORS["GRAY"]))
               for o in data.get("statusOptions", [])]
    order = {o["name"]: i for i, o in enumerate(options)}
    done_set = {d.lower() for d in done_names}
    known = {o["name"] for o in options}

    rows = []
    for it in items:
        status = it["fields"].get(status_field) or NO_STATUS
        if status not in known and status != NO_STATUS:
            options.append({"name": status, "color": "GRAY", "hex": GITHUB_COLORS["GRAY"], "description": ""})
            order[status] = len(order)
            known.add(status)
        est = it["fields"].get(estimate_field) if estimate_field else None
        est = float(est) if isinstance(est, (int, float)) else 0.0
        done = status.lower() in done_set or (
            closed_as_done and it["state"] in ("CLOSED", "MERGED"))
        rows.append(dict(it, status=status, estimate=est, done=done))

    if any(r["status"] == NO_STATUS for r in rows):
        options.append({"name": NO_STATUS, "color": None, "hex": NO_STATUS_COLOR,
                        "description": "Projede statü atanmamış"})
        order[NO_STATUS] = len(order)

    for o in options:
        members = [r for r in rows if r["status"] == o["name"]]
        o["count"] = len(members)
        o["estimate"] = sum(r["estimate"] for r in members)
        o["done"] = o["name"].lower() in done_set

    people = {}
    for r in rows:
        for a in r["assignees"] or ["Atanmamış"]:
            p = people.setdefault(a, {"name": a, "planned": 0.0, "done": 0.0, "count": 0})
            p["planned"] += r["estimate"]
            p["count"] += 1
            if r["done"]:
                p["done"] += r["estimate"]

    planned = sum(r["estimate"] for r in rows)
    completed = sum(r["estimate"] for r in rows if r["done"])
    return {
        "rows": rows,
        "order": order,
        "options": options,
        "people": sorted(people.values(), key=lambda p: (-p["planned"], p["name"])),
        "planned": planned,
        "completed": completed,
        "count": len(rows),
        "count_done": sum(1 for r in rows if r["done"]),
        "missing_estimate": sum(1 for r in rows if r["estimate"] == 0),
    }


# --------------------------------------------------------------------------- render

def fmt(n: float) -> str:
    s = f"{n:,.1f}".rstrip("0").rstrip(".")
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def render(model: dict, title: str, subtitle: str, unit: str, sort: str, columns: int,
           estimate_field: str | None, warnings: list[str]) -> str:
    u = f" {unit}" if unit else ""
    planned, completed = model["planned"], model["completed"]
    pct = (completed / planned * 100) if planned else 0.0
    used_opts = [o for o in model["options"] if o["count"]]

    # Lejant
    legend = "".join(
        f'<li title="{esc(o["description"])}"><span class="dot" style="--c:{o["hex"]}"></span>'
        f'{esc(o["name"])}{" ✓" if o["done"] else ""}<span class="muted"> · {o["count"]}</span></li>'
        for o in model["options"] if o["count"] or o["name"] != NO_STATUS)

    # Statü bazlı efor şeridi (parça-bütün)
    seg_total = sum(o["estimate"] for o in used_opts) or 1
    segs = "".join(
        f'<div class="seg" style="flex:{o["estimate"]};--c:{o["hex"]}" '
        f'data-tip="{esc(o["name"])}: {fmt(o["estimate"])}{u} ({o["estimate"] / seg_total * 100:.0f}%) · {o["count"]} iş"></div>'
        for o in used_opts if o["estimate"] > 0)
    status_rows = "".join(
        f'<tr><td><span class="dot" style="--c:{o["hex"]}"></span>{esc(o["name"])}</td>'
        f'<td class="num">{o["count"]}</td><td class="num">{fmt(o["estimate"])}{u}</td>'
        f'<td class="num">{o["estimate"] / seg_total * 100:.0f}%</td></tr>'
        for o in used_opts)

    # Kişi bazlı planlanan vs tamamlanan (bullet bar)
    pmax = max([p["planned"] for p in model["people"]] + [1])
    people = "".join(
        f'<div class="prow"><div class="pname" title="{esc(p["name"])}">{esc(p["name"])}</div>'
        f'<div class="ptrack" data-tip="{esc(p["name"])} · Planlanan {fmt(p["planned"])}{u} · '
        f'Tamamlanan {fmt(p["done"])}{u} · {p["count"]} iş">'
        f'<div class="pplan" style="width:{p["planned"] / pmax * 100:.2f}%"></div>'
        f'<div class="pdone" style="width:{p["done"] / pmax * 100:.2f}%"></div></div>'
        f'<div class="pval">{fmt(p["done"])} / {fmt(p["planned"])}</div></div>'
        for p in model["people"] if p["planned"] > 0)
    if not people:
        people = '<p class="muted">Tahmin girilmiş iş yok.</p>'

    # Liste
    rows = list(model["rows"])
    if sort == "status":
        rows.sort(key=lambda r: (model["order"].get(r["status"], 999), r["title"].lower()))
    elif sort == "title":
        rows.sort(key=lambda r: r["title"].lower())
    hexes = {o["name"]: o["hex"] for o in model["options"]}
    items = []
    for r in rows:
        name = esc(r["title"])
        if r.get("url"):
            name = f'<a href="{esc(r["url"])}" target="_blank" rel="noopener">{name}</a>'
        meta = f'{fmt(r["estimate"])}{u}' if r["estimate"] else ""
        tip = f'{r["status"]}' + (f' · {", ".join(r["assignees"])}' if r["assignees"] else "") + \
              (f' · {fmt(r["estimate"])}{u}' if r["estimate"] else "")
        items.append(
            f'<li title="{esc(tip)}"><span class="dot" style="--c:{hexes.get(r["status"], NO_STATUS_COLOR)}"></span>'
            f'<span class="t">{name}</span>'
            + (f'<span class="est">{meta}</span>' if meta else "") + "</li>")

    note_bits = []
    if estimate_field:
        note_bits.append(f'Efor alanı: <b>{esc(estimate_field)}</b>')
    if model["missing_estimate"]:
        note_bits.append(f'{model["missing_estimate"]} işin tahmini boş (0 sayıldı)')
    note_bits += [esc(w) for w in warnings]
    notes = " · ".join(note_bits)
    today = dt.date.today().strftime("%d.%m.%Y")

    return TEMPLATE.format(
        title=esc(title), subtitle=esc(subtitle), today=today, legend=legend,
        planned=fmt(planned), completed=fmt(completed), remaining=fmt(planned - completed),
        unit=esc(u), pct=f"{pct:.0f}", pct_w=f"{min(pct, 100):.2f}",
        count=model["count"], count_done=model["count_done"],
        segs=segs or '<div class="seg" style="flex:1;--c:var(--track)"></div>',
        status_rows=status_rows, people=people, items="\n".join(items),
        columns=columns, notes=notes,
    )


TEMPLATE = """<!doctype html>
<html lang="tr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;800&display=swap" rel="stylesheet">
<style>
:root {{
  --bg:#ffffff; --surface:#ffffff; --ink:#0B1F3A; --ink-2:#3B4A5E; --muted:#6B7788;
  --rule:#DCE6EE; --accent:#1FA3B5; --track:#E8EDF2; --plan:#C9D5E1; --done:#0B1F3A;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --bg:#0E1622; --surface:#0E1622; --ink:#EEF3F8; --ink-2:#C3CDD8; --muted:#8C99A8;
    --rule:#243244; --accent:#3CC3D4; --track:#1C2838; --plan:#34465C; --done:#EEF3F8;
  }}
}}
:root[data-theme="dark"] {{
  --bg:#0E1622; --surface:#0E1622; --ink:#EEF3F8; --ink-2:#C3CDD8; --muted:#8C99A8;
  --rule:#243244; --accent:#3CC3D4; --track:#1C2838; --plan:#34465C; --done:#EEF3F8;
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink);
  font:15px/1.4 "Plus Jakarta Sans", Inter, system-ui, -apple-system, "Segoe UI", sans-serif; }}
.wrap {{ max-width:1280px; margin:0 auto; padding:32px 40px 48px; }}
header {{ display:flex; flex-wrap:wrap; align-items:flex-end; justify-content:space-between; gap:12px 24px; }}
h1 {{ font-size:44px; font-weight:800; letter-spacing:-.02em; margin:0; line-height:1; }}
.sub {{ color:var(--muted); font-size:13px; margin-top:8px; }}
.legend {{ list-style:none; margin:0; padding:10px 14px; display:flex; flex-wrap:wrap; gap:6px 18px;
  border:1px solid var(--rule); border-radius:10px; font-size:13px; color:var(--ink-2); }}
.legend-title {{ font-weight:600; color:var(--ink); margin-right:4px; }}
.dot {{ display:inline-block; width:10px; height:10px; border-radius:50%; background:var(--c);
  margin-right:8px; flex:none; }}
.muted {{ color:var(--muted); }}
section {{ margin-top:28px; }}
h2 {{ font-size:14px; font-weight:600; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); margin:0 0 12px; }}
.kpis {{ display:grid; grid-template-columns:repeat(4, minmax(0,1fr)); gap:12px; }}
.kpi {{ border:1px solid var(--rule); border-radius:10px; padding:14px 16px; }}
.kpi .l {{ font-size:12px; color:var(--muted); }}
.kpi .v {{ font-size:28px; font-weight:800; margin-top:4px; font-variant-numeric:tabular-nums; }}
.kpi .v small {{ font-size:14px; font-weight:500; color:var(--muted); }}
.progress {{ margin-top:14px; }}
.bar {{ height:14px; border-radius:7px; background:var(--track); overflow:hidden; position:relative; }}
.bar > i {{ display:block; height:100%; width:{pct_w}%; background:var(--accent); border-radius:7px; }}
.bar-lbl {{ display:flex; justify-content:space-between; font-size:12px; color:var(--muted); margin-top:6px; }}
.charts {{ display:grid; grid-template-columns:1fr 1fr; gap:24px; }}
.card {{ border:1px solid var(--rule); border-radius:10px; padding:16px 18px; }}
.card h3 {{ margin:0 0 12px; font-size:15px; font-weight:600; }}
.strip {{ display:flex; gap:2px; height:22px; }}
.seg {{ background:var(--c); min-width:3px; }}
.seg:first-child {{ border-radius:4px 0 0 4px; }} .seg:last-child {{ border-radius:0 4px 4px 0; }}
.seg:hover, .ptrack:hover {{ filter:brightness(1.08); }}
table {{ width:100%; border-collapse:collapse; margin-top:12px; font-size:13px; }}
td {{ padding:5px 0; border-top:1px solid var(--rule); color:var(--ink-2); }}
td:first-child {{ color:var(--ink); }}
td .dot {{ vertical-align:-1px; }}
.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
.pkey {{ display:flex; gap:16px; font-size:12px; color:var(--ink-2); margin-bottom:10px; }}
.pkey span::before {{ content:""; display:inline-block; width:12px; height:8px; border-radius:2px;
  margin-right:6px; background:var(--k); }}
.prow {{ display:grid; grid-template-columns:120px 1fr 80px; gap:10px; align-items:center; margin:7px 0; font-size:13px; }}
.pname {{ overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--ink-2); }}
.ptrack {{ position:relative; height:12px; }}
.pplan, .pdone {{ position:absolute; left:0; top:0; height:100%; border-radius:0 4px 4px 0; }}
.pplan {{ background:var(--plan); }}
.pdone {{ background:var(--done); height:6px; top:3px; }}
.pval {{ text-align:right; color:var(--muted); font-variant-numeric:tabular-nums; }}
.list {{ list-style:none; margin:0; padding:0; columns:{columns}; column-gap:40px;
  column-rule:1px solid var(--accent); }}
.list li {{ break-inside:avoid; display:flex; align-items:flex-start; gap:4px; padding:12px 0;
  border-bottom:1px solid var(--rule); font-size:15px; font-weight:500; color:var(--ink); }}
.list .dot {{ margin-top:6px; }}
.list .t {{ flex:1; }}
.list a {{ color:inherit; text-decoration:none; }}
.list a:hover {{ text-decoration:underline; }}
.est {{ font-size:11px; color:var(--muted); border:1px solid var(--rule); border-radius:999px;
  padding:1px 7px; margin-top:2px; white-space:nowrap; font-variant-numeric:tabular-nums; }}
.notes {{ margin-top:18px; font-size:12px; color:var(--muted); }}
#tip {{ position:fixed; pointer-events:none; background:var(--ink); color:var(--bg); font-size:12px;
  padding:6px 9px; border-radius:6px; opacity:0; transition:opacity .1s; z-index:9; max-width:280px; }}
@media (max-width: 900px) {{
  .wrap {{ padding:20px 16px 32px; }} h1 {{ font-size:32px; }}
  .kpis {{ grid-template-columns:repeat(2, minmax(0,1fr)); }}
  .charts {{ grid-template-columns:1fr; }} .list {{ columns:1; }}
}}
@media print {{ .est {{ border-color:#bbb; }} #tip {{ display:none; }} }}
</style>
</head>
<body>
<div class="wrap">
<header>
  <div>
    <h1>{title}</h1>
    <div class="sub">{subtitle} · {today}</div>
  </div>
  <ul class="legend" aria-label="Statü lejantı"><li class="legend-title">Statüler:</li>{legend}<li class="muted">✓ = tamamlandı sayılır · sayı = iş adedi</li></ul>
</header>

<section aria-label="Efor özeti">
  <h2>Planlanan vs. tamamlanan iş</h2>
  <div class="kpis">
    <div class="kpi"><div class="l">Toplam planlanan</div><div class="v">{planned}<small>{unit}</small></div></div>
    <div class="kpi"><div class="l">Tamamlanan</div><div class="v">{completed}<small>{unit}</small></div></div>
    <div class="kpi"><div class="l">Kalan</div><div class="v">{remaining}<small>{unit}</small></div></div>
    <div class="kpi"><div class="l">Tamamlanma oranı</div><div class="v">%{pct} <small>· {count_done}/{count} iş</small></div></div>
  </div>
  <div class="progress" data-tip="Tamamlanan {completed}{unit} / Planlanan {planned}{unit}">
    <div class="bar" role="img" aria-label="Tamamlanma %{pct}"><i></i></div>
    <div class="bar-lbl"><span>0</span><span>Tamamlanan %{pct}</span><span>{planned}{unit}</span></div>
  </div>
</section>

<section class="charts">
  <div class="card">
    <h3>Statü bazında efor dağılımı</h3>
    <div class="strip" role="img" aria-label="Statü bazında efor dağılımı">{segs}</div>
    <table><tbody>{status_rows}</tbody></table>
  </div>
  <div class="card">
    <h3>Kişi bazında planlanan vs. tamamlanan</h3>
    <div class="pkey"><span style="--k:var(--plan)">Planlanan</span><span style="--k:var(--done)">Tamamlanan</span></div>
    {people}
  </div>
</section>

<section>
  <h2>İş listesi</h2>
  <ul class="list">
{items}
  </ul>
  <div class="notes">{notes}</div>
</section>
</div>
<div id="tip" role="tooltip"></div>
<script>
(() => {{
  const tip = document.getElementById('tip');
  document.addEventListener('mousemove', e => {{
    const el = e.target.closest('[data-tip]');
    if (!el) {{ tip.style.opacity = 0; return; }}
    tip.textContent = el.dataset.tip;
    const x = Math.min(e.clientX + 14, innerWidth - tip.offsetWidth - 8);
    tip.style.left = x + 'px'; tip.style.top = (e.clientY + 14) + 'px'; tip.style.opacity = 1;
  }});
}})();
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_argument_group("veri kaynağı")
    src.add_argument("--org", default="borusanotomotiv")
    src.add_argument("--project", type=int, default=34, help="Proje numarası")
    src.add_argument("--view", type=int, default=11, help="Filtresi uygulanacak görünüm numarası (0 = yok)")
    src.add_argument("--from-json", help="GitHub yerine bu normalize JSON dosyasını kullan")
    src.add_argument("--dump-json", help="Çekilen normalize veriyi bu dosyaya da yaz")
    flt = ap.add_argument_group("filtre / hesap")
    flt.add_argument("--filter", help="Görünüm filtresi yerine kullanılacak filtre (GitHub sözdizimi)")
    flt.add_argument("--extra-filter", default="", help="Görünüm filtresine eklenecek ek filtre")
    flt.add_argument("--me", help="Filtredeki @me için kullanıcı adı")
    flt.add_argument("--status-field", default="Status")
    flt.add_argument("--estimate-field", default="Original Estimate")
    flt.add_argument("--estimate-field-id", type=int, default=402706858,
                     help="Efor alanının databaseId'si (görünüm URL'sindeki sayısal alan)")
    flt.add_argument("--done-status", action="append",
                     help="Tamamlandı sayılacak statü adı (tekrarlanabilir)")
    flt.add_argument("--closed-as-done", action="store_true",
                     help="Kapalı issue/merge edilmiş PR'ları statüden bağımsız tamamlandı say")
    flt.add_argument("--include-archived", action="store_true")
    out = ap.add_argument_group("çıktı")
    out.add_argument("-o", "--output", default="gelistirmeler.html")
    out.add_argument("--title", default="Geliştirmeler")
    out.add_argument("--unit", default="", help="Efor birimi etiketi (ör. 'sa', 'gün', 'SP')")
    out.add_argument("--sort", choices=["status", "title", "none"], default="status")
    out.add_argument("--columns", type=int, default=3)
    a = ap.parse_args()

    if a.from_json:
        with open(a.from_json, encoding="utf-8") as fh:
            data = json.load(fh)
    else:
        data = normalize(fetch_project(a.org, a.project, a.view or None), a.status_field)
    if a.dump_json:
        with open(a.dump_json, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)

    items = data["items"] if a.include_archived else [i for i in data["items"] if not i.get("archived")]
    view = data.get("view") or {}
    expr = a.filter if a.filter is not None else (view.get("filter") or "")
    expr = f"{expr} {a.extra_filter}".strip()
    items, warnings = apply_filter(items, expr, a.me)

    est_field = pick_estimate_field(data, a.estimate_field, a.estimate_field_id)
    model = build_model(data, items, est_field, a.done_status or DEFAULT_DONE, a.closed_as_done)

    proj = data.get("project") or {}
    subtitle = " · ".join(x for x in [proj.get("title"), view.get("name")] if x)
    page = render(model, a.title, subtitle, a.unit, a.sort, a.columns, est_field, warnings)
    with open(a.output, "w", encoding="utf-8") as fh:
        fh.write(page)

    pct = model["completed"] / model["planned"] * 100 if model["planned"] else 0
    print(f"{a.output} yazıldı: {model['count']} iş, planlanan {fmt(model['planned'])}, "
          f"tamamlanan {fmt(model['completed'])} (%{pct:.0f}). Filtre: {expr or '-'}")
    for w in warnings:
        print("uyarı:", w, file=sys.stderr)


if __name__ == "__main__":
    main()
