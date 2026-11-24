"""真实 PostgreSQL 验证确认卡事务；默认全部回滚，并发模式只写指定验收库。"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
import json
from time import time_ns
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi.encoders import jsonable_encoder
from psycopg.conninfo import make_conninfo,conninfo_to_dict
from psycopg.types.json import Jsonb

from app.access import Account
from app.mall.chat_actions import ChatConfirmation, prepare_action
from app.mall.dialogue_state import task_signature
from app.mall.rules import MallOperation, MallOffer
from app.settings import ROOT, local_setting
from app.storage.postgres_mall import PostgresMall
from app.tickets.service_requests import ServiceError


BUYER=Account('buyer-001','buyer')
SHOP=Account('shop-001','merchant')
STAFF=Account('staff','staff')
TODAY=datetime.now(ZoneInfo('Asia/Shanghai')).date()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--concurrent',action='store_true',help='提交4笔独立验收订单，验证多连接并发；记录保留')
    parser.add_argument('--acceptance-db',action='store_true',help='选择已初始化的独立验收库')
    args=parser.parse_args()
    if args.concurrent and not args.acceptance_db:
        parser.error('持久化并发验收只允许配合--acceptance-db，不写正式工作台库')
    connection=local_setting('ORDER_DATABASE_URL')
    database='after_sales_v3_release_20261122' if args.acceptance_db else conninfo_to_dict(connection).get('dbname')
    store=PostgresMall(make_conninfo(connection,dbname=database))
    orders=[f'CN-88{time_ns()%10**7:07d}{i}' for i in range(4 if args.concurrent else 6)]
    results=[]
    concurrency=[]

    def ok(name):
        results.append(name)
        print(name,flush=True)

    def seed(conn):
        for order in orders:
            conn.execute("""INSERT INTO public.mall_orders SELECT %s,'buyer-001',shop_id,terms_version,now()-interval '2 days'
                FROM public.mall_orders WHERE order_id='CN-300003'""",(order,))
            conn.execute("""INSERT INTO public.mall_packages SELECT %s,package_no,'delivered',carrier,tracking,
                now()-interval '1 day',current_date FROM public.mall_packages WHERE order_id='CN-300003'""",(order,))
            conn.execute("""INSERT INTO public.mall_items SELECT %s,item_no,package_no,sku,title,variant,3,
                list_unit,paid_unit,return_credit,true,true FROM public.mall_items WHERE order_id='CN-300003'""",(order,))

    def card(order,memory):
        with store.connect() as conn:
            item=store._items(conn,BUYER.account_id,order,1)[0]
            cases=store._page(conn,BUYER,order_id=order,item_no=1,exact=True)['cases']
            proposal=prepare_action(item,memory,cases,TODAY,requested=True)
            assert proposal['state']=='ready',proposal
            result={'action_draft':proposal,'task_signature':task_signature(memory)}
            number=conn.execute("""INSERT INTO public.mall_consultations(buyer_id,order_id,item_no,question,result,context)
                VALUES(%s,%s,1,%s,%s,%s) RETURNING turn_no""",(BUYER.account_id,order,'事务边界验收',
                Jsonb(jsonable_encoder(result)),Jsonb(memory))).fetchone()['turn_no']
            proposal['turn_no']=number
            conn.execute('UPDATE public.mall_consultations SET result=%s WHERE turn_no=%s',
                         (Jsonb(jsonable_encoder(result)),number))
        return proposal

    def confirm(order,proposal,**data):
        return store.confirm_chat_action(BUYER.account_id,order,1,uuid4() if data.pop('wrong_id',False) else proposal['id'],
            ChatConfirmation(turn_no=proposal['turn_no'],**data))

    def rejected(code,fn):
        try:
            # SQL异常或失败操作也必须能回滚，不污染上一个成功的动作。
            fn()
        except ServiceError as error:
            assert error.status_code==code,(code,error.status_code,error.message)
        else:
            raise AssertionError(f'应返回{code}')

    memory={'reason':'quality','issue_description':'插电无法开机','requested_kind':'return_refund','quantity':1}

    def verify():
        first=card(orders[0],memory)
        earlier=card(orders[0],memory)
        first=card(orders[0],memory)
        rejected(404,lambda:store.confirm_chat_action('buyer-002',orders[0],1,first['id'],ChatConfirmation(turn_no=first['turn_no'])))
        rejected(409,lambda:confirm(orders[0],first,wrong_id=True))
        ok('跨买家和伪造确认卡被拒绝')
        if args.concurrent:
            original_connect=store.connect
            source_barrier=Barrier(2)
            class SourceCursor:
                def __init__(self,cursor,backend):
                    self.cursor,self.backend=cursor,backend
                def fetchone(self):
                    row=self.cursor.fetchone()
                    assert row and not row['result'].get('action_execution')
                    concurrency.append({'backend_pid':self.backend,'read_unconfirmed_source':True})
                    source_barrier.wait(timeout=10)
                    return row
            class ConcurrentConnection:
                def __init__(self,conn):
                    self.conn,self.gated=conn,False
                def __getattr__(self,name):
                    return getattr(self.conn,name)
                def execute(self,query,*values,**kwargs):
                    cursor=self.conn.execute(query,*values,**kwargs)
                    if (not self.gated and ' '.join(str(query).split()).startswith('SELECT turn_no,result,context FROM public.mall_consultations')
                            and 'FOR UPDATE' not in str(query)):
                        self.gated=True
                        return SourceCursor(cursor,self.conn.info.backend_pid)
                    return cursor
            @contextmanager
            def concurrent_connection():
                with original_connect() as conn:
                    yield ConcurrentConnection(conn)
            # 强制两个独立连接都读到未确认的卡，再同时争用原业务锁。
            with patch.object(store,'connect',concurrent_connection),ThreadPoolExecutor(max_workers=2) as pool:
                receipts=list(pool.map(lambda _:confirm(orders[0],first),range(2)))
            assert len({row['backend_pid'] for row in concurrency})==2
            assert receipts[0]==receipts[1]
            receipt=receipts[0]
            ok('两个独立连接并发确认返回同一回执')
        else:
            receipt=confirm(orders[0],first)
        assert confirm(orders[0],first)==receipt
        rejected(409,lambda:confirm(orders[0],first,note='改成另一个说明'))
        with store.connect() as conn:
            assert conn.execute('SELECT count(*) AS n FROM public.mall_cases WHERE order_id=%s',(orders[0],)).fetchone()['n']==1
            assert conn.execute('SELECT count(*) AS n FROM public.mall_case_events WHERE case_no=%s',(receipt['case_no'],)).fetchone()['n']==1
            assert conn.execute('SELECT result FROM public.mall_consultations WHERE turn_no=%s',(earlier['turn_no'],)).fetchone()['result']['action_draft']['state']=='outdated'
        rejected(409,lambda:confirm(orders[0],earlier))
        ok('重复确认不新增售后单或审计事件，变更请求不能复用旧回执')
        old=card(orders[1],memory)
        card(orders[1],dict(memory,quantity=2,memory_revision=1))
        card(orders[1],dict(memory,memory_revision=2))
        rejected(409,lambda:confirm(orders[1],old))
        newest=card(orders[1],dict(memory,quantity=2,memory_revision=3))
        rejected(409,lambda:confirm(orders[1],old))
        second=confirm(orders[1],newest)
        assert store.detail(BUYER,int(second['case_no']))['quantity']==2
        ok('改口后旧卡失效，新卡按两件办理')
        invalid=card(orders[2],memory)
        rejected(400,lambda:confirm(orders[2],invalid,photos=[{'mime':'image/png','data':'aW52YWxpZA=='}]))
        with store.connect() as conn:
            assert conn.execute('SELECT count(*) AS n FROM public.mall_cases WHERE order_id=%s',(orders[2],)).fetchone()['n']==0
        third=confirm(orders[2],invalid)
        ok('无效照片没有半张售后单，修正后同一卡片可重试')

        number=int(receipt['case_no'])
        store.operate(SHOP,number,MallOperation(operation_id=uuid4(),action='negotiate',note='留货协商',
            offer=MallOffer(kind='partial_refund',quantity=1,amount='10')))
        accept=card(orders[0],dict(memory,case_action='accept_offer',action_note='同意这个方案'))
        store.operate(SHOP,number,MallOperation(operation_id=uuid4(),action='close_prompt',note='更新方案',
            prompt_id=store.detail(SHOP,number)['prompt']['id']))
        store.operate(SHOP,number,MallOperation(operation_id=uuid4(),action='negotiate',note='新版留货协商',
            offer=MallOffer(kind='partial_refund',quantity=1,amount='12')))
        rejected(409,lambda:confirm(orders[0],accept))
        replacement=card(orders[0],dict(memory,case_action='reject_offer',action_note='我仍希望退货'))
        confirm(orders[0],replacement)
        ok('店铺修改方案后旧确认卡失效，买家可确认拒绝新版方案')
        dispute=card(orders[0],dict(memory,case_action='dispute',action_note='双方未协商一致，请平台介入'))
        final=confirm(orders[0],dispute)
        assert final['status']=='disputed'
        assert str(number) in {row['case_no'] for row in store.page(STAFF,orders[0],None,'all')['cases']}
        ok('聊天确认平台介入后，平台队列显示同一原单')
        withdraw=card(orders[2],dict(memory,case_action='withdraw',action_note='撤回申请'))
        final=confirm(orders[2],withdraw)
        assert final['status']=='withdrawn'
        assert confirm(orders[2],withdraw)==final
        ok('撤回及重复确认保留原单结果与审计')

        fourth=confirm(orders[3],card(orders[3],memory))
        fourth_no=int(fourth['case_no'])
        store.operate(SHOP,fourth_no,MallOperation(operation_id=uuid4(),action='request_evidence',note='请补充当前故障说明'))
        supplement=card(orders[3],dict(memory,case_action='provide_evidence',action_note='补充说明：目前仍不能开机'))
        confirm(orders[3],supplement)
        assert store.detail(BUYER,fourth_no)['prompt'] is None
        store.operate(SHOP,fourth_no,MallOperation(operation_id=uuid4(),action='negotiate',note='留货补偿12元',
            offer=MallOffer(kind='partial_refund',quantity=1,amount='12')))
        agreed=card(orders[3],dict(memory,case_action='accept_offer',action_note='我接受这次12元留货方案'))
        accepted=confirm(orders[3],agreed)
        assert accepted['status']=='awaiting_outcome'
        assert confirm(orders[3],agreed)==accepted
        detail=store.detail(BUYER,fourth_no)
        assert str(detail['agreement']['amount']) in ('12','12.00')
        assert detail['recorded_refund'] is None
        ok('聊天回复补证并接受原单方案，金额采用店铺版本，接受不冒充已退款')

        if not args.concurrent:
            from app.mall.rules import ReturnDetails
            exchange=confirm(orders[4],card(orders[4],dict(memory,requested_kind='exchange')))
            exchange_no=int(exchange['case_no'])
            store.operate(SHOP,exchange_no,MallOperation(operation_id=uuid4(),action='approve',note='同意换货'))
            shop_view=store.detail(SHOP,exchange_no)
            print('换货批准后',shop_view['status'],shop_view['actions'],store.detail(BUYER,exchange_no)['actions'],flush=True)
            if 'provide_return_details' in shop_view['actions']:
                store.operate(SHOP,exchange_no,MallOperation(operation_id=uuid4(),action='provide_return_details',note='寄回资料',
                    return_details=ReturnDetails(recipient='收件员',address='杭州市西湖区收件中心十号',phone='13800000000')))
            shipment=card(orders[4],dict(memory,case_action='ship',fulfillment_case=str(exchange_no),
                fulfillment={'carrier':'顺丰快递','tracking':'SF1234567890'}))
            assert confirm(orders[4],shipment)==confirm(orders[4],shipment)
            store.operate(SHOP,exchange_no,MallOperation(operation_id=uuid4(),action='receive',note='收到退件'))
            address=card(orders[4],dict(memory,case_action='confirm_delivery_address',fulfillment_case=str(exchange_no),
                fulfillment={'recipient':'李四','address':'杭州市西湖区新收货街道八号','phone':'13800000001'}))
            destination=confirm(orders[4],address)
            assert confirm(orders[4],address)==destination
            with store.connect() as conn:
                parcel=conn.execute('SELECT destination FROM public.mall_outbound_parcels WHERE case_no=%s',(exchange_no,)).fetchone()
                assert parcel['destination']['recipient']=='李四'
                events=conn.execute("SELECT action,count(*) AS n FROM public.mall_case_events WHERE case_no=%s AND action IN ('ship','confirm_delivery_address') GROUP BY action",(exchange_no,)).fetchall()
                assert len(events)==2 and all(row['n']==1 for row in events)
            ok('聊天登记寄回运单并确认新收货资料，重复请求各只留一次事件')

    if args.concurrent:
        with store.connect() as conn:
            seed(conn)
        allocation=ROOT/'.local/evaluations'/('chat-actions-allocation-'+orders[0]+'.json')
        allocation.parent.mkdir(parents=True,exist_ok=True)
        with allocation.open('x',encoding='utf-8') as handle:
            json.dump({'database':database,'orders':orders,'data_retained':True},handle,ensure_ascii=False,indent=2)
        print(allocation,flush=True)
        verify()
    else:
        with store.connect() as conn,conn.transaction(force_rollback=True):
            seed(conn)
            @contextmanager
            def borrowed():
                with conn.transaction():
                    yield conn
            with patch.object(store,'connect',borrowed):
                verify()
    report={'checks':len(results),'passed':results,'multi_connection':args.concurrent,
            'concurrency_proof':concurrency,
            'database':database,'orders':orders,'data_retained':args.concurrent,
            'note':'验证真实业务事务与权限，不计为模型理解准确率。'}
    output=ROOT/'.local/evaluations'/('chat-actions-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.json')
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf-8') as handle:
        json.dump(report,handle,ensure_ascii=False,indent=2)
    print(output)


if __name__=='__main__':
    main()
