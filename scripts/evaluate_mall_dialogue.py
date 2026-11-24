"""真实模型连续对话评测：在现有商城的回滚事务中核对状态与生成答复。"""

import argparse
from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
from time import perf_counter
from unittest.mock import patch
from psycopg.conninfo import make_conninfo

from app.mall.agent import deepseek_mall_planner
from app.mall.read_loop import evidence_decider, business_decider
from app.mall.grounded_reply import grounded_reviewer, grounded_writer
from app.mall.rules import MallQuestion
from app.policies.mall_retrieval import MallRetrieval
from app.settings import ROOT, local_setting
from app.storage.postgres_mall import PostgresMall


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--retrieval', action='store_true', help='实际运行 Milvus 与 E5')
    parser.add_argument('--audit',action='store_true',help='增加独立的答复相关性、事实和任务一致性模型审阅')
    parser.add_argument('--window-rerank',action='store_true',help='使用当前生产的保留融合名次窗口排序')
    parser.add_argument('--persistent-dense',action='store_true',help='使用已获授权的持久化双路新集合')
    parser.add_argument('--acceptance-db', action='store_true', help='在独立验收库执行，不导出连接信息')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--cases', type=Path, default=ROOT/'tests/evaluation_cases/semantic_dialogues.json')
    parser.add_argument('--all', action='store_true', help='复跑开发组与留出组全部66轮')
    parser.add_argument('--task-memory',action='store_true',help='追加25轮数量、多问题和凭证状态检查')
    args = parser.parse_args()
    key = local_setting('DEEPSEEK_API_KEY')
    if not key:
        raise SystemExit('需要配置 DEEPSEEK_API_KEY')
    database=local_setting('ORDER_DATABASE_URL')
    if args.acceptance_db:
        database=make_conninfo(database,dbname='after_sales_v3_release_20261122')
    calls=[]
    planner=deepseek_mall_planner(key,semantic=True)
    def traced_planner(question,*,dialogue=None):
        planned=planner(question,dialogue=dialogue)
        calls.append({'question':question,'plan':planned})
        return planned
    store = PostgresMall(database, traced_planner,
                         model_dialogue=True, composer=grounded_writer(key),reviewer=grounded_reviewer(key),read_decider=evidence_decider(key),business_decider=business_decider(key),
                         retrieval=MallRetrieval(local_setting('MILVUS_URL'),persistent_dense=args.persistent_dense,window_rerank=args.window_rerank) if args.retrieval else None)
    cases = json.loads(args.cases.read_text('utf-8'))['conversations']
    if args.all:
        cases += json.loads((ROOT/'tests/evaluation_cases/semantic_dialogues_holdout.json').read_text('utf-8'))['conversations']
    if args.task_memory:
        cases += json.loads((ROOT/'tests/evaluation_cases/task_dialogues.json').read_text('utf-8'))['conversations']
    samples = []
    started = perf_counter()
    with store.connect() as conn, conn.transaction(force_rollback=True):
        @contextmanager
        def borrowed():
            yield conn
        with patch.object(store, 'connect', borrowed):
            for serial, case in enumerate(cases, 1):
                order_id = f'CN-970{serial:03d}'
                # 所有插入都回滚；不覆盖或清空任何已有业务数据。
                conn.execute("""INSERT INTO public.mall_orders SELECT %s,'buyer-001',shop_id,terms_version,now()-interval '2 days'
                    FROM public.mall_orders WHERE order_id='CN-300003'""", (order_id,))
                conn.execute("""INSERT INTO public.mall_packages SELECT %s,package_no,'delivered',carrier,tracking,
                    now()-interval '1 day',current_date FROM public.mall_packages WHERE order_id='CN-300003'""", (order_id,))
                conn.execute("""INSERT INTO public.mall_items SELECT %s,item_no,package_no,sku,%s,'白色 · 标准款',2,
                    list_unit,paid_unit,return_credit,true,true FROM public.mall_items WHERE order_id='CN-300003'""",
                             (order_id, case['title']))
                for turn in case['turns']:
                    timing = {}
                    result = store.consult('buyer-001', order_id, 1, MallQuestion(question=turn['question']), timings=timing)
                    memory = conn.execute("""SELECT context FROM public.mall_consultations WHERE order_id=%s
                        ORDER BY turn_no DESC LIMIT 1""", (order_id,)).fetchone()['context']
                    errors = []
                    if 'reason' in turn and memory.get('reason') != turn['reason']:
                        errors.append('原因记忆不符')
                    if 'kind' in turn and memory.get('requested_kind') != turn['kind']:
                        errors.append('处理意愿不符')
                    if 'target' in turn and turn['target'] not in memory.get('target_spec',''):
                        errors.append('目标规格丢失')
                    if 'topic' in turn and turn['topic'] not in result.get('topics',[]):
                        errors.append('工具选择不符')
                    if 'response_kind' in turn and result.get('response_kind') != turn['response_kind']:
                        errors.append('话题边界不符')
                    if turn.get('no_reship') and result.get('recommended_kind') == 'reship':
                        errors.append('旧补发意愿未撤回')
                    if 'quantity' in turn and memory.get('quantity')!=turn['quantity']:
                        errors.append('申请件数记忆不符')
                    if 'quantity_mode' in turn and memory.get('quantity_mode')!=turn['quantity_mode']:
                        errors.append('全部申请意愿丢失')
                    if 'paused' in turn and memory.get('processing_paused',False)!=turn['paused']:
                        errors.append('办理暂停状态不符')
                    if 'active_reasons' in turn and sorted(row['reason'] for row in memory.get('issues',[]) if row['state']=='active')!=sorted(turn['active_reasons']):
                        errors.append('多问题生命周期不符')
                    for kind,state in (('reported_available','reported_available'),('reported_missing','reported_missing')):
                        if kind in turn and not any(row['kind']==turn[kind] and row['state']==state for row in memory.get('evidence',[])):
                            errors.append('凭证报告记忆不符')
                    preview=result.get('action_draft') or {}
                    if 'draft_state' in turn and preview.get('state')!=turn['draft_state']:
                        errors.append('确认卡状态不符')
                    if 'draft_quantity' in turn and preview.get('quantity')!=turn['draft_quantity']:
                        errors.append('确认卡件数不符')
                    if 'draft_missing' in turn and turn['draft_missing'] not in preview.get('missing',[]):
                        errors.append('未追问办理缺失字段')
                    if turn.get('evidence') and not result.get('evidence'):
                        errors.append('依据没有原文')
                    if turn.get('not_submitted') and any(word in result['answer'] for word in ('已为你提交','已经帮你申请','退款成功')):
                        errors.append('宣称执行了不存在的业务动作')
                    if any(word in result['answer'] for word in ('哪个商品或凭证','哪笔订单有问题')):
                        errors.append('重复追问已知对象')
                    row = {'case':case['name'], 'question':turn['question'], 'errors':errors, 'result':result,
                           'plans':[row['plan'] for row in calls if row['question']==turn['question']],
                           'memory':memory, 'timings_ms':timing}
                    samples.append(row)
                    if args.audit:
                        from app.mall.quality_audit import audit_reply
                        try:
                            row['quality_audit']=audit_reply(key,turn['question'],result)
                        except Exception as error:
                            row['quality_audit']={'unavailable':type(error).__name__}
                    print(json.dumps({'case':case['name'], 'question':turn['question'], 'errors':errors,
                                      'generated':result.get('generation_status'), 'answer':result['answer']},ensure_ascii=False),flush=True)
    elapsed = sorted(s['timings_ms']['consult'] for s in samples)
    report = {'conversations':len(cases), 'turns':len(samples), 'passed':sum(not s['errors'] for s in samples),
              'generated':sum(s['result'].get('generation_status')=='grounded' for s in samples),
              'p50_ms':elapsed[len(elapsed)//2], 'p95_ms':elapsed[min(len(elapsed)-1,int(len(elapsed)*.95))],
              'elapsed_s':perf_counter()-started, 'samples':samples}
    if args.audit:
        report['quality_audit']={
            'passed':sum(all(s.get('quality_audit',{}).get(k) is True for k in ('relevant','grounded','task_consistent')) for s in samples),
            'total':len(samples),'note':'同模型独立提示复核，不能替代人工或外部盲评；不混入状态通过率。'}
    output = args.output or ROOT/'.local/evaluations'/('semantic-dialogue-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.json')
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf-8') as handle:
        json.dump(report,handle,ensure_ascii=False,indent=2,default=str)
    print(json.dumps({k:v for k,v in report.items() if k!='samples'},ensure_ascii=False))
    print(output)
    if report['passed'] != report['turns']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
