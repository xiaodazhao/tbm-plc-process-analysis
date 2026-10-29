"""Migrated reviewed pilot computations; orchestration is in pipeline.py."""
from __future__ import annotations
import importlib.metadata
from pathlib import Path
import re
import numpy as np
from .common import *
from .source_text import complete_measure, reference_condition, spatial_relation
from .knowledge import search, local_locations

class HybridReferences:
    def __init__(self, config, output, resources):
        self.resources = Path(resources)
        from sentence_transformers import SentenceTransformer
        policy = config['embedding']
        self.policy = policy
        self.seeds = load(self.resources/'measure_candidates.json')
        self.output = output
        self.model = SentenceTransformer(policy['model'], revision=policy['revision'], device=policy['device'],
                    cache_folder=str(ROOT/'.cache/local_embedding_models'), trust_remote_code=False)
        self.model.max_seq_length = policy['max_sequence_length']
        texts = [r['original_quote'] for r in self.seeds]
        cached=output/'local_reference_vectors.npz'
        if cached.exists():
            previous=load(output/'embedding_metadata.json')
            assert previous['model']==policy['model'] and previous['revision']==policy['revision']
            assert load(output/'reference_index_records.json')==self.seeds
            vectors=np.load(cached)['vectors']
        else:
            vectors = self.model.encode(texts, batch_size=policy['batch_size'], normalize_embeddings=True,
                                        show_progress_bar=False, convert_to_numpy=True)
        self.vectors = np.asarray(vectors,dtype=np.float32)
        assert np.isfinite(self.vectors).all() and np.allclose(np.linalg.norm(self.vectors,axis=1),1.,atol=1e-5)
        np.savez_compressed(output/'local_reference_vectors.npz',vectors=self.vectors)
        save(output/'reference_index_records.json',self.seeds)
        lengths = [len(self.model.tokenizer.encode(t,verbose=False)) for t in texts]
        save(output/'embedding_metadata.json',{'model':policy['model'],'revision':policy['revision'],
            'local_inference':True,'private_documents_sent_to_embedding_service':False,'rows':len(self.seeds),
            'dimensions':self.vectors.shape[1],'normalized':True,'query_prefix':policy['query_prefix'],
            'long_seed_count_over_model_limit':sum(n>self.model.max_seq_length for n in lengths),
            'long_seed_handling':'embedding seed may truncate; reference text later expanded from original PDF before use',
            'sentence_transformers_version':importlib.metadata.version('sentence-transformers'),
            'torch_version':importlib.metadata.version('torch')})

    def retrieve(self, query):
        vector = self.model.encode([self.policy['query_prefix']+query],normalize_embeddings=True,convert_to_numpy=True)[0]
        # Explicit finite float64 accumulation avoids platform BLAS floating-point warnings.
        assert np.isfinite(vector).all() and np.isclose(np.linalg.norm(vector),1.,atol=1e-5)
        similarities = np.sum(self.vectors.astype(np.float64)*np.asarray(vector,dtype=np.float64)[None,:],axis=1)
        assert np.isfinite(similarities).all() and np.max(np.abs(similarities))<=1.+1e-6
        np.save(self.output/(ident('QUERY',query).replace(':','_')+'.npy'),vector)
        order = np.argsort(-similarities,kind='stable')
        rank_dense = {self.seeds[i]['chunk_id']: n+1 for n,i in enumerate(order)}
        lexical = search(self.resources/'engineering_sources.sqlite',query,top_k=80)
        rank_lexical = {r['id']:i+1 for i,r in enumerate(lexical)}
        rrf = self.policy['rrf_constant']
        rows = []
        for seed,cosine in zip(self.seeds,similarities):
            cid = seed['chunk_id']
            dense_rank = rank_dense[cid]
            lexical_rank = rank_lexical.get(cid)
            score = 1./(rrf+dense_rank)+(1./(rrf+lexical_rank) if lexical_rank else 0.)
            rows.append({**seed,'cosine':float(cosine),'dense_rank':dense_rank,'lexical_rank':lexical_rank,
                         'hybrid_RRF_score':score,'retrieval_score_is_not_probability_or_applicability':True})
        rows.sort(key=lambda r:(-r['hybrid_RRF_score'],r['control_id']))
        return rows[:self.policy['candidate_count']]

def expanded_reference(seed):
    row = complete_measure(seed)
    lines = row['expanded_lines']
    # A retrieved line may sit inside an unnumbered prose section. Keep that section,
    # not a preceding temperature rule or a following earthquake chapter.
    start = next((i for i,line in enumerate(lines) if re.match(r'^\d+[、.．].*有害气体.*风险对策',line['text'])),None)
    if start is not None:
        end = next((i for i in range(start+1,len(lines)) if re.match(r'^\d+[、.．]',lines[i]['text'])),len(lines))
        lines = lines[start:end]
    quote = '\n'.join(line['text'] for line in lines)
    if '主要包括以下方面' in quote:
        # Quote only the complete principle sentences, never the dangling introduction.
        quote = quote.split('主要包括以下方面',1)[0].rstrip()
    row.update({'reference_quote':quote,'quoted_lines':lines,'source_condition':reference_condition(quote),
                'quote_extraction':'same_original_page_column_section_or_complete_principle_sentences',
                'all_conditions_confirmed_at_site':False})
    return row

def eligible_reference(row, context):
    text = compact(row['reference_quote'])
    reasons = []
    if not row['source_sha_verified']:reasons.append('source_SHA_mismatch')
    if not text.endswith(('。','；')):reasons.append('incomplete_clause')
    if re.search('运营通风|运营期间|运营使用',text):reasons.append('operation_not_construction')
    if '钻爆法' in text and 'TBM' not in text and not row.get('heading_context',''):
        reasons.append('drill_blast_only_not_TBM')
    locations = local_locations(text)
    if locations:
        bounds = context.get('scope_m')
        applicable = any(r['prefix']=='DyK' and bounds and (
            spatial_relation(bounds,[r['start_m'],r['end_m']])['matches'] if r['geometry']=='interval'
            else spatial_relation(bounds,point=r['point_m'])['matches']) for r in locations)
        if not applicable:reasons.append('special_location_not_applicable_to_context')
    if context['kind']=='gas':
        if not re.search('有害气体|有毒气体|二氧化硫|硫化氢|甲烷|瓦斯|二氧化碳',text):reasons.append('not_gas_context')
        if not re.search('通风|监测|检测|监（检）测',text):reasons.append('not_gas_management_reference')
        if '除尘' in text:reasons.append('dust_action_not_supported_by_gas_facts')
    else:
        if not re.search('破碎|软弱|节理|出水|富水',text):reasons.append('not_matching_ground_condition')
        if not re.search('支护|加固|探孔|注浆|预报',text):reasons.append('no_relevant_action')
        if re.search('高地温|热害|岩爆',text) and not re.search('高地温|热害|岩爆',context['query']):
            reasons.append('different_hazard_type')
    return reasons

def choose_references(retriever, context, output):
    hits = retriever.retrieve(context['query'])
    audit, accepted, seen = [], [], set()
    for rank,hit in enumerate(hits,1):
        row = expanded_reference(hit)
        reasons = eligible_reference(row,context)
        key = (row['source']['sha256'],row['source']['PDF_page'],compact(row['reference_quote']))
        if key in seen:reasons.append('duplicate_same_original_clause')
        if not reasons:seen.add(key)
        row.update({'hybrid_rank':rank,'rejection_reasons':reasons,'context_id':context['context_id']})
        audit.append(row)
        if not reasons and len(accepted)<retriever.policy['max_references_per_context']:
            accepted.append(row)
    save(output/(context['context_id']+'.retrieval.json'),{'context':context,'candidates':audit,'selected':accepted})
    csv_file(output/(context['context_id']+'.retrieval.csv'),[{
        'context_id':context['context_id'],'hybrid_rank':r['hybrid_rank'],'dense_rank':r['dense_rank'],
        'lexical_rank':r['lexical_rank'],'cosine':r['cosine'],'file':Path(r['source']['path']).name,
        'page':r['source']['PDF_page'],'reference_quote':r['reference_quote'],
        'rejection_reasons':r['rejection_reasons'],'selected':r['control_id'] in {v['control_id'] for v in accepted},
        'human_judgment':''} for r in audit])
    return accepted
