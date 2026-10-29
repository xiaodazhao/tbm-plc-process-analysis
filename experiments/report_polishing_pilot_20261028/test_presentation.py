from copy import deepcopy
import json

import pytest

from tbm_process.evidence_graph.catalog import SourceAudit, document_gate
from tbm_process.evidence_graph.sketches import load_current_catalog
from tbm_process.reporting_v2.facts import digest
from .presentation import validate, window_rows, photo_evidence


def merged_sketch(tmp_path, value='2024-01-23'):
    folder=tmp_path/'伯舒拉岭右线洞身素描'/'record';folder.mkdir(parents=True)
    p=folder/'数据.json'
    p.write_text(json.dumps({'里程':'DyK1014+825～DyK1014+855','预报时间':value,
                             '现场照片':[{'左边墙':'https://example.invalid/img/2024/1/24/a.jpg'}]},ensure_ascii=False))
    result,_=load_current_catalog({'reports':[]},tmp_path)
    return p,result['reports'][0]['report_metadata']


def test_merged_sketch_explicit_report_date_is_not_upload_or_observation(tmp_path):
    p,m=merged_sketch(tmp_path)
    assert m['available_date']=='2024-01-23'
    assert m['observation_date'] is None
    assert m['image_upload_date_candidates']==['2024-01-24']
    audit=SourceAudit().metadata(m,{'path':str(p),'sha256':digest(p)})
    assert audit['status']=='PASS'
    assert document_gate({'metadata':m,'date_audit':audit},'2024-01-24')['status']=='eligible'


@pytest.mark.parametrize('value',['2024-02-30','unknown','',None])
def test_bad_explicit_date_never_falls_back_to_upload(tmp_path,value):
    _,m=merged_sketch(tmp_path,value)
    assert m['available_date'] is None
    assert m['invalid_explicit_report_date']


@pytest.mark.parametrize('value,status',[('2024-01-24','review'),('2024-01-25','excluded')])
def test_new_dates_do_not_admit_same_or_future_day(tmp_path,value,status):
    p,m=merged_sketch(tmp_path,value)
    audit=SourceAudit().metadata(m,{'path':str(p),'sha256':digest(p)})
    assert document_gate({'metadata':m,'date_audit':audit},'2024-01-24')['status']==status


def test_merged_folder_takes_precedence_over_legacy(tmp_path):
    merged_sketch(tmp_path)
    old=tmp_path/'伯舒拉岭右线洞身素描1';old.mkdir()
    (old/'数据.json').write_text('{}')
    _,audit=load_current_catalog({'reports':[]},tmp_path)
    assert audit['current_sketch_count']==1


def minimal():
    facts=[{'paragraph_id':'p1','locked_text':'已认可概况','support_ids':['D']},
           {'paragraph_id':'p2','support_ids':['W'],'windows':[{'range':['02:15:00','02:19:50'],
            'pattern':{'trend_fields':['advance_speed','thrust']},'trends':{'advance_speed':'increasing','thrust':'increasing'}}]},
           {'paragraph_id':'p3','support_ids':['P']},
           {'paragraph_id':'p4','support_ids':['G'],'day_range':['DyK1014+907','DyK1014+915']},
           {'paragraph_id':'p5','support_ids':['S'],'historical_range':['DyK1014+825','DyK1014+855'],'historical_report_date':'2024-01-23'},
           {'paragraph_id':'p6','support_ids':['A'],'ahead_range':['DyK1014+915','DyK1014+935']}]
    texts=['已认可概况','02:15:00至02:19:50推进速度、总推力同向上升。','局部表现不同。',
           'DyK1014+907至DyK1014+915缺少匹配预报。','历史资料预报日期2024-01-23，范围DyK1014+825至DyK1014+855。',
           'DyK1014+915至DyK1014+935缺少可用预报资料。']
    payload={'paragraphs':[{'paragraph_id':f['paragraph_id'],'text':t,'support_ids':f['support_ids']} for f,t in zip(facts,texts)]}
    return {'paragraphs':facts,'projected_fields':[]},payload


def test_valid_display_projection():
    data,payload=minimal();assert validate(payload,data)['passed']


def test_Chinese_date_and_ISO_date_have_equal_role_and_value():
    data,payload=minimal();payload['paragraphs'][4]['text']=payload['paragraphs'][4]['text'].replace('2024-01-23','2024年1月23日')
    assert validate(payload,data)['passed']
    payload['paragraphs'][4]['text']=payload['paragraphs'][4]['text'].replace('1月23日','1月24日')
    assert not validate(payload,data)['passed']


@pytest.mark.parametrize('index,change',[
    (0,lambda p:p.update(text='改变第一章')),
    (1,lambda p:p.update(text=p['text'].replace('上升','下降'))),
    (1,lambda p:p.update(text=p['text']+'（19条样本，支持约5分钟）')),
    (2,lambda p:p.update(text=p['text']+'扭矩999999kN·m。')),
    (3,lambda p:p.update(text=p['text']+'地质导致运行平稳。')),
    (4,lambda p:p.update(text=p['text'].replace('预报日期','观测日期为'))),
    (4,lambda p:p.update(support_ids=['INVENTED'])),
    (5,lambda p:p.update(text=p['text']+'没有风险。')),
    (3,lambda p:p.update(text=p['text']+'按名义里程。')),
])
def test_display_error_injections(index,change):
    data,payload=minimal();change(payload['paragraphs'][index])
    assert not validate(payload,data)['passed']


def test_wrong_original_row_or_quartile_rejected():
    import pandas as pd
    df=pd.DataFrame({'time':pd.to_datetime(['2024-01-24T02:15:00']), 'source_file':['x.csv'],'source_row':[4],
                     'advance_speed':[1.0],'thrust':[2.0],'torque':[3.0]})
    window={'source':[{'source_file':'x.csv','source_rows':[99]}],'sample_count':1}
    with pytest.raises(ValueError,match='row/time'):
        window_rows(df,window)


def test_JSON_photo_order_uses_user_confirmed_rule_with_original_SHA_anchors(tmp_path):
    from PIL import Image
    folder=tmp_path/'伯舒拉岭右线洞身素描'/'record';folder.mkdir(parents=True)
    raw_folder=tmp_path/'右线地质素描'/'record';raw_folder.mkdir(parents=True)
    labels=['左边墙','拱部','右边墙']
    for i,color in enumerate(['red','green','blue']):
        Image.new('RGB',(8,8),color).save(folder/f'图{i+3}.jpg')
        (raw_folder/f'{i}.jpg').write_bytes((folder/f'图{i+3}.jpg').read_bytes())
    (raw_folder/'1.jpg').write_text('<html>download failed</html>')
    (raw_folder/'original.html').write_text('<div id="content1">预报日期2024-01-23 DyK1014+825～DyK1014+855<table><tr><td>现场照片</td></tr><tr>'+''.join('<td>'+v+'</td>' for v in labels)+'</tr><tr>'+''.join(f'<td><img src="{i}.jpg"></td>' for i in range(3))+'</tr></table></div>')
    raw={'现场照片':[{v:f'https://example.invalid/{i}.jpg'} for i,v in enumerate(labels)]}
    p=folder/'数据.json';p.write_text(json.dumps(raw))
    h={'report':{'report_metadata':{'structured_source_path':str(p),'coverage_start_m':1014825.,'coverage_end_m':1014855.,'available_date':'2024-01-23'},'current_structured_sketch':raw}}
    result=photo_evidence(h,tmp_path)
    assert [r['role'] for r in result]==labels
    assert 'user_confirmed' in result[1]['role_verification']
    assert result[0]['sha256']==result[0]['original_HTML_asset_sha256']
    assert result[1]['sha256']!=result[1]['original_HTML_asset_sha256']
