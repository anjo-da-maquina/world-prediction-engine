"""Public-source collectors and immutable, attributed domain records."""
import argparse
import csv
import hashlib
import html
import io
import json
import math
import os
import re
import urllib.parse
from datetime import date, datetime, timezone
from pathlib import Path

DOMAINS = {'crime','justice','corporate','policy','religion','education','society','migration',
           'drugs','conflict','military','diplomacy','budget','finance'}
REQUIRED = {'id','domain','kind','known_on','source_url','source_name','title','definition'}
OPTIONAL = {'occurred_on','claim_status','value','unit','denominator','population','stage','classification',
            'period_start','region','entities','relations','notes','source_text','fetch_id'}
STAGES = {'unknown','reported','suspect','arrest','prosecution','conviction','non_prosecution','acquittal',
          'population_stock','prisoner','victim_survey','seizure','death','appropriation','expenditure','filing','survey'}
RELATIONS = {'owns','invests_in','lends_to','receives_subsidy','awarded_contract','influences',
             'reports_managed_position','anticipates','benefits_from','burdened_by'}
MAX_RECORDS = 20000


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS domain_records(
        id TEXT PRIMARY KEY,domain TEXT NOT NULL,kind TEXT NOT NULL,known_on TEXT NOT NULL,
        region TEXT NOT NULL,payload TEXT NOT NULL,recorded_at TEXT NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS domain_cutoff ON domain_records(domain,known_on)')


def finite(value):
    return not isinstance(value,bool) and isinstance(value,(int,float)) and math.isfinite(value)


def validate(record, cutoff):
    from .autonomous import check_url
    if not isinstance(record,dict) or not REQUIRED<=record.keys() or record.keys()-(REQUIRED|OPTIONAL):
        raise ValueError('domain record fields missing or unknown')
    for name in REQUIRED:
        if not isinstance(record[name],str) or not record[name].strip() or len(record[name])>10000:
            raise ValueError('nonempty bounded text required: '+name)
    if record['domain'] not in DOMAINS or record['kind'] not in {'statistic','article','filing','relationship','survey'}:
        raise ValueError('invalid domain or record kind')
    check_url(record['source_url'])
    known=date.fromisoformat(record['known_on'])
    if known>date.fromisoformat(cutoff):raise ValueError('future knowledge is not accepted')
    occurred=record.get('occurred_on')
    if record.get('period_start') is not None and (not occurred or date.fromisoformat(record['period_start'])>date.fromisoformat(occurred)):raise ValueError('invalid observation period')
    if occurred is not None and date.fromisoformat(occurred)>known:raise ValueError('observation after knowledge date')
    if record.get('claim_status','report') not in {'report','official_record','hypothesis','reviewed'}:
        raise ValueError('invalid claim status')
    if record.get('stage','unknown') not in STAGES:raise ValueError('invalid stage')
    if record['kind'] in {'statistic','survey'}:
        if not finite(record.get('value')) or not occurred:raise ValueError('dated finite statistic required')
        for field in ('unit','population','classification','region'):
            if not isinstance(record.get(field),str) or not record[field].strip():raise ValueError('statistical definition missing: '+field)
        if record['domain'] in {'crime','justice','migration','drugs'} and record['value']<0:raise ValueError('negative count/rate rejected')
        den=record.get('denominator')
        if den is not None and (not finite(den) or den<=0):raise ValueError('denominator must be positive or null')
        if record['domain'] in {'crime','justice','migration','drugs'} and record.get('stage','unknown')=='unknown':
            raise ValueError('explicit measurement stage required')
    for field in ('region','unit','population','classification','notes','source_text'):
        if field in record and not isinstance(record[field],str):raise ValueError('invalid text field: '+field)
    if not isinstance(record.get('entities',[]),list) or not isinstance(record.get('relations',[]),list):
        raise ValueError('entities and relations must be lists')
    ids=set()
    for entity in record.get('entities',[]):
        if not isinstance(entity,dict) or set(entity)!={'id','name'} or not all(isinstance(v,str) and v.strip() for v in entity.values()):
            raise ValueError('entity needs explicit id/name')
        if entity['id'] in ids:raise ValueError('duplicate entity')
        ids.add(entity['id'])
    for edge in record.get('relations',[]):
        if not isinstance(edge,dict) or set(edge)!={'from','to','relation','status','quote'}:raise ValueError('relation fields invalid')
        if edge['from'] not in ids or edge['to'] not in ids or edge['relation'] not in RELATIONS:
            raise ValueError('relation entity/type invalid')
        if edge['status'] not in {'report','hypothesis','reviewed'} or not isinstance(edge['quote'],str) or not edge['quote'].strip():
            raise ValueError('relation needs attribution and quote')
        if not record.get('source_text') or edge['quote'] not in record['source_text']:
            raise ValueError('relation quote must exist in stored source text')
    if record['kind']=='relationship' and not record.get('relations'):raise ValueError('relationship record requires edges')
    return record


def store(db, records, cutoff):
    if not isinstance(records,list) or len(records)>MAX_RECORDS:raise ValueError('bounded record list required')
    schema(db)
    # Savepoints make a whole import atomic even inside the collector's transaction.
    db.execute('SAVEPOINT domain_import')
    count=0
    try:
        for record in records:
            validate(record,cutoff)
            payload=json.dumps(record,sort_keys=True,ensure_ascii=False,allow_nan=False)
            old=db.execute('SELECT payload FROM domain_records WHERE id=?',(record['id'],)).fetchone()
            if old:
                if old[0]!=payload:raise ValueError('immutable record conflict: use a new id for corrections')
                continue
            db.execute('INSERT INTO domain_records VALUES(?,?,?,?,?,?,?)',
                       (record['id'],record['domain'],record['kind'],record['known_on'],record.get('region','unknown'),payload,datetime.now(timezone.utc).isoformat()))
            count+=1
        db.execute('RELEASE domain_import')
    except Exception:
        db.execute('ROLLBACK TO domain_import');db.execute('RELEASE domain_import');raise
    return count


def identity(source,key):return hashlib.sha256((source+'\n'+key).encode()).hexdigest()


def collect_gdelt(db,s,when,rules,fetcher):
    from .autonomous import archive,topics_for,check_url
    from .actors import store_text
    query={'query':s['query'],'mode':'artlist','format':'json','maxrecords':250,'sort':'datedesc',
           'timespan':s.get('timespan','1h')}
    url='https://api.gdeltproject.org/api/v2/doc/doc?'+urllib.parse.urlencode(query)
    body=fetcher(url);data=json.loads(body)
    if not isinstance(data,dict) or not isinstance(data.get('articles'),list):raise ValueError('unexpected GDELT response')
    articles=data['articles']
    if len(articles)>250:raise ValueError('GDELT result exceeds limit')
    records=[];new=0;skipped=0
    with db:
        fetchid=archive(db,s['id'],url,body,when)
        for item in articles:
            link=item.get('url');title=item.get('title')
            if not isinstance(link,str) or not isinstance(title,str) or not title.strip():raise ValueError('article url/title missing')
            try:check_url(link)
            except ValueError:skipped+=1;continue
            ident=identity(s['id'],link)
            cur=db.execute('INSERT OR IGNORE INTO documents VALUES(?,?,?,?,?,?,?,?,?)',
                           (ident,s['id'],link,title[:2000],link,None,when,json.dumps(topics_for(title,rules)),fetchid))
            if cur.rowcount:
                new+=1;store_text(db,ident,title,when,fetchid)
                records.append({'id':ident,'domain':s['domain'],'kind':'article','known_on':when[:10],
                                'source_url':link,'source_name':item.get('domain') or 'GDELT indexed publisher',
                                'title':title[:2000],'definition':'報道見出し。出来事の発生・場所・犯人・因果を検証した記録ではない。',
                                'claim_status':'report','region':'unknown','fetch_id':fetchid,
                                'notes':'GDELT seendate is indexing time; sourcecountry is publisher location, not event location.'})
        store(db,records,when[:10])
    return {'new_documents':new,'seen_items':len(articles),'skipped_non_https':skipped,
            'coverage_truncated':len(articles)==250,'measure':'indexed_news_not_event_count'}


def collect_sec(db,s,when,rules,fetcher):
    from .autonomous import archive
    from .actors import store_text
    if s.get('concept'):return collect_sec_concept(db,s,when,rules,fetcher)
    url=f"https://data.sec.gov/submissions/CIK{s['cik'].zfill(10)}.json"
    body=fetcher(url);data=json.loads(body)
    if not isinstance(data,dict) or int(data.get('cik',-1))!=int(s['cik']):raise ValueError('SEC identity mismatch')
    recent=data.get('filings',{}).get('recent',{});accessions=recent.get('accessionNumber',[])
    fields=['form','filingDate','primaryDocument']
    lengths={k:len(recent[k]) if isinstance(recent.get(k),list) else None for k in fields}
    if not isinstance(accessions,list) or len(accessions)>50000 or any(lengths[k]!=len(accessions) for k in ('form','filingDate')):
        raise ValueError('SEC recent arrays incompatible: '+json.dumps({'accessionNumber':len(accessions) if isinstance(accessions,list) else None,**lengths}))
    if not isinstance(recent.get('primaryDocument',[]),list):raise ValueError('SEC primaryDocument must be an array')
    records=[];new=0;skipped=0
    with db:
        fetchid=archive(db,s['id'],url,body,when)
        for i,acc in enumerate(accessions):
            form=recent['form'][i];filed=recent['filingDate'][i]
            if date.fromisoformat(filed)>date.fromisoformat(when[:10]):skipped+=1;continue
            primary=recent.get('primaryDocument',[])[i] if i<len(recent.get('primaryDocument',[])) else ''
            if primary=='':primary=acc+'-index.html'
            if not re.fullmatch(r'\d{10}-\d{2}-\d{6}',acc) or not isinstance(primary,str) or not re.fullmatch(r'[A-Za-z0-9_. -]+(?:/[A-Za-z0-9_. -]+)*',primary) or any(part in ('.','..') for part in primary.split('/')):
                raise ValueError('unsafe SEC accession/document path')
            link=f"https://www.sec.gov/Archives/edgar/data/{int(s['cik'])}/{acc.replace('-','')}/{urllib.parse.quote(primary,safe='/')}"
            ident=identity(s['id'],acc);title=f"{data.get('name','')} / {form} / {filed}"
            cur=db.execute('INSERT OR IGNORE INTO documents VALUES(?,?,?,?,?,?,?,?,?)',
                           (ident,s['id'],acc,title,link,None,when,'[]',fetchid))
            if cur.rowcount:
                new+=1;store_text(db,ident,title,when,fetchid)
                records.append({'id':ident,'domain':'finance','kind':'filing','known_on':when[:10],
                                'occurred_on':filed,'source_url':link,'source_name':'SEC EDGAR','title':title,
                                'definition':'届出索引。13F・13D/Gなどを含むが、保有額や所有関係は本文解析前には認定しない。',
                                'stage':'filing','claim_status':'official_record','region':'USA','fetch_id':fetchid,
                                'entities':[{'id':'sec:'+str(int(s['cik'])),'name':data.get('name') or s['cik']}],
                                'notes':'recent filings only; older-file pagination not downloaded'})
        for offset in range(0,len(records),MAX_RECORDS):store(db,records[offset:offset+MAX_RECORDS],when[:10])
    return {'new_documents':new,'returned':len(accessions),'skipped_future':skipped,
            'older_files_not_loaded':len(data.get('filings',{}).get('files',[])),
            'entity_name':data.get('name'),'measure':'filing_index_not_fund_flow'}


def collect_mapped(db,s,when,rules,fetcher):
    """Configured CSV / JSON exports, with explicit per-source meaning and column mapping."""
    from .autonomous import archive
    body=fetcher(s['url'])
    if s['format']=='csv':raw=list(csv.DictReader(io.StringIO(body.decode('utf-8-sig'))))
    else:
        raw=json.loads(body)
        for key in s.get('records_path',[]):raw=raw[key]
    if not isinstance(raw,list) or len(raw)>MAX_RECORDS:raise ValueError('mapped export too large or invalid')
    records=[]
    for item in raw:
        record=dict(s.get('defaults',{}))
        for target,column in s['columns'].items():record[target]=item[column]
        for key in ('value','denominator'):
            if key in record and isinstance(record[key],str):record[key]=float(record[key]) if record[key].strip() else None
        # Hash includes capture day: corrections are new immutable vintages.
        record['id']=identity(s['id'],when[:10]+'\n'+json.dumps(record,sort_keys=True))
        record['known_on']=when[:10];records.append(record)
    with db:
        fetchid=archive(db,s['id'],s['url'],body,when)
        for record in records:
            old=db.execute('SELECT payload FROM domain_records WHERE id=?',(record['id'],)).fetchone()
            record['fetch_id']=json.loads(old[0]).get('fetch_id',fetchid) if old else fetchid
        count=store(db,records,when[:10])
    return {'inserted':count,'returned':len(records),'measure':'explicitly_mapped_records'}


def report(db,path,config,as_of):
    schema(db);sections=[];coverage=[]
    for domain in sorted(DOMAINS):
        count=db.execute('SELECT COUNT(*) FROM domain_records WHERE domain=? AND known_on<=?',(domain,as_of)).fetchone()[0]
        sources=[s['id'] for s in config['sources'] if s.get('domain')==domain]
        coverage.append(f'<tr><td>{html.escape(domain)}</td><td>{count}</td><td>{html.escape(", ".join(sources)) or "登録資料・マッピングが必要"}</td></tr>')
    latest=db.execute('''SELECT p.source,p.polled_at,p.ok,p.detail FROM polls p JOIN
             (SELECT source,MAX(id) id FROM polls GROUP BY source) q ON q.id=p.id''').fetchall()
    health=''.join(f'<tr><td>{html.escape(r["source"])}</td><td>{"成功" if r["ok"] else "失敗"}</td><td>{html.escape(r["polled_at"])}</td><td>{html.escape(r["detail"])}</td></tr>' for r in latest if r['source'] in {s['id'] for s in config['sources']})
    configured=''.join(f'<li>{html.escape(s["id"])}: '+('連絡先User-Agent未設定のため停止' if s['type']=='sec' and not os.environ.get('WPE_SEC_USER_AGENT') else html.escape(s['type']))+'</li>' for s in config['sources'] if s['type'] in {'sec','gdelt','mapped','eurostat','unhcr'})
    graph=[];entries=[]
    for row in db.execute('SELECT payload FROM domain_records WHERE known_on<=? ORDER BY recorded_at DESC,id LIMIT 2000',(as_of,)):
        r=json.loads(row[0]);nodes={e['id']:e['name'] for e in r.get('entities',[])}
        for edge in r.get('relations',[]):graph.append({**edge,'from_name':nodes[edge['from']],'to_name':nodes[edge['to']],'record_id':r['id'],'source_url':r['source_url'],'domain':r['domain']})
        if len(entries)<150:
            detail=f" / {r['value']} {r.get('unit','')} / 母集団: {r.get('population','')} / 段階: {r.get('stage','unknown')}" if 'value' in r else ''
            entries.append(f'<li><b>{html.escape(r["domain"])}</b> <a href="{html.escape(r["source_url"],quote=True)}" rel="noreferrer">{html.escape(r["title"])}</a> {html.escape(detail)}<br>{html.escape(r["definition"])} / 状態:{html.escape(r.get("claim_status","report"))}</li>')
    edges=''.join(f'<tr><td>{html.escape(e["from_name"])}</td><td>{html.escape(e["relation"])}</td><td>{html.escape(e["to_name"])}</td><td>{html.escape(e["status"])}</td><td>{html.escape(e["quote"])}</td></tr>' for e in graph)
    # World Bank coverage is numerical, separate from domain document counts.
    numbers=db.execute("SELECT variable,COUNT(DISTINCT region) regions,MAX(observed_on) latest,COUNT(*) vintages FROM observations WHERE source LIKE 'global_%' AND known_on<=? GROUP BY variable",(as_of,)).fetchall()
    numeric=''.join(f'<tr><td>{html.escape(r["variable"])}</td><td>{r["regions"]}</td><td>{r["latest"]}</td><td>{r["vintages"]}</td></tr>' for r in numbers)
    text='''<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>WPE 横断分析</title><style>body{font:15px/1.7 system-ui;background:#eef2f7;padding:24px;max-width:1200px;margin:auto}section{background:white;padding:20px;margin:20px 0;overflow:auto}table{border-collapse:collapse;width:100%}td,th{padding:8px;border-bottom:1px solid #ddd}li{margin:12px 0}</style><h1>WPE 横断収集・分析</h1>'''+f'<p>情報基準日 {html.escape(as_of)}</p>' + '''<p>報道件数は事件数ではありません。届出索引は資金移動や保有関係の証明ではありません。宗教・移民と犯罪の関係は、母集団・定義・記録制度を合わせた検証が必要です。欠損はゼロにしません。受益関係は原文と出典を伴う登録記録のみ表示します。</p><p><a href="relationships/index.html">年次指標の関連探索</a> ／ <a href="economic/index.html">経済予測</a> ／ <a href="actors.html">政策候補・利害仮説</a> ／ <a href="risk.html">危機シナリオ</a></p><section><h2>国際統計の取得範囲</h2><table><tr><th>指標</th><th>地域・集計区分</th><th>最新観測日</th><th>保存観測数（改訂を含む）</th></tr>'''+numeric+'''</table></section><section><h2>分野別の登録記録</h2><table><tr><th>分野</th><th>件数</th><th>設定</th></tr>'''+''.join(coverage)+'''</table><p>この件数は domain_records の記録です。国際統計の件数とは別です。</p></section><section><h2>収集器の状態</h2><ul>'''+configured+'''</ul><table><tr><th>収集先</th><th>結果</th><th>取得時刻</th><th>詳細</th></tr>'''+health+'''</table></section><section><h2>公開記録による関係</h2><table><tr><th>主体</th><th>関係</th><th>対象</th><th>主張状態</th><th>原文</th></tr>'''+edges+'''</table><p>最大2000件の最新記録内の関係を表示。全件は export コマンドで出力できます。</p></section><section><h2>最新の出典付き記録</h2><ul>'''+''.join(entries)+'''</ul></section></html>'''
    from .autonomous import write_json
    output=Path(path);output.parent.mkdir(parents=True,exist_ok=True)
    temp=output.with_suffix('.html.tmp');temp.write_text(text,encoding='utf-8');os.replace(temp,output)
    write_json(output.with_suffix('.json'),{'as_of':as_of,'displayed_edges':graph,'limited_to_latest_records':2000})
    return {'dashboard':str(output),'record_count':db.execute('SELECT COUNT(*) FROM domain_records WHERE known_on<=?',(as_of,)).fetchone()[0]}


def main():
    from .core import connect
    from .autonomous import cycle_lock
    p=argparse.ArgumentParser();p.add_argument('command',choices=['import','export']);p.add_argument('path');p.add_argument('--db',default='data/runtime.sqlite');p.add_argument('--as-of',default=date.today().isoformat());a=p.parse_args()
    date.fromisoformat(a.as_of);Path(a.db).parent.mkdir(parents=True,exist_ok=True)
    with cycle_lock(a.db):
        db=connect(a.db)
        try:
            schema(db)
            if a.command=='import':
                data=json.loads(Path(a.path).read_text(encoding='utf-8-sig'))
                if not isinstance(data,dict) or set(data)!={'records'}:raise ValueError('bundle must contain records only')
                with db:count=store(db,data['records'],a.as_of)
                print(json.dumps({'inserted':count}))
            else:
                records=[json.loads(r[0]) for r in db.execute('SELECT payload FROM domain_records WHERE known_on<=? ORDER BY id',(a.as_of,))]
                Path(a.path).write_text(json.dumps({'records':records},ensure_ascii=False,indent=2),encoding='utf-8')
                print(json.dumps({'exported':len(records)}))
        finally:db.close()


# Eurostat JSON-stat arrays are decoded from dimension metadata, not positional guesses.
EU_REGIONS = dict(zip('AT BE BG HR CY CZ DK EE FI FR DE EL HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE IS NO CH TR'.split(),
                      'AUT BEL BGR HRV CYP CZE DNK EST FIN FRA DEU GRC HUN IRL ITA LVA LTU LUX MLT NLD POL PRT ROU SVK SVN ESP SWE ISL NOR CHE TUR'.split()))

def decode_jsonstat(data):
    dimensions=data.get('id');sizes=data.get('size');values=data.get('value')
    if not isinstance(dimensions,list) or not dimensions or not isinstance(sizes,list) or len(dimensions)!=len(sizes):
        raise ValueError('invalid JSON-stat dimensions')
    if any(type(size) is not int or size<1 for size in sizes) or math.prod(sizes)>2000000:
        raise ValueError('JSON-stat dimensions exceed limits')
    if len(set(dimensions))!=len(dimensions):raise ValueError('duplicate dimension')
    categories={};labels={}
    for key,size in zip(dimensions,sizes):
        category=data['dimension'][key]['category'];index=category['index']
        if isinstance(index,list):index={code:i for i,code in enumerate(index)}
        if not isinstance(index,dict) or set(index.values())!=set(range(size)):raise ValueError('invalid category index')
        categories[key]={i:code for code,i in index.items()};labels[key]=category.get('label',{})
    if isinstance(values,list):values={str(i):v for i,v in enumerate(values) if v is not None}
    if not isinstance(values,dict) or len(values)>MAX_RECORDS:raise ValueError('too many JSON-stat values')
    statuses=data.get('status',{})
    output=[]
    for index,value in values.items():
        flat=int(index)
        if flat<0 or flat>=math.prod(sizes):raise ValueError('value index out of bounds')
        remainder=flat;point={};names={}
        for key,size in reversed(list(zip(dimensions,sizes))):
            code=categories[key][remainder%size];remainder//=size
            point[key]=code;names[key]=labels[key].get(code,code)
        flag=statuses.get(str(index),'') if isinstance(statuses,dict) else (statuses[flat] if isinstance(statuses,list) and flat<len(statuses) else '')
        output.append((point,names,value,flag))
    return output

def collect_eurostat(db,s,when,rules,fetcher):
    db.execute('CREATE TABLE IF NOT EXISTS eurostat_year_polls(source TEXT,year INTEGER,known_on TEXT,PRIMARY KEY(source,year))')
    total={'inserted':0,'returned':0,'skipped_uninterpretable':0,'years_fetched':0,
           'measure':'citizenship_not_immigration_status' if 'ctz' in s['dataset'] else 'police_recorded_offences'}
    for year in range(s.get('start',2000),int(when[:4])):
        cached=db.execute('SELECT known_on FROM eurostat_year_polls WHERE source=? AND year=?',(s['id'],year)).fetchone()
        if cached and (date.fromisoformat(when[:10])-date.fromisoformat(cached[0])).days<7:continue
        progress=getattr(fetcher,'progress',None)
        if progress:progress({'status':'fetching_year','year':year})
        result=_collect_eurostat_year(db,{**s,'_year':year},when,rules,fetcher)
        for key in ('inserted','returned','skipped_uninterpretable'):total[key]+=result[key]
        total['years_fetched']+=1
        with db:db.execute('INSERT OR REPLACE INTO eurostat_year_polls VALUES(?,?,?)',(s['id'],year,when[:10]))
    return total

def _collect_eurostat_year(db,s,when,rules,fetcher):
    from .autonomous import archive
    query={'lang':'EN','time':str(s['_year'])}
    url='https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/'+s['dataset']+'?'+urllib.parse.urlencode(query)
    body=fetcher(url);data=json.loads(body)
    if isinstance(data,dict) and data.get('size') and 0 in data['size']:
        return {'inserted':0,'returned':0,'skipped_uninterpretable':0,'measure':'no_observations_for_year'}
    decoded=decode_jsonstat(data);records=[];skipped=0
    with db:
        fetchid=archive(db,s['id'],url,body,when)
        for point,names,value,flag in decoded:
            year=point.get('time','');geo=point.get('geo','')
            if year!=str(s['_year']):skipped+=1;continue
            if not re.fullmatch(r'\d{4}',year) or point.get('freq','A')!='A' or not geo or value is None:
                skipped+=1;continue
            if year+'-12-31'>when[:10]:skipped+=1;continue
            stage=s['stage']
            if s['dataset']=='crim_just_ctz':
                label=names.get('leg_stat','').lower()
                stage=next((meaning for word,meaning in [('suspect','suspect'),('prosecut','prosecution'),('convict','conviction')] if word in label),'unknown')
                if stage=='unknown':skipped+=1;continue
            population='; '.join(f'{key}={point[key]} ({names[key]})' for key in sorted(point) if key not in {'time','geo','unit','freq','iccs'}) or 'dataset total'
            classification=point.get('iccs',s['dataset'])
            record={'domain':s['domain'],'kind':'statistic','known_on':when[:10],'occurred_on':year+'-12-31',
                    'source_url':url,'source_name':'Eurostat/'+s['dataset'],'title':data.get('label') or s['dataset'],
                    'definition':s['definition'],'claim_status':'official_record','region':EU_REGIONS.get(geo,'EUROSTAT:'+geo),
                    'stage':stage,'classification':classification,'unit':point.get('unit','dataset_unit'),
                    'population':population,'value':value,'denominator':None,
                    'notes':f'Eurostat status={flag}; raw geo={geo}; raw dimensions='+json.dumps(point,sort_keys=True)}
            if s['dataset']!='crim_pris_ctz':record['period_start']=year+'-01-01'
            record['id']=identity(s['id'],json.dumps(record,sort_keys=True))
            previous=db.execute('SELECT payload FROM domain_records WHERE id=?',(record['id'],)).fetchone()
            record['fetch_id']=json.loads(previous[0]).get('fetch_id',fetchid) if previous else fetchid
            records.append(record)
        count=store(db,records,when[:10])
    return {'inserted':count,'returned':len(decoded),'skipped_uninterpretable':skipped,
            'measure':'citizenship_not_immigration_status' if 'ctz' in s['dataset'] else 'police_recorded_offences'}



def collect_sec_concept(db,s,when,rules,fetcher):
    from .autonomous import archive
    concept=s['concept'];url=f"https://data.sec.gov/api/xbrl/companyconcept/CIK{s['cik'].zfill(10)}/us-gaap/{concept}.json"
    body=fetcher(url);data=json.loads(body)
    if int(data.get('cik',-1))!=int(s['cik']) or data.get('tag')!=concept:raise ValueError('SEC concept identity mismatch')
    records=[]
    with db:
        fetchid=archive(db,s['id'],url,body,when)
        for unit,facts in data.get('units',{}).items():
            for fact in facts:
                # Assets and liabilities are point-in-time amounts, not cash flows.
                end=fact.get('end');filed=fact.get('filed')
                if not end or not filed or filed>when[:10] or end>filed:continue
                r={'domain':'corporate','kind':'statistic','known_on':when[:10],'occurred_on':end,
                   'source_url':url,'source_name':'SEC EDGAR/'+s['cik'],'title':data.get('entityName',s['cik'])+' / '+concept,
                   'definition':'US-GAAP '+concept+'; point-in-time amount; not a transaction, investment flow or ownership link.',
                   'value':fact['val'],'unit':unit,'population':'SEC CIK '+s['cik'],'classification':concept,
                   'region':'SEC:'+s['cik'],'stage':'filing','claim_status':'official_record','denominator':None,
                   'notes':json.dumps({k:fact.get(k) for k in ('accn','form','filed','frame')},sort_keys=True)}
                r['id']=identity(s['id'],json.dumps(r,sort_keys=True))
                old=db.execute('SELECT payload FROM domain_records WHERE id=?',(r['id'],)).fetchone()
                r['fetch_id']=json.loads(old[0]).get('fetch_id',fetchid) if old else fetchid
                records.append(r)
        count=store(db,records,when[:10])
    return {'inserted':count,'returned':len(records),'measure':'balance_sheet_amount_not_flow'}



def collect_unhcr(db,s,when,rules,fetcher):
    """Legacy public Refugee Statistics API; totals remain explicitly coverage-qualified."""
    from .autonomous import archive
    db.execute('CREATE TABLE IF NOT EXISTS unhcr_year_polls(source TEXT,year INTEGER,known_on TEXT,coverage_verified INTEGER,PRIMARY KEY(source,year))')
    direction=s['direction'];dimension='coo' if direction=='origin' else 'coa'
    totals={'inserted':0,'returned':0,'skipped_missing':0,'pages':0,'coverage_verified':True,
            'measure':'refugee_population_stock_not_immigration_or_crime'}
    for year in range(s.get('start',1990),int(when[:4])):
        cached=db.execute('SELECT known_on,coverage_verified FROM unhcr_year_polls WHERE source=? AND year=?',(s['id'],year)).fetchone()
        if cached and (date.fromisoformat(when[:10])-date.fromisoformat(cached[0])).days<30:
            totals['coverage_verified']=totals['coverage_verified'] and bool(cached[1])
            continue
        page=1;seen=set();complete=False
        while page<=100:
            progress=getattr(fetcher,'progress',None)
            if progress:progress({'status':'fetching_year_page','year':year,'page':page})
            query={'year':year,dimension+'_all':'true','limit':1000,'page':page,'cf_type':'ISO'}
            url='https://api.unhcr.org/population/v1/population/?'+urllib.parse.urlencode(query)
            body=fetcher(url);data=json.loads(body)
            items=data.get('items') if isinstance(data,dict) else None
            if not isinstance(items,list):raise ValueError('UNHCR response needs items array; API schema may have changed')
            total=data.get('total')
            if isinstance(total,str) and total.isdigit():total=int(total)
            if isinstance(total,bool) or not isinstance(total,int):total=None
            records=[]
            with db:
                fetchid=archive(db,s['id'],url,body,when)
                for item in items:
                    if int(item.get('year',-1))!=year:raise ValueError('UNHCR returned a different year')
                    raw=item.get(dimension);value=item.get('refugees')
                    if raw is None or value is None:totals['skipped_missing']+=1;continue
                    key=str(raw)
                    if key in seen:raise ValueError('UNHCR pagination repeated a country; aggregate coverage cannot be verified')
                    seen.add(key)
                    iso=item.get(dimension+'_iso') or item.get(dimension+'_iso3')
                    region=iso if isinstance(iso,str) and re.fullmatch(r'[A-Z]{3}',iso) else 'UNHCR:'+key
                    if isinstance(value,str):value=float(value.replace(',',''))
                    r={'domain':'migration','kind':'statistic','known_on':when[:10],'occurred_on':str(year)+'-12-31',
                       'source_url':url,'source_name':'UNHCR/'+direction,'title':'Refugees by '+direction+' / '+str(item.get(dimension+'_name') or key),
                       'definition':'Refugees, year-end stock, by country of '+direction+'. Not asylum applications, migrants or crime counts.',
                       'value':value,'unit':'persons','population':'UNHCR refugee population by '+direction,
                       'classification':'refugees_'+direction,'region':region,'stage':'population_stock',
                       'claim_status':'official_record','denominator':None,'notes':'raw country code='+key+'; ISO code used only when explicitly returned'}
                    r['id']=identity(s['id'],json.dumps(r,sort_keys=True));old=db.execute('SELECT payload FROM domain_records WHERE id=?',(r['id'],)).fetchone()
                    r['fetch_id']=json.loads(old[0]).get('fetch_id',fetchid) if old else fetchid;records.append(r)
                totals['inserted']+=store(db,records,when[:10])
            totals['returned']+=len(items);totals['pages']+=1
            if total is not None and len(seen)>=total:complete=True;break
            if not items or len(items)<1000:
                # Without a total, server-side limit clamping may make this incomplete.
                complete=total is not None and len(seen)>=total
                if total is not None and not complete:raise ValueError('UNHCR returned fewer countries than declared total')
                break
            page+=1
        if page>100:raise ValueError('UNHCR exceeded bounded pagination')
        totals['coverage_verified']=totals['coverage_verified'] and complete
        with db:db.execute('INSERT OR REPLACE INTO unhcr_year_polls VALUES(?,?,?,?)',(s['id'],year,when[:10],int(complete)))
    return totals

if __name__=='__main__':main()
