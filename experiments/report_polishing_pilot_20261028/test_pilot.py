"""Synthetic interface fixtures only; not used to create either real report."""
from copy import deepcopy

import fitz
import pytest

from tests.test_three_chapter_report import fixture
from .pilot import SECTIONS,artifact_check,validate_layout,validate_payload


def case():
    pack,plan,draft=fixture()
    return {'day':'2024-04-26','pack':pack,'plan':plan,'draft':draft,'assets':{},
        'blocks':[{'paragraph_id':'p1','section':'1'},{'paragraph_id':'p2','section':'2.1'},
                  {'paragraph_id':'p3','section':'3.1'}]}


def template():
    parts={'1':'{{claim:p1}}','2.1':'{{claim:p2}}','3.1':'{{claim:p3}}'}
    body='{{status}}'+''.join('<section data-section="'+s+'">'+parts.get(s,'')+'</section>' for s in SECTIONS)+'{{scope}}'
    return '<!doctype html><html><head><style>body{font-size:10pt}</style></head><body>'+body+'</body></html>'


def payload():
    return {'paragraphs':[{'paragraph_id':'p'+str(i),'text':'原报告建议Ⅳ级'} for i in range(1,4)],'html_template':template()}


def test_valid_layout_and_immutable_metadata():
    original=case();before=deepcopy(original);candidate,result=validate_payload(payload(),original)
    assert result['passed'] and original==before
    assert candidate==original['draft']


@pytest.mark.parametrize('bad',[
    lambda s:s.replace('{{claim:p1}}',''),
    lambda s:s.replace('{{claim:p1}}','{{claim:p1}}{{claim:p1}}'),
    lambda s:s.replace('{{claim:p1}}','{{claim:p9}}'),
    lambda s:s.replace('{{claim:p1}}','设备故障。{{claim:p1}}'),
    lambda s:s.replace('font-size:10pt','display:none'),
    lambda s:s.replace('font-size:10pt','background:url(https://example.com/a)'),
    lambda s:s.replace('</body>','<script>alert(1)</script></body>'),
    lambda s:s.replace('</body>','<img src="file:///etc/passwd"></body>'),
    lambda s:s.replace('{{claim:p1}}','').replace('{{claim:p2}}','{{claim:p2}}{{claim:p1}}'),
    lambda s:s.replace('<body>','<body style="opacity:0">'),
    lambda s:s.replace('font-size:10pt','content:"新增地质结论"'),
])
def test_invalid_or_hidden_layout_rejected(bad):
    assert validate_layout(bad(template()),case())


def test_wrong_grade_still_rejected_after_polishing():
    data=payload();data['paragraphs'][2]['text']='原报告建议Ⅲ级'
    assert not validate_payload(data,case())[1]['passed']


@pytest.mark.parametrize('paragraphs',[[],['bad'],[{'paragraph_id':'p1','text':'x'}]*3])
def test_missing_malformed_or_duplicate_paragraphs_rejected(paragraphs):
    data=payload();data['paragraphs']=paragraphs
    assert not validate_payload(data,case())[1]['passed']


@pytest.mark.parametrize('size,expected',[(7,False),(10.5,True)])
def test_actual_PDF_print_size_catches_page_scaling(tmp_path,size,expected):
    path=tmp_path/'synthetic_layout_fixture.pdf';document=fitz.open();page=document.new_page()
    page.insert_text((60,80),'Synthetic layout fixture only.',fontsize=size);document.save(path);document.close()
    candidate={'chapters':[{'claims':[{'paragraph_id':'fixture','text':'Synthetic layout fixture only.'}]}]}
    result=artifact_check(path,candidate,{'assets':{}},tmp_path)
    assert result['passed'] is expected
