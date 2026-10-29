"""Share only tables actually identical across all four reports; neutral blind view."""
from html.parser import HTMLParser
import html
from common import *
from adapters import group_folder


class Tables(HTMLParser):
    def __init__(self,text):
        super().__init__(convert_charrefs=True)
        self.depth=0;self.fragment=[];self.heading='';self.in_heading=False;self.heading_parts=[];self.tables=[]
        self.feed(text)
    def handle_starttag(self,tag,attrs):
        if tag in {'h2','h3'}:self.in_heading=True;self.heading_parts=[]
        if tag=='table':
            if not self.depth:self.fragment=[]
            self.depth+=1
        if self.depth:self.fragment.append(self.get_starttag_text())
    def handle_endtag(self,tag):
        if self.depth:self.fragment.append('</'+tag+'>')
        if tag=='table':
            self.depth-=1
            if not self.depth:self.tables.append({'heading':self.heading,'HTML':''.join(self.fragment)})
        if tag in {'h2','h3'}:
            self.in_heading=False;self.heading=''.join(self.heading_parts)
    def handle_data(self,data):
        if self.in_heading:self.heading_parts.append(data)
        if self.depth:self.fragment.append(html.escape(data))


def main():
    root=HERE/'human_blind';audit=[]
    for day in load(HERE/'SAMPLING.json')['human_dates']:
        table_sets=[];views={}
        for arm in 'ABCD':
            folder=group_folder('formal',day,arm);result=load(folder/'result.json')
            if not result.get('pdf'):
                table_sets.append(set());continue
            path=Path(result['pdf']).with_suffix('.html')
            parsed=Tables(path.read_text()).tables;keys={digest(t['HTML']) for t in parsed}
            table_sets.append(keys)
            for t in parsed:views[digest(t['HTML'])]=t
            audit.append({'date':day,'arm':arm,'table_count':len(parsed),'table_hashes':sorted(keys)})
        shared=set.intersection(*table_sets) if table_sets else set()
        body='<html lang="zh-CN"><meta charset="utf-8"><style>body{font:15px/1.7 system-ui;margin:25px}table{border-collapse:collapse;width:100%;margin:16px 0}th,td{padding:8px;border:1px solid #ccc;text-align:left}th{background:#eef2f0}</style><h1>'+day+'共同表格</h1><p>仅展示四份实际成品中都存在且内容一致的表格。证据中有但成品未展示的信息，不能自动算作报告覆盖。</p>'
        for key in sorted(shared):body+='<h2>'+html.escape(views[key]['heading'])+'</h2>'+views[key]['HTML']
        dest=root/'common_tables';dest.mkdir(exist_ok=True)
        (dest/(day+'.html')).write_text(body+'</html>',encoding='utf-8')
    csv_write(HERE/'COMMON_TABLE_IDENTITY_AUDIT.csv',audit)
    save(root/'COMMON_TABLES_STATUS.json',{'date_count':len(load(HERE/'SAMPLING.json')['human_dates']),
        'only_actual_four_arm_shared_tables_exported':True,'allocation_key_read':False,'human_labels_filled':False})


if __name__=='__main__':main()
