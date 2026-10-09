"""Renderer genérico: opportunity_record[] (+ configuración de presentación del caso + locale) -> una página HTML autocontenida.

No conoce ningún caso. Todo lo específico (título, etiquetas, facetas, vocabulario de decisión humana) llega por `presentation` (cases/<caso>/presentation.json)
y los textos por oi/presentation/i18n/<locale>.json. Las métricas se CALCULAN de los registros: no hay cifras escritas aquí.
Seguridad: todo texto de datos pasa por html.escape; no se ejecuta contenido de los registros; sin red.
"""
import collections
import html
import json
import os

from oi.contracts.validator import validate

HERE = os.path.dirname(os.path.abspath(__file__))
E = lambda s: html.escape("" if s is None else str(s), quote=True)


class RenderError(Exception):
    pass


def load_i18n(locale):
    p = os.path.join(HERE, "i18n", f"{locale}.json")
    if not os.path.isfile(p): raise RenderError(f"locale sin traducción: {locale}")
    with open(p, encoding="utf-8") as f: return json.load(f)


def validate_records(records):
    bad = []
    for i, r in enumerate(records):
        errs = validate(r, "opportunity_record.v1.schema.json")
        if errs: bad.append((i, r.get("record_id"), errs[:3]))
    if bad: raise RenderError(f"{len(bad)} registro(s) no cumplen opportunity_record.v1: {bad[:2]}")


def metrics(records):
    """Todas las cifras salen de los registros."""
    rc = lambda r: r["contact"].get("recommended_contact")
    return {"total": len(records),
            "by_action": dict(collections.Counter(r["recommended_action"]["action"] for r in records)),
            "by_decision": dict(collections.Counter(((r["qualification"].get("human_review") or {}).get("decision")) for r in records if r["qualification"].get("human_review"))),
            "by_grade": dict(collections.Counter(rc(r)["contact_quality"]["grade"] for r in records if rc(r))),
            "with_contact": sum(1 for r in records if rc(r)), "without_contact": sum(1 for r in records if not rc(r)),
            "origins": dict(collections.Counter(r["provenance"]["data_origin"] for r in records)),
            "credits": sum(r["cost"].get("credits", 0) for r in records), "paid_calls": sum(r["cost"].get("paid_calls", 0) for r in records)}


def _kv(label, value): return f'<div class="kv"><span>{E(label)}</span><b>{value}</b></div>'


def _tag(text, cls=""): return f'<span class="tag {cls}">{E(text)}</span>'


def _contact_block(c, T, ev_idx):
    U, N = T["ui"], T["enums"]
    rc = c.get("recommended_contact"); out = []
    ch = {x["channel_id"]: x for x in c.get("channels", [])}
    def one(r, title):
        q = r["contact_quality"]
        via = ch.get(r["via_channel_id"]) if r.get("via_channel_id") else None
        tgt = next((p for p in c.get("people", []) if p["person_id"] == r["ref"]), None) if r["target"] == "PERSON" else ch.get(r["ref"])
        who = (f'{E(tgt["name"])} — {E(tgt["title"])}' if tgt and "name" in tgt else (f'{E(tgt["kind"])} {E(tgt["value"])}' if tgt else E(r["ref"])))
        h = f'<div class="contact"><h4>{E(title)}: {who}</h4>'
        h += f'<p>{E(r["why_this_contact"])}</p>'
        h += '<div class="grid">' + _kv(U["grade"], E(N["grade"].get(q["grade"], q["grade"]))) + _kv(U["strength"], E(N["strength"].get(q["evidence_strength"], q["evidence_strength"]))) \
            + _kv(U["reach"], E(N["reach"].get(q["reachability"], q["reachability"]))) + _kv(U["target_fit"], E(N["target_fit"].get(q["target_fit"], q["target_fit"]))) \
            + _kv(U["fresh"], E(N["fresh"].get(q["freshness"], q["freshness"]))) + _kv(U["rung"], E(r["ladder_rung"])) \
            + (_kv(U["via"], f'{E(via["kind"])} · {E(via["value"])}') if via else "") + '</div>'
        return h + f'<p class="next">{E(U["next"])}: {E(r["next_action"])}</p></div>'
    if rc: out.append(one(rc, U["contact"]))
    else:
        sb = c.get("selection_blocked")
        out.append(f'<div class="contact none"><h4>{E(U["no_contact"])}</h4>' + (f'<p>{E(U["why_no_contact"])}: {E(sb if isinstance(sb, str) else json.dumps(sb, ensure_ascii=False))}</p>' if sb else "") + '</div>')
    for a in c.get("alternatives", []): out.append(one(a, U["alternatives"]))
    nr = c.get("not_recommended", [])
    if nr: out.append(f'<h5>{E(U["not_recommended"])}</h5><ul>' + "".join(f'<li>{E(x.get("reason") if isinstance(x, dict) and x.get("reason") else json.dumps(x, ensure_ascii=False))}</li>' for x in nr) + '</ul>')
    if c.get("not_found"): out.append(f'<h5>{E(U["not_found"])}</h5><ul>' + "".join(f'<li>{E(x)}</li>' for x in c["not_found"]) + '</ul>')
    if c.get("contradictions"): out.append(f'<h5>{E(U["contradictions"])}</h5><ul>' + "".join(f'<li>{E(x.get("description", x))}</li>' for x in c["contradictions"]) + '</ul>')
    return "".join(out)


def _record(r, T, P):
    U, N = T["ui"], T["enums"]
    q = r["qualification"]; hr = q.get("human_review"); co = r["company"]; act = r["recommended_action"]
    dec = hr["decision"] if hr else ""
    dec_label = (P.get("human_decision_vocabulary") or {}).get(dec) or N["decision"].get(dec, dec)
    facets = r.get("facets") or {}
    fattr = "".join(f' data-f-{E(k)}="{E("|".join(map(str, v)) if isinstance(v, list) else v)}"' for k, v in sorted(facets.items()))
    ev_idx = {e["evidence_id"]: e for e in r.get("evidence", [])}
    h = f'<details class="rec" data-action="{E(act["action"])}" data-decision="{E(dec)}" data-name="{E(co["name"]).lower()}"{fattr}>'
    h += f'<summary><b>{E(co["name"])}</b> {_tag(N["action"].get(act["action"], act["action"]), "a-" + act["action"])}'
    if dec: h += " " + _tag(dec_label, "d-" + dec)
    rc = r["contact"].get("recommended_contact")
    if rc: h += " " + _tag(rc["contact_quality"]["grade"], "g")
    h += f' <small>{E(co.get("domain") or "")}</small></summary><div class="body">'
    h += f'<p class="sum">{E(act["summary"])}</p>'
    if act.get("blocking_reasons"): h += "<ul>" + "".join(f"<li>{E(x)}</li>" for x in act["blocking_reasons"]) + "</ul>"
    h += '<div class="grid">' + _kv(U["location"], E((co.get("location") or {}).get("text") or U["none"])) + _kv(U["size"], E((co.get("size") or {}).get("text") or U["none"])) \
        + _kv(U["business_model"], E(", ".join(co.get("business_model") or []) or U["none"])) + _kv(U["identity"], E(r["company_identity"]["status"])) + "</div>"
    if co.get("description"): h += f'<p class="desc">{E(co["description"])}</p>'
    w = r["why_opportunity"]
    h += f'<h5>{E(U["why"])}</h5>'
    if w.get("summary"): h += f'<p>{E(w["summary"])}</p>'
    h += "<ul>" + "".join(f'<li>{_tag(N["claim"].get(x["claim_status"], x["claim_status"]), "c-" + x["claim_status"])} {E(x["text"])}</li>' for x in w.get("reasons", [])) + "</ul>"
    if w.get("unknowns"): h += f'<h5>{E(U["unknown"])}</h5><ul>' + "".join(f"<li>{E(x)}</li>" for x in w["unknowns"]) + "</ul>"
    if r.get("signals"):
        h += f'<h5>{E(U["signals"])}</h5><ul>' + "".join(f'<li>{E(s["label"])}: {_tag(N["signal_state"].get(s["state"], s["state"]))} {E("" if s.get("value") is None else s["value"])}</li>' for s in r["signals"]) + "</ul>"
    h += f'<h5>{E(U["qualification"])}</h5><div class="grid">' + _kv(U["gates"], E(", ".join(f'{g["gate_id"]}: {N["gate"].get(g["status"], g["status"])}' for g in q["gates"]) or U["none"])) \
        + _kv(U["fit"], E(N["fit"].get(q["fit_verification"], q["fit_verification"]))) + _kv(U["human"], E(dec_label or U["none"]) + (f' · {E(hr.get("comment") or "")}' if hr else "")) \
        + _kv(U["ranking"], E(U["not_scored"] if r["ranking"]["status"] == "NOT_SCORED" else r["ranking"]["status"])) + "</div>"
    h += _contact_block(r["contact"], T, ev_idx)
    if r.get("evidence"):
        h += f'<details class="ev"><summary>{E(U["evidence"])} ({len(r["evidence"])})</summary><ul>' + "".join(
            f'<li><b>{E(e["field"])}</b>: {E(e["value"])} {_tag(N["strength"].get(e["evidence_strength"], e["evidence_strength"]))} {_tag(e["source_class"])} {_tag(N["claim"].get(e["claim_status"], e["claim_status"]), "c-" + e["claim_status"])}'
            f' — {E(e.get("source_label") or "")} {E(e.get("source_url") or "")} ({E(U["retrieved"])} {E(e.get("retrieved_at") or "")})</li>' for e in r["evidence"]) + "</ul></details>"
    pv = r["provenance"]; cost = r["cost"]
    h += f'<p class="foot">{E(U["provenance"])}: {E(pv["data_origin"])} · {E(U["cost"])}: {E(cost.get("credits", 0))} {E(U["credits_spent"])}, {E(cost.get("paid_calls", 0))} {E(U["paid_calls"])}</p>'
    return h + "</div></details>"


CSS = """:root{--bg:#f7f7f4;--fg:#1d2327;--mut:#5d6870;--card:#fff;--line:#dcdfe0;--acc:#1f5f8b;--warn:#8a4b00;--warnbg:#fff3df}
@media(prefers-color-scheme:dark){:root{--bg:#14181b;--fg:#e8ecee;--mut:#9aa7ae;--card:#1c2226;--line:#2f393f;--acc:#6fb3de;--warn:#ffcf8a;--warnbg:#3a2a10}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}main{max-width:980px;margin:0 auto;padding:20px 16px 60px}
h1{margin:.2em 0}.sub{color:var(--mut);margin:0 0 14px}.banner{background:var(--warnbg);color:var(--warn);border:1px solid var(--warn);border-radius:8px;padding:10px 14px;margin:10px 0;font-weight:600}
.principle{color:var(--mut);font-size:13px;margin:6px 0 14px}.metrics{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}.metric{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 12px}.metric b{font-size:20px;display:block}
.filters{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0}.filters select,.filters input{padding:6px 8px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--fg)}
.rec{background:var(--card);border:1px solid var(--line);border-radius:8px;margin:8px 0}.rec>summary{cursor:pointer;padding:10px 14px}.rec .body{padding:2px 16px 14px}
.tag{display:inline-block;border:1px solid var(--line);border-radius:10px;padding:0 8px;font-size:12px;margin-right:3px}.a-CONTACT{border-color:#2c7a4b;color:#2c7a4b}.a-HUMAN_REVIEW{border-color:var(--acc);color:var(--acc)}.c-INFERRED,.c-UNKNOWN{border-style:dashed}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:6px 14px;margin:8px 0}.kv span{display:block;color:var(--mut);font-size:12px}.contact{border-left:3px solid var(--acc);padding:2px 12px;margin:10px 0}.contact.none{border-color:var(--warn)}
h4,h5{margin:12px 0 4px}.foot,.desc{color:var(--mut);font-size:13px}small{color:var(--mut)}ul{margin:4px 0;padding-left:20px}"""

JS = """const q=s=>[...document.querySelectorAll(s)];function ap(){const a=document.getElementById('fa').value,d=document.getElementById('fd').value,t=document.getElementById('ft').value.toLowerCase();let n=0;
q('.rec').forEach(r=>{let ok=(!a||r.dataset.action===a)&&(!d||r.dataset.decision===d)&&(!t||r.dataset.name.includes(t));q('select[data-facet]').forEach(s=>{if(s.value&&!(r.getAttribute('data-f-'+s.dataset.facet)||'').split('|').includes(s.value))ok=false});r.hidden=!ok;if(ok)n++});document.getElementById('cnt').textContent=n}
q('.filters select,.filters input').forEach(e=>e.addEventListener('input',ap));ap();"""


def render(records, presentation, locale=None, validate_input=True):
    """-> str (HTML). `presentation`: dict de cases/<caso>/presentation.json."""
    if validate_input: validate_records(records)
    T = load_i18n(locale or presentation.get("locale", "es")); U = T["ui"]; M = metrics(records)
    act_opts = sorted(M["by_action"]); dec_opts = sorted(M["by_decision"])
    fac = []
    for f in presentation.get("facets") or []:
        vals = sorted({str(x) for r in records for x in (lambda v: v if isinstance(v, list) else [v])((r.get("facets") or {}).get(f["key"], [])) if x not in ("", None)})
        labels = f.get("value_labels") or {}
        if vals: fac.append((f, [(v, labels.get(v, v)) for v in vals]))
    vocab = presentation.get("human_decision_vocabulary") or {}
    o = [f'<!doctype html><html lang="{E(T["lang"])}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{E(presentation["title"])}</title><style>{CSS}</style></head><body><main>']
    o.append(f'<h1>{E(presentation["title"])}</h1><p class="sub">{E(presentation.get("subtitle") or "")}</p>')
    for origin in M["origins"]:
        if origin in T["banners"] and origin != "REAL_PROVIDER": o.append(f'<div class="banner" role="note">{E(T["banners"][origin])}</div>')
    for n in presentation.get("notes") or []: o.append(f'<p class="principle">{E(n)}</p>')
    o.append(f'<p class="principle">{E(U["principle"])}</p><div class="metrics"><div class="metric"><b>{M["total"]}</b>{E(U["records"])}</div>')
    o.append(f'<div class="metric"><b>{M["with_contact"]}</b>{E(U["with_contact"])}</div><div class="metric"><b>{M["without_contact"]}</b>{E(U["without_contact"])}</div>')
    for k in sorted(M["by_action"]): o.append(f'<div class="metric"><b>{M["by_action"][k]}</b>{E(T["enums"]["action"].get(k, k))}</div>')
    for k in sorted(M["by_decision"]): o.append(f'<div class="metric"><b>{M["by_decision"][k]}</b>{E(vocab.get(k) or T["enums"]["decision"].get(k, k))}</div>')
    o.append(f'<div class="metric"><b>{M["credits"]}</b>{E(U["credits_spent"])}</div></div>')
    o.append(f'<div class="filters"><input id="ft" placeholder="{E(U["search"])}"><select id="fa"><option value="">{E(U["filter_action"])}: {E(U["all"])}</option>' + "".join(f'<option value="{E(a)}">{E(T["enums"]["action"].get(a, a))}</option>' for a in act_opts) + "</select>")
    if dec_opts: o.append(f'<select id="fd"><option value="">{E(U["filter_decision"])}: {E(U["all"])}</option>' + "".join(f'<option value="{E(d)}">{E(vocab.get(d) or T["enums"]["decision"].get(d, d))}</option>' for d in dec_opts) + "</select>")
    else: o.append('<select id="fd" hidden></select>')
    for f, vals in fac: o.append(f'<select data-facet="{E(f["key"])}"><option value="">{E(f["label"])}: {E(U["all"])}</option>' + "".join(f'<option value="{E(v)}">{E(l)}</option>' for v, l in vals) + "</select>")
    o.append(f'<span><b id="cnt"></b> {E(U["shown"])}</span></div>')
    for r in records: o.append(_record(r, T, presentation))
    o.append(f'<p class="foot">{E(U["generated"])}: {M["total"]} {E(U["records_count"])} · opportunity_record.v1</p><script>{JS}</script></main></body></html>')
    return "".join(o)


def render_file(records_path, presentation_path, out_path, locale=None):
    with open(records_path, encoding="utf-8") as f: recs = json.load(f)
    with open(presentation_path, encoding="utf-8") as f: pres = json.load(f)
    page = render(recs, pres, locale)
    with open(out_path, "w", encoding="utf-8") as f: f.write(page)
    return out_path
