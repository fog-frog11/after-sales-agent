# 电商售后 Agent

登录后进入对应工作台。买家查本人购物记录和售后，店铺查本店订单并处理申请，平台客服查商城档案及已介入的售后。

## 商城

读商城后端从 `app/mall/` 开始。买家发送问题 → `api.py` 接收请求 → `agent.py` 选择查询 → `consultation.py` 根据订单事实生成回复与表单建议。订单读写在 `app/storage/postgres_mall.py`，买家页面在 `web/commerce/Orders.jsx`。

订单、商品咨询、售后队列和商城档案由 React 渲染。首次启动需要 Python、Node.js 22 或更新版本以及 Docker。复制 `.env.example` 为 `.env`，填好本机 PostgreSQL 密码与对应的 `ORDER_DATABASE_URL`，再从项目根目录执行。改动 [商城前端](web/commerce/main.jsx) 后重新执行 `npm run build`。

```powershell
npm install --ignore-scripts
npm run build
python -m pip install -r requirements.txt
docker pull postgres:17-alpine
docker compose up -d --wait --pull never postgres
python -X utf8 -B -m scripts.init_demo_access
python -X utf8 -B -m scripts.init_mall
python -X utf8 -B -m scripts.init_mall_directory
python -X utf8 -B -m scripts.merge_mall_logins
python -X utf8 -B server.py
```

商城工作台提供 100 家店铺、50 位买家和 300 笔订单。订单保存商品规格、数量、成交价、优惠、配送费、付款方式和包裹。早期订单及已办理售后保留在数据库中，平台可用完整订单号打开对应订单详情。

### 作品集案例

用 `buyer-001` 打开 [项目案例](http://127.0.0.1:8765/mall/portfolio)。店铺为 `shop-001`，平台客服为 `staff`，初始化会生成登录口令和账号索引 `.local/mall-logins.json`。案例和检查点以 [案例清单](app/portfolio_cases.json) 为准。初始化会额外准备四笔独立订单。已有商城可运行 `python -X utf8 -B -m scripts.init_portfolio_cases` 补齐案例。重跑保留已有售后和下单日期，若要重走有办理时限的流程，可在单独的空数据库初始化。

| 案例 | 订单 | 看到的结果 |
|---|---|---|
| 少了一根充电线 | `CN-410001` | 一轮回答补发和拍照，追问店铺依据，改口更新草稿。通过商家补发、买家确认，实现从对话到办结。 |
| 退款协商与平台介入 | `CN-410002` | 店铺提出留货部分退款，买家拒绝，平台支持恢复原退货办理。同一售后单展示三方事件。 |
| 同一问题，两期条款 | `CN-410003`、`CN-410004` | 同店同款的两期购买规则显示不同寄回报价，额外承诺无依据时说明查不到。 |

案例聊天可展开「查看本轮查询」，核对订单、查询路径和版本。后端链路是 [LangGraph 选查询](app/mall/agent.py) → [读取订单和售后状态](app/storage/postgres_mall.py) → [答复与草稿](app/mall/consultation.py) / [条款依据](app/mall/dialogue.py)。

在 PowerShell 运行 `$env:MALL_BROWSER_JOURNEY='portfolio'` 后运行 `node tests/browser/platform_review.mjs`，从同一个案例清单跑三条浏览器验收。验收用固定的模型工具响应、临时登录和可回滚的数据库事务。在独立的空库运行可同时验收初始化。

用下方[语义评测的模型与 CPU 依赖](#混合检索与重排对照)复跑两组商城条款题。加 `--out .local/evaluations/新报告.json` 可保存逐题标注、候选、选中的原文及判断结果。输出路径须是一个新文件。

```powershell
python -X utf8 -B -m scripts.evaluate_mall_citations --expand
python -X utf8 -B -m scripts.evaluate_mall_citations --dataset scripts/fixtures/mall_citation_validation.json --expand
```

2026-11-20 复测共 39 题。扩词 BM25 和混合召回在共用引用校验下均命中 21 道有依据的题，16 道无依据的题均未误引。两道待澄清的题在混合路径上正确触发澄清。此结果衡量条款引用，聊天与售后衔接另外用上面的浏览器案例验收。

### 店铺条款与买家咨询

店铺在「补充条款」按主题和商品范围起草，自定义正文或选用通用和品类示例。平台可对照新旧版本后审核。买家的订单查询使用该商品在购买时已生效的条款，审核记录保留每次修订；资格、金额和状态继续按订单事实和结构化规则核对。已有数据库执行 `python -X utf8 -B -m scripts.init_mall_supplements` 后重启工作台。

买家可以在商品咨询中发「查本单条款」。输入具体事项后，助手会展示购买时条款的版本及原文。像终身换新、上门维修或会员免运费等额外承诺，需要原文明确支持才能引用。可以用 `python -X utf8 -B -m scripts.evaluate_mall_citations --dataset scripts/fixtures/mall_citation_validation.json` 复跑引用校验对照。

商家可以展开「预览新订单的条款引用」，选择本店商品和一条问题，查看按当前购买时间适用的原文。勾选「同时试跑本机混合召回」可对照 E5 路径，界面分别展示索引准备耗时和查询耗时。两条路径共用买家端的引用校验；试查是只读操作。使用 `--expand` 重跑评测时，会在同一题集里同时输出线上 BM25 与混合召回经过引用校验后的结果。

商城条款检索评测采用三家自编店铺、两期购买版本。每个版本 50 条，题目包含口语问法、多诉求、跨店、跨版本及无适用条款。运行 `python -X utf8 -B -m scripts.evaluate_mall_terms --out .local/evaluations/mall-terms-新报告.json`，在本机比较 BM25、E5 混合召回和证据窗口重排。找不到本机向量模型时可加 `--lexical-only` 运行词法基线。报告保留每题的标注条款、候选顺序、漏召回和排序结果，并抽查跨版本的原文。数据位于 [条款语料](scripts/fixtures/mall_terms_catalog.json) 和 [标注题](scripts/fixtures/mall_terms_questions.json)，用来评测检索。

商家在「店铺条款」设置本店的退换期限、质保、响应时间、寄回报价、运费权益和品类指引。发布时校验平台最低保障，并保存生效日和版本。订单在购买时选取已生效的版本，每条商品明细保存当时取得的运费权益。买家在订单详情的「付款与收货」可分别阅读购买时条款、店铺现行条款和平台保障。

已有商城升级时运行 `python -X utf8 -B -m scripts.init_shop_policies` 后重启工作台。该命令可以重跑。通过 `python -X utf8 -B -m scripts.evaluate_mall_consultations` 可逐笔核对购买记录和条款版本。

商品咨询默认直接给结果。询问“退货运费谁出”并说明原因后，可以接着问“那多少钱”或“根据哪条”。问“本店现在的寄回运费是多少”会查店铺现行承诺，实际办理仍读取该订单绑定的版本。

- `staff` 从「商城档案」查店铺、买家和订单。
- 店铺从「本店订单」查看所属店铺的购物明细，买家从「我的订单」查看自己的购买和售后记录。
- `.local/mall-logins.json` 按买家、店铺和平台客服展示中文名称、账号编号和密码文件位置。买家和店铺使用 `.local/mall-directory-accounts.json`，平台客服 `staff` 使用 `.local/demo-accounts.json`。
- 买家可以输入姓名登录，店铺可以输入店铺名登录。使用对应账号原有的口令，重名时使用账号编号。「商城档案」展示中文名和编号。

商城工作台使用现有的购物档案。后文涉及旧订单来源的章节记录早期实现和评测，工作台的初始化与运行使用本节命令。

- `buyer-001` 的 `CN-300001` 可申请待发货仅退款。
- `buyer-002` 的 `CN-300002` 可查询在途物流。`buyer-003` 的 `CN-300003` 已签收，可在商品咨询询问退货运费。
- 从运输中的商品进入「咨询物流或申请在途退回」，选择「买错或不想要」。整包申请由店铺核实拦截或拒收，确认收回后办理退款。只退同包中的部分商品，或实际已经签收，可在原售后单转为按地址寄回。承运反馈由店铺登记。
- 同一在途包裹含多种商品时，从订单明细点「整包在途退回」。一张售后单覆盖包裹里的全部商品，退款成功后按各商品实付与剩余退款余额登记。已有在办售后的商品须先查原单。
- 拦截失败时，店铺在原单登记承运方反馈，办理期限继续计时。后来快递受理拒收，可登记原包裹退回。买家已签收时，可在原单选择「我已签收，改为自行寄回」，由店铺补寄回地址。店铺逾期时，买家可在原单申请平台介入。
- 申请在所属店铺队列处理。店铺能要求补凭证和提出方案，买家可补证或接受、拒绝方案。拒绝方案、申请被拒绝或店铺待办超时后，可从原售后单申请平台介入。
- 符合介入条件时，订单商品卡、原售后单顶部以及询问「申请人工介入」的聊天回复均提供「申请平台介入」入口。进入后填写争议说明并确认提交。
- 平台支持寄回且缺少地址时，原单先进入店铺补寄回信息的待办。填写后，买家有 7 天登记寄回。店铺逾期未补资料或未确认收件时，买家可以在原单补证并申请复核。
- 换货、补发和修后返还在原售后单创建独立的店铺寄出包裹。买家确认本次收货信息后，店铺登记新运单，买家确认收到后办结。包裹超期未发、没收到、发错或破损可在原单反馈，店铺安排重寄时保留历史运单。订单里的收货资料是虚构的本地演练数据。
- 店铺逾期未发，或已发包裹的问题逾期未处理，买家可在原售后单的「店铺寄出的售后包裹」中点「申请平台处理包裹问题」。也可以在商品咨询问「补发包裹没收到，能找平台介入吗」。平台客服从「待我处理」打开申请，按凭证要求发货、重寄或继续跟踪运单。两次申请及其结论均记录在原售后时间线。
- 店铺核查后建议继续等待时，买家可以在原单查看回复。已收到可确认收货，仍未收到可直接申请平台核对店铺回复。
- 店铺方案需确认商品件数、办理类型及相应金额或寄回说明。接受「留货部分退款」后，退款登记使用该版金额。订单的商品件数保持原有数量，退款余额按实际登记扣减。
- 店铺在原售后单点击「发起退款」，再通过「记录退款结果」提交处理结果。处理中和失败时，买家可问「退款到账了吗」或打开原单核对进度。失败后，店铺可按原金额重试。只有成功结果写入商品退款账。
- 「退款明细」逐项展示涉及件数、实付单价和本次退款，单列原配送费与寄回运费。原配送费按订单结算记录展示，目前退款入账按商品处理。拦截或拒收费用由店铺核实；整包自行寄回按一次条款报价抵扣商品可用权益。商品咨询读取同一份记录。
- 申请和补证可上传 JPEG 或 PNG，一次最多 3 张。图片与对应事件关联，按买家、所属店铺及已介入平台角色授权。
- 我的待办可筛选 4 小时内到期和已超时。退款、运单及运费返还属于本机演练记录。未发货商品全额退款后，从待发货筛选退出。
- 商品咨询保存最近的对话，重新打开可继续核对原商品。外包装破损时会追问商品是否损坏，并提示拍照、给出可带入申请的说明。

### V1.0 开发路线

V1.0 是用于求职的电商售后 Agent 作品。项目自带虚构的中文商城数据、店铺条款和三角色演示账号。读者在干净环境启动后，可以按案例操作，也可以查阅 Agent 如何查单、选取证据、追问和把咨询交给售后流程。首个公开版本以 V1.0 为目标。

| 顺序 | 开发范围 | 验收条件 |
|---|---|---|
| 1. 代表案例与自带数据 | 从自带商城里挑选缺件补发、退款协商与平台介入、新旧条款对照案例。整理演示账号、订单、操作顺序和预期结果。 | 读者从空库启动后，可以用文档里指定的订单复现案例；售后期限和条款版本与预期结果一致。 |
| 2. 验收 Agent 与条款 | 让读者可以核对 Agent 的查单、上下文、引用版本、预填表单和超时处理。保留典型错例和策略对照。 | 用独立问法测试口语、复合问题、改口、无依据和模型故障。报告可复跑，能看出检索策略的收益和代价。 |
| 3. 验收三角色售后 | 核对读单权限、办理状态、协商、退款登记、包裹记录和平台介入。 | 同一笔售后从申请到结束，可核对下一步、操作者和时间线。重复提交和跨角色访问有回归。 |
| 4. GitHub 发布验收 | 提供精简启动说明、案例演示、架构与代码阅读入口、评测结果、CI 和许可。 | 在干净环境用仓库自带数据跑通代表案例，完成公开文件检查后发布 V1.0。 |

第 1 阶段已通过空库和宽屏浏览器验收。第 2 阶段已具备多轮咨询、改口、依据展示、申请预填和超时回退，并完成独立题集的引用对照。买家端使用扩词后的 BM25，商家可试查本机 E5 并对照结果。公开版仍需把评测、三角色流程和干净环境启动整合进发布验收。

### 从商品咨询体验 Agent

在「我的订单」打开一件商品，点「咨询或申请售后」。输入「退货邮费咋算」，按提示选择实际原因。预计自付金额和已抵扣权益会直接显示。继续问「根据哪条」可读到对应版本的原句。外箱破损时会先问商品本身情况，经买家确认才带入申请。询问退款则读取售后单上的退款记录，已有售后可从聊天直接打开。

商品咨询图使用本机配置的 DeepSeek 对当前问题规划只读事项，规则负责金额和状态。表单预检和明确的快捷回复直接走规则。历史状态按买家、订单、商品保存。模型调用出错会回到规则路径；已保存回复的 `route` 区分 `function_call`、`rules` 和 `fallback`。

可在项目根目录运行 `python -X utf8 -B -m scripts.evaluate_mall_consultations`。脚本在同一 PostgreSQL 只读快照里逐商品检查购买事实、店铺、下单条款版本和运费算式，输出覆盖数与错误数。该结果用于核对业务数据；搜索策略的效果需要独立标注的检索题集。

### 三角色浏览器验收

在商城 PostgreSQL 已启动、商城前端已构建、本机已有 Node.js 和 Chrome 时，从项目根目录运行：

```powershell
node tests/browser/platform_review.mjs
```

测试使用独立本机端口和临时登录身份，覆盖退货裁定、换货收货、补发催发与重寄、过期裁定、重复发货与退款失败重试。验收订单、售后操作及所需表结构在回滚事务内运行，超时由测试脚本触发。可用 `MALL_TEST_PYTHON` 和 `MALL_TEST_CHROME` 指定本机程序路径。

设置 `MALL_DESKTOP_ONLY=1` 可只跑宽屏。默认使用固定的工具调用验收商品咨询，另设 `MALL_LIVE_SMOKE=1` 会对一条预设的商品与退款复合问题使用真实 DeepSeek，验证页面经模型路由后能取得两项业务答复。

## 在本机运行

需要 Python 3.10 或更新版本。在项目文件夹执行：

```powershell
python -m pip install -r requirements.txt
npm install --ignore-scripts
npm run build
python -X utf8 -B -m scripts.init_demo_access
python -X utf8 -B -m scripts.init_mall
python -X utf8 -B -m scripts.init_mall_directory
python -X utf8 -B -m scripts.merge_mall_logins
python -X utf8 -B server.py
```

使用初始化时生成的账号和口令登录。[打开售后工作台](http://127.0.0.1:8765/)
打开 [FastAPI 接口文档](http://127.0.0.1:8765/docs)，用 `POST /api/conversation` 查询订单的店铺政策。单轮 `POST /api/query` 可手动指定期限做演示对比。

要试 DeepSeek，将 `.env.example` 复制为 `.env`，在本机填写 `DEEPSEEK_API_KEY` 后重启服务。模型提出工具调用，应用验证订单号，最终结果由 Python 计算。测试使用模拟订单和模型响应。

## PostgreSQL 订单

Docker Compose 使用本机的 `postgres:17-alpine` 镜像，在 `127.0.0.1:55432` 启动项目数据库。首次初始化数据卷时，会执行订单、政策和工单 SQL，创建四笔样本、两条政策和空工单表。

首次配置时在项目的 `.env` 填入 `AFTER_SALES_POSTGRES_PASSWORD` 和 `ORDER_DATABASE_URL`。两处使用同一个数据库密码，连接地址格式为 `postgresql://after_sales:{PASSWORD}@127.0.0.1:55432/after_sales`。建议使用随机十六进制密码，便于直接放进连接地址。

```powershell
python -m pip install -r requirements.txt # 这条安装过了，下面两条顺序运行！启动命令！
docker compose up -d --wait --pull never --no-recreate postgres
python -X utf8 -B -m scripts.init_demo_access
python -X utf8 -B server.py
```

已有数据库升级时，先启动数据库，再从项目目录运行增量 SQL。脚本可重复执行，保留已有签收日期和已配置的店铺。

```powershell
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Get-Content -LiteralPath sql/apply_shop_policies.sql -Raw -Encoding UTF8 |
    docker compose exec -T postgres psql -X -v ON_ERROR_STOP=1 -U after_sales -d after_sales
Get-Content -LiteralPath sql/create_support_tickets.sql -Raw -Encoding UTF8 |
    docker compose exec -T postgres psql -X -v ON_ERROR_STOP=1 -U after_sales -d after_sales
Get-Content -LiteralPath sql/add_ticket_resolution.sql -Raw -Encoding UTF8 |
    docker compose exec -T postgres psql -X -v ON_ERROR_STOP=1 -U after_sales -d after_sales
```

页面顶部显示「PostgreSQL」后，可用买家账号在订单列表查询本人可见的历史订单及商品明细。

```powershell
docker compose exec postgres psql -U after_sales -d after_sales -c "SELECT order_id, signed_on FROM public.orders ORDER BY order_id"
```

## 在 VS Code 读一笔订单

| 目录或文件 | 内容 |
| --- | --- |
| `server.py`、`app/api.py` | 本机启动入口、FastAPI 路由与服务装配 |
| `app/access.py`、`web/session.js` | 本机账号、会话和登录界面交互 |
| `app/orders/` | 退货判断、订单样本、LangGraph 查单与追问 |
| `app/tickets/` | 人工工单的预览、确认与状态流转 |
| `app/policies/` | 条款样本、检索与引用检查、买家问答与请求追踪 |
| `app/integrations/` | DeepSeek 调用和 MCP 工具、服务端与客户端 |
| `app/storage/` | PostgreSQL 仓储、对话检查点和 Milvus 索引 |
| `scripts/`、`tests/` | 初始化和验收命令、单元测试 |
| `web/`、`sql/` | 买家和客服页面、数据库初始化 SQL |

1. [页面事件](web/app.js) 里的 `queryOrder()` 把问题和规则发给 `POST /api/conversation`。
2. [FastAPI 路由](app/api.py) 校验输入后，由 `send_message()` 把消息交给 LangGraph。
3. [LangGraph 流程](app/orders/conversation.py) 缺订单号就暂停。收到带相同会话 ID 的回复后，继续查单和应用店铺政策。
4. [工具调用](app/integrations/deepseek.py) 的 `validate_order_tool()` 校验模型返回的工具和订单号。
5. [数据库查询](app/storage/postgres_orders.py) 读取签收日期、所属店铺和政策，[业务规则](app/orders/rules.py) 计算天数并输出结果。

先发「这单还能退吗？」，等页面追问后，在同一个浏览器标签页刷新。恢复原问题后，发送从「我的订单」复制的编号，检查系统是否继续回答原问题。

数据库模式把当前订单、逐轮答复和条款出处保存在 PostgreSQL 的 `after_sales_dialogue` schema 中。浏览器为当前标签页保存会话 ID。首次使用此功能时，启动 PostgreSQL 后从项目目录执行 [检查点初始化](scripts/init_dialogue_checkpoints.py)，再启动工作台。

```powershell
& 'C:\Python314\python.exe' -X utf8 -B -m scripts.init_dialogue_checkpoints
```

使用 PostgreSQL 检查点的会话可以在服务重启后重新登录同一账号继续查询。本地内存演示支持同进程内刷新。账号接入前创建的匿名会话需要开始新查询。

## 演示账号与订单归属

| 账号 | 可以进行的操作 |
| --- | --- |
| `buyer-a` | 查看本地分配的偶数末位 Olist 订单，提交和查询自己的工单 |
| `buyer-b` | 查看本地分配的奇数末位 Olist 订单，提交和查询自己的工单 |
| `staff` | 查看待处理队列、检索工单对应的店铺政策和保存处理结果 |

可在两个浏览器配置或普通窗口与隐私窗口分别登录。服务重启后需要重新登录。已登录账号的订单归属和对话检查点由服务端核对。

## 转人工工单

发送订单后，在聊天输入框旁点「转人工」，或直接发送「帮我转人工」。在聊天内填写原因，预览订单和要交接的对话，点「确认提交」后显示工单号和等待处理状态。具备店铺售后渠道的订单先提交店铺申请，再按需要申请平台介入。

[工单流程](app/tickets/workflow.py) 用 LangGraph 暂停等待确认。[PostgreSQL 工单仓储](app/storage/postgres_tickets.py) 用确认 ID 保证重复提交返回同一张工单。已提交的工单保存在 PostgreSQL，待确认预览随服务重启失效。

以 `staff` 登录后，在工单列表选择「待处理」并打开一张工单。核对订单依据后，填写给买家看的处理说明并保存。处理结果和 `public.support_ticket_audit` 的操作者、动作、时间一起提交。买家可从会话顶部的「售后进度」打开当前订单的处理记录，也可以在「我的订单」中选择订单查看。

客服工单列表支持全部、待处理和已处理三种状态，按提交时间倒序，每页 10 条。用订单号筛选关联工单，输入 `#10` 可直接打开该工单。筛选状态、订单号和当前详情保存在页面地址中，刷新后回到该记录，列表从第一页重新读取。已处理详情会显示原始原因、订单依据、处理说明、处理人和时间。代码从 [筛选路由](app/api.py) 和 [分页查询](app/storage/postgres_tickets.py) 读起。

### Olist 订单人工处理

已签收、未签收或已取消的订单都可发起人工处理。例如 `buyer-a` 可以打开已签收的 `OL-00048CC3AE`，在聊天中提交商品问题。`staff` 在客服工作台处理后，买家可以在原会话刷新人工进度，也可以从顶部「售后进度」查看结果。

同一 Olist 历史物流异常已有工单时，再申请会返回已有工单号和处理状态，并提供订单售后进度入口。历史上已经生成多张工单的订单会优先返回待处理的一张，否则返回最新一张。客服列表显示每条历史记录的申请原因，便于核对独立提交的记录。

首次预览 Olist 工单时，「随工单提交的交接内容」会列出买家诉求、已核实事实和待复核事项。有可关联的对话时，可展开查看最近四轮相关片段。点「确认提交」后，预览内容与工单一同保存，客服详情可以回看这个版本。代码可从 [交接内容](app/tickets/handoff.py) 到 [预览和确认](app/tickets/workflow.py) 再到 [工单保存](app/storage/postgres_tickets.py) 阅读。

已有数据库升级时，先运行 `python -X utf8 -B -m scripts.init_demo_access`。迁移为 `support_tickets` 增加第二个订单外键，每张工单恰好关联一笔模拟订单或一笔 Olist 订单。工单查询使用统一的订单号。代码从 [预览确认](app/tickets/workflow.py) 和 [数据库关联](sql/link_olist_support_tickets.sql) 读起。

## 店铺售后条款检索

在客服详情里输入「包装破损怎么处理？」，查看说明、条款编号和模拟手册原文。PostgreSQL 提供该订单的店铺编号，Milvus 先过滤店铺，再用中文分词和 BM25 对条款排序。DeepSeek 根据命中的条款写说明，客服核对后决定如何处理工单。

当前项目使用本机已有的 Milvus 3.0.0 镜像，发布到 `127.0.0.1:19531`。在本机 `.env` 配置 `MILVUS_URL=http://127.0.0.1:19531`。在项目终端运行：

```powershell
docker compose --profile policy-search up -d --wait --pull never --no-recreate postgres milvus
& 'C:\Python314\python.exe' -X utf8 -B -m scripts.index_policies
& 'C:\Python314\python.exe' -X utf8 -B server.py
```

[政策样本](app/policies/articles.py)用于保留店铺私有条款隔离的回归用例。导入脚本按条款编号重跑。

代码阅读顺序为 [客服接口](app/api.py) → [Milvus 检索](app/storage/milvus_policies.py) → [说明与引用校验](app/policies/search.py)。

## 本机 MCP 工具

[MCP 服务](app/integrations/mcp/server.py) 提供两个只读工具。客户端启动它作为 stdio 子进程，先通过 `initialize` 握手，再通过 `tools/list` 发现工具，通过 `tools/call` 传入参数。

| 工具 | 参数 | 返回 |
| --- | --- | --- |
| `get_order` | `order_id` | PostgreSQL 中的签收日期、店铺和退货期限 |
| `search_shop_policy` | `order_id`、`question` | 订单所属店铺和 Milvus 查到的条款编号、出处与原文 |

MCP 使用独立的 Python 环境和 [MCP 依赖](requirements-mcp.txt)。启动项目 PostgreSQL 和 Milvus 后，从项目目录运行 [独立验收客户端](scripts/mcp_probe.py)：

```powershell
& '.\.local\mcp-env\Scripts\python.exe' -X utf8 -B -m scripts.mcp_probe
```

客户端应发现两个工具。给 `get_order` 传一笔已导入的 Olist 订单号，可看到真实签收记录。用同一订单号查 `search_shop_policy`，结果应引用巴西公开电商规则。

## 买家售后条款问答

从「我的订单」选择一笔 Olist 订单并发送订单卡，输入「网购签收后想无理由退货，期限是几天？另外能改公司发票抬头吗？」。继续追问会沿用已发送的订单。同卖家订单可以沿用该卖家的历史，换卖家时打开独立对话。

右侧「继续问售后条款」也会把问题发送到同一对话。历史答复可以展开「查看条款原文」，右侧「查看工具调用」记录当前订单最近一次条款调用。

查到订单后，「查看工具调用」出现在政策输入框下方。提交政策问题后可查看请求 ID、总耗时和各阶段结果。MCP 或模型调用失败时，页面展开已有记录。拿页面上的请求 ID 可以在 `.local/logs/policy-requests.jsonl` 中查找同一次请求，确认在哪一步耗时或失败。日志只保存运行元数据。

两个角色都用 DeepSeek。材料整理 Agent 先交出工具名和参数，LangGraph 节点校验后通过 MCP 查询订单和原文。客服 Agent 根据查到的同一笔订单和原文答复。运行需要本机 DeepSeek 配置、PostgreSQL、Milvus 和已安装的独立 MCP 环境。

代码从 [LangGraph 问答](app/policies/assistance.py) 读起。实际调用由 [本机 MCP 客户端](app/integrations/mcp/client.py) 完成，计时与日志位于 [请求追踪](app/policies/trace.py)。

## 买家订单与客服订单查询

买家登录后进入 [我的订单](http://127.0.0.1:8765/orders)。可输入完整订单号或开头几位搜索，例如 `OL-0006`，也可同时筛选已签收、未签收或已取消。点击「详情」可以查看签收、商品和售后进度，再从详情带着订单号进入售后对话。

客服从工单页面进入 [订单查询](http://127.0.0.1:8765/staff/orders)，可以用买家账号和准确订单号筛选。订单详情可直接打开该订单的工单列表，工单页面上的订单号也可回到详情。

## Olist 历史物流样本

当前工作台从本机 CSV 导入了 **98,202 笔可用订单、111,916 行商品明细和 3,028 个匿名卖家**。导入检查历史物流时序、订单编号唯一性和关联明细，并在一个事务中提交。首次准备同一批本地数据时运行以下命令。两条命令省略 `--apply` 可先预览。

```powershell
python -X utf8 -B -m scripts.import_olist_dataset --apply
python -X utf8 -B -m scripts.index_olist_reference --apply
```

公开电商规则和店铺样本使用各自的 Milvus 集合。Olist 订单含真实匿名卖家关系，公开规则是可溯源的中文整理，店铺保修、发票等具体约定由人工确认。依据见[巴西《消费者保护法》](https://www.planalto.gov.br/ccivil_03/leis/l8078compilado.htm)、[第 7.962/2013 号法令](https://www2.camara.leg.br/legin/fed/decret/2013/decreto-7962-15-marco-2013-775557-publicacaooriginal-139266-pe.html)和[圣卡塔琳娜州 PROCON 网购指引](https://www.procon.sc.gov.br/veja-4-direitos-do-consumidor-em-compras-online/)。

买家从「我的订单」选中订单，发送订单卡后可以询问物流。比如 `buyer-a` 可以查询 `OL-002F19A65A`。结果依据实际签收记录、预计送达日和数据快照时间，未签收逾期以 **2018-10-17** 为判断日期。

源文件是 [Olist 发布的 Brazilian E-Commerce Public Dataset](https://www.kaggle.com/olistbr/brazilian-ecommerce/home/) 中的 `olist_orders_dataset.csv`，采用 CC BY-NC-SA 4.0 许可。本机原文件位于 `.local/datasets/olist/`。默认每类取 50 笔，保留订单状态和与物流有关的日期。导入目标是 `public.olist_orders`，与演示退货表 `public.orders` 独立。

```powershell
& 'C:\Python314\python.exe' -X utf8 -B -m scripts.import_olist
& 'C:\Python314\python.exe' -X utf8 -B -m scripts.import_olist --apply
```

第一条预览数量和样本编号，第二条才写入 PostgreSQL。相同源订单只写入一次。代码可按 [CSV 抽样](scripts/import_olist.py) → [数据库仓储](app/storage/postgres_olist.py) → [对话分支](app/orders/conversation.py) → [物流判定](app/orders/logistics.py) 的顺序看。

## Olist 商品明细

使用 `buyer-a` 查询 `OL-0008288AA4`。在「订单商品明细」问「买了几件？每件多少钱？运费多少？」，可以核对两条同商品记录、商品金额、运费与两项合计。金额以巴西雷亚尔（BRL）表示，品类沿用源数据的葡萄牙语标签。`OL-00B1CB0320` 可以用来验证取消订单缺少商品明细时的提示。

将官方 `olist_order_items_dataset.csv` 和 `olist_products_dataset.csv` 放到 `.local/datasets/olist/` 后，从项目根目录执行：

```powershell
& 'C:\Python314\python.exe' -X utf8 -B -m scripts.import_olist_items
& 'C:\Python314\python.exe' -X utf8 -B -m scripts.import_olist_items --apply
```

第一条命令只读预览已导入的 Olist 样本与 CSV 的匹配范围。第二条命令将明细写入 `public.olist_order_items`，以完整源订单号和明细序号去重。代码可按 [明细导入](scripts/import_olist_items.py) → [仓储查询](app/storage/postgres_olist.py) → [问答和金额计算](app/orders/olist_items.py) 阅读。

### 店铺售后与平台介入

买家在订单详情点击「联系店铺售后」。会话先显示「咨询此订单」卡片，点「发送订单」后把卡片写入历史，并以该订单回答物流、商品和售后条款。同一买家在同一家店铺共享聊天历史，换单时点发送才会切换上下文。订单卡和商品答复中的「查看订单详情」会在会话内展开。

咨询由售后助手自动回复。买家点「提交给店铺」才会生成店铺售后申请，人工店铺可从队列打开关联订单并答复。需要时，买家另行申请平台客服介入，平台工单详情显示店铺申请和当前答复。

「我的订单」详情按时间展示店铺申请、店铺答复、当前平台工单及其处理结果。同一笔订单已有待处理的平台工单时，买家会看到原工单和进度入口。不同的确认请求也会复用该工单。

下列命令属于早期独立店铺队列的初始化。当前商城登录使用文首「商城」的账号配置。

```powershell
& 'C:\Python314\python.exe' -X utf8 -B -m scripts.init_merchant_service
```

店铺工作台只处理所属店铺的售后申请。

Olist 历史样本可用已有商品 CSV 中的匿名 `seller_id` 归组。单卖家可以跨订单续聊，多卖家或缺少关联的样本独立咨询，并沿用平台物流复核场景。第一条命令预览关联，第二条保存关联：

```powershell
& 'C:\Python314\python.exe' -X utf8 -B -m scripts.import_olist_sellers
& 'C:\Python314\python.exe' -X utf8 -B -m scripts.import_olist_sellers --apply
```

## 买家统一对话

顶部「售后对话」先打开当前买家的历史会话列表，按最后更新时间倒序展示。选择一条会话才打开其历史，点「新对话」进入空白聊天。订单页的售后入口仍可以直接打开对应店铺和订单。会话列表和详情都从已有的 LangGraph 检查点读取。

用 `buyer-a` 在售后对话里问「这单怎么还没收到？我买了几件？」。按提示补 `OL-002F19A65A` 后，答复一起展示物流状态和商品结论。继续问「运费多少？」会沿用顶部显示的订单，前一轮答复可回看。输入「帮我转人工」会在当前聊天展开人工申请或已有工单进度。

订单资料从消息里的订单卡片展开。转人工时桌面打开右侧处理栏，窄屏在聊天消息区展开同一表单。用同一账号刷新，可以恢复按轮保存的回复。升级前只保存末轮的检查点会从其最近答复开始展示。点「选择订单」可以切换订单，直接输入另一个订单号也会重新核对归属。

[LangGraph 对话](app/orders/conversation.py) 保存当前轮的结果，并在下一轮沿用已经验证的 Olist 订单。[工具选择](app/orders/dialogue_tools.py) 通过一次 Function Calling 返回操作列表，响应无效或模型不可用时由本地规则选择。订单归属、商品金额和工单状态使用后端查询结果。

## 公开售后条款检索评测

将 Sierra Research 发布的[零售条款](https://github.com/sierra-research/tau2-bench/blob/main/data/tau2/domains/retail/policy.md)和[原始任务](https://github.com/sierra-research/tau2-bench/blob/main/data/tau2/domains/retail/tasks.json)分别存为 `.local/datasets/after-sales-terms/tau-retail/policy.md` 和 `.local/datasets/after-sales-terms/tau-retail/tasks.json`。启动本机 Milvus 并配置 `MILVUS_URL` 后运行：

```powershell
python -X utf8 -B -m scripts.evaluate_public_terms --workers 4 --stress-requests 300
```

脚本按原文标题切分条款，直接使用任务的来电原因检索。可评分的任务通过公开动作标注映射到相关章节。Hit@3 表示前 3 条中至少含一条目标条款的任务占比。Recall@3 表示每道题前 3 条召回的目标条款比例，再对所有题取平均。All@3 要求该题的全部目标条款同时出现。报告还会保存逐题命中的编号、源文件哈希和并发延迟。

可选扩容使用 [ShopTC-100K](https://huggingface.co/datasets/eltsai/ShopTC-100K) 的 `sanitized_split2.csv`，保存到同一数据目录的 `shoptc/`。以固定随机种子从退换货等售后段落里抽样，将相近条款加入同一检索池，保持问题和评分规则一致。

```powershell
python -X utf8 -B -m scripts.evaluate_public_terms --noise-count 10000 --workers 4 --stress-requests 300
```

每种语料进入独立的实验集合，浏览器继续使用店铺自己的条款。JSON 报告写入 `.local/evaluations`。该英文评测覆盖 Milvus 检索，本机并发结果仅适用于报告中的语料规模与线程数。生成回答和引用由下面的工作台 API 评测验证。

扩容评测会在同一个混合集合上额外跑一轮「限定手册来源」。所有题使用固定的适用手册来源，评分标注仍只参与评分。报告的 `eligible_articles` 记录过滤后参与检索的条款数量。公开数据只提供这份手册快照，条款类别和生效日期需要可信的业务数据才能进一步过滤。如果两个实验集合已经加载，在上述扩容命令加上 `--existing-only` 可只读复跑。

### 混合检索与重排对照

把 Xenova 发布的 [model_int8.onnx](https://huggingface.co/Xenova/multilingual-e5-small/blob/main/onnx/model_int8.onnx) 和 [tokenizer.json](https://huggingface.co/Xenova/multilingual-e5-small/blob/main/tokenizer.json) 放到 `.local/models/multilingual-e5-small/`。安装本机 CPU 评测依赖，然后使用已加载的公开实验集合运行：

```powershell
python -m pip install --target .local/retrieval-deps -r requirements-retrieval.txt
python -X utf8 -B -m scripts.evaluate_hybrid_terms
```

评测以同一已知手册为候选范围。BM25 由 Milvus 执行。语义路把范围内的条款编码成向量并在本机计算余弦相似度。RRF 按两路名次融合，重排再比较前八个候选的局部证据窗口。可加 `--lexical-weight 4` 试验偏向关键词的融合。报告保存每题候选与最终的前三条，可以核查候选阶段和排序阶段分别漏掉哪些证据。延迟计入问题编码，条款向量在计时前准备。

### 中文条款验收

将 Sierra 的 [τ-bench 2.0.0 中文零售包](https://github.com/sierra-research/tau2-bench/blob/v2.0.0/data/tau2/multilingual/zh/regional/retail.json) 放到 `.local/datasets/after-sales-terms/tau-retail-zh/retail.json`。官方包的商品和地址已经本地化，任务说明和条款正文以英文为主。项目的中文译写用公开任务编号关联动作标注，另补充 6 道手册无依据的反例。

```powershell
# 首次加载 12 段中文条款时运行一次，写入独立的评测集合
python -X utf8 -B -m scripts.evaluate_chinese_terms --prepare
# 已加载时只读复跑
python -X utf8 -B -m scripts.evaluate_chinese_terms
```

报告分别记录 35 道公开任务改写的检索分数、19 道多条款题的证据覆盖，以及无依据反例是否仍返回候选。买家和客服端在模型判定所有被问事项缺少依据时显示人工核对提示。有部分依据时，答复应引用覆盖到的条款并逐项提示缺少依据的问题。

### 原生中文条款评测

将[京东自营售后运费说明](https://help.jd.com/user/issue/131-496.html)、[京东纠纷处理规则](https://help.jd.com/user/issue/464-1501.html)和[市场监管总局 2020 修订版 PDF](https://sjfg.samr.gov.cn/law/file/pdf/3235243/1663578568290.pdf)保存在 `.local/datasets/after-sales-terms/native-zh/`。文件依次命名为 `jd-self-operated.html`、`jd-marketplace.html` 和 `samr-return-rules-2020.pdf`。继续使用本机 E5 和评测依赖，此评测还需要 `pypdf`。

```powershell
# 首次写入独立评测集合
python -X utf8 -B -m scripts.evaluate_native_terms --prepare
# 只读复跑，并用 DeepSeek 评测选定案例的最终答复
python -X utf8 -B -m scripts.evaluate_native_terms --with-answers
```

评测使用项目标注的中文问题，其中订单渠道已知。程序检查前三条召回、渠道隔离和多诉求覆盖。报告保留来源链接和 SHA256、检索候选，以及选定答复的引用和验证结果。网页显示的店铺售后手册仍按实际订单所属店铺查找。

### 独立中文检索题集

复用已经导入的原生中文条款实验集合，使用 22 道事先标注的新题对照四种策略。

```powershell
python -X utf8 -B -m scripts.evaluate_retrieval_holdout
python -X utf8 -B -m scripts.evaluate_retrieval_holdout --with-answers
```

报告记录整句 BM25、分问 BM25、分问后融合 BM25 与语义召回，以及融合后加局部重排的结果。整句基线最多取三条，后三组沿用同一套分问证据预算。每道题记录分问、候选名次、实际分配和缺失依据。`--with-answers` 额外抽查 10 道题在四种策略下的实际答复，分别核对引用、每项状态和无依据时是否转人工核对。结果写入 `.local/evaluations/`。

### 多诉求答复

打开一笔已签收的 Olist 订单，发送订单卡后问「网购签收后想无理由退货，期限是几天？另外能改公司发票抬头吗？」。缺少依据的事项会单独提示人工核对。客服可用现有 Olist 工单查询同一批公开规则。

整句前三条会保留。两个或三个明确问点可以在同一店铺范围内补查，总候选最多五条。单问使用一次 BM25 检索，模型在一次输出里写完各事项。使用原生中文条款的已加载实验集合复跑：

```powershell
python -X utf8 -B -m scripts.evaluate_aspect_terms
python -X utf8 -B -m scripts.evaluate_aspect_terms --with-answers
```

### 条款适用性复核

买家聊天和客服条款查询会复核拟发送结论，核对该事项的动作、业务阶段和对象，并校验条款原文摘录。问到两件事时，保留通过复核的答复。需人工核对的事项会在当前订单的聊天里提供申请入口。

```powershell
python -X utf8 -B -m scripts.evaluate_policy_applicability --with-answers
```

评测复用原生中文条款实验集合。报告保存 BM25 口语改写前后的证据和同一份草稿复核前后的答复。省略 `--with-answers` 时只跑检索。

## 业务评测

用京东自营和第三方订单比较查件数、问条款和混合问法的首条业务结果及整体耗时。此命令启动临时本机服务，使用已配置模型和内存会话。

```powershell
python -X utf8 -B -m scripts.evaluate_jd_chat_latency --progressive
```

混合问题可先显示商品或物流事实，随后在同一气泡补上复核通过的答复。“查看工具调用”可展开 Milvus 检索、模型起草和引用复核耗时。

启动 PostgreSQL、Milvus 和本机工作台后，在项目终端运行：

```powershell
& 'C:\Python314\python.exe' -X utf8 -B -m scripts.evaluate_workbench
```

[评测脚本](scripts/evaluate_workbench.py)使用本机演示账号登录，通过工作台 API 调用实际的 DeepSeek 和 MCP，检查两个店铺的退货判定、未知订单、恢复会话、政策引用、无匹配条款和重复确认。终端显示每项通过与否、原因和用时。报告保存在 `.local/evaluations`，每次运行会写一份新 JSON。

导入 Olist 样本后，可给评测命令加 `--with-olist`，同时核对四类历史物流状态和日期依据。

商品明细导入后可再加 `--with-olist-items`，核对多件订单的金额与缺失明细的取消样本。

需要验收超期物流工单时，加 `--with-olist-ticket`。已有工单时验证重复申请复用和账号隔离；首次运行且样本还没有工单时，创建并处理一张验收工单。

可额外指定 `--history-ticket 10`，验收该已处理工单可按订单号查到，分页顺序正确，买家账号不能访问客服列表。

加 `--with-buyer-dialogue` 可验收补订单号后的多意图、沿用订单查运费、恢复会话、跨账号拒绝和返回已有工单。

加 `--handoff-order` 和一笔尚无工单的超期 Olist 样本编号，可创建并处理一张验收单，核对交接快照、重复确认和客服审计。

重复确认检查默认使用已经提交的演示工单 #1。需要指定另一张已提交的演示工单时，在命令后加 `--demo-ticket 工单号`。某项失败时，其余检查继续，最终命令返回失败退出码。

测试命令：

```powershell
python -m pip install -r requirements-dev.txt
python -X utf8 -B -m unittest discover -s tests -v
openspec validate after-sales-workbench --strict
openspec validate postgres-order-storage --strict
openspec validate fastapi-workbench --strict
openspec validate langgraph-order-followup --strict
openspec validate apply-shop-return-policy --strict
openspec validate confirm-human-support-ticket --strict
openspec validate staff-ticket-resolution --strict
openspec validate retrieve-shop-policy-evidence --strict
openspec validate expose-read-only-mcp-tools --strict
openspec validate collaborate-on-after-sales-evidence --strict
openspec validate resume-order-dialogue --strict
openspec validate evaluate-after-sales-workflows --strict
```

## 后续开发

| 技术 | 接到哪个场景 | 验收方式 |
| --- | --- | --- |
| FastAPI | 已接入查询接口、请求模型与 OpenAPI | 用页面或接口文档查询一笔已导入订单 |
| LangGraph | 追问、政策、人工工单确认和买家会话恢复 | 追问后刷新，再补订单号 |
| PostgreSQL | 已存储订单、店铺政策和人工工单 | 重复确认返回同一张工单 |
| Milvus + RAG | 已接入可信范围过滤、分问 BM25、引用校验；混合检索和重排用于对照评测 | 在 Olist 订单对话展开公开规则出处 |
| MCP | 从本机 stdio 客户端调用订单和政策查询 | 在 MCP 环境用 `-m scripts.mcp_probe` 验证工具发现与调用 |
| 多 Agent | 材料整理 Agent 调用 MCP，客服 Agent 据此答复 | 发订单卡并追问，核对原文和工具调用 |

规格与验收条件见 [OpenSpec 变更](openspec/changes/after-sales-workbench/proposal.md)。
