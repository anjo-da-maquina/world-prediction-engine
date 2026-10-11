import argparse
import csv
import json
import math
import random
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from .core import connect, import_csv, insert, forecast, save_prediction, resolve, backtest, report

def demo(db):
    if db.execute('SELECT COUNT(*) FROM observations').fetchone()[0]:
        raise ValueError('demo requires a new empty database')
    rng = random.Random(42)
    start = date(2010,1,1)
    value = 80
    rows = []
    for i in range(1200):
        value = 80+.97*(value-80)+rng.gauss(0,2)+.3*math.sin(i/30)
        when = (start+timedelta(days=i)).isoformat()
        rows.append(dict(variable='synthetic_price',region='DEMO',observed_on=when,
                         known_on=when,value=value,source='synthetic-seed42',mode='demo'))
    insert(db,rows)
    cutoff = rows[900]['observed_on']
    p = forecast(db,'synthetic_price','DEMO',cutoff,30,90)
    ident = save_prediction(db,p)
    outcome = resolve(db,ident,rows[-1]['observed_on'])
    bt = backtest(db,'synthetic_price','DEMO',rows[-1]['observed_on'],30,90)
    return {'mode':'DEMO — synthetic data only','prediction_id':ident,'forecast':p,'outcome':outcome,'backtest':bt}

def worldbank(db,country,indicator,start,end):
    """Current data has NO guaranteed historical availability. known_on=today."""
    for token in (country,indicator):
        if not token or not all(c.isalnum() or c in '._-' for c in token):
            raise ValueError('invalid country or indicator')
    if start>end:
        raise ValueError('start must be <= end')
    today = date.today().isoformat()
    all_rows = []
    pages = 1
    page = 1
    while page<=pages:
        query = urllib.parse.urlencode({'format':'json','date':f'{start}:{end}','per_page':1000,'page':page})
        url = f'https://api.worldbank.org/v2/country/{country}/indicator/{indicator}?{query}'
        with urllib.request.urlopen(url,timeout=30) as response:
            raw = json.load(response)
        if not isinstance(raw,list) or len(raw)!=2 or not isinstance(raw[0],dict) or 'pages' not in raw[0]:
            raise ValueError('unexpected World Bank API response')
        pages = int(raw[0]['pages'])
        if pages>100:
            raise ValueError('too many pages')
        for r in raw[1] or []:
            if r['value'] is not None:
                observed = f"{int(r['date']):04d}-12-31"
                if observed<=today:
                    all_rows.append(dict(variable=indicator,region=country,observed_on=observed,
                     known_on=today,value=r['value'],source='WorldBank-current',mode='live'))
        page+=1
    count = insert(db,all_rows)
    return {'inserted':count,'known_on':today,'note':'Current vintages only. Historical point-in-time backtesting intentionally unavailable.'}

def main():
    parser = argparse.ArgumentParser(description='Auditable research forecasting prototype')
    parser.add_argument('--db',default='wpe.sqlite')
    sub = parser.add_subparsers(dest='command',required=True)
    sub.add_parser('demo')
    imp = sub.add_parser('import-csv'); imp.add_argument('path')
    wb = sub.add_parser('worldbank')
    wb.add_argument('--country',default='JPN'); wb.add_argument('--indicator',default='FP.CPI.TOTL.ZG')
    wb.add_argument('--start',type=int,default=1960); wb.add_argument('--end',type=int,default=date.today().year-1)
    for name in ('forecast','backtest'):
        cmd = sub.add_parser(name)
        cmd.add_argument('--variable',required=True); cmd.add_argument('--region',required=True)
        cmd.add_argument('--as-of',required=True); cmd.add_argument('--horizon',type=int,required=True)
        cmd.add_argument('--threshold',type=float,required=True); cmd.add_argument('--source')
    res = sub.add_parser('resolve'); res.add_argument('id'); res.add_argument('--as-of',required=True)
    rep = sub.add_parser('report'); rep.add_argument('--output',default='report.md')
    args = parser.parse_args()
    db = connect(args.db)
    try:
        if args.command=='demo': result = demo(db)
        elif args.command=='import-csv': result = {'inserted':import_csv(db,args.path)}
        elif args.command=='worldbank': result = worldbank(db,args.country,args.indicator,args.start,args.end)
        elif args.command in ('forecast','backtest'):
            fn = forecast if args.command=='forecast' else backtest
            result = fn(db,args.variable,args.region,args.as_of,args.horizon,args.threshold,args.source)
            if args.command=='forecast': result['id'] = save_prediction(db,result)
        elif args.command=='resolve': result = resolve(db,args.id,args.as_of)
        else:
            report(db,args.output); result = {'report':str(Path(args.output).resolve())}
        print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    except (ValueError,OSError) as exc:
        parser.exit(1,f'Error: {exc}\n')
    finally:
        db.close()

if __name__=='__main__': main()
