"""Locked, sequential paired experiment. No automatic outcome-driven reruns."""
import argparse
import os
import signal
import subprocess
import time

from common import *
from adapters import MODEL,FIXED_PLAN_RULE,direct_request,fixed_plan,relation_pair_tasks,run_date,group_folder
from tbm_process.engineering_reporting import writer


def lock():
    if (HERE/'EXPERIMENT_REGISTRY.json').exists():raise RuntimeError('registry_already_locked')
    if not freeze_check()['passed']:raise RuntimeError('frozen_source_changed')
    sample=load(HERE/'SAMPLING.json');requests=[];rel=[]
    for day in DEVELOPMENT+sample['formal_dates']:
        prep=load(HERE/'inputs'/day/'preparation.result.json');folder=HERE/'prepared_requests'/day
        if prep['status']!='PREPARED':out={'date':day,'status':'INPUT_UNAVAILABLE','preparation':prep}
        else:
            try:
                pack=load(input_path(day));plan=fixed_plan(pack)
                pc=writer.validate_plan(plan,pack)
                if not pc['passed']:raise ValueError('fixed_plan_invalid')
                reqs={'A.request.json':direct_request(pack),'C.plan.request.json':writer.request('plan',pack),
                      'B.write.request.json':writer.request('write',pack,plan=plan)}
                views=[json.loads(r['messages'][1]['content'])['facts'] for r in reqs.values()]
                if any(v!=views[0] for v in views):raise ValueError('arm_fact_views_differ')
                for name,req in reqs.items():save(folder/name,req)
                save(folder/'B.fixed_plan.json',plan)
                out={'date':day,'status':'REQUESTS_PREPARED','input_sha256':sha(input_path(day)),
                     'identical_fact_view_SHA256':digest(views[0])}
            except Exception as exc:
                out={'date':day,'status':'REQUEST_PREPARATION_FAILED','error_class':type(exc).__name__,
                     'reason':str(exc),'new_API_calls':0,'date_not_replaced':True}
        save(folder/'request_status.json',out);requests.append(out)
    for day in load(HERE/'RELATION_SUBSAMPLE.json')['dates']:
        if next(r for r in requests if r['date']==day)['status']!='REQUESTS_PREPARED':continue
        task=relation_pair_tasks(load(input_path(day)))
        if task and task['REL+']!=task['REL-']:
            save(HERE/'prepared_requests'/day/'relation.tasks.json',task);rel.append(day)
    files=[HERE/'FREEZE.json',HERE/'SAMPLING.json',HERE/'TEST_DATES.csv',HERE/'PRE_REGISTRATION.md',HERE/'ANNOTATION_GUIDE.md']
    files+=list(HERE.glob('*.py'))+list((HERE/'tests').glob('*.py'))+list((HERE/'prepared_requests').glob('*/*.json'))
    registry={'version':VERSION,'created_utc':now(),'model':MODEL,'temperature':.1,
        'interface_dates':DEVELOPMENT,'formal_dates':sample['formal_dates'],'human_dates':sample['human_dates'],
        'relation_dates':rel,'formal_and_relations_total_cap':180,'interface_cap':24,'development_fix_cap':4,
        'HTTP_retries':0,'date_timeout_seconds':1500,'plan_repairs':1,'local_patches':1,'fixed_plan_rule':FIXED_PLAN_RULE,
        'input_statuses':requests,'locks':[{'path':str(p.relative_to(HERE)),'sha256':sha(p)} for p in sorted(set(files))],
        'failure_policy':'retain_failed_or_interrupted_attempt; no_date_replacement_or_prompt_tuning',
        'D_implementation':'exact_frozen_pipeline.generate_with_seed_C_plan_and_draft; transport_only_intercepted',
        'independent_human_metrics':'NOT_EVALUATED'}
    save(HERE/'EXPERIMENT_REGISTRY.json',registry)
    save(HERE/'REGISTRY_SHA.json',{'sha256':sha(HERE/'EXPERIMENT_REGISTRY.json')})
    csv_write(HERE/'REQUEST_PREPARATION.csv',requests)
    log('experiment registry locked; executable='+str(sum(r['status']=='REQUESTS_PREPARED' for r in requests)))


def input_path(day):return HERE/'inputs'/day/'unified_report_input.json'


def validate_locks():
    r=load(HERE/'EXPERIMENT_REGISTRY.json')
    if sha(HERE/'EXPERIMENT_REGISTRY.json')!=load(HERE/'REGISTRY_SHA.json')['sha256']:raise RuntimeError('registry_changed')
    bad=[x['path'] for x in r['locks'] if sha(HERE/x['path'])!=x['sha256']]
    if bad or not freeze_check()['passed']:raise RuntimeError('locked_sources_changed:'+','.join(bad))
    return r


def interface_check():
    rows=[]
    for day in DEVELOPMENT:
        for arm in 'ABCD':
            p=group_folder('interface',day,arm)/'result.json'
            if not p.exists():rows.append({'date':day,'arm':arm,'issue':'missing_terminal_result'});continue
            result=load(p)
            if result.get('error_class') in {'KeyError','TypeError','AttributeError','AssertionError'}:
                rows.append({'date':day,'arm':arm,'issue':'adapter_exception:'+result['error_class']})
    calls=[r for r in Calls('interface').rows() if r['scope']=='interface']
    if not any(r['status']=='SUCCESS' for r in calls):rows.append({'issue':'no_successful_model_call'})
    if any(r.get('http_status') in {401,402,403} for r in calls):rows.append({'issue':'service_access_failed'})
    record={'passed':not rows,'errors':rows,'date_count':4,'arm_terminal_expected':16,'actual_calls':len(calls),
            'rule_or_method_failure_not_hidden_as_interface_fix':True,'checked_utc':now()}
    save(HERE/'INTERFACE_ACCEPTANCE.json',record);return record


def execute(scope):
    registry=validate_locks()
    if scope=='formal' and not interface_check()['passed']:raise RuntimeError('interface_not_accepted')
    days=registry['interface_dates'] if scope=='interface' else registry['formal_dates']
    consecutive=0;blocked=False
    for index,day in enumerate(days,1):
        log(f'{scope} {index}/{len(days)} {day} start')
        base=HERE/'executions'/scope/day
        if (base/'result.json').exists():continue
        if blocked:
            for arm in 'ABCD':save(group_folder(scope,day,arm)/'result.json',
                {'status':'NOT_RUN_SERVICE_STOP','semantic_status':'NOT_EVALUATED'})
            continue
        if (base/'started.json').exists():
            log(day+' interrupted attempt retained without repeat');continue
        save(base/'started.json',{'UTC':now()});started=time.monotonic()
        with (base/'execution.log').open('w',encoding='utf-8') as out:
            child=subprocess.Popen([sys.executable,'-B',str(HERE/'run.py'),'worker','--scope',scope,'--date',day],
                stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
            try:code=child.wait(timeout=registry['date_timeout_seconds'])
            except subprocess.TimeoutExpired:
                os.killpg(child.pid,signal.SIGTERM)
                try:child.wait(timeout=10)
                except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait()
                code='TIMEOUT'
        save(base/'result.json',{'date':day,'exit_code':code,'wall_seconds':round(time.monotonic()-started,3)})
        for arm in 'ABCD':
            p=group_folder(scope,day,arm)/'result.json'
            if not p.exists():save(p,{'status':'INTERRUPTED_OR_WORKER_FAILED','date':day,'semantic_status':'NOT_EVALUATED'})
        calls=[r for r in Calls(scope).rows() if r['scope']==scope and r['date']==day]
        account=any(r.get('http_status') in {401,402,403} for r in calls)
        failed=bool(calls) and not any(r['status']=='SUCCESS' for r in calls)
        consecutive=consecutive+1 if failed else 0;blocked=account or consecutive>=2
        log(scope+' '+day+' '+','.join(a+':'+load(group_folder(scope,day,a)/'result.json')['status'] for a in 'ABCD'))
    if scope=='interface':interface_check()


def relations():
    registry=validate_locks()
    for index,day in enumerate(registry['relation_dates']):
        calls=Calls('relations').rows()
        remaining=180-sum(r['scope'] in {'formal','relations'} for r in calls)
        if remaining<2:
            save(HERE/'relations'/day/'budget_stop.json',{'status':'NOT_RUN','reason':'paired_budget_below_two'});break
        tasks=load(HERE/'prepared_requests'/day/'relation.tasks.json')
        order=['REL+','REL-'] if index%2==0 else ['REL-','REL+']
        for arm in order:
            folder=HERE/'relations'/day/arm
            if (folder/'result.json').exists():continue
            try:
                response=call('relations',day,arm,'generation',tasks[arm],folder)
                save(folder/'result.json',{'status':'GENERATED','payload':response,'semantic_status':'NOT_EVALUATED'})
            except Exception as exc:
                save(folder/'result.json',{'status':'FAILED','error_class':type(exc).__name__,'semantic_status':'NOT_EVALUATED'})


if __name__=='__main__':
    os.environ.setdefault('MPLCONFIGDIR',str(HERE/'.matplotlib'))
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['lock','interface','formal','relations','worker','check'])
    p.add_argument('--scope',choices=['interface','formal']);p.add_argument('--date');a=p.parse_args()
    if a.stage=='worker':validate_locks();run_date(a.date,a.scope)
    elif a.stage=='lock':lock()
    elif a.stage in {'interface','formal'}:execute(a.stage)
    elif a.stage=='relations':relations()
    else:print(interface_check())
