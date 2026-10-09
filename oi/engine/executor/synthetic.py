"""Dataset SINTÉTICO determinista para DRY_EXECUTION. Solo sirve para probar flujo, estados, ledger, presupuesto, errores,
deduplicación e idempotencia. NO sirve para calibrar recall, supervivencia, ruido, calidad ni scoring."""
LABEL = "SYNTHETIC / NON-CALIBRATING"


def build(n=120):
    lookup, enrich = [], {}
    for i in range(1, n + 1):
        oid = f"{i:024x}"; dom = None if i % 12 == 0 else f"synth{i:03d}.example"
        if i == 40: dom = "grupo-synth.example"; 
        if i == 41: dom = "grupo-synth.example"               # padre/filial: ids distintos, mismo dominio
        lookup.append({"id": oid, "name": f"SYNTH Org {i:03d}", "domain": dom, "website_url": f"http://www.{dom}" if dom else None, "logo_url": None})
        if dom and dom not in enrich:
            country, raw = ("Spain", f"calle {i}, madrid 28001, es")
            if i % 9 == 0: country, raw = "Portugal", f"rua {i}, lisboa, pt"
            if i % 13 == 0: raw = f"rua {i}, luanda 00000-000, ao"          # conflicto estructura/raw
            emp = None if i % 11 == 0 else (20 + (i * 37) % 900)
            enrich[dom] = {"id": oid, "name": f"SYNTH Org {i:03d}", "primary_domain": dom, "website_url": f"http://www.{dom}", "estimated_num_employees": emp,
                           "industry": "construction" if i % 2 else "information technology & services", "industries": ["construction"] if i % 2 else ["information technology & services", "construction"],
                           "city": "Lisboa" if country == "Portugal" else "Madrid", "state": None, "country": country, "raw_address": raw,
                           "organization_headcount_twelve_month_growth": ((i * 7) % 50) / 100.0}
    lookup.insert(5, dict(lookup[2]))                          # id repetido dentro de la página (caso sintético de duplicado)
    return {"label": LABEL, "lookup": lookup, "enrich": enrich}
