"""Update numerical and relationship reports from the already-open collector DB."""
from pathlib import Path
from .core import series
from .economic_report import TARGETS,analyze,render
from .autonomous import write_json
from .relationships import generate as relationships

def generate(db,output,as_of):
    output=Path(output);economic=output/'economic';economic.mkdir(parents=True,exist_ok=True)
    results=[]
    for region,variable,source,title in TARGETS:
        r={'region':region,'variable':variable,'title':title}
        try:r.update(analyze(series(db,variable,region,as_of,source)))
        except ValueError as exc:r['error']=str(exc)
        results.append(r)
    temp=economic/'index.html.tmp';temp.write_text(render(results,as_of),encoding='utf-8');temp.replace(economic/'index.html')
    write_json(economic/'forecast.json',{'as_of':as_of,'results':results})
    return {'status':'updated','economic_computed':sum('error' not in r for r in results),
            'relationships':relationships(db,output/'relationships',as_of)}
