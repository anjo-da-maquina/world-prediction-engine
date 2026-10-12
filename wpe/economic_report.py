"""Annual forecasts and explicitly retrospective validation; standard library only."""
import argparse
import html
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path
from .core import connect, series

MODELS = ('persistence', 'mean5', 'trend5')
LABELS = {'persistence': '前年値維持', 'mean5': '直近5年平均', 'trend5': '直近5年の傾向'}
TARGETS = [('JPN', 'FP.CPI.TOTL.ZG', 'wb_jpn_inflation', '日本の年間インフレ率'),
           ('USA', 'NY.GDP.MKTP.KD.ZG', 'wb_usa_growth', '米国の年間GDP成長率'),
           ('WLD', 'NY.GDP.MKTP.KD.ZG', 'wb_world_growth', '世界の年間GDP成長率')]

def estimate(values, model):
    if model == 'persistence': return values[-1]
    window = values[-5:]
    if model == 'mean5': return statistics.mean(window)
    if model != 'trend5': raise ValueError('unknown model')
    n = len(window); mid = (n-1)/2
    slope = sum((i-mid)*v for i,v in enumerate(window))/sum((i-mid)**2 for i in range(n))
    return statistics.mean(window)+slope*(n-mid)

def quantile(values, q):
    ordered=sorted(values); pos=(len(ordered)-1)*q; lo=int(pos); hi=math.ceil(pos)
    return ordered[lo]+(ordered[hi]-ordered[lo])*(pos-lo)

def analyze(rows):
    if len(rows)<25: raise ValueError('少なくとも25年の観測が必要です')
    if {r['mode'] for r in rows}!={'live'}: raise ValueError('実データのみを使用します')
    years=[int(r['observed_on'][:4]) for r in rows]
    if any(b-a!=1 for a,b in zip(years,years[1:])): raise ValueError('年次データの欠損または重複があります')
    values=[float(r['value']) for r in rows]
    if not all(math.isfinite(v) for v in values): raise ValueError('有限数値が必要です')
    # Evaluation is held out from the older period used to rank models.
    split=max(15,len(values)-10)
    selection={m:statistics.mean(abs(values[i]-estimate(values[:i],m)) for i in range(10,split)) for m in MODELS}
    winner=min(MODELS,key=lambda m:selection[m])
    errors={m:[values[i]-estimate(values[:i],m) for i in range(split,len(values))] for m in MODELS}
    scores={m:statistics.mean(abs(e) for e in errors[m]) for m in MODELS}
    # Prospective intervals use prequential residuals, not normality assumptions.
    residuals=[values[i]-estimate(values[:i],winner) for i in range(10,len(values))]
    center=estimate(values,winner)
    lower=center+quantile(residuals,.1); upper=center+quantile(residuals,.9)
    delta=values[-1]-values[-2]; acceleration=delta-(values[-2]-values[-3])
    return {'last_year':years[-1], 'target_year':years[-1]+1,'latest':values[-1],
            'prediction':center,'lower':lower,'upper':upper,'model':winner,
            'models':[{'id':m,'prediction':estimate(values,m),'selection_mae':selection[m],
                       'holdout_mae':scores[m]} for m in MODELS],
            'evaluation_years':[years[split],years[-1]],'evaluation_count':len(values)-split,
            'beats_persistence':scores[winner]<scores['persistence'],
            'velocity_pp_per_year':delta,'acceleration_pp_per_year2':acceleration,
            'history':[{'year':y,'value':v} for y,v in zip(years,values)],
            'probability':None,
            'validation_kind':'retrospective_latest_vintage_not_point_in_time',
            'interval_kind':'historical_residual_10_90_percentiles_not_calibrated_coverage'}

def chart(result):
    history=result['history'][-20:]; vals=[p['value'] for p in history]+[result['lower'],result['upper'],result['prediction']]
    low=min(vals)-1; high=max(vals)+1
    x=lambda i:50+i*680/len(history)
    y=lambda v:220-(v-low)*180/(high-low)
    points=' '.join(f'{x(i):.1f},{y(p["value"]):.1f}' for i,p in enumerate(history))
    end=x(len(history)); start=x(len(history)-1)
    return f'''<svg viewBox="0 0 800 270" role="img" aria-label="年次実績と翌年予測"><line x1="50" y1="220" x2="740" y2="220" stroke="#a5b4c8"/><polyline points="{points}" fill="none" stroke="#2563eb" stroke-width="3"/><line x1="{end}" y1="{y(result['lower'])}" x2="{end}" y2="{y(result['upper'])}" stroke="#d97706" stroke-width="10"/><line x1="{start}" y1="{y(history[-1]['value'])}" x2="{end}" y2="{y(result['prediction'])}" stroke="#d97706" stroke-width="2" stroke-dasharray="5 4"/><text x="50" y="250">{history[0]['year']}</text><text x="{start-15}" y="250">{result['last_year']}</text><text x="{end-15}" y="250">{result['target_year']}</text><text x="5" y="45">{high:.1f}%</text><text x="5" y="220">{low:.1f}%</text></svg>'''

def render(results, as_of):
    cards=[]
    for r in results:
        title=html.escape(r['title'])
        if 'error' in r:
            cards.append(f'<section><h2>{title}</h2><p>{html.escape(r["error"])}</p></section>');continue
        table=''.join(f'<tr><td>{LABELS[m["id"]]}</td><td>{m["prediction"]:.2f}%</td><td>{m["holdout_mae"]:.2f}ポイント</td></tr>' for m in r['models'])
        skill='前年値維持を上回りました' if r['beats_persistence'] else '前年値維持を上回っていません。予測の優位性は確認できません'
        cards.append(f'''<section><h2>{title}</h2><p>{r['target_year']}年・年間値の推定</p><strong class="number">{r['prediction']:.2f}%</strong><p>過去の誤差から得た参考範囲：{r['lower']:.2f}% ～ {r['upper']:.2f}%</p><p>この範囲に80%の確率で入る保証はありません。</p>{chart(r)}<p>青：実績／橙：推定と参考範囲</p><p>最新実績：{r['last_year']}年 {r['latest']:.2f}% ／ 年間変化：{r['velocity_pp_per_year']:+.2f}ポイント ／ 変化の加速：{r['acceleration_pp_per_year2']:+.2f}ポイント/年²</p><table><tr><th>モデル</th><th>推定値</th><th>検証平均絶対誤差</th></tr>{table}</table><p>採用：{LABELS[r['model']]}。古い期間で選び、その後の{r['evaluation_count']}年（{r['evaluation_years'][0]}～{r['evaluation_years'][1]}）で比較。{skill}。</p><p>出典：World Bank ／ 地域：{r['region']} ／ 指標：{r['variable']}。データを再取得して更新するか、実績が参考範囲から外れた場合に見直してください。</p></section>''')
    return '''<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>WPE 経済予測</title><style>body{font:16px/1.7 system-ui;background:#eef2f7;color:#172438;max-width:1000px;margin:auto;padding:24px}section{background:white;padding:24px;margin:24px 0;border-radius:12px}.number{font-size:42px;color:#1d4ed8}svg{width:100%;height:auto}table{border-collapse:collapse;width:100%}td,th{text-align:left;padding:10px;border-bottom:1px solid #ddd}.notice{background:#fff3cf;padding:18px}</style><h1>WPE 経済予測</h1><p>情報基準日：'''+html.escape(as_of)+'''</p><div class="notice">世界情勢全体の予測はまだ実装されていません。この画面は年次経済指標の翌年値を推定します。検証には現在取得できる改訂後の過去データを使用しており、当時の公表値・公表時点を再現した検証ではありません。金利・政策・紛争などの外部要因はモデルに含まれません。利益を受ける人物や原因をこの計算で認定することはできません。</div>'''+''.join(cards)+'</html>'

def main():
    p=argparse.ArgumentParser();p.add_argument('--db',default='data/runtime.sqlite');p.add_argument('--output',default='outputs/economic');p.add_argument('--as-of',default=datetime.now(timezone.utc).date().isoformat());a=p.parse_args()
    if not Path(a.db).is_file():p.error('既存の収集DBが見つかりません')
    db=connect(a.db);results=[]
    try:
        for region,variable,source,title in TARGETS:
            r={'region':region,'variable':variable,'title':title}
            try:r.update(analyze(series(db,variable,region,a.as_of,source)))
            except ValueError as exc:r['error']=str(exc)
            results.append(r)
    finally:db.close()
    out=Path(a.output);out.mkdir(parents=True,exist_ok=True)
    (out/'index.html').write_text(render(results,a.as_of),encoding='utf-8')
    (out/'forecast.json').write_text(json.dumps({'as_of':a.as_of,'results':results},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'report':str((out/'index.html').resolve()),'computed':sum('error' not in r for r in results)},ensure_ascii=False))

if __name__=='__main__':main()
