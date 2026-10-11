"""Bounded Diet speech API collector. Speech date is not publication time."""
import hashlib
import json
import urllib.parse
from datetime import datetime,timedelta
from .actors import schema,store_text

def collect_kokkai(db,s,when,rules,fetcher):
    from .autonomous import archive,topics_for
    schema(db)
    day=datetime.fromisoformat(when).date()
    query={'any':s['query'],'from':(day-timedelta(days=s.get('lookback_days',30))).isoformat(),
           'until':day.isoformat(),'maximumRecords':100,'recordPacking':'json','startRecord':1}
    # One request/cycle avoids bulk crawling. Larger result sets are reported as truncated.
    url='https://kokkai.ndl.go.jp/api/speech?'+urllib.parse.urlencode(query)
    body=fetcher(url);data=json.loads(body)
    if not isinstance(data,dict) or 'numberOfRecords' not in data:raise ValueError('unexpected Diet response')
    records=data.get('speechRecord') or []
    if not isinstance(records,list):raise ValueError('invalid speechRecord')
    texts=[];new=0
    with db:
        fetchid=archive(db,s['id'],url,body,when)
        for record in records:
            key=record.get('speechID');text=record.get('speech')
            if not isinstance(key,str) or not isinstance(text,str):raise ValueError('speech id/text missing')
            ident=hashlib.sha256((s['id']+'\n'+key).encode()).hexdigest()
            title=f"{record.get('date','')} {record.get('nameOfMeeting','')} {record.get('speaker','')}"
            # date is meeting date; do not fabricate an official publication timestamp.
            cur=db.execute('INSERT OR IGNORE INTO documents VALUES(?,?,?,?,?,?,?,?,?)',
             (ident,s['id'],key,title,record.get('speechURL') or record.get('meetingURL') or url,None,
              when,json.dumps(topics_for(text,rules)),fetchid))
            new+=cur.rowcount;texts.append((ident,text,fetchid))
    for ident,text,fetchid in texts:store_text(db,ident,text,when,fetchid)
    return {'new_documents':new,'returned':len(records),'matched':int(data['numberOfRecords']),
            'coverage_truncated':int(data['numberOfRecords'])>len(records),
            'note':'Only first 100 newest matching speeches; meeting date does not equal publication date.'}
