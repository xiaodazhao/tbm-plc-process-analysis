"""Bounded typed claim checks. Unparsed prose remains REVIEW, never certified true."""
from decimal import Decimal
import re
import unicodedata

VERSION='typed_claim_checks_candidate2'
ALIASES={'advance_speed':['推进速度','掘进速度'],'thrust':['总推力','推进力','推力'],
         'torque':['刀盘扭矩'],'rpm_canonical':['刀盘转速','实际转速']}
LABEL=re.compile('|'.join(sorted((x for v in ALIASES.values() for x in v),key=len,reverse=True)))
FIELDS={label:field for field,labels in ALIASES.items() for label in labels}
NUM=r'[+−-]?\d+(?:\.\d+)?'
UNIT=r'毫米/分钟|毫米/分|米/分钟|米/分|mm/min|m/min|kN[·⋅.]m|kNm|N[·⋅.]m|kN|r/min|转/分钟|转/分|%'
QUANTITY=re.compile(r'(?P<value>'+NUM+r')\s*(?P<unit>'+UNIT+r')')
RANGE=re.compile(r'P10\s*(?:至|～|~|—|-)\s*P90\s*(?:为|是|：|:)?\s*('+NUM+r')\s*(?:至|～|~|—|-)\s*('+NUM+r')\s*('+UNIT+r')',re.I)
ROLES=re.compile('中位数|P10|P90|均值|平均值|最大值|最小值|峰值',re.I)
UNITS={'mm/min':('speed',Decimal(1)),'毫米/分钟':('speed',Decimal(1)),
       '毫米/分':('speed',Decimal(1)),'m/min':('speed',Decimal(1000)),
       '米/分钟':('speed',Decimal(1000)),'米/分':('speed',Decimal(1000)),
       'kN':('force',Decimal(1)),'kN·m':('torque',Decimal(1)),
       'kN.m':('torque',Decimal(1)),'kN⋅m':('torque',Decimal(1)),'kNm':('torque',Decimal(1)),
       'N·m':('torque',Decimal('.001')),'N.m':('torque',Decimal('.001')),
       'r/min':('rotation',Decimal(1)),'转/分钟':('rotation',Decimal(1)),
       '转/分':('rotation',Decimal(1)),'%':('percent',Decimal(1))}
ROLE_NAMES={'中位数':'median','p10':'p10','p90':'p90','均值':'mean','平均值':'mean',
            '最大值':'maximum','峰值':'maximum','最小值':'minimum'}


def normalized(text):
    return unicodedata.normalize('NFKC',str(text)).replace('−','-')


def grades(text):
    text=normalized(text)
    return set(re.findall(r'(?<![A-Za-z])(VI|IV|III|II|V|I)\s*级',text)) | set(
        re.findall(r'(?:围岩等级|建议等级|设计等级|围岩分级)[：:\s]*(VI|IV|III|II|V|I)(?![A-Za-z])',text))


def positive_assertions(text,pattern):
    """Negation belongs to the local clause; a prior clause cannot negate a later one."""
    result=[]
    for clause in re.split('[。；，,\n]',text):
        for m in re.finditer(pattern,clause):
            prefix=clause[max(0,m.start()-14):m.start()]
            if re.search(r'(?:未|尚未|并未|没有|并非|不是|不能|不得|不应|不表示|不代表|不保证|不记录|不作).{0,12}$',prefix):
                continue
            result.append(m.group())
    return result


def quantities(text):
    result=[]
    for clause in re.split('[。；\n]',normalized(text)):
        labels=list(LABEL.finditer(clause))
        if len(labels)>1 and '分别' in clause:
            continue
        for n,label in enumerate(labels):
            field=FIELDS[label.group()]
            block=clause[label.end():labels[n+1].start() if n+1<len(labels) else len(clause)]
            ranges=list(RANGE.finditer(block))
            for r in ranges:
                for role,value in [('p10',r[1]),('p90',r[2])]:
                    result.append({'field':field,'role':role,'value':Decimal(value),'unit':r[3],'clause':clause})
            for q in QUANTITY.finditer(block):
                if any(r.start()<=q.start()<r.end() for r in ranges):continue
                prefix=block[:q.start()];roles=list(ROLES.finditer(prefix))
                role=ROLE_NAMES[roles[-1].group().lower()] if roles and q.start()-roles[-1].end()<18 else None
                # A long intervening clause or unlabeled list is not a reliably parsed attachment.
                if q.start()>55 and not role:continue
                value=Decimal(q['value'])
                if q['unit']=='%' and not q['value'].startswith(('+','-')) and re.search(r'(?:下降|降低|减少|下调)[^，,。；]{0,8}$',prefix):
                    value=-value
                result.append({'field':field,'role':role,'value':value,
                    'unit':q['unit'],'clause':clause})
    return result


def _base(q):
    dimension,factor=UNITS[q['unit']]
    return dimension,q['value']*factor


def _durations(text):
    roles={'累计推进':'advancing','推进时间':'advancing','有效状态支持':'observed',
           '有效状态记录':'observed','未覆盖':'unobserved','设备停止':'stopped','停机':'stopped',
           '非推进':'nonadvancing','总跨度':'elapsed','时间跨度':'elapsed'}
    out=[]
    pattern=re.compile('('+'|'.join(roles)+r')(?:时长|时间)?(?:为|约|共|：|:)?\s*((?:\d+小时)?(?:\d+分)?(?:\d+秒)?)')
    for m in pattern.finditer(text):
        if not m[2]:continue
        values={unit:int(n) for n,unit in re.findall(r'(\d+)(小时|分|秒)',m[2])}
        out.append((roles[m[1]],values.get('小时',0)*3600+values.get('分',0)*60+values.get('秒',0)))
    return out


def check(text,facts):
    errors=[];reviews=[];checks=[]
    evidence='\n'.join(f['canonical_text'] for f in facts)
    expected=quantities(evidence)
    for q in quantities(text):
        candidates=[e for e in expected if e['field']==q['field'] and (q['role'] is None or e['role']==q['role'])]
        if not candidates:
            reviews.append('quantity_without_parsed_field_role_evidence:'+q['field']);continue
        target_dimension,value=_base(q)
        matches=[e for e in candidates if _base(e)[0]==target_dimension and abs(_base(e)[1]-value)<=Decimal('.00001')]
        if not matches:
            errors.append('typed_quantity_mismatch:'+q['field']+':'+str(q['role'])+':'+str(q['value'])+q['unit'])
        elif q['role'] is None:
            reviews.append('quantity_scope_or_statistic_role_requires_review:'+q['field'])
        checks.append({'field':q['field'],'statistic_role':q['role'],'value':str(q['value']),'unit':q['unit'],
            'matched':bool(matches),'scope_binding':'bound_paragraph_evidence_not_full_semantic_scope_resolution'})
    expected_durations=set(_durations(evidence))
    for role,seconds in _durations(text):
        if (role,seconds) not in expected_durations:
            if any(t==seconds for _,t in expected_durations):
                errors.append('duration_role_mismatch:'+role)
            else:reviews.append('duration_not_resolved_by_typed_parser:'+role)
    pages={s.get('PDF_page',s.get('page')) for f in facts for s in f.get('sources',[]) if s.get('PDF_page',s.get('page')) is not None}
    for p in re.findall(r'(?:原)?PDF第\s*(\d+)\s*页',text,re.I):
        if pages and int(p) not in pages:errors.append('PDF_page_not_in_bound_sources:'+p)
        elif not pages:reviews.append('PDF_page_not_verifiable_from_bound_sources:'+p)
    chain=re.compile(r'(DyK|DK)(\d+)\+(\d+(?:\.\d+)?)至(?:DyK|DK)?(?:(\d+)\+)?(\d+(?:\.\d+)?)')
    for m in chain.finditer(text):
        lo=int(m[2])*1000+float(m[3]);hi=int(m[4] or m[2])*1000+float(m[5])
        if lo>hi:errors.append('reversed_chainage_interval')
    if any(f['role']=='prior_forecast' for f in facts):
        if re.search('前(?:一)?段.{0,12}(?:程序切分|PLC统计子区段)|(?:程序切分|PLC统计子区段).{0,10}前(?:一)?',text):
            errors.append('original_forecast_comparator_rebound_to_PLC')
        if positive_assertions(text,r'现场证实|已揭露|现场确认|当日揭示|实际已经揭露'):
            errors.append('forecast_promoted_to_observation')
    for assertion in positive_assertions(text,r'已完成.{0,8}(?:支护|处置)|(?:轴承|电机|设备).{0,6}(?:故障|损坏)|刀具.{0,8}(?:磨损)|(?:可保证|绝对安全)'):
        if assertion not in evidence:errors.append('unsupported_added_assertion:'+assertion)
    if re.search('前一PLC统计|前一统计子区段',text) and any(f['role']=='prior_forecast' for f in facts):
        errors.append('original_forecast_comparator_rebound_to_PLC')
    if facts and re.fullmatch(r'相关资料已收集[，,]详细内容见来源[。.]?',text.strip()):
        reviews.append('bound_ID_without_substantive_content_coverage_unverified')
    clauses=[c.strip() for c in re.split('[。；\n]',text) if c.strip()]
    reviews.append('unverified_atomic_content_and_scope_REQUIRE_REVIEW')
    return {'version':VERSION,'errors':sorted(set(errors)),'reviews':sorted(set(reviews)),
        'typed_quantity_checks':checks,'unconfirmed_clauses':clauses,
        'semantic_status':'REVIEW','not_engineering_fact_accuracy':True}
