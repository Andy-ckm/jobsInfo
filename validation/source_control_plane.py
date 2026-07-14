#!/usr/bin/env python3
"""P0/P1 source control plane and authorized local collectors.

Commands:
  validate                 Validate all 25 P0/P1 bindings.
  health --context cloud   Produce cloud executor health matrix.
  health --context local   Produce local executor health matrix.
  collect-local            Collect BOSS and Liepin from the user's own device.

Security: read-only; no auto-apply, greetings, messaging, CAPTCHA bypass,
proxy rotation, or risk-control bypass. Challenge pages stop collection.
"""
from __future__ import annotations
import argparse,csv,hashlib,json,os,re,shutil,subprocess,time,urllib.error,urllib.request
from datetime import datetime,timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

ROOT=Path(__file__).resolve().parents[1]
REGISTRY=ROOT/"validation"/"source_registry.json"
STATES={"success","zero_result","blocked","condition_unmet","failed","not_run"}
CHALLENGE=re.compile(r"验证码|安全验证|访问异常|环境异常|IP地址存在异常|操作频繁|登录后查看|captcha",re.I)

def now()->str:return datetime.now(timezone.utc).isoformat()
def load(path:Path)->Any:return json.loads(path.read_text(encoding="utf-8"))

def validate(r:dict[str,Any])->list[str]:
 e=[]; ex=r.get("executors")or{}; hc=r.get("health_checks")or{}; ss=r.get("sources")or[]; seen=set()
 if r.get("schema_version")!="2.0":e.append("schema_version must be 2.0")
 for i,s in enumerate(ss):
  p=f"sources[{i}]"; sid=s.get("source_id")
  if not sid:e.append(f"{p}: source_id required")
  elif sid in seen:e.append(f"{p}: duplicate {sid}")
  else:seen.add(sid)
  if s.get("priority") not in {"P0","P1"}:e.append(f"{p}: priority must be P0/P1")
  if s.get("executor_id") not in ex:e.append(f"{p}: unknown executor")
  checks=s.get("health_check_ids")
  if not isinstance(checks,list)or not checks:e.append(f"{p}: health_check_ids required")
  else:
   for c in checks:
    if c not in hc:e.append(f"{p}: unknown health check {c}")
  if not s.get("runtime"):e.append(f"{p}: runtime required")
  pol=s.get("policy")or{}
  if pol.get("read_only")is not True:e.append(f"{p}: read_only must be true")
  if pol.get("stop_on_challenge")is not True:e.append(f"{p}: stop_on_challenge must be true")
  if pol.get("allow_auto_apply")is not False:e.append(f"{p}: allow_auto_apply must be false")
  if pol.get("allow_messaging")is not False:e.append(f"{p}: allow_messaging must be false")
 expected={"SRC001","SRC002","SRC003","SRC004","SRC005","SRC006","SRC007","SRC008","SRC009","SRC010","SRC011","SRC012","SRC016","SRC017","SRC018","SRC019","SRC020","SRC021","SRC026","SRC027","SRC028","SRC029","SRC030","SRC031","SRC032"}
 if seen!=expected:e.append(f"P0/P1 coverage mismatch missing={sorted(expected-seen)} extra={sorted(seen-expected)}")
 if len(ss)!=25:e.append(f"expected 25 sources, got {len(ss)}")
 return e

def http(url:str)->dict[str,Any]:
 try:
  req=urllib.request.Request(url,headers={"User-Agent":"JobSourceHealth/2.0","Accept":"application/json,text/html,*/*"})
  with urllib.request.urlopen(req,timeout=15)as resp:
   body=resp.read(2048).decode("utf-8","replace")
   return {"state":"blocked" if CHALLENGE.search(body) else "success","http_status":resp.status}
 except urllib.error.HTTPError as x:return {"state":"failed","http_status":x.code,"error":str(x)}
 except Exception as x:return {"state":"failed","error":f"{type(x).__name__}: {x}"}

def drop_health()->dict[str,Any]:
 raw=os.environ.get("JOB_SOURCE_DROP_DIR","").strip()
 if not raw:return {"state":"condition_unmet","reason":"JOB_SOURCE_DROP_DIR not configured"}
 p=Path(raw).expanduser()
 if not p.exists():return {"state":"condition_unmet","reason":"drop directory missing","path":str(p)}
 return {"state":"success" if os.access(p,os.W_OK)else"failed","path":str(p),"writable":os.access(p,os.W_OK)}

def boss_health()->dict[str,Any]:
 boss=shutil.which("boss")or shutil.which("boss.exe")
 if not boss:return {"state":"condition_unmet","reason":"boss-cli not installed"}
 try:
  p=subprocess.run([boss,"status","--json"],capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=90)
  data=json.loads(p.stdout[p.stdout.find("{"):])if"{"in p.stdout else{}
  return {"state":"success"if data.get("authenticated")else"condition_unmet","authenticated":bool(data.get("authenticated")),"search_authenticated":data.get("search_authenticated")}
 except Exception as x:return {"state":"failed","error":f"{type(x).__name__}: {x}"}

def liepin_health()->dict[str,Any]:
 profile=Path(os.environ.get("LIEPIN_PROFILE_DIR",str(Path.home()/".job-source-node"/"liepin-profile"))).expanduser()
 try:import playwright
 except Exception:return {"state":"condition_unmet","reason":"playwright not installed","profile":str(profile)}
 return {"state":"success"if profile.exists()else"condition_unmet","profile":str(profile),"profile_exists":profile.exists()}

def source_health(s:dict[str,Any],r:dict[str,Any],context:str)->dict[str,Any]:
 eid=s["executor_id"]; kind=r["executors"][eid]["kind"]
 if context not in s["runtime"]:d={"state":"condition_unmet","reason":f"not configured for {context}"}
 elif eid=="boss_local_authorized":d=boss_health()
 elif eid=="liepin_local_authorized":d=liepin_health()
 elif eid in{"domestic_local_browser","get_jobs_readonly_import","drive_snapshot_ingest"}:d=drop_health()
 elif eid=="jobspy_mcp":
  ep=os.environ.get("JOBSPY_MCP_ENDPOINT","").strip();d=http(ep)if ep else{"state":"condition_unmet","reason":"JOBSPY_MCP_ENDPOINT not configured"}
 elif eid in{"official_career_fanout","greenhouse_api","lever_api","smartrecruiters_api","company_site_connector"}:
  p=ROOT/"config"/"target_companies.json"
  if not p.exists():d={"state":"condition_unmet","reason":"target company config missing"}
  else:
   cfg=load(p);key={"greenhouse_api":"greenhouse","lever_api":"lever","smartrecruiters_api":"smartrecruiters","company_site_connector":"company_sites","official_career_fanout":"official_careers"}[eid];n=len(cfg.get(key)or[])
   d={"state":"success"if n else"condition_unmet","target_count":n,"reason":None if n else f"no {key} targets"}
 elif eid=="jobspy_executor":
  try:
   import jobspy
   d={"state":"success","dependency":"jobspy"}
  except Exception:d={"state":"condition_unmet","reason":"python-jobspy not installed"}
 elif eid=="web_search_discovery":d={"state":"success","reason":"provided by scheduled cloud runtime"}
 else:d={"state":"not_run","reason":f"no health implementation for {kind}"}
 return {"source_id":s["source_id"],"source_name":s["source_name"],"priority":s["priority"],"executor_id":eid,"executor_kind":kind,"state":d.get("state","failed"),"detail":d,"checked_at":now()}

def key(sid:str,title:str,company:str,loc:str,url:str)->str:
 return hashlib.sha256("|".join([sid,title.lower().strip(),company.lower().strip(),loc.lower().strip(),url.strip()]).encode()).hexdigest()
def stamp()->str:return datetime.now().isoformat(timespec="seconds")
def write(out:Path,rows:list[dict[str,Any]],health:dict[str,Any])->None:
 out.mkdir(parents=True,exist_ok=True);(out/"jobs.json").write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding="utf-8");(out/"health.json").write_text(json.dumps(health,ensure_ascii=False,indent=2),encoding="utf-8")
 cols=["source_id","source_name","job_key","title","company","salary_text","location","source_url","description","published_at","collected_at"]
 with(out/"jobs.csv").open("w",newline="",encoding="utf-8-sig")as f:w=csv.DictWriter(f,fieldnames=cols,extrasaction="ignore");w.writeheader();w.writerows(rows)

def collect_boss(cfg:dict[str,Any],out:Path)->dict[str,Any]:
 boss=shutil.which("boss")or shutil.which("boss.exe")
 if not boss:h={"state":"condition_unmet","reason":"boss-cli not installed","checked_at":stamp()};write(out,[],h);return h
 bh=boss_health()
 if bh["state"]!="success":h={"state":"condition_unmet","reason":"run boss login locally","auth":bh,"checked_at":stamp()};write(out,[],h);return h
 rows=[];attempts=[];blocked=False;raw=out/"raw";raw.mkdir(parents=True,exist_ok=True)
 for city in cfg.get("cities",["苏州","上海"]):
  for kw in cfg.get("keywords",["解决方案架构师","云架构师","Azure","AI解决方案","FDE"]):
   path=raw/f"{city}_{re.sub(r'[^0-9A-Za-z\u4e00-\u9fff]+','_',kw)}.json"
   p=subprocess.run([boss,"export",kw,"--city",city,"-n",str(cfg.get("count",30)),"--format","json","-o",str(path)],capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=600)
   challenge=bool(CHALLENGE.search(p.stdout+"\n"+p.stderr));attempts.append({"city":city,"keyword":kw,"returncode":p.returncode,"challenge":challenge})
   if challenge:blocked=True;break
   try:data=load(path)if path.exists()else[]
   except Exception:data=[]
   for x in data if isinstance(data,list)else[]:
    title=str(x.get("jobName")or"");company=str(x.get("brandName")or"");loc=" ".join(filter(None,[str(x.get("cityName")or""),str(x.get("areaDistrict")or"")]));url=str(x.get("jobUrl")or x.get("securityId")or"")
    rows.append({"source_id":"SRC016","source_name":"BOSS直聘","job_key":key("SRC016",title,company,loc,url),"title":title,"company":company,"salary_text":str(x.get("salaryDesc")or""),"location":loc,"source_url":url,"description":"","published_at":"","collected_at":stamp()})
   time.sleep(float(cfg.get("delay_seconds",3)))
  if blocked:break
 rows=list({x["job_key"]:x for x in rows}.values());(out/"attempts.json").write_text(json.dumps(attempts,ensure_ascii=False,indent=2),encoding="utf-8");h={"state":"blocked"if blocked else("success"if rows else"zero_result"),"job_count":len(rows),"attempts":len(attempts),"stopped_on_challenge":blocked,"checked_at":stamp()};write(out,rows,h);return h

def find_jobs(v:Any,o:list[dict[str,Any]])->None:
 if isinstance(v,dict):
  ks={str(k).lower()for k in v};title=any(k in ks for k in("jobtitle","jobname","title","positionname"));company=any(k in ks for k in("companyname","company","brandname","compname"))
  if title and(company or any("salary"in k for k in ks)):o.append(v)
  for c in v.values():find_jobs(c,o)
 elif isinstance(v,list):
  for c in v:find_jobs(c,o)
def pick(x:dict[str,Any],*names:str)->str:
 d={str(k).lower():v for k,v in x.items()}
 for n in names:
  v=d.get(n.lower())
  if v is not None and not isinstance(v,(dict,list)):return str(v)
 return ""

def collect_liepin(cfg:dict[str,Any],out:Path)->dict[str,Any]:
 try:from playwright.sync_api import sync_playwright
 except Exception:h={"state":"condition_unmet","reason":"playwright not installed","checked_at":stamp()};write(out,[],h);return h
 profile=Path(os.environ.get("LIEPIN_PROFILE_DIR",str(Path.home()/".job-source-node"/"liepin-profile"))).expanduser();profile.mkdir(parents=True,exist_ok=True);rows=[];attempts=[];blocked=False
 with sync_playwright()as pw:
  ctx=pw.chromium.launch_persistent_context(user_data_dir=str(profile),headless=bool(cfg.get("headless",False)),viewport={"width":1440,"height":1000},locale="zh-CN");page=ctx.pages[0]if ctx.pages else ctx.new_page();captured=[]
  def cap(resp):
   if"json"in(resp.headers.get("content-type")or"").lower()and re.search(r"job|search|position|recruit",resp.url,re.I):
    try:captured.append(resp.json())
    except Exception:pass
  page.on("response",cap)
  for city in cfg.get("cities",["苏州","上海"]):
   for kw in cfg.get("keywords",["解决方案架构师","云架构师","Azure","AI解决方案","FDE"]):
    captured.clear();url="https://www.liepin.com/zhaopin/?key="+quote(f"{kw} {city}")
    try:
     page.goto(url,wait_until="domcontentloaded",timeout=60000);page.wait_for_timeout(5000);body=page.locator("body").inner_text(timeout=10000);challenge=bool(CHALLENGE.search(body));attempts.append({"city":city,"keyword":kw,"url":page.url,"challenge":challenge,"json_responses":len(captured)})
     (out/"screenshots").mkdir(parents=True,exist_ok=True);page.screenshot(path=str(out/"screenshots"/f"{city}_{re.sub(r'[^0-9A-Za-z\u4e00-\u9fff]+','_',kw)}.png"))
     if challenge:blocked=True;break
     cand=[]
     for payload in captured:find_jobs(payload,cand)
     nodes=page.locator("a[href*='/job/'],a[href*='job-detail'],.job-card,.job-list-item,[class*='job-card']")
     for i in range(min(nodes.count(),int(cfg.get("dom_limit",60)))):
      try:
       n=nodes.nth(i);text=re.sub(r"\s+"," ",n.inner_text(timeout=2000)).strip();href=n.get_attribute("href")or""
      except Exception:continue
      if len(text)>3:cand.append({"title":text.split(" ")[0],"company":"","salary":"","location":city,"url":href,"description":text[:500]})
     for x in cand:
      title=pick(x,"jobTitle","jobName","title","positionName");company=pick(x,"companyName","company","brandName","compName");salary=pick(x,"salary","salaryDesc","salaryText");loc=pick(x,"location","city","cityName","dq")or city;u=pick(x,"url","jobUrl","link","href")
      if u.startswith("/"):u="https://www.liepin.com"+u
      if title:rows.append({"source_id":"SRC017","source_name":"猎聘","job_key":key("SRC017",title,company,loc,u),"title":title,"company":company,"salary_text":salary,"location":loc,"source_url":u,"description":pick(x,"description","jobDesc","content"),"published_at":pick(x,"publishTime","publishedAt","refreshTime"),"collected_at":stamp()})
    except Exception as x:attempts.append({"city":city,"keyword":kw,"url":url,"error":f"{type(x).__name__}: {x}"})
    time.sleep(float(cfg.get("delay_seconds",4)))
   if blocked:break
  ctx.close()
 rows=list({x["job_key"]:x for x in rows}.values());(out/"attempts.json").write_text(json.dumps(attempts,ensure_ascii=False,indent=2),encoding="utf-8");h={"state":"blocked"if blocked else("success"if rows else"zero_result"),"job_count":len(rows),"attempts":len(attempts),"stopped_on_challenge":blocked,"profile":str(profile),"checked_at":stamp()};write(out,rows,h);return h

def main()->int:
 p=argparse.ArgumentParser();p.add_argument("command",choices=["validate","health","collect-local"]);p.add_argument("--context",choices=["cloud","local"],default="cloud");p.add_argument("--registry",default=str(REGISTRY));p.add_argument("--config",default=str(ROOT/"local_node"/"config.example.json"));p.add_argument("--sources",default="boss,liepin");p.add_argument("--output",default="");a=p.parse_args();r=load(Path(a.registry));errors=validate(r)
 if a.command=="validate":report={"valid":not errors,"errors":errors,"source_count":len(r.get("sources",[])),"executor_count":len(r.get("executors",{})),"health_check_count":len(r.get("health_checks",{})),"generated_at":now()}
 elif a.command=="health":
  if errors:report={"valid":False,"errors":errors,"generated_at":now()}
  else:
   rs=[source_health(s,r,a.context)for s in r["sources"]];report={"valid":True,"context":a.context,"source_count":len(rs),"summary":{st:sum(x["state"]==st for x in rs)for st in STATES},"results":rs,"generated_at":now()}
 else:
  if errors:report={"valid":False,"errors":errors,"generated_at":now()}
  else:
   cfg=load(Path(a.config));drop=Path(a.output or os.environ.get("JOB_SOURCE_DROP_DIR","")or cfg.get("drop_dir","")or str(ROOT/"job-source-drop")).expanduser();rid="LOCAL-"+datetime.now().strftime("%Y%m%d-%H%M%S");run=drop/rid;run.mkdir(parents=True,exist_ok=True);selected={x.strip()for x in a.sources.split(",")if x.strip()};rs=[]
   if"boss"in selected:rs.append({"source_id":"SRC016","source_name":"BOSS直聘",**collect_boss(cfg.get("boss",{}),run/"boss")})
   if"liepin"in selected:rs.append({"source_id":"SRC017","source_name":"猎聘",**collect_liepin(cfg.get("liepin",{}),run/"liepin")})
   report={"valid":True,"run_id":rid,"mode":"authorized_local_read_only","results":rs,"summary":{st:sum(x.get("state")==st for x in rs)for st in STATES},"generated_at":stamp()};(run/"manifest.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8");report["archive"]=shutil.make_archive(str(run),"zip",root_dir=run)
 text=json.dumps(report,ensure_ascii=False,indent=2);print(text)
 if a.output and a.command!="collect-local":Path(a.output).write_text(text,encoding="utf-8")
 return 0 if report.get("valid") else 1
if __name__=="__main__":raise SystemExit(main())
