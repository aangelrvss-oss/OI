import sys,json
sys.path.insert(0,'/tmp/claude-0/ie/x/intent_engine'); sys.path.insert(0,'/tmp/claude-0/ie/gen')
from intent_engine.engine import IntentEngine
from intent_engine.versioning import SpecStore
from drafts import T
e=IntentEngine(store=SpecStore('/tmp/claude-0/ie/gen/specs.jsonl'),clock=lambda:"2026-10-08T00:00:00+00:00")
out={}
for k,d in T.items():
    r=e.process(d)
    s=r['spec']
    print('==',k,r['status'],'| errors',[x['code'] for x in r['errors']],'| warnings',[w['code'] for w in r['warnings']])
    print('  layers',{a:('skip' if b.get('skipped') else ('OK' if b['passed'] else 'FAIL')) for a,b in r['layers'].items()})
    for q in r['clarifications']: print('  Q',('BLOQ' if q['blocking'] else 'conf'),q['trigger_rule'],'|',q['question'][:150])
    if s:
        for c in s['criteria']: print('  C',c['id'],c['field'],c['operator'],c.get('value'),c['activation'],c['definition_status'])
        print('  geo',s['restrictions']['geography']['include'],'qty',s['restrictions']['quantity'])
        print('  assumptions',[(a['basis'],a['field'],a['value']) for a in s['traceability']['assumptions']])
    print('  unsupported',r['unsupported'])
