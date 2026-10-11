"""Bounded, restartable collection loop; no self-modifying or remote-execution code."""
import argparse
import hashlib
import json
import os
import re
import signal
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from datetime import datetime,timezone,timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from .core import connect,insert,series,features,forecast,save_prediction,resolve
from .signals import cumulative_excess,empirical_paths

MAX_BYTES=5*1024*1024

def utcnow(): return datetime.now(timezone.utc)

def init(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS fetches(id TEXT PRIMARY KEY,source TEXT NOT NULL,
      fetched_at TEXT NOT NULL,url TEXT NOT NULL,sha256 TEXT NOT NULL,body BLOB NOT NULL);
    CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY,source TEXT NOT NULL,
      source_key TEXT NOT NULL,title TEXT NOT NULL,url TEXT NOT NULL,published_at TEXT,
      first_seen_at TEXT NOT NULL,topics TEXT NOT NULL,fetch_id TEXT NOT NULL REFERENCES fetches(id));
    CREATE TABLE IF NOT EXISTS polls(id INTEGER PRIMARY KEY,source TEXT NOT NULL,
      polled_at TEXT NOT NULL,ok INTEGER NOT NULL,detail TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY,started_at TEXT NOT NULL,
      finished_at TEXT NOT NULL,status TEXT NOT NULL,summary TEXT NOT NULL);
    ''')

def load_config(path):
    c=json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(c.get('sources'),list) or not 1<=len(c['sources'])<=50:
        raise ValueError('configure 1..50 sources')
    ids=[]
    for s in c['sources']:
        sid=s.get('id','')
        if not re.fullmatch(r'[a-zA-Z0-9_.-]+',sid): raise ValueError('invalid source id')
        ids.append(sid)
        if s.get('type') not in ('rss','worldbank','kokkai'): raise ValueError('unknown source type')
        if not isinstance(s.get('interval_seconds'),int) or s['interval_seconds']<60:
            raise ValueError('source interval must be >=60 seconds')
        if s['type']=='rss': check_url(s['url'])
        elif s['type']=='kokkai':
            if not isinstance(s.get('query'),str) or not s['query'].strip():raise ValueError('kokkai query required')
        else:
            for key in ('country','indicator'):
                if not re.fullmatch(r'[A-Za-z0-9_.-]+',s[key]): raise ValueError('invalid World Bank code')
    if len(set(ids))!=len(ids): raise ValueError('duplicate source id')
    return c

def check_url(url):
    u=urllib.parse.urlsplit(url)
    if u.scheme!='https' or not u.hostname or u.username or u.password:
        raise ValueError('source URLs must use HTTPS without embedded credentials')

class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        check_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def fetch_bytes(url):
    check_url(url)
    opener=urllib.request.build_opener(HTTPSRedirect())
    req=urllib.request.Request(url,headers={'User-Agent':'WorldPredictionEngine/0.4 research collector'})
    last=None
    for _ in range(2):
        try:
            with opener.open(req,timeout=25) as response:
                check_url(response.geturl())
                body=response.read(MAX_BYTES+1)
            if len(body)>MAX_BYTES: raise ValueError('response exceeds 5 MiB')
            return body
        except OSError as exc: last=exc
    raise OSError('source fetch failed after 2 attempts') from last

def archive(db,sid,url,body,when):
    digest=hashlib.sha256(body).hexdigest()
    ident=hashlib.sha256((sid+when+url+digest).encode()).hexdigest()
    db.execute('INSERT OR IGNORE INTO fetches VALUES(?,?,?,?,?,?)',(ident,sid,when,url,digest,body))
    return ident

def parse_feed(body):
    if b'<!doctype' in body.lower() or b'<!entity' in body.lower():
        raise ValueError('DTD/entity declarations not supported')
    root=ET.fromstring(body)
    local=lambda tag:tag.split('}')[-1]
    if local(root.tag) not in ('rss','feed','RDF'): raise ValueError('not an RSS/Atom feed')
    result=[]
    for entry in root.iter():
        if local(entry.tag) not in ('item','entry'): continue
        fields={}; url=''
        for child in entry:
            tag=local(child.tag)
            fields[tag]=''.join(child.itertext()).strip()
            if tag=='link' and (child.get('rel','alternate')=='alternate'):
                url=child.get('href') or fields[tag]
        title=fields.get('title','')[:2000]
        key=fields.get('guid') or fields.get('id') or url or title
        if not key: continue
        published=None
        raw=fields.get('pubDate') or fields.get('published') or fields.get('updated')
        if raw:
            try:
                try: dt=parsedate_to_datetime(raw)
                except (ValueError,TypeError): dt=datetime.fromisoformat(raw.replace('Z','+00:00'))
                if dt.tzinfo: published=dt.astimezone(timezone.utc).isoformat()
            except (ValueError,TypeError,OverflowError): pass
        text=title+' '+fields.get('description','')+' '+fields.get('summary','')
        result.append({'key':key,'title':title,'url':url,'published_at':published,'text':text[:20000]})
    return result

def topics_for(text,rules):
    """Transparent mention tags; never assert that an event actually occurred."""
    text=text.casefold()
    found=[]
    for topic,words in rules.items():
        for word in words:
            token=word.casefold()
            if token.isascii():
                match=re.search(r'(?<!\w)'+re.escape(token)+r'(?!\w)',text)
            else: match=token in text
            if match:
                found.append(topic); break
    return sorted(found)

def collect(db,s,when,rules,fetcher=fetch_bytes):
    sid=s['id']
    if s['type']=='kokkai':
        from .policy_source import collect_kokkai
        return collect_kokkai(db,s,when,rules,fetcher)
    if s['type']=='rss':
        body=fetcher(s['url']); items=parse_feed(body)
        with db:
            fetchid=archive(db,sid,s['url'],body,when)
            count=0
            for item in items:
                ident=hashlib.sha256((sid+'\n'+item['key']).encode()).hexdigest()
                cur=db.execute('INSERT OR IGNORE INTO documents VALUES(?,?,?,?,?,?,?,?,?)',
                   (ident,sid,item['key'],item['title'],item['url'],item['published_at'],when,
                    json.dumps(topics_for(item['text'],rules)),fetchid))
                from .actors import store_text
                store_text(db,ident,item['text'],when,fetchid)
                count+=cur.rowcount
        return {'new_documents':count,'seen_items':len(items)}
    year=datetime.fromisoformat(when).year
    today=when[:10]; rows=[]; pages=1; page=1
    with db:
        while page<=pages:
            q=urllib.parse.urlencode({'format':'json','date':f"{s.get('start',1960)}:{year-1}",'per_page':1000,'page':page})
            url=f"https://api.worldbank.org/v2/country/{s['country']}/indicator/{s['indicator']}?{q}"
            body=fetcher(url); data=json.loads(body)
            if not isinstance(data,list) or len(data)!=2 or 'pages' not in data[0]:
                raise ValueError('unexpected World Bank response')
            pages=int(data[0]['pages'])
            if not 0<=pages<=100: raise ValueError('unexpected page count')
            archive(db,sid,url,body,when)
            for r in data[1] or []:
                if r['value'] is not None:
                    rows.append({'variable':s['indicator'],'region':s['country'],
                      'observed_on':f"{int(r['date']):04d}-12-31",'known_on':today,
                      'value':r['value'],'source':sid,'mode':'live'})
            page+=1
        # Validate all rows before writing; a conflicting same-day vintage rolls back.
        return {'inserted':insert(db,rows),'returned':len(rows)}

def freeze_received_counts(db,sid,as_of,rules):
    """Count captured documents for closed UTC days with >=1 successful poll.
    A day with no successful poll is missing, not zero. Today stays unfinished.
    """
    days=[r[0] for r in db.execute('SELECT DISTINCT substr(polled_at,1,10) FROM polls WHERE source=? AND ok=1 AND substr(polled_at,1,10)<?',(sid,as_of))]
    rows=[]
    for day in days:
        docs=db.execute('SELECT topics FROM documents WHERE source=? AND substr(first_seen_at,1,10)=?',(sid,day)).fetchall()
        counts={'received_items':len(docs)}
        for topic in rules: counts['mentions.'+topic]=sum(topic in json.loads(r[0]) for r in docs)
        for variable,value in counts.items():
            old=db.execute('SELECT 1 FROM observations WHERE variable=? AND region=? AND observed_on=? AND source=?',
                           (variable,sid,day,sid)).fetchone()
            if not old:
                rows.append({'variable':variable,'region':sid,'observed_on':day,'known_on':as_of,
                             'value':value,'source':sid,'mode':'live'})
    return insert(db,rows)

@contextmanager
def cycle_lock(db):
    # SQLite lock is released by the OS on crash. Separate DB avoids blocking writes.
    lock=connect(str(db)+'.lock.sqlite')
    try:
        lock.execute('BEGIN EXCLUSIVE')
        yield
    finally:
        lock.rollback();lock.close()

def write_json(path,payload):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.tmp')
    tmp.write_text(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    os.replace(tmp,path)

def run_cycle(config,db_path,output_dir,now=None,fetcher=fetch_bytes):
    now=now or utcnow()
    if now.tzinfo is None: raise ValueError('timezone-aware clock required')
    now=now.astimezone(timezone.utc);when=now.isoformat();today=now.date().isoformat()
    Path(db_path).parent.mkdir(parents=True,exist_ok=True)
    with cycle_lock(db_path):
        db=connect(db_path);init(db)
        from .actors import schema,extract_candidates,render_report,suggest_actions
        schema(db)
        try:
            result={'version':'0.4.0','started_at':when,'mode':'live','sources':[],'signals':[],
                    'forecasts':[],'resolutions':[],'warning':'Coverage is limited to configured sources. Mentions are not verified events.'}
            rules=config.get('topic_keywords',{})
            for s in config['sources']:
                last=db.execute('SELECT polled_at FROM polls WHERE source=? ORDER BY id DESC LIMIT 1',(s['id'],)).fetchone()
                if last and (now-datetime.fromisoformat(last[0])).total_seconds()<s['interval_seconds']:
                    result['sources'].append({'id':s['id'],'status':'not_due'}); continue
                try:
                    detail=collect(db,s,when,rules,fetcher)
                    with db: db.execute('INSERT INTO polls(source,polled_at,ok,detail) VALUES(?,?,1,?)',(s['id'],when,json.dumps(detail)))
                    result['sources'].append({'id':s['id'],'status':'ok',**detail})
                except Exception as exc:
                    # Isolate one broken collector and record failure, never fabricate observations.
                    with db: db.execute('INSERT INTO polls(source,polled_at,ok,detail) VALUES(?,?,0,?)',(s['id'],when,type(exc).__name__))
                    result['sources'].append({'id':s['id'],'status':'failed','error_type':type(exc).__name__})
            for s in config['sources']:
                if s['type']=='rss': freeze_received_counts(db,s['id'],today,rules)
            for spec in config.get('signals',[]):
                view={'variable':spec['variable'],'region':spec['region']}
                rows=series(db,spec['variable'],spec['region'],today,spec.get('source'))
                view['samples']=len(rows)
                stale=(now.date()-datetime.fromisoformat(rows[-1]['observed_on']).date()).days if rows else None
                view['stale_days']=stale
                for key,fn in [('derivatives',lambda:features(rows,today)),
                 ('integral',lambda:cumulative_excess(rows[-spec.get('window',30):],spec.get('baseline',0),spec.get('max_gap_days',2))),
                 ('projection',lambda:empirical_paths(rows,spec.get('horizon_days',7)) if stale is not None and stale<=1 else (_ for _ in ()).throw(ValueError('daily projection needs fresh observations')))]:
                    try: view[key]=fn()
                    except ValueError as exc: view[key]={'status':'insufficient_or_incompatible_data','reason':str(exc)}
                result['signals'].append(view)
            for q in config.get('questions',[]):
                try:
                    health=db.execute('SELECT ok FROM polls WHERE source=? ORDER BY id DESC LIMIT 1',(q.get('source',''),)).fetchone()
                    if health and not health[0]: raise ValueError('source is failing; forecast suspended')
                    p=forecast(db,q['variable'],q['region'],today,q['horizon_days'],q['threshold'],q.get('source'))
                    if p['mode']!='live': raise ValueError('autonomous run refuses demo forecasts')
                    p['question_id']=q['id']
                    p['id']=save_prediction(db,p)
                    result['forecasts'].append(p)
                except ValueError as exc: result['forecasts'].append({'question_id':q['id'],'status':'pending_data','reason':str(exc)})
            pending=db.execute('''SELECT p.id FROM predictions p LEFT JOIN outcomes o ON p.id=o.prediction_id
               WHERE o.prediction_id IS NULL AND p.deadline<=?''',(today,)).fetchall()
            for row in pending:
                try:
                    pred=db.execute('SELECT * FROM predictions WHERE id=?',(row['id'],)).fetchone()
                    snap=json.loads(pred['snapshot'])
                    if snap['mode']!='live': raise ValueError('autonomous run refuses demo resolutions')
                    rss_ids={s['id'] for s in config['sources'] if s['type']=='rss'}
                    if snap['source'] in rss_ids:
                        rr=series(db,pred['variable'],pred['region'],today,snap['source'])
                        window=[r for r in rr if pred['created_on']<r['observed_on']<=pred['deadline']]
                        if not any(r['value']>pred['threshold'] for r in window) and len(window)!=pred['horizon']:
                            raise ValueError('negative RSS resolution requires complete daily coverage')
                    result['resolutions'].append(resolve(db,row['id'],today))
                except ValueError as exc: result['resolutions'].append({'id':row['id'],'status':'pending_data','reason':str(exc)})
            result['source_health']=[dict(r) for r in db.execute('''SELECT p.source,p.polled_at,p.ok,p.detail FROM polls p
                JOIN (SELECT source,MAX(id) id FROM polls GROUP BY source) last ON last.id=p.id''')]
            result['finished_at']=utcnow().isoformat()
            failures=sum(not r['ok'] for r in result['source_health'])
            result['status']='degraded' if failures else 'ok'
            result['policy_candidates']=extract_candidates(db,when)
            result['action_candidates']=suggest_actions(db,when)
            from .risk import evaluate as evaluate_risk, report as risk_report
            try:
                result['risk_scenarios']=evaluate_risk(db,when)
            except ValueError as exc:
                result['risk_scenarios']={'status':'evaluation_failed','reason':str(exc)}
                result['status']='degraded'
            with db: db.execute('INSERT INTO runs(started_at,finished_at,status,summary) VALUES(?,?,?,?)',
              (when,result['finished_at'],result['status'],json.dumps(result,ensure_ascii=False)))
            output=Path(output_dir)
            output.mkdir(parents=True,exist_ok=True)
            render_report(db,output/'actors.html')
            risk_report(db,output/'risk.html',when)
            write_json(output/'latest.json',result)
            write_json(output/'runs'/f"{now.strftime('%Y%m%dT%H%M%S%fZ')}.json",result)
            return result
        finally: db.close()

def main():
    p=argparse.ArgumentParser(description='WPE autonomous collection and evaluation loop')
    p.add_argument('command',choices=('once','loop'))
    p.add_argument('--config',default='config/autonomous.json')
    p.add_argument('--db',default='data/runtime.sqlite')
    p.add_argument('--output',default='outputs')
    p.add_argument('--tick-seconds',type=int,default=60)
    a=p.parse_args()
    if a.tick_seconds<60: p.error('tick must be >=60 seconds')
    stop=False
    def stopping(*_):
        nonlocal stop
        stop=True
    signal.signal(signal.SIGINT,stopping)
    signal.signal(signal.SIGTERM,stopping)
    while not stop:
        try:
            result=run_cycle(load_config(a.config),a.db,a.output)
            print(json.dumps({'status':result['status'],'started_at':result['started_at'],
                             'sources':result['sources'],'forecasts':len(result['forecasts'])},ensure_ascii=False),flush=True)
        except Exception as exc:
            print(json.dumps({'status':'cycle_failed','error_type':type(exc).__name__}),flush=True)
            if a.command=='once': p.exit(1)
        if a.command=='once': break
        until=time.monotonic()+a.tick_seconds
        while not stop and time.monotonic()<until: time.sleep(1)

if __name__=='__main__': main()
