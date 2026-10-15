"""Exploratory within-region annual changes; never causal or individual scoring."""
import argparse,html,json,math,statistics
from datetime import datetime,timezone
from itertools import combinations
from pathlib import Path
from .core import connect

NAMES={'NY.GDP.MKTP.KD.ZG':'GDP成長率','FP.CPI.TOTL.ZG':'インフレ率','SL.UEM.TOTL.ZS':'失業率','SP.POP.TOTL':'人口','SP.URB.TOTL.IN.ZS':'都市人口比率','SE.SEC.ENRR':'中等教育総就学率','EG.ELC.ACCS.ZS':'電力アクセス','IT.NET.USER.ZS':'インターネット利用率','NE.EXP.GNFS.ZS':'輸出/GDP','VC.IHR.PSRC.P5':'人口10万人当たり故意殺人','SH.XPD.CHEX.GD.ZS':'医療支出/GDP','BX.KLT.DINV.WD.GD.ZS':'海外直接投資/GDP','MS.MIL.XPND.GD.ZS':'軍事費/GDP','MS.MIL.XPND.CD':'軍事費（米ドル）','MS.MIL.TOTL.P1':'軍人員','GC.XPN.TOTL.GD.ZS':'政府経費/GDP','GC.REV.XGRT.GD.ZS':'政府歳入（贈与を除く）/GDP','GC.DOD.TOTL.GD.ZS':'中央政府債務/GDP','GC.TAX.TOTL.GD.ZS':'税収/GDP','SM.POP.TOTL.ZS':'国際移民ストック比率','SM.POP.REFG.OR':'出身国別難民数','SM.POP.REFG':'受入国別難民数'}

def correlation(pairs):
    xs,ys=zip(*pairs);a=statistics.mean(xs);b=statistics.mean(ys)
    den=math.sqrt(sum((x-a)**2 for x in xs)*sum((y-b)**2 for y in ys))
    return sum((x-a)*(y-b) for x,y in pairs)/den if den else None

def changes(points):
    return {y:v-points[y-1] for y,v in points.items() if y-1 in points}

def find(groups,min_samples=20):
    results=[];tested=0
    for region,variables in sorted(groups.items()):
        for left,right in combinations(sorted(variables),2):
            a=changes(variables[left]);b=changes(variables[right])
            for lag in (-1,0,1):
                years=sorted(y for y in a if y+lag in b)
                if len(years)<min_samples:continue
                pairs=[(a[y],b[y+lag]) for y in years];r=correlation(pairs);tested+=1
                if r is None:continue
                midpoint=len(pairs)//2;early=correlation(pairs[:midpoint]);late=correlation(pairs[midpoint:])
                stable=early is not None and late is not None and early*late>0 and r*early>0
                results.append({'region':region,'left':left,'right':right,'lag_years':lag,'r':r,'n':len(pairs),'start':years[0],'end':years[-1],'early_r':early,'late_r':late,'direction_stable':stable})
    return sorted(results,key=lambda r:(r['direction_stable'],abs(r['r'])),reverse=True),tested

def load(db,as_of):
    groups={}
    rows=db.execute("SELECT * FROM observations WHERE source LIKE 'global_%' AND mode='live' AND known_on<=? AND observed_on<=? ORDER BY observed_on,known_on,id",(as_of,as_of))
    for r in rows:
        if not math.isfinite(r['value']):continue
        groups.setdefault(r['region'],{}).setdefault(r['variable'],{})[int(r['observed_on'][:4])]=r['value']
    exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='domain_records'").fetchone()
    if exists:
        rows=db.execute("SELECT payload FROM domain_records WHERE known_on<=? AND kind IN ('statistic','survey') ORDER BY known_on,recorded_at,id",(as_of,))
        for row in rows:
            r=json.loads(row[0])
            if r.get('occurred_on','9999')>as_of:continue
            # Only explicitly full-calendar-year observations can join annual comparisons.
            if r['occurred_on'][4:]!='-12-31':continue
            if r.get('period_start')!=r['occurred_on'][:4]+'-01-01' and r.get('stage')!='population_stock':continue
            key=' | '.join([r['domain'],r['classification'],r['definition'],r['unit'],r['population'],r.get('stage','unknown'),r['source_name']])
            groups.setdefault(r['region'],{}).setdefault(key,{})[int(r['occurred_on'][:4])]=r['value']
    return groups

def generate(db,output,as_of):
    datetime.strptime(as_of,'%Y-%m-%d')
    groups=load(db,as_of)
    results,tested=find(groups);out=Path(output);out.mkdir(parents=True,exist_ok=True)
    payload={'as_of':as_of,'regions':len(groups),'comparisons_tested':tested,'results':results,'meaning':'exploratory_association_not_causation_or_prediction'}
    (out/'relationships.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    rows=''
    for r in results[:200]:
        early='—' if r['early_r'] is None else f"{r['early_r']:.2f}";late='—' if r['late_r'] is None else f"{r['late_r']:.2f}"
        rows+=f"<tr><td>{html.escape(r['region'])}</td><td>{html.escape(NAMES.get(r['left'],r['left']))}</td><td>{html.escape(NAMES.get(r['right'],r['right']))}</td><td>{r['lag_years']:+d}</td><td>{r['r']:.2f}</td><td>{early} / {late}</td><td>{r['n']} ({r['start']}–{r['end']})</td><td>{'一致' if r['direction_stable'] else '不安定'}</td></tr>"
    text=f'''<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>WPE 指標間の関連</title><style>body{{font:15px/1.7 system-ui;padding:24px;background:#eef2f7}}table{{background:white;border-collapse:collapse}}td,th{{padding:10px;border-bottom:1px solid #ddd}}.scroll{{overflow:auto}}</style><h1>指標間の関連探索</h1><p>基準日 {html.escape(as_of)} ／地域・集計区分 {len(groups)} ／比較数 {tested}</p><p>同一地域の年ごとの変化量を比較しています。時間差 +1 は左の変化と翌年の右の変化、-1 は前年の右の変化です。欠損年は補間せず、20組以上ある比較を掲載します。</p><p>多数の比較から偶然の強い相関も見つかります。下表は仮説候補であり、因果・予測性能を実証した結果ではありません。前半・後半の方向一致も有意性の検定ではありません。改訂後の過去データを使っています。国・地域の統計から個人や集団の犯罪性を推定しません。就学率は教育の質を表しません。</p><p>方向一致を優先し、相関の絶対値順に最大200件表示。全結果は relationships.json に保存しています。</p><div class="scroll"><table><tr><th>地域</th><th>左指標</th><th>右指標</th><th>時間差</th><th>相関</th><th>前半 / 後半</th><th>組数・左年</th><th>方向</th></tr>{rows}</table></div></html>'''
    (out/'index.html').write_text(text,encoding='utf-8');return {'regions':len(groups),'comparisons':tested,'report':str((out/'index.html').resolve())}

def main():
    p=argparse.ArgumentParser();p.add_argument('--db',default='data/runtime.sqlite');p.add_argument('--output',default='outputs/relationships');p.add_argument('--as-of',default=datetime.now(timezone.utc).date().isoformat());a=p.parse_args()
    if not Path(a.db).is_file():p.error('既存DBが見つかりません')
    datetime.strptime(a.as_of,'%Y-%m-%d');db=connect(a.db)
    try:print(json.dumps(generate(db,a.output,a.as_of),ensure_ascii=False))
    finally:db.close()

if __name__=='__main__':main()
