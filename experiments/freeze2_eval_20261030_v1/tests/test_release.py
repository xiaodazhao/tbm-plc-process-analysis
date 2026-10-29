from copy import deepcopy
from pathlib import Path
import sys

HERE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE));import common
from tbm_process.engineering_reporting.source_text import scoped_geology_text
from tbm_process.engineering_reporting.writer import request,validate_text


def test_mixed_point_list_is_clipped_not_whole_clause_retained():
    quote='破碎岩体。DyK1017+374、+383位置附近存在掉块及卡机风险。'
    text,excluded=scoped_geology_text({'original_quote':quote},[1017374,1017381])
    assert '+374' in text and '+383' not in text and '掉块' in text and excluded
    text,_=scoped_geology_text({'original_quote':quote},[1017380,1017385])
    assert '+383' in text and '+374' not in text and 'DyK1017+383' in text


def test_no_local_point_is_invented_when_both_outside():
    text,excluded=scoped_geology_text({'original_quote':'板岩。DyK1017+374、+383位置掉块。'},[1017384,1017390])
    assert text=='板岩' and excluded


def test_real_full_scope_preserved():
    text,excluded=scoped_geology_text({'original_quote':'DyK1017+374、+383位置附近掉块。'},[1017370,1017390])
    assert '+374' in text and '+383' in text and not excluded


def test_interval_is_not_mistaken_for_two_independent_points():
    text,_=scoped_geology_text({'original_quote':'DyK1017+374至+387段岩体破碎。'},[1017380,1017385])
    assert '+374至+387' in text
