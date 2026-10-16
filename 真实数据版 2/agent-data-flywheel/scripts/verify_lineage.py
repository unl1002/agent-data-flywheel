#!/usr/bin/env python3
"""Independently audit frozen statistics and their evidence (Python 3.9 stdlib).

No imports from analyze/operations/merge are used. Nothing is regenerated and no
input is written. Counts, distinct groups, issue events and measurements use
separate rules. Optional --out writes only this audit result.
"""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys

SOURCES = ('feedback', 'guardrail', 'dialogue', 'inference')
REF_FIELDS = ('member_refs', 'numerator_refs', 'denominator_refs', 'excluded_refs')
TZ = timezone(timedelta(hours=8))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def identifier(value):
    return isinstance(value, str) and bool(value.strip())


def month(value):
    if not numeric(value):
        return None
    try:
        date = datetime.fromtimestamp(value / 1000, TZ)
        return date.strftime('%Y-%m') if 2000 <= date.year <= 2100 else None
    except (ValueError, OverflowError, OSError):
        return None


def project(value):
    if not identifier(value) or value.strip().lower() in {'null','none','undefined','n/a','-'}:
        return None
    return value.strip()


def placeholder(value):
    return isinstance(value, str) and value.lower() in {'cloud_campus_project_id', 'default',
        'default-project', 'default_project', 'default_project_id', 'unknown', 'unknown-project',
        'unknown_project_id', 'placeholder', 'project_placeholder'}


def same(left, right):
    if numeric(left) and numeric(right):
        return math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-12)
    return left == right


class Audit:
    def __init__(self, analysis):
        self.a = analysis
        self.errors, self.warnings = [], []
        rows = analysis.get('records', [])
        self.records = {r['id']: r for r in rows if isinstance(r,dict) and identifier(r.get('id'))}
        self.raw = {r['ref']: r for r in analysis.get('source_rows', []) if isinstance(r,dict) and identifier(r.get('ref'))}
        self.traces = analysis.get('lineage', {})
        self.stats = {'records': len(rows), 'source_rows': len(analysis.get('source_rows', [])),
                      'analysis_traces': len(self.traces), 'review_traces': 0, 'checked_files': 0,
                      'checked_raw_rows': 0, 'reference_occurrences': 0}
        if len(self.records) != len(rows):
            self.error('record_id', 'records', 'Missing or duplicate record ID')
        if len(self.raw) != len(analysis.get('source_rows', [])):
            self.error('raw_ref', 'source_rows', 'Missing or duplicate raw ref')
        self.resolvable = set(self.records) | set(self.raw)
        for r in self.records.values():
            for kind, field in [('node','nodes'),('check','checks')]:
                for i, item in enumerate(r.get(field, [])):
                    if isinstance(item,dict):
                        self.resolvable.add(r['id']+'#'+kind+':'+str(item.get('original_index', i)))
            for e in r.get('evidence', []):
                match = re.fullmatch(r'responseData\.checkResults\[(\d+)\]', str(e.get('path','')))
                if match:
                    self.resolvable.add(r['id']+'#check:'+match.group(1))

    def error(self, code, where, message):
        self.errors.append({'code':code, 'location':where, 'message':message})

    def equal(self, code, where, actual, expected):
        if not same(actual, expected):
            self.error(code, where, 'Observed %r; expected %r' % (actual, expected))

    def members(self, tid, expected, field='member_refs', traces=None, code='membership'):
        traces = self.traces if traces is None else traces
        if tid in traces:
            actual = traces[tid].get(field, [])
            if Counter(actual) != Counter(expected):
                self.error(code, tid+'.'+field, 'Membership differs: actual=%d expected=%d, missing=%d extra=%d' % (
                    len(actual),len(expected),sum((Counter(expected)-Counter(actual)).values()),sum((Counter(actual)-Counter(expected)).values())))

    def trace_math(self, traces):
        for tid, t in traces.items():
            if not isinstance(t,dict):
                self.error('trace_shape',tid,'Trace must be an object');continue
            lists = {}
            for field in REF_FIELDS:
                refs=t.get(field,[])
                if not isinstance(refs,list):
                    self.error('trace_shape',tid+'.'+field,'References must be an array');refs=[]
                lists[field]=refs
                for ref in refs:
                    self.stats['reference_occurrences'] += 1
                    if not isinstance(ref,str) or ref not in self.resolvable:
                        self.error('unresolved_ref',tid+'.'+field,str(ref))
                if len(refs)!=len(set(refs)) and not (field=='member_refs' and t.get('entity_type')=='quality_issue'):
                    self.error('duplicate_ref',tid+'.'+field,'Repeated member is not an issue event')
            m,n,d,x=(lists[f] for f in REF_FIELDS)
            if set(m)&set(x):
                self.error('excluded_overlap',tid,'Members overlap exclusions')
            for child in t.get('input_trace_ids',[]):
                if child not in self.traces:
                    self.error('unresolved_trace',tid,str(child))
            value=t.get('value')
            if t.get('entity_type')=='document_aggregate' or self.a.get('provenance',{}).get('mode')=='document_snapshot':
                if not t.get('reason'):
                    self.error('snapshot_reason',tid,'Document aggregates need an explicit boundary')
                continue
            if t.get('entity_type')=='quality_issue':
                source=t.get('source')
                expected=[i for i,q in enumerate(self.a.get('quality_issues',[])) if q.get('source')==source and q.get('severity')=='error']
                mapping=t.get('issue_indexes',{})
                indices=[i for indexes in mapping.values() for i in indexes]+t.get('unlocated_issue_indexes',[])
                if Counter(indices)!=Counter(expected):
                    self.error('issue_indexes',tid,'Issue indices must cover each error event once')
                if Counter(m)!=Counter({ref:len(indexes) for ref,indexes in mapping.items()}):
                    self.error('issue_indexes',tid,'Repeated refs must match their issue event counts')
                for ref,indexes in mapping.items():
                    for index in indexes:
                        if not isinstance(index,int) or isinstance(index,bool) or not 0<=index<len(self.a.get('quality_issues',[])):
                            continue
                        issue=self.a['quality_issues'][index]
                        bound=issue.get('raw_ref') or issue.get('record_id')
                        if not bound and isinstance(issue.get('raw_locator'),dict):
                            bound='raw:'+str(source)+':'+str(issue['raw_locator'].get('index'))
                        if bound and bound!=ref:
                            self.error('issue_binding',tid,'Issue event points to a different record')
                self.equal('count_value',tid,value,len(expected))
            elif 'sample_values' in t:
                samples=t['sample_values']
                if set(samples)!=set(m) or Counter(d)!=Counter(m):
                    self.error('sample_members',tid,'All valid measurements must be listed and sortable')
                if any(not numeric(v) for v in samples.values()):
                    self.error('sample_value',tid,'Non-finite measurement');continue
                ranked=sorted(samples.items(),key=lambda pair:(pair[1],pair[0]))
                rank=math.ceil(len(ranked)*.95) if ranked else None
                selected=ranked[rank-1] if rank else (None,None)
                self.equal('p95_rank',tid,t.get('rank'),rank)
                self.equal('p95_ref',tid,t.get('selected_ref'),selected[0])
                self.equal('p95_value',tid,value,selected[1])
                field='cost_ms' if tid=='metric:dialogue_cost_p95_ms' else 'first_token_ms' if tid=='metric:first_token_p95_ms' else None
                if field:
                    for ref,v in samples.items():
                        self.equal('sample_binding',tid+':'+ref,v,self.records.get(ref,{}).get(field))
            elif 'group_values' in t:
                groups=t['group_values']
                if set(groups)!=set(m):
                    self.error('group_members',tid,'Distinct groups must bind every member')
                self.equal('count_value',tid,value,len({json.dumps(v,sort_keys=True) for v in groups.values()}))
                field='conversation_id' if t.get('entity_type')=='conversation' else 'project_id' if t.get('entity_type')=='project' else None
                if field:
                    for ref,v in groups.items():
                        self.equal('group_binding',tid+':'+ref,v,self.records.get(ref,{}).get(field))
            elif t.get('unit')=='ratio':
                if not set(n).issubset(d):
                    self.error('numerator_subset',tid,'Ratio numerator is outside denominator')
                if Counter(m)!=Counter(d):
                    self.error('ratio_members',tid,'Ratio members must equal denominator')
                self.equal('ratio_value',tid,value,len(n)/len(d) if d else None)
            elif value is not None and t.get('unit')=='count':
                self.equal('count_value',tid,value,len(m))
            if 'ratio' in t:
                self.equal('ratio_value',tid+'.ratio',t['ratio'],len(n)/len(d) if d else None)

    def aliases(self, node, traces, where):
        if isinstance(node,list):
            for i,item in enumerate(node):self.aliases(item,traces,where+'['+str(i)+']')
        elif isinstance(node,dict):
            if 'trace_id' in node:
                tid=node['trace_id']
                if tid not in traces:self.error('unresolved_trace',where,str(tid))
                else:
                    t=traces[tid]
                    if 'value' in node:self.equal('trace_value',where,node['value'],t.get('value'))
                    if 'unit' in node:self.equal('trace_unit',where,node['unit'],t.get('unit'))
                    for key,field in [('numerator','numerator_refs'),('denominator','denominator_refs')]:
                        if key in node:self.equal('trace_'+key,where,node[key],len(t.get(field,[])))
            for key,value in node.items():
                if isinstance(value,(dict,list)):self.aliases(value,traces,where+'.'+key)

    def source_members(self):
        for source,info in self.a.get('sources',{}).items():
            rows=[r for r in self.raw.values() if r.get('source')==source]
            retained=[r['ref'] for r in rows if r.get('log_retained') is True]
            expected={'raw_records':[r['ref'] for r in rows], 'records':retained,
                      'duplicates':[r['ref'] for r in rows if r.get('log_retained') is False]}
            for field,tid in info.get('trace_ids',{}).items():
                if tid not in self.traces:self.error('unresolved_trace','sources.'+source,str(tid));continue
                self.equal('trace_value',tid,self.traces[tid].get('value'),info.get(field))
                if field in expected:self.members(tid,expected[field])
        for ref,row in self.raw.items():
            index=row.get('index')
            if not isinstance(index,int) or isinstance(index,bool) or index<0 or ref!='raw:'+str(row.get('source'))+':'+str(index):
                self.error('raw_index',ref,'Raw ref and zero-based array index disagree')
            rid=row.get('retained_record_id')
            if rid:
                r=self.records.get(rid)
                if not r:self.error('retained_record',ref,'Retained normalized record is missing')
                elif r.get('source')!=row.get('source') or r.get('raw_locator',{}).get('index')!=index:
                    self.error('retained_record',ref,'Normalized record points to a different source row')
            duplicate=row.get('duplicate_of')
            if duplicate and (duplicate not in self.raw or not self.raw[duplicate].get('log_retained')):
                self.error('duplicate_binding',ref,'Duplicate winner is missing or was dropped')
        for rid,r in self.records.items():
            loc=r.get('raw_locator')
            if not isinstance(loc,dict):continue
            ref='raw:'+str(r.get('source'))+':'+str(loc.get('index'))
            row=self.raw.get(ref)
            if row is None:self.error('record_locator',rid,'No source_rows entry for normalized record')
            else:
                self.equal('record_locator',rid,loc.get('line'),row.get('line'))
                if r.get('source_ref') is not None:self.equal('record_locator',rid,r['source_ref'],ref)
                if r.get('raw_sha256') is not None:self.equal('record_hash',rid,r['raw_sha256'],row.get('sha256'))
                if row.get('retained_record_id')!=rid:self.error('retained_record',rid,'Source row does not bind this record')

    def semantic_members(self):
        groups={s:[r for r in self.records.values() if r.get('source')==s] for s in SOURCES}
        feedback,guards,dialogues,inferences=(groups[s] for s in SOURCES)
        ids=lambda rows:[r['id'] for r in rows]
        identified=[r for r in dialogues if identifier(r.get('chat_id'))]
        rated=[r for r in feedback if type(r.get('feedback')) is int and r['feedback'] in (-1,3)]
        specs={s+'_records':(ids(groups[s]),None) for s in ('feedback','guardrail','inference')}
        specs.update(dialogue_records=(ids(identified),None),
            dialogue_missing_id=(ids([r for r in dialogues if not identifier(r.get('chat_id'))]),None),
            rated_feedback=(ids(rated),None),
            conversation_count=(ids([r for r in dialogues if identifier(r.get('conversation_id'))]),None),
            answer_nodes=([r['id']+'#node:'+str(n.get('original_index',i)) for r in dialogues for i,n in enumerate(r.get('nodes',[])) if isinstance(n,dict)],None),
            negative_feedback_rate=(ids([r for r in rated if r['feedback']==-1]),ids(rated)),
            reason_coverage=(ids([r for r in feedback if r.get('has_reason')]),ids(feedback)),
            scene_missing_rate=(ids([r for r in identified if r.get('scene') is None]),ids(identified)),
            end_marker_coverage=(ids([r for r in identified if any(n.get('status')=='END' for n in r.get('nodes',[]) if isinstance(n,dict))]),ids(identified)),
            inference_http_200_rate=(ids([r for r in inferences if r.get('response_status')==200]),ids(inferences)),
            first_token_zero_records=(ids([r for r in inferences if r.get('first_token_ms')==0]),None))
        checks=[(r,c,i) for r in guards for i,c in enumerate(r.get('checks',[])) if isinstance(c,dict) and type(c.get('is_legal')) is bool]
        checkref=lambda r,c,i:r['id']+'#check:'+str(c.get('original_index',i))
        blocked={r['id'] for r,c,i in checks if not c['is_legal']}
        valid={r['id'] for r,c,i in checks}
        specs['guardrail_block_rate']=([r['id'] for r in guards if r['id'] in blocked],[r['id'] for r in guards if r['id'] in valid])
        specs['guardrail_question_block_rate']=([checkref(r,c,i) for r,c,i in checks if not c['is_legal']],[checkref(r,c,i) for r,c,i in checks])
        comparable=[r for r in inferences if r.get('requested_model') and r.get('actual_models')]
        specs['model_route_difference_rate']=(ids([r for r in comparable if any(m!=r['requested_model'] for m in r['actual_models'])]),ids(comparable))
        for key,rows,field,valid in [('dialogue_cost_p95_ms',dialogues,'cost_ms',lambda r:numeric(r.get('cost_ms')) and r['cost_ms']>=0),
                                   ('first_token_p95_ms',inferences,'first_token_ms',lambda r:r.get('stream') is True and numeric(r.get('first_token_ms')) and r['first_token_ms']>0)]:
            specs[key]=(ids([r for r in rows if valid(r)]),None)
        joined=all(self.a.get('sources',{}).get(s,{}).get('status')=='imported' for s in ('feedback','dialogue'))
        eligible=[r for r in feedback if identifier(r.get('chat_id'))]
        matched=[r for r in eligible if r.get('linked_dialogue_id') in set(ids(identified))]
        for r in matched:
            target=self.records[r['linked_dialogue_id']]
            self.equal('join_binding',r['id'],r.get('chat_id'),target.get('chat_id'))
        specs['feedback_missing_chat_id']=(ids([r for r in feedback if not identifier(r.get('chat_id'))]),None)
        if joined:
            specs.update(feedback_match_count=(ids(matched),None),feedback_link_rate=(ids(matched),ids(eligible)),
                dialogue_feedback_coverage=(ids([r for r in identified if r['id'] in {f['linked_dialogue_id'] for f in matched}]),ids(identified)),
                orphan_feedback=(ids([r for r in eligible if r not in matched]),None))
        for key,(members,denominator) in specs.items():
            tid='metric:'+key
            self.members(tid,denominator if denominator is not None else members)
            if denominator is not None:
                self.members(tid,members,'numerator_refs');self.members(tid,denominator,'denominator_refs')
        scalar=lambda v:v if isinstance(v,str) else '' if v is None else json.dumps(v,sort_keys=True,ensure_ascii=False,separators=(',',':'))
        for group,rows in self.a.get('distributions',{}).items():
            for row in rows:
                name=scalar(row.get('name'));tid=row.get('trace_id')
                if group=='scenes':selected=[r for r in identified if (r.get('scene') or '无场景')==name]
                elif group=='recipe_dialogue_coverage':selected=[r for r in identified if any(n.get('recipe')==name for n in r.get('nodes',[]) if isinstance(n,dict))]
                elif group in ('request_models','http_status','response_formats'):
                    field={'request_models':'requested_model','http_status':'response_status','response_formats':'response_format'}[group]
                    selected=[r for r in inferences if (scalar(r.get(field)) or '未知')==name]
                else:continue
                self.members(tid,ids(selected))
        usage=self.a.get('usage',{})
        business=identified
        for row in usage.get('source_months',[]):
            selected=[r['ref'] for r in self.raw.values() if r.get('source')==row['source'] and r.get('log_retained') is True and month(r.get('time'))==row['month']]
            self.members(row['trace_id'],selected)
        for row in usage.get('project_rows',[]):
            selected=[r for r in business if project(r.get('project_id'))==row.get('project_id')]
            self.members(row['total'],ids(selected))
            for m,tid in row.get('months',{}).items():self.members(tid,ids([r for r in selected if month(r.get('time'))==m]))
        known=lambda r:project(r.get('project_id')) is not None and not placeholder(project(r.get('project_id')))
        for key,tid in usage.get('metrics',{}).items():
            if key=='active_projects':self.members(tid,ids([r for r in business if known(r)]))
            elif key=='active_sessions':self.members(tid,ids([r for r in business if identifier(r.get('conversation_id'))]))
            elif key=='user_count' and self.traces.get(tid,{}).get('value') is not None:
                self.error('unsupported_user_count',tid,'No confirmed stable user identity field is specified')
        for m,metrics in usage.get('monthly_metrics',{}).items():
            selected=[r for r in business if month(r.get('time'))==m]
            for key,tid in metrics.items():
                rows=selected if key=='business_questions' else [r for r in selected if known(r)] if key=='active_projects' else [r for r in selected if identifier(r.get('conversation_id'))]
                self.members(tid,ids(rows))

    def files(self):
        for source,spec in self.a.get('provenance',{}).get('files',{}).items():
            path=Path(spec.get('path',''))
            try:data=path.read_bytes();content=data.decode('utf-8-sig')
            except (OSError,UnicodeError) as exc:self.error('source_read',source,str(exc));continue
            self.stats['checked_files']+=1
            self.equal('source_hash',source,hashlib.sha256(data).hexdigest(),spec.get('sha256'))
            self.equal('source_bytes',source,len(data),spec.get('bytes'))
            self.equal('source_name',source,path.name,spec.get('name'))
            # Stream array elements from the text; do not allocate a second full
            # decoded source array. Newline counting never rescans earlier text.
            decoder=json.JSONDecoder();cursor=0
            while cursor<len(content) and content[cursor].isspace():cursor+=1
            if cursor>=len(content) or content[cursor]!='[':
                self.error('source_array',source,'Source is not an array');continue
            cursor+=1;index=0;line_cursor=0;line=1;rawrows={r.get('index'):r for r in self.raw.values() if r.get('source')==source}
            try:
                while True:
                    while cursor<len(content) and content[cursor].isspace():cursor+=1
                    if cursor<len(content) and content[cursor]==']':break
                    if index:
                        if content[cursor]!=',':raise ValueError('Missing array comma')
                        cursor+=1
                        while cursor<len(content) and content[cursor].isspace():cursor+=1
                    line+=content.count('\n',line_cursor,cursor);line_cursor=cursor
                    raw,cursor=decoder.raw_decode(content,cursor)
                    ref='raw:'+source+':'+str(index);row=rawrows.get(index)
                    if row is None:self.error('raw_index',ref,'Source object has no indexed source_rows entry')
                    else:
                        self.equal('raw_line',ref,row.get('line'),line)
                        self.equal('row_hash',ref,row.get('sha256'),digest(raw))
                        rid=row.get('retained_record_id');r=self.records.get(rid)
                        if r:
                            self.equal('record_path',rid,r.get('raw_locator',{}).get('path'),str(path.resolve()))
                        self.raw_facts(source,ref,row,raw,r)
                    self.stats['checked_raw_rows']+=1;index+=1
                if content[cursor+1:].strip():raise ValueError('Trailing data after array')
            except (ValueError,IndexError) as exc:self.error('source_array',source,str(exc))
            self.equal('source_record_count',source,index,spec.get('record_count'))
            if set(rawrows)!=set(range(index)):
                self.error('raw_index',source,'source_rows indices do not cover exactly the source array')

    def raw_facts(self,source,ref,row,raw,r):
        if not isinstance(raw,dict):return
        event=raw.get('createTime') if source=='feedback' else raw.get('updateTime')
        detail=None
        if source=='dialogue':
            try:
                body=raw.get('body',{});body=json.loads(body) if isinstance(body,str) else body
                detail=body.get('detail',{});detail=json.loads(detail) if isinstance(detail,str) else detail
                event=next((detail[k] for k in ('askTime','questionTime') if numeric(detail.get(k))),event)
            except (ValueError,AttributeError,TypeError):pass
        self.equal('raw_time',ref,row.get('time'),event)
        if r:
            self.equal('record_time',r['id'],r.get('time'),event)
            for field,rawfield in [('project_id','projectId'),('user_type','userType'),('tenant_id','tenantId')]:
                if field in r:self.equal('record_field',r['id']+'.'+field,r[field],raw.get(rawfield))
            if detail is not None and isinstance(detail,dict):
                self.equal('detail_id',r['id'],r.get('chat_id'),detail.get('id'))

    def review(self,review):
        traces=review.get('lineage',{});self.stats['review_traces']=len(traces)
        self.equal('analysis_fingerprint','review',review.get('analysis_fingerprint'),digest(self.a))
        caps=review.get('capabilities',{})
        self.equal('capabilities_fingerprint','review',review.get('capabilities_fingerprint'),digest(caps))
        items=review.get('items',[]);itemmap={i.get('record_id'):i for i in items}
        if len(itemmap)!=len(items):self.error('review_duplicate','review','Repeated reviewed record')
        for item in items:
            rid=item.get('record_id');r=self.records.get(rid)
            if not r:self.error('review_record','review',str(rid));continue
            evidence={e.get('id') for e in r.get('evidence',[])}
            for field in ('evidence_refs','response_evidence_refs'):
                for ref in item.get(field,[]):
                    if ref not in evidence:self.error('review_evidence',rid+'.'+field,str(ref))
            if item.get('response_support') in ('supported','unsupported','partial') and not item.get('response_evidence_refs'):
                self.error('review_evidence',rid,'Determined response requires evidence')
        questions={r['id'] for r in self.records.values() if str(r.get('question','')).strip() and (r.get('source')=='dialogue' or (r.get('source')=='feedback' and not r.get('linked_dialogue_id')))}
        if review.get('scope')=='all_question_records' and not questions.issubset(itemmap):
            self.error('review_scope','review','Full-question scope has unreviewed questions')
        self.trace_math(traces)
        self.aliases({k:v for k,v in review.items() if k in ('metrics','monthly','primary_metrics','by_source_metrics','primary_monthly','primary_distributions')},traces,'review')
        for tid in traces:
            if ':distribution:' in tid:continue
            key=tid.rsplit(':',1)[-1]
            selected=items
            m=re.search(r':monthly:([^:]+):',tid)
            if m:selected=[i for i in selected if (month(self.records.get(i['record_id'],{}).get('time')) or 'unknown')==m.group(1)]
            if tid.startswith('review:primary:'):selected=[i for i in selected if self.records.get(i['record_id'],{}).get('source')=='dialogue']
            m=re.search(r':source:([^:]+):',tid)
            if m:selected=[i for i in selected if self.records.get(i['record_id'],{}).get('source')==m.group(1)]
            valid=[i for i in selected if i.get('validity')=='valid']
            judged=[i for i in valid if i.get('response_support') in ('supported','unsupported','partial')]
            evaluated=[i for i in selected if i.get('correctness') in ('correct','incorrect') and i.get('evaluation_refs')]
            refs=lambda rows:[i['record_id'] for i in rows]
            states={
                'reviewed_records':selected,'valid_questions':valid,
                'unsupported_data_queries':[i for i in valid if 'data_query' in i.get('intent',[]) and i.get('support')=='unsupported'],
                'partial_data_queries':[i for i in valid if 'data_query' in i.get('intent',[]) and i.get('support')=='partial'],
                'confirmed_badcases':[i for i in selected if i.get('badcase')=='confirmed'],
                'candidate_badcases':[i for i in selected if i.get('badcase')=='candidate'],
                'value_questions':[i for i in valid if i.get('value_level') in ('high','medium')],
                'response_unknown':[i for i in valid if i.get('response_support') not in ('supported','unsupported','partial')],
                **{'response_'+state:[i for i in valid if i.get('response_support')==state] for state in ('supported','unsupported','partial')}}
            ratios={'valid_question_rate':(valid,selected),'response_support_rate':(states['response_supported'],valid),
                'response_judged_support_rate':(states['response_supported'],judged),
                'accuracy':([i for i in evaluated if i.get('correctness')=='correct'],evaluated)}
            if key in states:self.members(tid,refs(states[key]),traces=traces,code='review_membership')
            elif key in ratios:
                n,d=ratios[key]
                for field,rows in [('member_refs',d),('numerator_refs',n),('denominator_refs',d)]:
                    self.members(tid,refs(rows),field,traces,code='review_membership')
        for group,rows in review.get('primary_distributions',{}).items():
            for row in rows:
                selected=[i for i in items if self.records.get(i['record_id'],{}).get('source')=='dialogue' and i.get('validity')=='valid']
                if group in ('unsupported_intents','unsupported_causes','unsupported_reasons'):
                    selected=[i for i in selected if i.get('response_support') in ('unsupported','partial')]
                    if group=='unsupported_intents':
                        selected=[i for i in selected if row['name'] in i.get('intent',[])]
                    else:
                        field='response_gap_type' if group=='unsupported_reasons' else 'root_cause'
                        selected=[i for i in selected if (i.get(field) or 'unknown')==row['name']]
                else:selected=[i for i in selected if i.get('value_level') in ('high','medium') and (i.get('opportunity_type') or 'unknown')==row['name']]
                self.members(row['trace_id'],[i['record_id'] for i in selected],traces=traces,code='review_membership')
        coverage=review.get('coverage')
        if coverage:
            self.equal('review_coverage','review',coverage.get('reviewed'),len(items))
            self.equal('review_coverage','review',coverage.get('available'),len(self.records))
            self.equal('review_coverage','review',coverage.get('ratio'),len(items)/len(self.records) if self.records else None)


def verify(analysis, review=None, check_sources=True):
    """Return errors, boundaries and counts without mutating either input."""
    audit=Audit(analysis)
    audit.trace_math(audit.traces)
    audit.aliases(analysis.get('metrics',{}),audit.traces,'metrics')
    audit.aliases(analysis.get('distributions',{}),audit.traces,'distributions')
    audit.source_members()
    if analysis.get('provenance',{}).get('mode')!='document_snapshot':audit.semantic_members()
    if check_sources:audit.files()
    else:audit.warnings.append('Source-file bytes, original row hashes and line locations were not checked.')
    if review is not None:audit.review(review)
    return {'ok':not audit.errors,'errors':audit.errors,'warnings':audit.warnings,'stats':audit.stats,
        'limits':['Checks deterministic arithmetic, member selection, fingerprints and recorded evidence references.',
                  'Does not certify AI semantic judgments or business truth without external evaluation labels.']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--analysis',required=True,type=Path)
    parser.add_argument('--report',type=Path,help='Optional completed reviewed_analysis.json')
    parser.add_argument('--out',type=Path,help='Audit JSON only; analysis and report are never written')
    parser.add_argument('--skip-source-files',action='store_true')
    args=parser.parse_args()
    try:
        analysis=json.loads(args.analysis.read_text(encoding='utf-8-sig'))
        report=json.loads(args.report.read_text(encoding='utf-8-sig')) if args.report else None
        review=report.get('review',report) if report is not None else None
        result=verify(analysis,review,not args.skip_source_files)
        if report and 'analysis' in report and digest(report['analysis'])!=digest(analysis):
            result['errors'].append({'code':'report_analysis','location':'report.analysis','message':'Report carries a different analysis'})
            result['ok']=False
    except (OSError,ValueError,KeyError,TypeError) as exc:
        result={'ok':False,'errors':[{'code':'input_error','location':'input','message':str(exc)}],'warnings':[],'stats':{}}
    output=json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
    if args.out:args.out.write_text(output,encoding='utf-8')
    print(output,end='')
    return 0 if result['ok'] else 1


if __name__=='__main__':
    sys.exit(main())
