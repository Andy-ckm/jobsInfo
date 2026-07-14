#!/usr/bin/env python3
"""Concrete public/API/file executor implementations for the source registry."""
from __future__ import annotations
import csv,hashlib,json,os,re,urllib.error,urllib.request,zipfile
from datetime import datetime,timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote
CHALLENGE=re.compile(r'验证码|安全验证|访问异常|环境异常|IP地址存在异常|操作频繁|登录后查看|captcha',re.I)

def now()->str:return datetime.now(timezone.utc).isoformat()
def load(path:Path)->Any:return json.loads(path.read_text(encoding='utf-8'))
def result(state:str,jobs:list[dict[str,Any]]|None=None,reason:str|None=None,evidence:dict[str,Any]|None=None)->dict[str,Any]:
 rows=jobs or [];return {'state':state,'jobs':rows,'job_count':len(rows),'reason':reason,'evidence':evidence or {},'executed_at':now()}
def job_key(sid:str,title:str,company:str,location:str,url:str)->str:
 return hashlib.sha256('|'.join([sid,title.lower().strip(),company.lower().strip(),location.lower().strip(),url.strip()]).encode()).hexdigest()
def normalize(sid:str,name:str,title:str,company:str,location:str,url:str,**extra:Any)->dict[str,Any]:
 row={'source_id':sid,'source_name':name,'job_key':job_key(sid,title,company,location,url),'title':title,'company':company,'location':location,'source_url':url,'collected_at':now()};row.update(extra);return row
def write_output(out:Path|None,jobs:list[dict[str,Any]],health:dict[str,Any])->None:
 if not out:return
 out.mkdir(parents=True,exist_ok=True);(out/'jobs.json').write_text(json.dumps(jobs,ensure_ascii=False,indent=2),encoding='utf-8');(out/'health.json').write_text(json.dumps(health,ensure_ascii=False,indent=2),encoding='utf-8')
 fields=['source_id','source_name','job_key','title','company','salary_text','location','source_url','description','published_at','collected_at']
 with(out/'jobs.csv').open('w',newline='',encoding='utf-8-sig')as handle:w=csv.DictWriter(handle,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(jobs)
def get(url:str)->tuple[Any,dict[str,Any]]:
 try:
  req=urllib.request.Request(url,headers={'User-Agent':'JobSourceExecutor/2.0','Accept':'application/json,text/html,*/*'})
  with urllib.request.urlopen(req,timeout=25)as response:
   raw=response.read();text=raw.decode('utf-8','replace');e={'state':'blocked'if CHALLENGE.search(text)else'success','http_status':response.status,'url':response.url,'bytes':len(raw)}
   if e['state']=='blocked':return None,e
   try:return json.loads(text),e
   except json.JSONDecodeError:return text,e
 except urllib.error.HTTPError as exc:return None,{'state':'failed','http_status':exc.code,'error':str(exc),'url':url}
 except Exception as exc:return None,{'state':'failed','error':f'{type(exc).__name__}: {exc}','url':url}

def greenhouse_api(config:dict[str,Any],out:Path|None=None)->dict[str,Any]:
 token=str(config.get('token')or config.get('board')or'').strip();company=str(config.get('company')or token)
 if not token:return result('condition_unmet',reason='Greenhouse board token missing')
 data,e=get(f'https://boards-api.greenhouse.io/v1/boards/{quote(token)}/jobs?content=true')
 if e['state']!='success':return result(e['state'],reason=e.get('error'),evidence=e)
 jobs=[]
 for x in(data or{}).get('jobs',[]):
  title=str(x.get('title')or'');location=str((x.get('location')or{}).get('name')or'');url=str(x.get('absolute_url')or'')
  if title:jobs.append(normalize('SRC002','Greenhouse Job Board API',title,company,location,url,description=str(x.get('content')or''),published_at=str(x.get('updated_at')or'')))
 r=result('success'if jobs else'zero_result',jobs,evidence=e);write_output(out,jobs,{k:v for k,v in r.items()if k!='jobs'});return r

def lever_api(config:dict[str,Any],out:Path|None=None)->dict[str,Any]:
 site=str(config.get('site')or config.get('token')or'').strip();company=str(config.get('company')or site)
 if not site:return result('condition_unmet',reason='Lever site token missing')
 data,e=get(f'https://api.lever.co/v0/postings/{quote(site)}?mode=json')
 if e['state']!='success':return result(e['state'],reason=e.get('error'),evidence=e)
 jobs=[]
 for x in data if isinstance(data,list)else[]:
  categories=x.get('categories')or{};title=str(x.get('text')or'');location=str(categories.get('location')or'');url=str(x.get('hostedUrl')or x.get('applyUrl')or'')
  if title:jobs.append(normalize('SRC003','Lever Postings API',title,company,location,url,description=str(x.get('descriptionPlain')or x.get('description')or''),published_at=str(x.get('createdAt')or'')))
 r=result('success'if jobs else'zero_result',jobs,evidence=e);write_output(out,jobs,{k:v for k,v in r.items()if k!='jobs'});return r

def smartrecruiters_api(config:dict[str,Any],out:Path|None=None)->dict[str,Any]:
 company_id=str(config.get('company_id')or config.get('company')or'').strip();display=str(config.get('display_name')or company_id)
 if not company_id:return result('condition_unmet',reason='SmartRecruiters company id missing')
 data,e=get(f'https://api.smartrecruiters.com/v1/companies/{quote(company_id)}/postings?limit=100')
 if e['state']!='success':return result(e['state'],reason=e.get('error'),evidence=e)
 jobs=[]
 for x in(data or{}).get('content',[]):
  loc=x.get('location')or{};location=', '.join(filter(None,[str(loc.get('city')or''),str(loc.get('country')or'')]));title=str(x.get('name')or'');url=str(x.get('ref')or'')
  if title:jobs.append(normalize('SRC004','SmartRecruiters Jobs API',title,display,location,url,published_at=str(x.get('releasedDate')or'')))
 r=result('success'if jobs else'zero_result',jobs,evidence=e);write_output(out,jobs,{k:v for k,v in r.items()if k!='jobs'});return r

def extract_jobpostings(value:Any,found:list[dict[str,Any]])->None:
 if isinstance(value,dict):
  kind=value.get('@type')
  if kind=='JobPosting'or(isinstance(kind,list)and'JobPosting'in kind):found.append(value)
  for child in value.values():extract_jobpostings(child,found)
 elif isinstance(value,list):
  for child in value:extract_jobpostings(child,found)
def company_site_connector(config:dict[str,Any],out:Path|None=None)->dict[str,Any]:
 url=str(config.get('url')or'').strip();company=str(config.get('company')or'')
 if not url:return result('condition_unmet',reason='company site URL missing')
 body,e=get(url)
 if e['state']!='success':return result(e['state'],reason=e.get('error'),evidence=e)
 text=body if isinstance(body,str)else json.dumps(body,ensure_ascii=False);postings=[]
 for block in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',text,re.I|re.S):
  try:extract_jobpostings(json.loads(block),postings)
  except Exception:continue
 jobs=[]
 for x in postings:
  org=x.get('hiringOrganization')or{};location_obj=x.get('jobLocation')or{}
  if isinstance(location_obj,list):location_obj=location_obj[0]if location_obj else{}
  address=(location_obj.get('address')or{})if isinstance(location_obj,dict)else{};location=' '.join(filter(None,[str(address.get('addressLocality')or''),str(address.get('addressRegion')or''),str(address.get('addressCountry')or'')]));title=str(x.get('title')or'');url2=str(x.get('url')or url)
  if title:jobs.append(normalize('SRC001','公司官方招聘页',title,str(org.get('name')or company),location,url2,description=str(x.get('description')or''),published_at=str(x.get('datePosted')or''),salary_text=str(x.get('baseSalary')or'')))
 r=result('success'if jobs else'zero_result',jobs,evidence=e);write_output(out,jobs,{k:v for k,v in r.items()if k!='jobs'});return r
def official_career_fanout(config:dict[str,Any],out:Path|None=None)->dict[str,Any]:
 targets=config.get('targets')or[]
 if not targets:return result('condition_unmet',reason='official career targets missing')
 jobs=[];evidence=[]
 for target in targets:
  r=company_site_connector(target);jobs.extend(r['jobs']);evidence.append({'target':target.get('company')or target.get('url'),'state':r['state'],'job_count':r['job_count']})
 state='success'if jobs else('blocked'if any(x['state']=='blocked'for x in evidence)else'zero_result');r=result(state,jobs,evidence={'targets':evidence});write_output(out,jobs,{k:v for k,v in r.items()if k!='jobs'});return r

def read_rows(path:Path)->list[dict[str,Any]]:
 if path.suffix.lower()=='.json':
  data=load(path);return data if isinstance(data,list)else(data.get('jobs')or[])if isinstance(data,dict)else[]
 if path.suffix.lower()=='.csv':
  with path.open(encoding='utf-8-sig',newline='')as handle:return list(csv.DictReader(handle))
 return []
def drive_snapshot_ingest(config:dict[str,Any],out:Path|None=None)->dict[str,Any]:
 env_name=str(config.get('drop_dir_env')or'JOB_SOURCE_DROP_DIR');raw=os.environ.get(env_name,'').strip()or str(config.get('drop_dir')or'')
 if not raw:return result('condition_unmet',reason='snapshot/drop directory not configured')
 root=Path(raw).expanduser()
 if not root.exists():return result('condition_unmet',reason='snapshot/drop directory missing',evidence={'path':str(root)})
 jobs=[];files=[];temp=root/'.ingest_tmp'
 for path in sorted([p for p in root.rglob('*')if p.is_file()and p.suffix.lower()in{'.json','.csv','.zip'}],key=lambda p:p.stat().st_mtime,reverse=True)[:100]:
  if path.suffix.lower()=='.zip':
   try:
    sub=temp/hashlib.sha256(str(path).encode()).hexdigest()[:12];sub.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(path)as archive:archive.extractall(sub)
    for item in sub.rglob('*'):
     if item.suffix.lower()in{'.json','.csv'}:jobs.extend(read_rows(item));files.append(str(item))
   except Exception:continue
  else:jobs.extend(read_rows(path));files.append(str(path))
 valid=[x for x in jobs if isinstance(x,dict)and(x.get('title')or x.get('job_name')or x.get('jobName'))];r=result('success'if valid else'zero_result',valid,evidence={'files_scanned':len(files),'path':str(root)});write_output(out,valid,{k:v for k,v in r.items()if k!='jobs'});return r
def jobspy_mcp(config:dict[str,Any],out:Path|None=None)->dict[str,Any]:
 endpoint=os.environ.get(str(config.get('endpoint_env')or'JOBSPY_MCP_ENDPOINT'),'').strip()or str(config.get('endpoint')or'')
 if not endpoint:return result('condition_unmet',reason='JobSpy MCP endpoint not configured')
 _,e=get(endpoint);return result(e['state'],reason=e.get('error'),evidence=e)
def web_search_discovery(config:dict[str,Any],out:Path|None=None)->dict[str,Any]:
 path=os.environ.get('WEB_SEARCH_RESULTS_FILE','').strip()or str(config.get('results_file')or'')
 if not path:return result('condition_unmet',reason='cloud search result file not supplied')
 return drive_snapshot_ingest({'drop_dir':str(Path(path).expanduser().parent)},out)
def get_jobs_readonly_import(config:dict[str,Any],out:Path|None=None)->dict[str,Any]:
 r=drive_snapshot_ingest(config,out);r['evidence']['mode']='isolated read-only import; automation actions disabled';return r
