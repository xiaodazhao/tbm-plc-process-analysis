"""Two evidence-anchored local patches; no rewrite of other paragraphs or old runs."""
from copy import deepcopy
import shutil
from common import *


def main():
    from tbm_process.engineering_reporting.local_revision import request,apply
    from tbm_process.engineering_reporting.writer import validate_text
    from tbm_process.engineering_reporting.delivery_status import decide
    from tbm_process.engineering_reporting.rendering import render_report
    parent=ROOT/'experiments/practical_report_policy_20261030_v1/accepted_reports'
    cases=[('2024-08-21','3.1:2:comparison_conclusion_without_any_computed_pair'),
           ('2024-09-17','3.3:1:original_point_1017383_outside_current_window_1017361_1017381')]
    results=[]
    for day,error in cases:
        source=parent/day;folder=HERE/'development_fixes'/day
        if folder.exists():raise RuntimeError('development_patch_not_repeated')
        shutil.copytree(source,folder,ignore=shutil.ignore_patterns('*.pdf','*.html','previews','figures','*.request.json',
            '*.response.json','*.metadata.json','*.payload.json','model_calls.json'))
        pack,plan,draft=[load(folder/n) for n in ['unified_report_input.json','content_plan.json','final_narrative.json']]
        req=request(pack,draft,[error],'deepseek-v4-flash')
        guidance=('该日没有任何实际比较结果；只删除或改正目标段无依据的“同一背景内仍有运行差异”，保留当前统计和地质资料状态。'
            if day=='2024-08-21' else '当日前方窗口为DyK1017+361至+381。原报告+383在窗口外，目标段只保留窗口内+374关注位置；原报告范围374至387可以保留并明确是原范围。其他段不能修改。')
        req['messages'].append({'role':'user','content':guidance})
        response=call('development_fix',day,'D','local_patch',req,folder)
        after,preservation=apply(draft,response,[error])
        save(folder/'revision.diff.json',{'before':draft,'after':after,'reasons':[error],
            'non_target_preservation':preservation,'patch_payload':response})
        save(folder/'final_narrative.json',after)
        check=validate_text(after,pack,plan)
        save(folder/'final_narrative.validation.json',check);decide(folder,check)
        artifact=render_report(folder)
        save(folder/'ORIGIN.json',{'original_folder':str(source),'original_text_SHA256':sha(source/'final_narrative.json'),
            'source_files_not_changed':True,'patch_not_independent_expert_confirmation':True})
        results.append({'date':day,'body_checks_passed':check['passed'],'preservation':preservation,
            'PDF_technical_passed':artifact['technical_passed'],'pdf':artifact['pdf']})
    save(HERE/'DEVELOPMENT_FIX_RESULTS.json',results)
    log('two local development patches complete')


if __name__=='__main__':main()
