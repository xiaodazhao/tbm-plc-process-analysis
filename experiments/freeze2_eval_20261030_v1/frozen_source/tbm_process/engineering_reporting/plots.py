"""Migrated reviewed pilot computations; orchestration is in pipeline.py."""
from __future__ import annotations
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib import font_manager
import numpy as np
import pandas as pd
from .common import GASES
from .gas import truth, good_samples

def setup():
    names={f.name for f in font_manager.fontManager.ttflist}
    family=next((n for n in ['Arial Unicode MS','PingFang SC','Heiti TC','Noto Sans CJK SC'] if n in names),'DejaVu Sans')
    plt.rcParams.update({'font.family':family,'axes.unicode_minus':False,'font.size':9})

def separated(times,values,dt):
    # Insert explicit breaks without removing or interpolating either endpoint.
    ts=[];ys=[]
    for i,(t,y) in enumerate(zip(times,values)):
        if i>0 and dt.iloc[i-1]<=0:
            ts.append(t-pd.Timedelta(microseconds=1));ys.append(np.nan)
        ts.append(t);ys.append(y)
    return ts,ys

def daily(day,frame,dt,output,unit_label=None):
    setup();output.mkdir(parents=True,exist_ok=True)
    fig,axes=plt.subplots(6,1,figsize=(12,12),sharex=True)
    approved=frame.mapping_gas_status.eq('structural_and_step_supported_units_unconfirmed')
    for ax,field,color in zip(axes,GASES,['#237a68','#996c26','#456c9b','#915a8b','#a44949','#596d34']):
        raw=pd.to_numeric(frame[field],errors='coerce');_, valid=good_samples(frame, field)
        t,y=separated(frame.time,raw.where(valid),dt)
        ax.plot(t,y,lw=.8,color=color)
        rejected=raw.notna()&~valid
        if rejected.any():ax.scatter(frame.time[rejected],raw[rejected],s=4,color='#ad3434',label='质量排除的原始值');ax.legend(loc='upper right',fontsize=7)
        ax.set_ylabel(field+'\n'+(unit_label or '原始PLC值'));ax.grid(alpha=.2)
    axes[-1].xaxis.set_major_locator(mdates.HourLocator(interval=3));axes[-1].xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
    axes[-1].set_xlim(pd.Timestamp(day),pd.Timestamp(day)+pd.Timedelta(days=1))
    subtitle='六种气体PLC记录（ppm按用户指定研究口径）' if unit_label else '六种气体原始信号（单位待通道资料确认；非安全报警）'
    fig.suptitle(day+' '+subtitle,fontsize=13)
    fig.tight_layout(rect=(0,0,1,.975));path=output/(day+'.gas.png');fig.savefig(path,dpi=160);plt.close(fig)
    return str(path)
