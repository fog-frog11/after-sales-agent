"""串行复跑业务发布检查；不创建数据库，不删除验收目录。--live会调用配置的模型。"""
import argparse,json,os,subprocess,sys
from datetime import datetime
from app.settings import ROOT

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true')
    args=parser.parse_args()
    env={**os.environ,'MALL_ACCEPTANCE_DB':'1','MALL_DESKTOP_ONLY':'1'}
    for key in ('MALL_MODEL_DIALOGUE','MALL_BROWSER_JOURNEY','MALL_LIVE_SMOKE','MALL_LIVE_WRITER'):
        env.pop(key,None)
    python=[sys.executable,'-X','utf8','-B']
    stages=[('自动化',python+['-m','unittest','discover','-s','tests'],{}),
        ('确认事务',python+['-m','scripts.check_chat_actions','--acceptance-db'],{}),
        ('三角色浏览器',['node','tests/browser/platform_review.mjs'],{}),
        ('只读并发',python+['-m','scripts.check_read_concurrency'],{})]
    if args.live:
        stages.append(('模型履约聊天',['node','tests/browser/platform_review.mjs'],{
            'MALL_MODEL_DIALOGUE':'1','MALL_TEST_RETRIEVAL':'1','MALL_TEST_PERSISTENT_DENSE':'1',
            'MALL_TEST_WINDOW_RERANK':'1','MALL_BROWSER_JOURNEY':'fulfillment-chat'}))
    results=[]
    for name,command,extra in stages:
        code=subprocess.run(command,cwd=ROOT,env={**env,**extra}).returncode
        results.append({'stage':name,'exit_code':code})
        if code:break
    report={'passed':all(r['exit_code']==0 for r in results),'live':args.live,'stages':results}
    directory=ROOT/'.local/evaluations';directory.mkdir(parents=True,exist_ok=True)
    path=directory/('business-release-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.json')
    with path.open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    print(path)
    raise SystemExit(not report['passed'])

if __name__=='__main__':main()
