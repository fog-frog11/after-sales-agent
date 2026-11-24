"""从新解包目录初始化独立空库；保留目录与数据库，不清理任何对象。"""
import argparse,json,os,re,subprocess,sys
from datetime import datetime
from pathlib import Path
from zipfile import ZipFile
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from app.settings import ROOT,local_setting

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive',type=Path)
    parser.add_argument('--database',default='after_sales_delivery_20261123')
    args=parser.parse_args()
    stamp=datetime.now().strftime('%Y%m%d%H%M%S')
    database=args.database
    if not re.fullmatch(r'after_sales_delivery_[0-9]{8}',database):
        parser.error('只允许指名的独立交付验收库')
    destination=ROOT/'.local'/('release-unpack-'+stamp)
    destination.mkdir(exist_ok=False)
    with ZipFile(args.archive) as archive:
        for name in archive.namelist():
            if not (destination/name).resolve().is_relative_to(destination.resolve()):
                raise ValueError('归档路径越界')
        archive.extractall(destination)
    project=next(destination.iterdir())
    connection=local_setting('ORDER_DATABASE_URL')
    with psycopg.connect(make_conninfo(connection,dbname='postgres'),autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
    env={**os.environ,'ORDER_DATABASE_URL':make_conninfo(connection,dbname=database)}
    subprocess.run([sys.executable,'-X','utf8','-B','-m','scripts.init_workbench'],cwd=project,env=env,check=True)
    with psycopg.connect(env['ORDER_DATABASE_URL']) as conn:
        counts={table:conn.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(table))).fetchone()[0]
            for table in ('mall_orders','mall_items','mall_buyer_profiles','mall_shop_profiles')}
    assert counts['mall_orders']>=300 and counts['mall_items']>=300
    assert counts['mall_buyer_profiles']>=50 and counts['mall_shop_profiles']>=100
    report={'passed':True,'database':database,'unpack_directory':str(destination),'counts':counts,
            'retained':True,'scope':'新解包Python源码与空库初始化；复用已安装依赖，不代表全新机器安装'}
    path=ROOT/'.local/evaluations'/('release-unpack-'+stamp+'.json')
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':
    main()
