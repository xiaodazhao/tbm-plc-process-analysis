"""Migrated reviewed pilot computations; orchestration is in pipeline.py."""
from __future__ import annotations
import re

def row_points(record):
    bounds=record['range_m'];risk=record['risk_quote'];points=[];unresolved=[]
    start_km=int(bounds[0]//1000);end_km=int(bounds[1]//1000)
    for token in re.findall(r'\+(\d{1,3}(?:\.\d+)?)',risk):
        candidates=[km*1000+float(token) for km in range(start_km,end_km+1) if bounds[0]<=km*1000+float(token)<=bounds[1]]
        if len(candidates)!=1:
            unresolved.append({'token':token,'range_m':bounds,'reason':'no_unique_point_within_original_closed_range'});continue
        points.append(candidates[0])
    return points,unresolved
