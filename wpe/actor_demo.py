"""Fictional end-to-end walkthrough; never use this DB as a live database."""
import argparse,json
from pathlib import Path
from .core import connect
from .actors import schema,store_text,extract_candidates,register_entity,link_interest,register_forecast,resolve_action,render_report,export,add_evidence

def run(db_path,output):
    if Path(db_path).exists():raise ValueError('demo requires a new database path')
    Path(db_path).parent.mkdir(parents=True,exist_ok=True)
    db=connect(db_path);schema(db)
    try:
        t='2026-01-01T00:00:00+00:00'
        text='【架空データ】設備導入の補助金制度が提案された。架空企業Aは対象設備を製造する。追加費用で利益が減る可能性もある。'
        tid=store_text(db,'fictional-policy',text,t,'fictional-fixture')
        extract_candidates(db,t)
        event=db.execute('SELECT * FROM policy_events').fetchone()
        evid=event['evidence_id']
        register_entity(db,{'id':'fictional-a','name':'架空企業A','kind':'company','region':'FICTION'})
        link_interest(db,dict(id='fictional-benefit',event_id=event['id'],entity_id='fictional-a',axis='benefit',
          statement='制度が成立すれば、対象設備への受注機会が増える可能性',status='hypothesis',
          evidence_ids=[evid],alternative='制度不成立、競争激化、追加費用で利益が減る可能性',created_at=t))
        register_forecast(db,dict(id='fictional-investment',event_id=event['id'],entity_id='fictional-a',
          action_kind='investment_announcement',question='架空企業Aが2026年2月1日までに設備投資を発表するか',
          created_at=t,deadline='2026-02-01T00:00:00+00:00',probability=.4,method='manual_estimate',
          evidence_ids=[evid],resolution_rule='架空の公式発表で設備投資を確認。記録不十分なら未解決。',
          counterevidence='制度はまだ提案段階、資金制約も未確認'))
        later='【架空データ】架空企業Aは2026年1月20日に設備投資を発表した。'
        tid2=store_text(db,'fictional-action',later,'2026-02-02T00:00:00+00:00','fictional-outcome')
        eid2=add_evidence(db,tid2,0,len(later),later)
        outcome=resolve_action(db,'fictional-investment',1,[eid2],'架空の発表記録で確認','2026-02-02T00:00:00+00:00')
        Path(output).mkdir(parents=True,exist_ok=True)
        render_report(db,Path(output)/'actors.html')
        data=export(db);data['mode']='FICTIONAL DEMO';data['warning']='All names, events and probabilities are fictional test fixtures.'
        (Path(output)/'actors.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        path=Path(output)/'actors.html';path.write_text(path.read_text().replace('<h1>','<p><strong>架空の動作例 — 現実の企業・制度・確率ではありません。</strong></p><h1>',1),encoding='utf-8')
        return {'mode':'FICTIONAL DEMO','event_id':event['id'],'outcome':outcome,'report':str(path.resolve())}
    finally:db.close()

def main():
    p=argparse.ArgumentParser();p.add_argument('--db',default='data/actor-demo.sqlite');p.add_argument('--output',default='outputs/actor-demo');a=p.parse_args()
    try:print(json.dumps(run(a.db,a.output),ensure_ascii=False,indent=2))
    except (ValueError,OSError) as exc:p.exit(1,f'Error: {exc}\n')
if __name__=='__main__':main()
