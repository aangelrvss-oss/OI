import json
META={"prompt_version":"intent_interpreter@1.0.0","author":"llm:claude","fixture":True}
def crit(id,label,dim,field,op,val,strength,origin,src=None,rat=None,**kw):
    c={"id":id,"label":label,"dimension":dim,"field":field,"operator":op,"value":val,"strength":strength,"origin":origin,"source_text":src}
    if rat: c["rationale"]=rat
    c.update(kw); return c
def draft(raw,offering,obj,crits,geo,qty,qsrc,notes):
    return {"meta":META,"control":{"raw_request":raw,"language":"es"},
     "intent":{"opportunity_type":"customer_prospect","target_entity_type":"company","objective":obj,"offering":offering},
     "criteria":crits,
     "restrictions":{"geography":{"include":geo,"exclude":[]},"excluded_entities":[],"quantity":{"target":qty,"source_text":qsrc}},
     "traceability":{"proposed_signals":[],"llm_notes":notes}}
ES=[{"name":"ES","level":"country"}]
T={}
T["t1"]=draft("Encuentra 20 empresas españolas de 50-500 empleados que puedan necesitar automatización de procesos con IA.",
 {"description":"automatización de procesos con IA","problem_solved":None,"source_text":"automatización de procesos con IA"},
 "Encontrar empresas españolas de 50-500 empleados que puedan necesitar automatización de procesos con IA",
 [crit("G2","Plantilla 50-500","fit","firmographic.employee_count","between",[50,500],"gate","user","50-500 empleados"),
  crit("O1","Ofertas en roles operativos/administrativos","opportunity","hiring.ops_admin_roles","exists",None,"preferred","inferred",None,"Hipótesis, no hecho."),
  crit("O2","Menciones de transformación digital en la web","opportunity","web.digital_transformation_mentions","exists",None,"preferred","inferred",None,"Hipótesis, no hecho.")],
 ES,20,"20 empresas","Sin sector indicado: no se propone ninguno.")
T["t2"]=draft("Encuentra 15 hoteles de Madrid con señales recientes de expansión o transformación digital.",
 {"description":None,"problem_solved":None,"source_text":None},
 "Encontrar hoteles de Madrid con señales recientes de expansión o transformación digital",
 [crit("G1","Sector hoteles","fit","firmographic.industry","eq","hoteles","gate","user","hoteles"),
  crit("O1","Expansión (nuevas sedes) reciente","opportunity","growth.new_locations","gte",None,"preferred","user","expansión",None),
  crit("O2","Menciones de transformación digital en la web","opportunity","web.digital_transformation_mentions","exists",None,"preferred","user","transformación digital")],
 [{"name":"Madrid","level":"municipality"}],15,"15 hoteles","No se indica qué se ofrece a los hoteles; 'recientes' sin ventana.")
T["t3"]=draft("Encuentra 20 empresas constructoras españolas en crecimiento que puedan necesitar software de gestión.",
 {"description":"software de gestión","problem_solved":None,"source_text":"software de gestión"},
 "Encontrar constructoras españolas en crecimiento que puedan necesitar software de gestión",
 [crit("G1","Sector construcción","fit","firmographic.industry","eq","construcción","gate","user","constructoras"),
  crit("C1","En crecimiento","opportunity","growth.employee_growth_pct","gte",None,"preferred","user","en crecimiento"),
  crit("O1","Ofertas de jefe de obra/administración","opportunity","hiring.site_management_roles","exists",None,"preferred","inferred",None,"Hipótesis, no hecho.")],
 ES,20,"20 empresas","Tamaño no indicado.")
T["t4"]=draft("Encuentra 15 distribuidores alimentarios españoles que puedan ser compradores de un nuevo producto.",
 {"description":None,"problem_solved":None,"source_text":None},
 "Encontrar distribuidores alimentarios españoles que puedan comprar un nuevo producto",
 [crit("G1","Sector distribución alimentaria","fit","firmographic.industry","eq","distribución alimentaria","gate","user","distribuidores alimentarios")],
 ES,15,"15 distribuidores","El producto no está especificado; tampoco el tipo de distribuidor (mayorista, retail, horeca).")
