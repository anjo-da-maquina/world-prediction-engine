"""Evidence-grounded policy candidates, interests, and auditable action forecasts."""
import argparse
import hashlib
import html
import json
import math
import re
from datetime import datetime,timezone
from pathlib import Path
from .core import connect

POLICY_WORDS=('法案','法律','補助金','規制','施行','制度','bill','legislation','regulation','subsidy')

def now():return datetime.now(timezone.utc).isoformat()

def timestamp(value):
    d=datetime.fromisoformat(value.replace('Z','+00:00'))
    if d.tzinfo is None:raise ValueError('timezone required')
    return d.astimezone(timezone.utc).isoformat()

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:32]

def schema(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS document_texts(id TEXT PRIMARY KEY,document_id TEXT NOT NULL,
      captured_at TEXT NOT NULL,text TEXT NOT NULL,sha256 TEXT NOT NULL,fetch_id TEXT NOT NULL,
      UNIQUE(document_id,sha256));
    CREATE TABLE IF NOT EXISTS evidence_spans(id TEXT PRIMARY KEY,text_id TEXT NOT NULL REFERENCES document_texts(id),
      start INTEGER NOT NULL,end INTEGER NOT NULL,quote TEXT NOT NULL,available_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS policy_events(id TEXT PRIMARY KEY,evidence_id TEXT NOT NULL REFERENCES evidence_spans(id),
      event_type TEXT NOT NULL,title TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS event_reviews(id INTEGER PRIMARY KEY,event_id TEXT NOT NULL REFERENCES policy_events(id),
      status TEXT NOT NULL,rationale TEXT NOT NULL,evidence_ids TEXT NOT NULL,reviewed_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS entities(id TEXT PRIMARY KEY,name TEXT NOT NULL,kind TEXT NOT NULL,region TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS interest_links(id TEXT PRIMARY KEY,event_id TEXT NOT NULL REFERENCES policy_events(id),
      entity_id TEXT NOT NULL REFERENCES entities(id),axis TEXT NOT NULL,statement TEXT NOT NULL,
      status TEXT NOT NULL,evidence_ids TEXT NOT NULL,alternative TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS action_forecasts(id TEXT PRIMARY KEY,event_id TEXT NOT NULL REFERENCES policy_events(id),
      entity_id TEXT NOT NULL REFERENCES entities(id),action_kind TEXT NOT NULL,question TEXT NOT NULL,
      created_at TEXT NOT NULL,deadline TEXT NOT NULL,probability REAL NOT NULL,
      method TEXT NOT NULL,evidence_ids TEXT NOT NULL,resolution_rule TEXT NOT NULL,counterevidence TEXT NOT NULL,recorded_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS action_outcomes(forecast_id TEXT PRIMARY KEY REFERENCES action_forecasts(id),
      occurred INTEGER NOT NULL,resolved_at TEXT NOT NULL,evidence_ids TEXT NOT NULL,rationale TEXT NOT NULL,brier REAL NOT NULL);
    ''')

def immutable(db,table,payload):
    # Table names are internal constants, never supplied by external inputs.
    old=db.execute(f'SELECT * FROM {table} WHERE id=?',(payload['id'],)).fetchone()
    if old:
        if any(old[k]!=v for k,v in payload.items()):raise ValueError('immutable record conflict')
        return payload['id']
    keys=list(payload)
    db.execute(f"INSERT INTO {table} ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})",[payload[k] for k in keys])
    return payload['id']

def store_text(db,doc_id,text,captured_at,fetch_id):
    schema(db)
    captured_at=timestamp(captured_at)
    sha=hashlib.sha256(text.encode()).hexdigest()
    ident=digest([doc_id,sha])
    with db:
        db.execute('INSERT OR IGNORE INTO document_texts VALUES(?,?,?,?,?,?)',(ident,doc_id,captured_at,text,sha,fetch_id))
    return ident

def add_evidence(db,text_id,start,end,quote):
    row=db.execute('SELECT * FROM document_texts WHERE id=?',(text_id,)).fetchone()
    if not row:raise ValueError('unknown text version')
    if not isinstance(start,int) or not isinstance(end,int) or not 0<=start<end<=len(row['text']):raise ValueError('invalid character offsets')
    if row['text'][start:end]!=quote:raise ValueError('quote does not match the stored source')
    ident=digest([text_id,start,end,quote])
    with db:immutable(db,'evidence_spans',dict(id=ident,text_id=text_id,start=start,end=end,quote=quote,available_at=row['captured_at']))
    return ident

def extract_candidates(db,cutoff=None):
    schema(db);cutoff=timestamp(cutoff or now());added=0
    for row in db.execute('SELECT * FROM document_texts WHERE captured_at<=? ORDER BY captured_at',(cutoff,)).fetchall():
        # A matching sentence is a candidate, even if negated, hypothetical or quoted.
        # No rule silently promotes it to an enacted law or a verified event.
        for match in re.finditer(r'[^。\n.!?]+[。.!?]?',row['text']):
            text=match.group(0)
            if not any(w.casefold() in text.casefold() for w in POLICY_WORDS):continue
            if len(text)>2000:continue
            evid=add_evidence(db,row['id'],match.start(),match.end(),text)
            event_id=digest(['policy-mention',evid])
            if not db.execute('SELECT 1 FROM policy_events WHERE id=?',(event_id,)).fetchone():
                with db:db.execute('INSERT INTO policy_events VALUES(?,?,?,?,?,?)',
                   (event_id,evid,'policy_mention',text[:240],'candidate',cutoff))
                added+=1
    return {'added_candidates':added,'total':db.execute('SELECT COUNT(*) FROM policy_events').fetchone()[0]}

def evidence_valid(db,ids,cutoff=None):
    if not isinstance(ids,list) or not ids or not all(isinstance(x,str) for x in ids):raise ValueError('nonempty evidence_ids required')
    for ident in ids:
        r=db.execute('SELECT available_at FROM evidence_spans WHERE id=?',(ident,)).fetchone()
        if not r:raise ValueError('unknown evidence id')
        if cutoff and r[0]>cutoff:raise ValueError('evidence was not available at the information cutoff')

def references(db,event_id,entity_id=None):
    if not db.execute('SELECT 1 FROM policy_events WHERE id=?',(event_id,)).fetchone():raise ValueError('unknown event')
    if entity_id and not db.execute('SELECT 1 FROM entities WHERE id=?',(entity_id,)).fetchone():raise ValueError('unknown entity')

def review_event(db,event_id,status,rationale,evidence_ids,reviewed_at=None):
    schema(db);references(db,event_id)
    if status not in ('confirmed','rejected','uncertain') or not rationale.strip():raise ValueError('review requires status and rationale')
    reviewed_at=timestamp(reviewed_at or now());evidence_valid(db,evidence_ids,reviewed_at)
    with db:db.execute('INSERT INTO event_reviews(event_id,status,rationale,evidence_ids,reviewed_at) VALUES(?,?,?,?,?)',
      (event_id,status,rationale,json.dumps(evidence_ids),reviewed_at))

def register_entity(db,p):
    schema(db)
    if set(p)!=set(('id','name','kind','region')):raise ValueError('entity requires id/name/kind/region')
    if p['kind'] not in ('person','company','organization','government','sector'):raise ValueError('invalid entity kind')
    if not all(isinstance(v,str) and v.strip() for v in p.values()):raise ValueError('empty entity field')
    with db:return immutable(db,'entities',p)

def link_interest(db,p):
    schema(db)
    required={'id','event_id','entity_id','axis','statement','status','evidence_ids','alternative','created_at'}
    if set(p)!=required:raise ValueError('interest link fields do not match schema')
    references(db,p['event_id'],p['entity_id'])
    if p['axis'] not in ('benefit','burden','influence','anticipation','action','impact'):raise ValueError('unknown interest axis')
    if p['status'] not in ('hypothesis','supported','contradicted','unknown'):raise ValueError('invalid evidence status')
    if not p['statement'].strip() or not p['alternative'].strip():raise ValueError('statement and alternative explanation required')
    p=dict(p);p['created_at']=timestamp(p['created_at']);evidence_valid(db,p['evidence_ids'],p['created_at'])
    p['evidence_ids']=json.dumps(p['evidence_ids'])
    with db:return immutable(db,'interest_links',p)

def register_forecast(db,p):
    schema(db)
    fields={'id','event_id','entity_id','action_kind','question','created_at','deadline','probability','method','evidence_ids','resolution_rule','counterevidence'}
    if set(p)!=fields:raise ValueError('forecast fields do not match schema')
    references(db,p['event_id'],p['entity_id'])
    p=dict(p);p['created_at']=timestamp(p['created_at']);p['deadline']=timestamp(p['deadline'])
    if p['deadline']<=p['created_at']:raise ValueError('deadline must follow forecast cutoff')
    if not isinstance(p['probability'],(int,float)) or isinstance(p['probability'],bool) or not math.isfinite(p['probability']) or not 0<=p['probability']<=1:raise ValueError('probability must be finite in [0,1]')
    if p['method'] not in ('manual_estimate','empirical_base_rate'):raise ValueError('unimplemented forecast method')
    if not all(isinstance(p[x],str) and p[x].strip() for x in ('id','action_kind','question','resolution_rule','counterevidence')):raise ValueError('forecast needs measurable question, rule and counterevidence')
    evidence_valid(db,p['evidence_ids'],p['created_at']);p['evidence_ids']=json.dumps(p['evidence_ids'])
    if p['method']=='empirical_base_rate':
        estimate=base_rate(db,p['action_kind'],p['created_at'])
        if estimate['status']!='available':raise ValueError('not enough resolved comparable action labels for base rate')
        p['probability']=estimate['probability']
    old=db.execute('SELECT recorded_at FROM action_forecasts WHERE id=?',(p['id'],)).fetchone()
    p['recorded_at']=old[0] if old else now()
    with db:return immutable(db,'action_forecasts',p)

def base_rate(db,action_kind,cutoff,min_n=10):
    cutoff=timestamp(cutoff)
    rows=db.execute('''SELECT o.occurred FROM action_outcomes o JOIN action_forecasts f ON f.id=o.forecast_id
       WHERE f.action_kind=? AND f.deadline<? AND o.resolved_at<=?''',(action_kind,cutoff,cutoff)).fetchall()
    if len(rows)<min_n:return {'status':'pending_data','n':len(rows),'required':min_n}
    return {'status':'available','n':len(rows),'probability':(1+sum(r[0] for r in rows))/(2+len(rows)),
            'warning':'Same action label does not guarantee comparable contexts; uncalibrated base rate.'}

def resolve_action(db,ident,occurred,evidence_ids,rationale,resolved_at=None):
    schema(db);resolved_at=timestamp(resolved_at or now())
    f=db.execute('SELECT * FROM action_forecasts WHERE id=?',(ident,)).fetchone()
    if not f:raise ValueError('unknown action forecast')
    if occurred not in (0,1) or isinstance(occurred,bool):raise ValueError('occurred must be integer 0 or 1')
    if resolved_at<f['deadline']:raise ValueError('resolve only after deadline')
    evidence_valid(db,evidence_ids,resolved_at)
    if not rationale.strip():raise ValueError('resolution rationale required; silence is not evidence of absence')
    old=db.execute('SELECT * FROM action_outcomes WHERE forecast_id=?',(ident,)).fetchone()
    if old:return dict(old)
    score=(f['probability']-occurred)**2
    with db:db.execute('INSERT INTO action_outcomes VALUES(?,?,?,?,?,?)',
      (ident,occurred,resolved_at,json.dumps(evidence_ids),rationale,score))
    return {'forecast_id':ident,'occurred':occurred,'brier':score}

def import_bundle(db,p):
    schema(db)
    if not isinstance(p,dict) or set(p)-{'entities','links','forecasts'}:raise ValueError('invalid bundle keys')
    for value in p.values():
        if not isinstance(value,list):raise ValueError('bundle sections must be arrays')
    class TransactionView:
        def __getattr__(self,key):return getattr(db,key)
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def executescript(self,*args):return None  # schema already initialized
    view=TransactionView()
    with db:
        for x in p.get('entities',[]):register_entity(view,x)
        for x in p.get('links',[]):link_interest(view,x)
        for x in p.get('forecasts',[]):register_forecast(view,x)
    return {'entities':len(p.get('entities',[])),'links':len(p.get('links',[])),'forecasts':len(p.get('forecasts',[]))}

def suggest_actions(db,cutoff=None):
    schema(db);cutoff=timestamp(cutoff or now());result=[]
    links=db.execute('SELECT * FROM interest_links WHERE created_at<=? AND status IN (?,?)',
                     (cutoff,'hypothesis','supported')).fetchall()
    for link in links:
        if link['axis'] not in ('benefit','burden'):continue
        event=db.execute('SELECT title FROM policy_events WHERE id=?',(link['event_id'],)).fetchone()
        # Explicit templates produce hypotheses; they do not discover intent.
        actions=('investment_announcement','market_entry','capacity_expansion') if link['axis']=='benefit' else ('cost_reduction','withdrawal','price_change')
        for action in actions:
            result.append({'event_id':link['event_id'],'entity_id':link['entity_id'],
             'action_kind':action,'status':'conditional_hypothesis',
             'condition':'The claimed interest is real, the policy takes effect, and capacity/resources allow the action.',
             'counterevidence':link['alternative'],'evidence_ids':json.loads(link['evidence_ids']),
             'probability_estimate':base_rate(db,action,cutoff),
             'note':'Template-generated candidate, not a verified intention or an automatically registered forecast.'})
    return result

def export(db):
    schema(db)
    tables=('policy_events','event_reviews','entities','interest_links','action_forecasts','action_outcomes','evidence_spans')
    data={table:[dict(r) for r in db.execute(f'SELECT * FROM {table}').fetchall()] for table in tables}
    for e in data['policy_events']:
        review=db.execute('SELECT status FROM event_reviews WHERE event_id=? ORDER BY id DESC LIMIT 1',(e['id'],)).fetchone()
        e['current_status']=review[0] if review else 'candidate'
    data['action_candidates']=suggest_actions(db)
    data['notes']=['Benefit, influence and anticipation are separate claims.','Candidate extraction does not verify enactment.','Forecast probabilities are manual or historical base-rate estimates.']
    return data

def render_report(db,path):
    data=export(db);esc=lambda x:html.escape(str(x))
    sections=[]
    labels={'policy_events':'出来事候補と確認状態','entities':'関係者','interest_links':'利害・関与・事前準備','action_forecasts':'期限付き行動予測','action_outcomes':'結果と採点','action_candidates':'条件付きの行動候補','evidence_spans':'原文の証拠'}
    for name in ('policy_events','entities','interest_links','action_forecasts','action_outcomes','action_candidates','evidence_spans'):
        rows=data[name]
        if not rows:sections.append(f'<h2>{esc(labels.get(name,name))}</h2><p>記録なし</p>');continue
        keys=list(rows[0]);body='<table><thead><tr>'+''.join(f'<th>{esc(k)}</th>' for k in keys)+'</tr></thead><tbody>'
        body+=''.join('<tr>'+''.join(f'<td>{esc(r[k])}</td>' for k in keys)+'</tr>' for r in rows)+'</tbody></table>'
        sections.append(f'<h2>{esc(labels.get(name,name))}</h2>'+body)
    Path(path).write_text('''<!doctype html><html lang="ja"><meta charset="utf-8"><title>WPE actors</title><style>body{font:15px system-ui;margin:32px;color:#172332}table{border-collapse:collapse;display:block;overflow:auto;margin-bottom:30px}td,th{border:1px solid #cbd5df;padding:8px;max-width:420px;overflow-wrap:anywhere}th{background:#edf3f8}h1{color:#183e68}</style><h1>WPE v0.3 — 出来事・利害・行動予測</h1><p>原文の言及候補と検証された出来事は区別。受益・関与・事前準備も別の分析軸です。確率は推定方法と併記します。</p>'''+''.join(sections)+'</html>',encoding='utf-8')

def main():
    p=argparse.ArgumentParser(description='Evidence and actor/action ledger')
    p.add_argument('--db',default='data/runtime.sqlite');sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('extract')
    im=sub.add_parser('import');im.add_argument('file')
    rep=sub.add_parser('report');rep.add_argument('--output',default='outputs/actors.html')
    rev=sub.add_parser('review');rev.add_argument('event_id');rev.add_argument('--status',choices=('confirmed','uncertain','rejected'),required=True);rev.add_argument('--evidence',nargs='+',required=True);rev.add_argument('--rationale',required=True)
    res=sub.add_parser('resolve');res.add_argument('forecast_id');res.add_argument('--occurred',type=int,choices=(0,1),required=True);res.add_argument('--evidence',nargs='+',required=True);res.add_argument('--rationale',required=True)
    a=p.parse_args();Path(a.db).parent.mkdir(parents=True,exist_ok=True);db=connect(a.db);schema(db)
    try:
        if a.command=='extract':result=extract_candidates(db)
        elif a.command=='import':result=import_bundle(db,json.loads(Path(a.file).read_text(encoding='utf-8')))
        elif a.command=='report':Path(a.output).parent.mkdir(parents=True,exist_ok=True);render_report(db,a.output);result={'report':str(Path(a.output).resolve())}
        elif a.command=='review':review_event(db,a.event_id,a.status,a.rationale,a.evidence);result={'reviewed':a.event_id}
        else:result=resolve_action(db,a.forecast_id,a.occurred,a.evidence,a.rationale)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (ValueError,OSError) as exc:p.exit(1,f'Error: {exc}\n')
    finally:db.close()
if __name__=='__main__':main()
