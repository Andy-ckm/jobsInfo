#!/usr/bin/env python3
"""Fail CI unless every P0/P1 source has a real executor and health checks."""
from __future__ import annotations
import ast,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
REGISTRY=ROOT/'validation/source_registry.json'
EXPECTED={f'SRC{i:03d}' for i in list(range(1,13))+list(range(16,22))+list(range(26,33))}

def callable_exists(entrypoint:str)->tuple[bool,str]:
 if '::' not in entrypoint:return False,'expected path.py::function'
 rel,name=entrypoint.split('::',1);path=ROOT/rel
 if not path.exists():return False,f'module missing: {rel}'
 try:tree=ast.parse(path.read_text(encoding='utf-8'),filename=str(path))
 except Exception as exc:return False,f'parse failed: {type(exc).__name__}: {exc}'
 names={n.name for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
 return (name in names,'' if name in names else f'callable missing: {entrypoint}')

def validate(registry:dict)->list[str]:
 errors=[];executors=registry.get('executors')or{};checks=registry.get('health_checks')or{};sources=registry.get('sources')or[];seen=set()
 if registry.get('schema_version')!='2.0':errors.append('schema_version must be 2.0')
 for executor_id,executor in executors.items():
  ok,reason=callable_exists(str(executor.get('entrypoint')or''))
  if not ok:errors.append(f'executors.{executor_id}: {reason}')
  if executor.get('output_contract')!='job_contract':errors.append(f'executors.{executor_id}: output_contract must be job_contract')
 for i,source in enumerate(sources):
  prefix=f'sources[{i}]';sid=source.get('source_id');eid=source.get('executor_id')
  if not sid:errors.append(f'{prefix}: source_id required')
  elif sid in seen:errors.append(f'{prefix}: duplicate {sid}')
  else:seen.add(sid)
  if source.get('priority') not in {'P0','P1'}:errors.append(f'{prefix}: priority must be P0/P1')
  if eid not in executors:errors.append(f'{prefix}: unknown executor {eid}')
  health=source.get('health_check_ids')
  if not isinstance(health,list) or not health:errors.append(f'{prefix}: health_check_ids required')
  else:
   for check in health:
    if check not in checks:errors.append(f'{prefix}: unknown health check {check}')
  if not source.get('runtime'):errors.append(f'{prefix}: runtime required')
  policy=source.get('policy')or{}
  required={'read_only':True,'stop_on_challenge':True,'allow_auto_apply':False,'allow_messaging':False}
  for key,value in required.items():
   if policy.get(key) is not value:errors.append(f'{prefix}: policy.{key} must be {value}')
 if seen!=EXPECTED:errors.append(f'coverage mismatch missing={sorted(EXPECTED-seen)} extra={sorted(seen-EXPECTED)}')
 if len(sources)!=25:errors.append(f'expected 25 P0/P1 sources, got {len(sources)}')
 return errors

def main()->int:
 registry=json.loads(REGISTRY.read_text(encoding='utf-8'));errors=validate(registry)
 report={'valid':not errors,'errors':errors,'source_count':len(registry.get('sources',[])),'executor_count':len(registry.get('executors',{})),'health_check_count':len(registry.get('health_checks',{}))}
 print(json.dumps(report,ensure_ascii=False,indent=2));return 0 if not errors else 1
if __name__=='__main__':raise SystemExit(main())
