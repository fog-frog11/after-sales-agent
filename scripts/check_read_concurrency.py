"""有限只读并发与跨买家隔离验证，不计为生产吞吐压测。"""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from time import perf_counter
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
from app.access import DemoAccess
from app.api import create_app
from app.settings import ROOT,local_setting
from app.storage.postgres_mall import PostgresMall

def main():
    store=PostgresMall(make_conninfo(local_setting('ORDER_DATABASE_URL'),dbname='after_sales_v3_release_20261122'))
    with store.connect() as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        owned={buyer:conn.execute('SELECT order_id FROM public.mall_orders WHERE buyer_id=%s ORDER BY order_id LIMIT 1',(buyer,)).fetchone()['order_id'] for buyer in ('buyer-001','buyer-002')}
    access=DemoAccess({buyer:'concurrency-only-'+buyer for buyer in owned})
    app=create_app(mall=store,access=access)
    def read(index):
        buyer=list(owned)[index%2];other=list(owned)[1-index%2];start=perf_counter()
        with TestClient(app) as client:
            assert client.post('/api/session/login',json={'account_id':buyer,'secret':'concurrency-only-'+buyer}).status_code==200
            assert client.get('/api/buyer/mall/orders/'+owned[buyer]).status_code==200
            assert client.get('/api/buyer/mall/orders/'+owned[other]).status_code==404
        return round((perf_counter()-start)*1000,2)
    with ThreadPoolExecutor(max_workers=4) as pool:
        timings=list(pool.map(read,range(20)))
    report={'passed':True,'workers':4,'sessions':20,'authorized_reads':20,'foreign_reads_rejected':20,
            'p95_session_ms':sorted(timings)[18],'scope':'进程内HTTP与真实PostgreSQL只读小规模并发，不是网络吞吐压测'}
    path=ROOT/'.local/evaluations'/('read-concurrency-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.json')
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=False));print(path)

if __name__=='__main__':main()
