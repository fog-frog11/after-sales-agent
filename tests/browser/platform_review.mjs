/** 用真实 Chrome 回归平台介入后的寄回、确认收件和超时复核。 */
import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {existsSync, mkdtempSync, readFileSync, writeFileSync} from "node:fs";
import {join} from "node:path";
import {setTimeout as delay} from "node:timers/promises";

const python = process.env.MALL_TEST_PYTHON ?? (existsSync("C:/Python314/python.exe") ? "C:/Python314/python.exe" : "python");
const executable = process.env.MALL_TEST_CHROME ?? "C:/Program Files/Google/Chrome/Application/chrome.exe";
const host = spawn(python, ["-X", "utf8", "-B", "-c",
  "import runpy; runpy.run_path('tests/browser/platform_review_fixture.py', run_name='__main__')"],
  {windowsHide: true, stdio: ["ignore", "pipe", "pipe"]});
let chrome;
let socket;
let stdout = "";
let stderr = "";
let serial = 0;
let origin;
let password;
const callbacks = new Map();
const pageErrors = [];
host.stdout.on("data", chunk => { stdout += chunk; });
host.stderr.on("data", chunk => { stderr += chunk; });

/** 等待 UI 或临时服务就绪，超时信息标明当前验收步骤。 */
async function eventually(check, label, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const value = await check().catch(() => null);
    if (value) return value;
    await delay(100);
  }
  throw new Error(label);
}

/** 在真实浏览器执行页面交互，并等待浏览器返回 DOM 状态。 */
function cdp(method, params = {}) {
  const id = ++serial;
  return new Promise((resolve, reject) => {
    callbacks.set(id, {resolve, reject});
    socket.send(JSON.stringify({id, method, params}));
  });
}
async function evaluate(expression) {
  const result = await cdp("Runtime.evaluate", {expression, awaitPromise: true, returnByValue: true});
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
  return result.result.value;
}
const expect = (expression, label) => eventually(() => evaluate(expression), label);
const go = path => cdp("Page.navigate", {url: origin + path});
const preview = process.env.MALL_UI_PREVIEW === "1";
const afterSalesPreview = process.env.MALL_UI_PREVIEW === "after-sales";
const orderPreview = process.env.MALL_UI_PREVIEW === "orders";
const desktopOnly = process.env.MALL_DESKTOP_ONLY === "1";
const previewStamp = Date.now();

/** 可选保存真实浏览器的验收截图，文件只放本地。 */
async function snapshot(label) {
  if (!preview && !(afterSalesPreview && label.startsWith("case-") && label.endsWith("-wide"))
      && !(orderPreview && label.startsWith("order-") && label.endsWith("-wide"))) return;
  await evaluate("Promise.allSettled(document.getAnimations().filter(a=>a.effect.getComputedTiming().endTime<1500).map(a=>a.finished)).then(()=>true)");
  const path = join(".local", "react-" + label + "-" + previewStamp + ".png");
  const screenshot = await cdp("Page.captureScreenshot", {format: "png"});
  writeFileSync(path, Buffer.from(screenshot.data, "base64"), {flag: "wx"});
  console.log(path);
}

const homes = {"buyer-001": "/mall/orders", "shop-001": "/merchant/mall", staff: "/staff/disputes"};
const cases = {...homes, "buyer-001": "/mall/after-sales"};
let role;

/** 通过登录表单切换角色并打开指定原单。 */
async function login(account, number, loginName = account) {
  await go("/login");
  await expect("document.readyState==='complete' && typeof loginToWorkbench==='function'", "登录页未加载");
  await evaluate("document.querySelector('#access-account').value=" + JSON.stringify(loginName) +
    ";document.querySelector('#access-secret').value=" + JSON.stringify(password) +
    ";document.querySelector('#access-login').requestSubmit();true");
  await expect("location.pathname===" + JSON.stringify(homes[account]), "未进入角色工作台");
  role = account;
  if (number) await go(cases[role] + "?case=" + number);
}

/** 选择可见操作并通过实际表单提交。 */
async function choose(action) {
  await expect("Boolean(document.querySelector('#mall-action option[value=" + action + "]'))", "缺少可用操作 " + action);
  await evaluate("document.querySelector('#mall-action').value=" + JSON.stringify(action) +
    ";document.querySelector('#mall-action').dispatchEvent(new Event('change',{bubbles:true}));true");
}
async function submit(note) {
  await evaluate("document.querySelector('#mall-operation [name=note]').value=" + JSON.stringify(note) +
    ";document.querySelector('#mall-operation').requestSubmit();true");
}
function state(label) {
  return expect("document.querySelector('#mall-case-next')?.textContent.includes(" + JSON.stringify(label) + ")", "未进入状态 " + label);
}
async function click(id) {
  await expect("Boolean(document.getElementById(" + JSON.stringify(id) + "))", "未找到按钮 " + id);
  await evaluate("document.getElementById(" + JSON.stringify(id) + ").click();true");
}
const text = (id, phrase) => expect("document.querySelector('#" + id + "')?.textContent.includes(" +
  JSON.stringify(phrase) + ")", "页面缺少 " + phrase);

async function chat(message) {
  await evaluate("document.querySelector('#mall-consult [name=question]').value=" + JSON.stringify(message) +
    ";document.querySelector('#mall-consult').requestSubmit();true");
}

/** 从实际提交到答复进入页面，并读取浏览器收到的服务端分段计时。 */
async function timedChat(message) {
  const expression = `new Promise((resolve,reject)=>{
    const form=document.querySelector('#mall-consult');
    const list=document.querySelector('#mall-dialogue');
    const count=list.querySelectorAll('.chat-reply').length;
    const started=performance.now();
    const observer=new MutationObserver(()=>{
      const answers=list.querySelectorAll('.chat-reply');
      if(answers.length<=count || !answers[answers.length-1].querySelector('p')) return;
      observer.disconnect();
      clearTimeout(timer);
      const visibleAt=performance.now();
      const response=performance.getEntriesByType('resource').filter(e=>e.startTime>=started && e.name.endsWith('/consult')).at(-1);
      resolve({visibleMs:visibleAt-started,responseMs:response?.duration,
        server:Object.fromEntries((response?.serverTiming??[]).map(entry=>[entry.name,entry.duration])),
        answer:answers[answers.length-1].textContent});
    });
    const timer=setTimeout(()=>{observer.disconnect();reject(new Error('咨询没有及时显示答复'))},20000);
    observer.observe(list,{childList:true,subtree:true});
    form.elements.question.value=${JSON.stringify(message)};
    form.requestSubmit();
  })`;
  const sample = await evaluate(expression);
  assert.ok(sample.server.consult >= 0 && sample.responseMs >= 0, "浏览器未收到咨询分段计时");
  return sample;
}

/** 买家从两个店铺的订单进入咨询，核对多项答复、条款隔离与改口后的申请草稿。 */
async function multiIntentJourney() {
  await login("buyer-001");
  const question = "充电线少了一根，外盒也压扁了。线能补寄吗？照片要拍哪些？";
  for (const [order,present,absent] of [
    ["CN-9868","甲店补发时请写清配件型号","乙店补发时请标明缺件位置"],
    ["CN-9869","乙店补发时请标明缺件位置","甲店补发时请写清配件型号"],
  ]) {
    await go("/mall/orders?order=" + order + "&item=1");
    await text("mall-chat-order", order);
    const answer = (await timedChat(question)).answer;
    assert.ok(answer.includes(present) && answer.includes("压坏的位置") && answer.includes("补发或仅退款"), answer);
    assert.ok(!answer.includes(absent) && !answer.includes("下单后批准"), answer);
    const path = "/api/buyer/mall/orders/" + order + "/items/1/consult";
    const route = () => evaluate("fetch("+JSON.stringify(path)+").then(r=>r.json()).then(r=>r.turns.at(-1).result.route)");
    assert.equal(await route(),"rules");
    const description = "document.querySelector('#mall-application [name=description]')";
    assert.equal(await evaluate(description+".value"),"充电线少了一根");
    if (order === "CN-9868") {
      const fallback = await timedChat(question + "这件买了几件？");
      assert.equal(await route(),"fallback");
      assert.ok(fallback.answer.includes("压坏的位置") && fallback.answer.includes("补发或仅退款") &&
        /买了 \d+ 件/.test(fallback.answer),fallback.answer);
    } else {
      await evaluate(description+".value='我自己补充的内容';true");
    }
    const corrected = await timedChat("线找到了，只是外盒破了，照片要拍哪些？");
    assert.ok(corrected.answer.includes("缺件建议已经撤掉") && corrected.answer.includes("破损处") &&
      !corrected.answer.includes("本身也有损坏吗"),corrected.answer);
    assert.equal(await evaluate("document.querySelector('#mall-reason').value"),"");
    assert.equal(await evaluate(description+".value"),order==="CN-9868"?"":"我自己补充的内容");
  }
}

/** 验收体验反馈经人工介入查询后仍可继续办理退货，并送达店铺。 */
async function productExperienceJourney() {
  await login("buyer-001");
  const order = "/mall/orders?order=CN-9866&item=1";
  const description = "document.querySelector('#mall-application [name=description]')";
  const complaint = "我这个订单的坐垫坐着不舒服哎";
  await go(order);
  await text("mall-chat-order", "CN-9866");
  const first = await timedChat(complaint);
  assert.match(first.answer, /坐着不舒服|哪里|使用|想.*退|坐垫/);
  assert.equal(await evaluate("document.querySelector('#mall-reason').value"), "");

  assert.match((await timedChat("申请人工介入")).answer, /还没有售后单/);
  const result = await timedChat("我觉得不舒服 能退货吗？");
  assert.match(result.answer, /期限至.*可申请退货退款/);
  assert.doesNotMatch(result.answer, /你是买错了|商品有质量问题/);
  await expect("document.querySelector('#mall-reason').value==='wrong_purchase' && " + description + ".value===" +
    JSON.stringify(complaint), "截图三轮对话没有恢复原因和说明");
  await go(order);
  await expect(description + "?.value===" + JSON.stringify(complaint), "刷新后没有恢复本商品的说明");
  await expect("Boolean(document.querySelector('#mall-kind option[value=return_refund]'))", "刷新后缺少合法的退货方式");
  await evaluate("document.querySelector('#mall-kind').value='return_refund'; document.querySelector('#mall-application').requestSubmit();true");
  const number = await expect("location.pathname==='/mall/after-sales' && new URLSearchParams(location.search).get('case')",
    "退货申请没有提交成功");
  await state("待店铺响应");
  await login("shop-001", number);
  await state("待店铺响应");
  await login("buyer-001");
  await go(order);
  await text("mall-chat-order", "CN-9866");
  const repeat = await timedChat("还是坐着不舒服，这件还可以退吗？");
  assert.ok(repeat.answer.includes("售后单 #" + number), repeat.answer);
}

/** 每轮都使用真实规划和回复模型，检查记忆、纠正、跨话题和三角色业务边界。 */
async function semanticJourney() {
  await login("buyer-001");
  const samples = [];
  const history = id => evaluate(`fetch('/api/buyer/mall/orders/${id}/items/1/consult').then(r=>r.json()).then(r=>r.turns)`);
  async function ask(id, question) {
    const sample = await timedChat(question);
    const result = (await history(id)).at(-1).result;
    samples.push({order:id, question, ...sample, result});
    console.log(JSON.stringify({question, answer:result.answer, route:result.route,
      generated:result.generation_status, generationError:result.generation_error, topics:result.topics, reason:result.reason}));
    assert.equal(result.route, "function_call", "真实模型规划失败: " + question);
    assert.ok(['grounded','fallback'].includes(result.generation_status), "回复未记录生成结果: " + question);
    assert.ok(result.answer.length < 430);
    return result;
  }
  await go('/mall/orders?order=CN-9870&item=1');
  await text('mall-chat-order','CN-9870');
  let result = await ask('CN-9870','围巾围起来有点紧，想换宽松一点的');
  assert.equal(result.reason,'wrong_purchase');
  assert.equal(result.recommended_kind,'exchange');
  await ask('CN-9870','那就换大一码吧');
  await ask('CN-9870','顺便看看这单快递的状态');
  result = await ask('CN-9870','我刚才说想怎么处理来着？');
  assert.ok(/换|大一码/.test(result.answer), result.answer);
  await go('/mall/orders?order=CN-9870&item=1');
  await expect("document.querySelector('#mall-application [name=description]')?.value.includes('大一码')",
    '刷新丢失了已确认的目标规格');
  result = await ask('CN-9870','我说错了，不是尺寸问题，是寄来的颜色不对，我想退掉');
  assert.equal(result.reason,'wrong_item');
  assert.equal(result.recommended_kind,'return_refund');
  assert.ok(!result.suggested_description.includes('大一码'), '旧规格污染了新原因');
  await ask('CN-9870','没有开箱录像还能申请吗？');
  result = await ask('CN-9870','帮我写个旅游攻略吧');
  assert.equal(result.response_kind,'out_of_scope');
  result = await ask('CN-9870','回到刚才，退回的快递费呢？');
  assert.equal(result.reason,'wrong_item');
  assert.ok(result.retrieval?.routes.includes('milvus_bm25'), '主聊天没有使用 Milvus');
  assert.ok(result.retrieval?.routes.includes('e5_dense'), '主聊天没有使用语义召回');
  if(process.env.MALL_TEST_PERSISTENT_DENSE==='1') assert.ok(result.retrieval?.routes.includes('milvus_dense'),'主聊天没有使用Milvus稠密索引');
  if(process.env.MALL_TEST_WINDOW_RERANK==='1') assert.ok(result.retrieval?.routes.includes('rrf_prior'),'窗口排序没有保留融合名次');
  result = await ask('CN-9870','刚才退运费的依据给我看看');
  assert.ok(result.evidence?.length > 0, '追问依据没有可追溯原文');
  await expect("document.querySelector('#mall-reason')?.value==='wrong_item'", '表单原因与记忆不一致');
  await evaluate("document.querySelector('#mall-kind').value='return_refund';document.querySelector('#mall-kind').dispatchEvent(new Event('change',{bubbles:true}));true");
  await expect("Boolean(document.querySelector('#mall-application [name=description]'))", '申请框未就绪');
  await evaluate("document.querySelector('#mall-application [name=description]').value='收到的颜色不符，请按退货退款处理';document.querySelector('#mall-application').requestSubmit();true");
  const number = await expect("location.pathname==='/mall/after-sales' && new URLSearchParams(location.search).get('case')", '申请没有到达售后进度');
  await login('shop-001',number);
  await state('待店铺响应');
  await login('buyer-001');
  await go('/mall/orders?order=CN-9870&item=1');
  await text('mall-chat-order','CN-9870');
  result = await ask('CN-9870','我申请的退货现在谁处理，别再让我重开一单');
  assert.equal(String(result.case_no),number);
  assert.ok(!result.eligibility, '已有售后还在引导重复开单');
  // 换商品时不得继承上一件的发错颜色和目标规格。
  await go('/mall/orders?order=CN-9877&item=1');
  await text('mall-chat-order','CN-9877');
  result = await ask('CN-9877','拆开快递，里面是空的，箱子也被挤烂了');
  assert.equal(result.reason,'missing');
  result = await ask('CN-9877','我没留开箱视频，只有快递箱的照片');
  assert.doesNotMatch(result.answer,/哪个商品|哪笔订单|哪个凭证/);
  result = await ask('CN-9877','东西找到了，之前的少件说错了，只是盒子变形');
  assert.ok(!result.kinds?.reship, '撤回缺件后仍推荐补发');
  result = await ask('CN-9877','盒子里面的东西其实完好，这样我先不申请了');
  assert.ok(!result.kinds?.reship);
  result = await ask('CN-9877','我以前买的针织围巾帮我找一下');
  assert.ok(result.order_candidates?.some(row=>row.order_id==='CN-9870'), '自然语言找单没有查询本人订单');
  result = await ask('CN-9877','我还想回顾一下以前在这买过哪些东西');
  assert.ok(result.purchase_history && result.order_candidates?.length > 0, '模型没有切换到本人历史范围');
  result = await ask('CN-9877','就里面价格最便宜的那件');
  assert.ok(result.topics?.includes('clarify_order') && result.candidate_filter?.price==='lowest', '没有按真实成交价筛选候选');
  const generated = samples.filter(s=>s.result.generation_status==='grounded').length;
  assert.ok(generated / samples.length >= .9, '模型回复降级超过本组10%');
  const ms = samples.map(s=>s.visibleMs).sort((a,b)=>a-b);
  const report = {turns:samples.length,generated, p50Ms:ms[Math.floor(ms.length*.5)],
    p95Ms:ms[Math.min(ms.length-1,Math.floor(ms.length*.95))], samples};
  const output = join('.local','evaluations','semantic-live-'+Date.now()+'.json');
  writeFileSync(output,JSON.stringify(report,null,2),{flag:'wx'});
  console.log(output);
}

/** 尺寸偏小后补充目标规格，换货申请从对话送达店铺。 */
async function fitExchangeJourney() {
  await login("buyer-001");
  const order = "/mall/orders?order=CN-9870&item=1";
  await go(order);
  await text("mall-chat-order", "CN-9870");
  const first = await timedChat("这个围巾偏小");
  assert.match(first.answer, /这单可以申请换货/);
  assert.doesNotMatch(first.answer, /标注的尺寸|您说的偏小/);
  const history = () => evaluate("fetch('/api/buyer/mall/orders/CN-9870/items/1/consult').then(r=>r.json()).then(r=>r.turns)");
  const latest = (await history()).at(-1).result;
  if (process.env.MALL_LIVE_SMOKE === "1") assert.equal(latest.route, "function_call");
  assert.equal(latest.recommended_kind, "exchange");
  await expect("document.querySelector('#mall-kind')?.value==='exchange'", "表单未推荐换货");
  const button = "document.querySelector('#mall-dialogue li:last-child .button-soft')";
  await expect(button + "?.textContent.includes('填写换货申请')", "未显示换货申请入口");
  await evaluate(button + ".click();true");
  await expect("document.querySelector('#mall-kind')?.value==='exchange'", "重新核对后未保留换货建议");
  await timedChat("那就大一码");
  const description = "document.querySelector('#mall-application [name=description]')";
  await expect(description + ".value.includes('那就大一码')", "申请未带入目标规格");
  await go(order);
  await expect(description + "?.value.includes('那就大一码') && document.querySelector('#mall-kind')?.value==='exchange'",
    "刷新后没有恢复换货方式和目标规格");
  await evaluate("document.querySelector('#mall-application').requestSubmit();true");
  const number = await expect("location.pathname==='/mall/after-sales' && new URLSearchParams(location.search).get('case')",
    "换货申请没有提交成功");
  await login("shop-001", number);
  await state("待店铺响应");
  await evaluate("document.querySelector('#show-case-history').click();true");
  await expect("document.body.innerText.includes('换货') && document.querySelector('#mall-timeline')?.innerText.includes('那就大一码')",
    "店铺没有看到买家的换货申请和目标规格");
}

/** 买家否定分类后补充到货情况，模型追问不能循环，表单按核对结果更新。 */
async function clarificationJourney() {
  await login("buyer-001");
  await go("/mall/orders?order=CN-9870&item=1");
  await text("mall-chat-order", "CN-9870");
  const reason = "document.querySelector('#mall-reason').value";
  const description = "document.querySelector('#mall-application [name=description]').value";
  const history = () => evaluate("fetch('/api/buyer/mall/orders/CN-9870/items/1/consult').then(r=>r.json()).then(r=>r.turns)");
  const first = await timedChat("这个玻璃保鲜盒打开后碎了");
  assert.equal((await history()).at(-1).result.answer_source, "model");
  assert.match(first.answer, /[？?]/);
  assert.doesNotMatch(first.answer, /可能属于|质量问题/);
  assert.equal(await evaluate(reason), "");
  const rejection = await timedChat("不是这个原因");
  assert.notEqual(rejection.answer, first.answer);
  assert.doesNotMatch(rejection.answer, /可能属于|质量问题/);
  const evidence = "买来的时候里面盒子打开已经碎了";
  const reply = await timedChat(evidence);
  assert.match(reply.answer, /拆包时就发现破损/);
  assert.doesNotMatch(reply.answer, /可能属于|质量问题/);
  await timedChat("这件还可以退吗？");
  assert.equal(await evaluate(reason), "quality");
  assert.equal(await evaluate(description), evidence);
  assert.ok(await evaluate("Boolean(document.querySelector('#mall-kind option[value=return_refund]'))"));
}

/** 检查同一商品的新反馈和无关话题对在办草稿的不同影响。 */
async function conversationScopeJourney() {
  await login("buyer-001");
  await go("/mall/orders?order=CN-9870&item=1");
  await text("mall-chat-order", "CN-9870");
  const reason = "document.querySelector('#mall-reason').value";
  const description = "document.querySelector('#mall-application [name=description]').value";
  await timedChat("打开是空盒，并且快递包装也压坏了");
  assert.equal(await evaluate(reason), "missing");
  const draft = await evaluate(description);

  const offTopic = await timedChat("你能帮我写首关于海边的诗吗？");
  assert.match(offTopic.answer, /售后/);
  assert.doesNotMatch(offTopic.answer, /空盒|缺件|补发|海边/);
  assert.equal(await evaluate(reason), "missing");
  assert.equal(await evaluate(description), draft);
  if (process.env.MALL_LIVE_WRITER === "1") {
    const saved = await evaluate("fetch('/api/buyer/mall/orders/CN-9870/items/1/consult').then(r=>r.json())");
    assert.equal(saved.turns.at(-1).result.answer_source, "model");
  }
  assert.match((await timedChat("这件商品的物流到哪了？")).answer, /已有签收记录/);

  const complaint = "这个毛刷用着效果不太好";
  const clarified = await timedChat(complaint);
  assert.match(clarified.answer, /效果|不好用|预期|使用|哪里/);
  assert.doesNotMatch(clarified.answer, /漏发|补发|填写商品售后申请/);
  assert.equal(await evaluate(reason), "");
  assert.equal(await evaluate(description), "");

  assert.match((await timedChat("这件还可以退吗？")).answer, /可申请退货退款/);
  assert.equal(await evaluate(reason), "wrong_purchase");
  assert.equal(await evaluate(description), complaint);
  const mixed = await timedChat("写首诗，顺便查一下原包裹到哪了？");
  assert.match(mixed.answer, /已有签收记录/);
  assert.equal(await evaluate(reason), "wrong_purchase");
}

/** 检查查看本单条款、追问、无依据及正常物流答复能在同一窗口使用。 */
async function citationJourney() {
  await login("buyer-001");
  await go("/mall/orders?order=CN-9870&item=1");
  await text("mall-chat-order","CN-9870");
  const ask = await timedChat("查本单条款");
  assert.ok(ask.answer.includes("你想查哪一项"),ask.answer);
  await expect("document.querySelector('.consult-choices')?.innerText.includes('退货运费')","条款追问没有可用选项");
  const checked = await timedChat("查本单条款：退货运费谁出？有会员包邮吗？");
  assert.ok(checked.answer.includes("找不到能核实「会员运费权益」") &&
    checked.answer.includes("下单条款 · 第") && checked.answer.includes("优先抵扣"),checked.answer);
  const tracked = await timedChat("原包裹到哪了？能上门维修吗？");
  assert.ok(tracked.answer.includes("TEST9877") && tracked.answer.includes("找不到能核实「上门维修」"),tracked.answer);
  const unsupported = await timedChat("有没有终身免费换新？");
  assert.ok(unsupported.answer.includes("找不到能核实「终身换新」"),unsupported.answer);
  const why = await timedChat("那依据呢？");
  assert.ok(why.answer.includes("找不到能核实「终身换新」"),why.answer);
  assert.equal(await evaluate("document.querySelector('#mall-dialogue li:last-child blockquote')===null"),true);
}

/** 使用相同入口分别测本地规则和需要外部模型的口语复合题。 */
async function consultPerformance() {
  await login("buyer-001");
  await go("/mall/orders?order=CN-9870&item=1");
  await text("mall-chat-order","CN-9870");
  const url = "/api/buyer/mall/orders/CN-9870/items/1/consult";
  const route = () => evaluate(`fetch('${url}').then(r=>r.json()).then(r=>r.turns.at(-1).result.route)`);
  const simple = [];
  const complex = [];
  for (const [question, expected] of [
    ["无线鼠标少了充电线","可以申请补发"],
    ["那我这个可以申请售后吧？","补发或仅退款"],
    ["那要把整套寄回吗？","不用为申请补发把整套寄回"],
    ["补发到哪了？","还没有补发申请"],
    ["找到了，不缺了","缺件原因先撤掉"],
    ["那我这个可以申请售后吧？","商品有问题"],
  ]) {
    const sample = await timedChat(question);
    assert.ok(sample.answer.includes(expected), sample.answer);
    assert.equal(await route(), "rules");
    simple.push(sample);
  }
  for (let repeat=0; repeat<3; repeat++) {
    const sample = await timedChat("那款啥样，款子回了没");
    assert.ok(sample.answer.includes("暂时没有售后申请"),sample.answer);
    assert.equal(await route(),"function_call");
    complex.push(sample);
  }
  const summarize = samples => {
    const middle = values => {
      values.sort((a,b)=>a-b);
      return Math.round((values[Math.floor(values.length/2)] + values[Math.floor((values.length-1)/2)]) / 2);
    };
    return {count:samples.length,visibleMs:middle(samples.map(s=>s.visibleMs)),
      slowestMs:Math.round(Math.max(...samples.map(s=>s.visibleMs))),
      httpMs:middle(samples.map(s=>s.responseMs)),planMs:middle(samples.map(s=>s.server.plan)),
      orderMs:middle(samples.map(s=>s.server.order))};
  };
  console.log(JSON.stringify({rules:summarize(simple),complex:summarize(complex),live:process.env.MALL_LIVE_SMOKE==="1"}));
}
async function expire(number) {
  assert.equal(await evaluate("fetch('/_test/expire/" + number + "',{method:'POST'}).then(r=>r.status)"), 200);
  await click("mall-refresh");
  await text("mall-intervention-start", "申请平台复核");
}
function enterDetails() {
  return evaluate("document.querySelector('#mall-return-details [name=recipient]').value='验收收件人';" +
    "document.querySelector('#mall-return-details [name=address]').value='本地演练园区一号收件点';" +
    "document.querySelector('#mall-return-details [name=phone]').value='01012340000';true");
}
function caseAction(number, action, data = {}) {
  return evaluate("fetch('/api/buyer/mall/cases/" + number + "/actions',{method:'POST'," +
    "headers:{'Content-Type':'application/json'},body:JSON.stringify({operation_id:crypto.randomUUID(),action:" +
    JSON.stringify(action) + ",note:'验收操作',..." + JSON.stringify(data) + "})}).then(r=>r.status)");
}
function deadlineOf(number) {
  return evaluate("fetch('/api/buyer/mall/cases/" + number + "').then(r=>r.json()).then(data=>data.due_at)");
}
function addPhoto() {
  return evaluate("(async()=>{const canvas=document.createElement('canvas');canvas.width=24;canvas.height=18;" +
    "const brush=canvas.getContext('2d');brush.fillStyle='#996633';brush.fillRect(0,0,24,18);" +
    "const blob=await new Promise(ok=>canvas.toBlob(ok,'image/jpeg'));" +
    "const transfer=new DataTransfer();transfer.items.add(new File([blob],'test.jpg',{type:'image/jpeg'}));" +
    "document.querySelector('#mall-operation-photos').files=transfer.files;return true})()");
}

/** 从用户页提交入单、拒绝方案，并从聊天进入真实的平台申请。 */
async function platformApproves(item, orderId = "CN-9877") {
  const order = "/mall/orders?order=" + orderId + "&item=" + item;
  await login("buyer-001");
  await go(order);
  await expect("document.querySelector('#mall-item-view')?.hidden===false", "临时订单未显示");
  await evaluate("document.querySelector('#mall-reason').value='quality';document.querySelector('#mall-reason').dispatchEvent(new Event('change',{bubbles:true}));true");
  await expect("document.querySelector('#mall-application-fields')?.hidden===false", "退货申请表未打开");
  await evaluate("document.querySelector('#mall-kind').value='return_refund';document.querySelector('#mall-application [name=description]').value='商品外壳损坏';document.querySelector('#mall-application').requestSubmit();true");
  const number = await expect("location.pathname==='/mall/after-sales' && new URLSearchParams(location.search).get('case')", "未提交临时售后单");

  await login("shop-001", number);
  await choose("negotiate");
  await evaluate("document.querySelector('#mall-offer-kind').value='partial_refund';document.querySelector('#mall-offer-kind').dispatchEvent(new Event('change',{bubbles:true}));true");
  await expect("Boolean(document.querySelector('#mall-offer-amount'))", "退款金额字段未显示");
  await evaluate("document.querySelector('#mall-offer-amount').value='10';true");
  await submit("商家提出留货退款");
  await state("待买家确认方案");
  await login("buyer-001", number);
  await choose("reject_offer");
  await submit("买家希望退货");
  await text("mall-intervention-start", "申请平台介入");
  await go(order);
  await expect("document.querySelector('#mall-item-view')?.hidden===false", "商品对话未打开");
  await chat("申请人工介入");
  await expect("document.querySelector('#mall-dialogue li:last-child a')?.href.includes('case=" + number + "&action=dispute')", "聊天跳转未指向当前原单");
  await evaluate("document.querySelector('#mall-dialogue li:last-child a').click();true");
  await expect("document.querySelector('#mall-action')?.value==='dispute'", "聊天跳转未打开介入表单");
  await submit("申请原单平台介入");
  await state("平台处理中");
  await login("staff", number);
  await choose("support_buyer");
  await submit("支持原退货申请");
  await state("待店铺补充寄回信息");
  return {number, order};
}

/** 从 README 对应的三个账户和固定订单实际走完项目案例。 */
async function portfolioJourney() {
  const guide = JSON.parse(readFileSync(join("app","portfolio_cases.json"),"utf8"));
  const [reship, dispute, edition] = guide.cases;
  const reshipId = reship.orders[0].id;
  const page = id => "/mall/orders?order=" + id + "&item=1";
  await login(guide.buyer);
  await go(page(dispute.orders[0].id));
  assert.equal(await evaluate("Boolean(document.querySelector('nav a[href=\"/mall/portfolio\"]'))"), false,
    "买家业务导航出现了项目案例入口");
  await text("mall-chat-order", dispute.orders[0].id);
  const empty = await timedChat("打开是空盒，并且快递包装也压坏了");
  assert.match(empty.answer, /空盒.*少件漏发/);
  await expect("document.querySelector('#mall-application [name=reason]').value==='missing'"
    + " && document.querySelector('#mall-application [name=description]').value.includes('快递包装也压坏了')"
    + " && document.querySelector('#mall-application').textContent.includes('建议准备的凭证')",
    "空盒、破损事实和凭证应与草稿一致");
  const evidenceHelp = await timedChat("我没拍开箱视频");
  assert.match(evidenceHelp.answer, /这件商品的少件漏发.*手头现有的材料/);
  assert.doesNotMatch(evidenceHelp.answer, /哪个商品|哪笔订单|哪个凭证|DeepSeek 根据查询结果撰写/);
  assert.match((await timedChat("这样还能提交吗？")).answer, /补发或仅退款/);
  await go(page(dispute.orders[0].id));
  await expect("document.querySelector('#mall-application [name=description]')?.value.includes('快递包装也压坏了')",
    "刷新后应从对话恢复完整申请说明");
  await evaluate("document.querySelector('#mall-application [name=description]').value='我已经拍了箱内照片';true");
  await timedChat("我说错了，商家发错型号了");
  await expect("document.querySelector('#mall-application [name=reason]')?.value==='wrong_item'"
    + " && document.querySelector('#mall-application [name=description]').value==='我已经拍了箱内照片'",
    "买家改口后应更新自动识别的原因，并保留手写说明");
  await go("/mall/portfolio");
  await expect("document.querySelectorAll('.portfolio-case h2').length===3 && Boolean(document.querySelector('.portfolio-case a'))",
    "项目案例页未展示三条路线或买家入口");
  await evaluate("document.querySelector('.portfolio-case a').click();true");
  await text("mall-chat-order", reshipId);
  const initial = await timedChat(reship.question);
  assert.ok(initial.answer.includes("补发或仅退款") && initial.answer.includes("压坏的位置"), initial.answer);
  await expect("document.querySelector('#mall-dialogue li:last-child .consult-trace')?.textContent.includes('本地规则')",
    "案例未展示实际查询路径");
  await expect("document.querySelector('#mall-application [name=description]')?.value==='充电线少了一根'", "案例没有预填缺件说明");
  const noMatch = await timedChat("找订单：不存在的测试品类ZZ987");
  if (process.env.MALL_LIVE_WRITER !== "1") assert.ok(noMatch.answer.includes("没找到"));
  await timedChat("还记得我原先买的充电线吗？");
  await expect("document.querySelectorAll('#mall-dialogue li:last-child .order-search-results a').length>=2",
    "多笔匹配未提供候选商品");
  if (process.env.MALL_LIVE_WRITER === "1") {
    await expect("document.querySelector('#mall-dialogue li:last-child .consult-trace')?.textContent.includes('DeepSeek 根据查询结果撰写')",
      "没有收到真实模型撰写的找单回复");
  }
  await timedChat("那你还记得我买过啥？");
  await expect(`document.querySelectorAll('#mall-dialogue li:last-child .order-search-results li').length===6
    && Boolean(document.querySelector('#mall-dialogue li:last-child > a[href="/mall/orders"]'))`,
    "购买历史应展示最近商品和全部订单入口");
  await timedChat("我想回顾一下以往在这里下单买的东西");
  await expect(`document.querySelectorAll('#mall-dialogue li:last-child .order-search-results li').length===6
    && document.querySelector('#mall-dialogue li:last-child .consult-trace')?.textContent.includes('DeepSeek 规划查询')`,
    "模型应把陌生的历史问法路由到本人订单");
  const ambiguous = await timedChat("那单退货运费呢？");
  assert.ok(ambiguous.answer.includes("你指哪一笔"), ambiguous.answer);
  await expect("document.querySelectorAll('#mall-dialogue li:last-child .order-search-results a').length===6",
    "指代不明时应保留同一批候选");
  const explanation = await timedChat("依据呢？");
  assert.ok(explanation.answer.includes("接口型号"),explanation.answer);
  const current = await timedChat("这单买的啥？");
  assert.ok(current.answer.includes("无线鼠标"), current.answer);
  await expect("!document.querySelector('#mall-dialogue li:last-child .order-search-results')",
    "明确问这单时不应展示搜索结果");
  await timedChat("第二笔呢？");
  await expect("Boolean(document.querySelector('#mall-dialogue li:last-child > a[href=\"/mall/orders\"]'))",
    "当前没有候选时应提供订单入口");
  await text("mall-chat-order", reshipId);
  assert.equal(await evaluate("document.querySelector('#mall-application [name=description]').value"), "充电线少了一根");
  await timedChat("找订单：编织充电线");
  const mixed = await timedChat("这单退货要我出邮费吗？刚才那单还能退吗？");
  assert.match(mixed.answer, /运费|邮费|原因|无需寄回/);
  await expect(`document.querySelector('#mall-dialogue li:last-child .consult-trace')?.textContent.includes(${JSON.stringify(reshipId)})`,
    "已回答的一半应使用当前鼠标订单");
  const candidate = ".order-search-results a[href=" + JSON.stringify(page(edition.orders[1].id)) + "]";
  const pendingLink = "#mall-dialogue li.chat-reply:last-child " + candidate;
  const resumePath = "/api/buyer/mall/orders/" + reshipId + "/items/1/consult/continue";
  const resume = turn => evaluate(`fetch(${JSON.stringify(resumePath)}, {
    method:"POST", headers:{"Content-Type":"application/json"},
    body:JSON.stringify({pending_turn:${JSON.stringify(turn)}, order_id:${JSON.stringify(edition.orders[1].id)}, item_no:1})
  }).then(response=>response.status)`);
  await expect(`document.querySelector(${JSON.stringify(pendingLink)})?.textContent === '选这件并继续问'`,
    "混合问题应保留另一笔的办理问题");
  const priceQuestion = await timedChat("买错了");
  assert.match(priceQuestion.answer, /运费|寄回|申请/);
  await expect(`document.querySelector(${JSON.stringify(pendingLink)})?.textContent === '选这件并继续问'`,
    "回答本单的原因后应保留另一单的问题");
  await timedChat("挑十一月二十号买的，里面价格最划算的那一件");
  await expect(`document.querySelectorAll('#mall-dialogue li.chat-reply:last-child .order-search-results a').length===1
    && document.querySelector(${JSON.stringify(pendingLink)})?.closest('li')?.textContent.includes('¥19.00')`,
    "月份和最低单价均应由真实订单事实验证");
  await timedChat("找订单：编织充电线");
  const obsoleteTurn = await evaluate(`document.querySelector(${JSON.stringify(pendingLink)}).dataset.pendingTurn`);
  const second = await timedChat("第二笔");
  assert.match(second.answer, /是这件商品吗/);
  await expect("document.querySelectorAll('#mall-dialogue li.chat-reply:last-child .order-search-results a').length===1",
    "第二笔应缩小为一个待确认商品");
  const cancelled = await timedChat("充电线不用查了");
  assert.match(cancelled.answer, /先不查另一笔订单/);
  assert.equal(await resume(obsoleteTurn), 409, "取消后不应接受旧卡");
  await timedChat("改一下，只看这单买的啥？");
  await expect(`[...document.querySelectorAll(${JSON.stringify(candidate)})].every(link => link.textContent === '咨询这件商品')`,
    "改口后旧商品卡不应自动续问");
  const reversed = await timedChat("刚才那笔订单还能退吗？另外这件商品的规格是什么？");
  assert.match(reversed.answer, /无线鼠标.*2\.4G/);
  assert.match(reversed.answer, /缩小范围/, "没有候选时应允许先搜索再继续");
  await timedChat("找订单：编织充电线");
  await expect(`document.querySelector(${JSON.stringify(pendingLink)})?.textContent === '选这件并继续问'`,
    "调换问句顺序后仍应等待选单");
  const confirmedTurn = await evaluate(`document.querySelector(${JSON.stringify(pendingLink)}).dataset.pendingTurn`);
  await evaluate(`document.querySelector(${JSON.stringify(pendingLink)}).click();true`);
  await text("mall-chat-order", edition.orders[1].id);
  await expect("document.querySelector('#mall-dialogue li.chat-user:nth-last-child(2)')?.textContent === '这笔订单还能退吗？'",
    "选定商品后应自动续问原问题");
  await expect(`/买错|少收到|商品有问题|退货|取消/.test(document.querySelector('#mall-dialogue li.chat-reply:last-child > p')?.textContent ?? '')
    && document.querySelector('#mall-dialogue li.chat-reply:last-child .consult-trace')?.textContent.includes(${JSON.stringify(edition.orders[1].id)})`,
    "选定商品后应按该笔订单接着核对退货");
  assert.equal(await resume(confirmedTurn), 200, "同一选择应可安全重试");
  await go(page(edition.orders[1].id));
  await expect("[...document.querySelectorAll('#mall-dialogue .chat-user')].filter(n=>n.textContent==='这笔订单还能退吗？').length === 1",
    "重复请求不能再写一条相同追问");
  await go(page(reshipId));
  await text("mall-chat-order", reshipId);
  await expect("Boolean(document.querySelector('#mall-dialogue a[href*=\"order=CN-410004\"]') && [...document.querySelectorAll('#mall-dialogue a')].some(a=>a.textContent==='查看另一笔订单的答复'))",
    "原对话刷新后应显示答复入口，并已结束待办");
  await timedChat("找订单：" + dispute.orders[0].id);
  await evaluate("document.querySelector('#mall-dialogue li.chat-reply:last-child .order-search-results a').click();true");
  await text("mall-chat-order", dispute.orders[0].id);
  await go(page(reshipId));
  await text("mall-chat-order", reshipId);
  await timedChat(reship.question);
  const corrected = await timedChat("线找到了，只是外盒破了");
  assert.ok(corrected.answer.includes("缺件建议已经撤掉"), corrected.answer);
  assert.equal(await evaluate("document.querySelector('#mall-application [name=description]').value"), "");
  await timedChat(reship.question);
  await evaluate("document.querySelector('#mall-kind').value='reship';document.querySelector('#mall-application').requestSubmit();true");
  const caseNo = await expect("location.pathname==='/mall/after-sales' && new URLSearchParams(location.search).get('case')", "缺件案例没有生成售后单");
  await login(guide.merchant, caseNo);
  await choose("approve"); await submit("同意补发充电线");
  await state("待买家确认收货信息");
  await login(guide.buyer, caseNo);
  await choose("confirm_delivery_address"); await submit("已核对收货信息");
  await state("待店铺发出新包裹");
  await login(guide.merchant, caseNo);
  await choose("reship"); await enterTracking("PORTFOLIO410001"); await submit("登记配件补发运单");
  await state("待买家确认收货");
  await login(guide.buyer, caseNo);
  await go(page(reshipId)); await text("mall-chat-order", reshipId);
  await timedChat("补发到哪了？");
  assert.ok((await timedChat("那单号呢？")).answer.includes("PORTFOLIO410001"));
  await go("/mall/after-sales?case=" + caseNo);
  await choose("confirm_delivery"); await submit("买家确认收到充电线");
  await state("已确认收到补发商品");
  const resolved = await loadRecord("buyer", caseNo);
  assert.equal(resolved.order_id,reshipId);
  console.log("缺件案例通过：复合咨询、依据、改口、预填、补发运单及确认收货。");

  const decision = await platformApproves(1, dispute.orders[0].id);
  await login(guide.buyer, decision.number);
  await state("待店铺补充寄回信息");
  const reviewed = await loadRecord("buyer", decision.number);
  assert.equal(reviewed.order_id,dispute.orders[0].id);
  for (const action of ["negotiate","reject_offer","dispute","support_buyer"]) {
    assert.ok(reviewed.events.some(event=>event.action===action),"案例时间线缺少 "+action);
  }
  await go(page(dispute.orders[0].id));
  await text("mall-chat-order", dispute.orders[0].id);
  const activeIncident = await timedChat("打开是空盒，并且快递包装也压坏了");
  assert.match(activeIncident.answer, /已有售后单/);
  await expect(`document.querySelector('#mall-dialogue li.chat-reply:last-child a[href="/mall/after-sales?case=${decision.number}"]') != null`,
    "商品已有售后时应提供原单入口");
  console.log("介入案例通过：协商、拒绝、同单介入及平台结论。");

  const terms = [];
  for (const order of edition.orders) {
    await go(page(order.id)); await text("mall-chat-order", order.id);
    const quote = await timedChat(edition.question);
    assert.ok(quote.answer.includes("找不到能核实「会员运费权益」"),quote.answer);
    const turns = await evaluate(`fetch('/api/buyer/mall/orders/${order.id}/items/1/consult').then(r=>r.json()).then(r=>r.turns)`);
    const latest = turns.at(-1).result;
    assert.equal(latest.reference.order_id,order.id);
    assert.deepEqual(latest.evidence.map(source=>source.id),["freight"]);
    terms.push(latest);
  }
  assert.ok(terms[0].terms_version < terms[1].terms_version);
  assert.notEqual(terms[0].evidence[0].text,terms[1].evidence[0].text);
  console.log("版本案例通过：本店两期报价不同，两次均未引入未承诺的会员权益。");
}

/** 真模型理解问题和改口，再通过聊天确认卡推进原业务。 */
async function taskActionsJourney() {
  const samples=[];
  const page='/mall/orders?order=CN-9877&item=1';
  const endpoint='/api/buyer/mall/orders/CN-9877/items/1/consult';
  const history=()=>evaluate(`fetch('${endpoint}').then(r=>r.json()).then(r=>r.turns)`);
  async function ask(question) {
    const sample=await timedChat(question);
    const turns=await history();
    const result=turns.at(-1).result;
    assert.equal(result.route,'function_call','本轮未使用真实规划');
    samples.push({question,answer:result.answer,generation_status:result.generation_status,visible_ms:sample.visibleMs,
      retrieval_routes:result.retrieval?.routes??[]});
    console.log(JSON.stringify({question,answer:result.answer,generated:result.generation_status,ms:sample.visibleMs}));
    return result;
  }
  async function confirmCurrent() {
    await expect("document.querySelector('.chat-action-confirm:not([disabled])') != null",'没有可确认的办理卡');
    await evaluate("(()=>{const button=document.querySelector('.chat-action-confirm:not([disabled])');button.click();button.click();return true})()");
    await expect("document.querySelector('.chat-action-receipt') != null",'确认操作没有成功回执');
    return (await history()).findLast(turn=>turn.result.action_execution)?.result.action_execution;
  }
  await login('buyer-001'); await go(page); await text('mall-chat-order','CN-9877');
  const first=await ask('主机插电完全没反应，少了一根数据线，我想退货退款，只退一件。');
  assert.equal(first.action_draft?.quantity,1);
  assert.equal(first.action_draft?.reason,'quality');
  let changed=await ask('数据线找到了，主机还是没反应，退货改成两件。');
  assert.equal(changed.action_draft?.quantity,2);
  assert.notEqual(changed.action_draft.id,first.action_draft.id);
  if(process.env.MALL_TEST_RETRIEVAL==='1') {
    const freight=await ask('这个故障退回去的运费谁承担？');
    assert.equal(freight.reason,'quality','插问运费丢失了当前故障原因');
    assert.equal(freight.action_draft?.quantity,2,'插问运费改变了申请件数');
    assert.ok(freight.retrieval?.routes.includes('milvus_bm25'),'没有实际查询Milvus稀疏索引');
    if(process.env.MALL_TEST_PERSISTENT_DENSE==='1') assert.ok(freight.retrieval.routes.includes('milvus_dense'),'没有实际查询Milvus稠密索引');
    if(process.env.MALL_TEST_WINDOW_RERANK==='1') assert.ok(freight.retrieval.routes.includes('rrf_prior'),'窗口排序没有保留融合名次');
  }
  await ask('先别办理了，故障还在，我先想一想。');
  await expect("document.querySelector('#mall-application')!=null && document.querySelector('#mall-reason')?.value===''",'暂停后没有恢复空白的手动申请入口');
  assert.equal(await evaluate("document.querySelectorAll('.chat-action-confirm').length"),0,'暂停后旧确认按钮仍能操作');
  changed=await ask('考虑好了，主机还是坏的，退两件。');
  assert.equal(changed.action_draft?.quantity,2);
  assert.equal(await evaluate("document.querySelectorAll('.chat-action-confirm').length"),1,'旧确认卡还允许执行');
  assert.equal(await evaluate("document.querySelector('#mall-application')==null"),true,'已经准备确认卡还重复展示整份申请表');
  await snapshot('task-card-wide');
  const saved=await confirmCurrent();
  assert.ok(saved?.case_no);
  assert.equal(await evaluate("location.pathname"),'/mall/orders','聊天办理不应跳离对话');
  await expect("!document.querySelector('#mall-application')",'已有原单仍显示重复新建申请表');
  const counts=await evaluate(`performance.getEntriesByType('resource').filter(r=>r.name.includes('/actions/${changed.action_draft.id}/confirm')).length`);
  assert.equal(counts,1,'双击发出了两次确认请求');
  await go(page); await text('mall-chat-order','CN-9877');
  await expect("document.querySelector('.chat-action-receipt') != null",'刷新后办理回执丢失');
  await snapshot('task-receipt-wide');
  await login('shop-001',saved.case_no);
  await choose('negotiate');
  await evaluate("document.querySelector('#mall-offer-kind').value='partial_refund';document.querySelector('#mall-offer-kind').dispatchEvent(new Event('change',{bubbles:true}));true");
  await expect("Boolean(document.querySelector('#mall-offer-amount'))",'没有协商金额字段');
  await evaluate("document.querySelector('#mall-offer-amount').value='12';true");
  await submit('店铺提出留货退款12元'); await state('待买家确认方案');
  await login('buyer-001'); await go(page); await text('mall-chat-order','CN-9877');
  const reject=await ask('店铺的留货方案我不同意，请拒绝这个方案，我还是想退货。');
  assert.equal(reject.action_draft?.action,'reject_offer');
  await confirmCurrent();
  const dispute=await ask('双方没谈拢，请帮我申请平台介入。');
  assert.equal(dispute.action_draft?.action,'dispute');
  await confirmCurrent();
  await login('staff',saved.case_no);
  await text('mall-case-next','平台处理中');
  const detail=await evaluate(`fetch('/api/staff/mall/cases/${saved.case_no}').then(r=>r.json())`);
  assert.ok(detail.events.some(row=>row.action==='reject_offer'));
  assert.ok(detail.events.some(row=>row.action==='dispute'));
  assert.equal(detail.events.filter(row=>row.action==='submit').length,1);
  assert.deepEqual(pageErrors,[]);
  const report=join('.local','evaluations','chat-actions-browser-'+previewStamp+'.json');
  writeFileSync(report,JSON.stringify({passed:true,turns:samples.length,samples,case_no:saved.case_no,
    checks:['多问题与改件数','双击只发一个确认请求','同页展示与刷新恢复回执','旧申请表收起','店铺协商','聊天拒绝方案','同一原单平台介入'],
    database_rolled_back:true},null,2),{flag:'wx'});
  console.log(report);
  console.log('聊天确认卡验收通过：多问题、改件数、双击、刷新、店铺协商、聊天拒绝、同单平台介入。');
}

/** 真模型提取履约资料，经确认卡登记运单并确认新收货地址。 */
async function fulfillmentChatJourney() {
  const page='/mall/orders?order=CN-9877&item=1';
  const endpoint='/api/buyer/mall/orders/CN-9877/items/1/consult';
  const samples=[];
  async function ask(question) {
    await timedChat(question);
    const data=await evaluate(`fetch('${endpoint}').then(r=>r.json())`);
    const result=data.turns.at(-1).result;
    samples.push({question,result});
    console.log(JSON.stringify({question,answer:result.answer,draft:result.action_draft?.state,missing:result.action_draft?.missing}));
    return result;
  }
  async function confirm() {
    await expect("Boolean(document.querySelector('.chat-action-confirm:not([disabled])'))",'履约确认卡不可操作');
    await evaluate("document.querySelector('.chat-action-confirm:not([disabled])').click();true");
    await expect("document.querySelectorAll('.chat-action-receipt').length>0",'履约未返回回执');
    await expect("!document.querySelector('.chat-action-confirm')",'履约确认后旧卡仍可操作');
    const data=await evaluate(`fetch('${endpoint}').then(r=>r.json())`);
    return data.turns.findLast(turn=>turn.result.action_execution).result.action_execution;
  }
  await login('buyer-001');await go(page);await text('mall-chat-order','CN-9877');
  const request=await ask('主机插电不亮，我要换一件正常的。');
  assert.equal(request.action_draft?.kind,'exchange');
  const saved=await confirm();const number=saved.case_no;
  await login('shop-001',number);await choose('approve');await submit('同意按原单换货');
  await login('buyer-001');await go(page);await text('mall-chat-order','CN-9877');
  const partial=await ask('我已经寄回了，运单号是SF1234567890，请帮我登记。');
  assert.equal(partial.action_draft?.state,'needs_input');
  assert.ok(partial.action_draft.missing.includes('carrier'));
  const shipping=await ask('用的是顺丰快递。');
  assert.equal(shipping.action_draft?.action,'ship');
  assert.equal(shipping.action_draft?.payload.tracking,'SF1234567890');
  await confirm();
  await login('shop-001',number);await choose('receive');await submit('收到退件，准备换货');
  await login('buyer-001');await go(page);await text('mall-chat-order','CN-9877');
  const address=await ask('新收件人李四，地址杭州市西湖区示例路八号，电话13800000001，请按这个地址寄换货。');
  assert.equal(address.action_draft?.action,'confirm_delivery_address');
  assert.equal(address.action_draft.payload.delivery_address.recipient,'李四');
  await confirm();
  await go(page);await text('mall-chat-order','CN-9877');
  const next=await ask('现在等谁处理，为什么还没发出？');
  assert.equal(String(next.case_no),String(number));
  assert.equal(next.business_read_loop?.status,'completed');
  const detail=await evaluate(`fetch('/api/buyer/mall/cases/${number}').then(r=>r.json())`);
  assert.equal(detail.events.filter(e=>e.action==='ship').length,1);
  assert.equal(detail.events.filter(e=>e.action==='confirm_delivery_address').length,1);
  const output=join('.local','evaluations','fulfillment-chat-'+Date.now()+'.json');
  writeFileSync(output,JSON.stringify({passed:true,samples,case_no:number,database_rolled_back:true},null,2),{flag:'wx'});
  console.log(output);
}

/** 执行店铺填写地址、登记运单及店铺确认收件超时的验收。 */
async function receiptReview() {
  const {number, order} = await platformApproves(1);
  await login("buyer-001", number);
  await state("待店铺补充寄回信息");
  assert.equal(await evaluate("document.querySelector('#mall-action option[value=ship]')===null"), true);
  assert.equal(await caseAction(number, "ship", {carrier: "演练快递", tracking: "BAD12345"}), 409);
  assert.equal(await caseAction(number, "review"), 409);
  const shopDeadline = Date.parse(await deadlineOf(number));
  await login("shop-001", number);
  await text("mall-return-progress-action", "补充寄回信息");
  await snapshot("merchant-case");
  await click("mall-return-progress-action");
  const invalid = await evaluate(`fetch('/api/merchant/mall/cases/${number}/actions',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({operation_id:crypto.randomUUID(),action:'provide_return_details',note:'已补充寄回信息。',
      return_details:{recipient:'151515',address:'944888',phone:'999'}})
  }).then(async r=>({status:r.status,data:await r.json()}))`);
  assert.equal(invalid.status, 400);
  assert.match(invalid.data.field_errors["return_details.phone"], /联系电话/);
  await evaluate("document.querySelector('#mall-return-recipient').value='151515';" +
    "document.querySelector('#mall-return-address').value='944888';document.querySelector('#mall-return-phone').focus();true");
  await cdp("Input.insertText", {text:"999"});
  await evaluate("document.querySelector('#mall-operation button[type=submit]').click();true");
  await text("mall-return-phone-error", "联系电话至少需要 5 个字符");
  await text("mall-operation-message", "请修正标出的收件信息");
  await expect("document.activeElement?.id==='mall-return-phone' && document.querySelector('#mall-return-phone').getAttribute('aria-invalid')==='true'",
    "联系电话的错误未标记或聚焦");
  await state("待店铺补充寄回信息");
  await enterDetails();
  await evaluate(`const originalOperationFetch=window.fetch;
    window.fetch=(url,options)=>{
      if(url.endsWith('/actions')) return new Promise(resolve=>{
        window.finishAddressSave=()=>{window.fetch=originalOperationFetch;resolve(originalOperationFetch(url,options));};
      });
      return originalOperationFetch(url,options);
    };true`);
  await submit("商家提供收件资料");
  await text("mall-operation-message", "正在提交");
  await expect("document.querySelector('#mall-operation button[type=submit]')?.disabled", "请求进行中仍可重复点击");
  await evaluate("window.finishAddressSave();true");
  await state("待买家寄回");
  await text("mall-operation-message", "寄回信息已保存");

  await login("buyer-001");
  assert.ok(Date.parse(await deadlineOf(number)) - shopDeadline >= 5 * 24 * 60 * 60 * 1000);
  await go(order);
  await expect("document.querySelector('#mall-item-view')?.hidden===false", "商品对话未打开");
  await chat("退货地址在哪");
  await text("mall-dialogue", "本地演练园区一号收件点");
  await text("mall-dialogue", "截止");
  await go(cases[role] + "?case=" + number);
  await text("mall-return-progress-action", "登记寄回");
  await click("mall-return-progress-action");
  assert.equal(await evaluate("document.querySelector('#mall-operation [name=carrier]').checkValidity()"), false);
  assert.equal(await evaluate("document.querySelector('#mall-operation [name=tracking]').checkValidity()"), false);
  assert.equal(await caseAction(number, "ship", {carrier: "演练快递"}), 400);
  assert.equal(await caseAction(number, "ship", {tracking: "TEST12345"}), 400);
  await evaluate("document.querySelector('#mall-operation [name=carrier]').value='演练快递';document.querySelector('#mall-operation [name=tracking]').value='TEST12345';true");
  await submit("登记寄回件");
  await state("待店铺确认收件");
  assert.equal(await evaluate("document.querySelector('#mall-intervention-start').hidden"), true);

  await expire(number);
  await click("mall-intervention-start");
  await addPhoto();
  await submit("店铺逾期未确认收件");
  await state("平台复核中");
  await login("staff", number);
  await text("mall-return-progress-message", "店铺逾期未确认收件");
  await click("show-case-history");
  await text("mall-timeline", "支持原退货申请");
  await expect("document.querySelector('#mall-timeline img')?.naturalWidth>0", "平台未收到复核照片");
  await choose("support_buyer");
  await submit("核对凭证后确认收件");
  await state("待店铺处理结果");
  await text("mall-timeline", "复核结论");
  await login("shop-001", number);
  const beforeRefund = await loadRecord("merchant", number);
  await choose("refund");
  await submit("按原单发起退款");
  await text("mall-case-next", "退款处理中");
  await text("mall-refund-progress", "退款处理中");
  const first = await loadRecord("merchant", number);
  assert.equal(first.refund_balance, beforeRefund.refund_balance);
  assert.equal(first.recorded_refund, null);
  assert.equal(await operationStatus("merchant", number, {action: "refund"}), 409);
  await login("buyer-001", number);
  assert.equal(await caseAction(number, "record_refund_result", {refund_attempt_id: first.refund_attempt.id, refund_result: "succeeded"}), 409);
  await go(order);
  await expect("document.querySelector('#mall-item-view')?.hidden===false", "退款咨询未打开");
  await chat("退款到账了吗？");
  await text("mall-dialogue", "退款正在处理");

  await login("shop-001", number);
  await choose("record_refund_result");
  await evaluate("document.querySelector('#mall-refund-result').value='failed';true");
  await submit("模拟渠道暂时不可用");
  await state("退款失败 · 待店铺重试");
  assert.equal((await loadRecord("merchant", number)).refund_balance, beforeRefund.refund_balance);
  await login("buyer-001", number);
  await text("mall-refund-progress-message", "模拟渠道暂时不可用");
  await go(order);
  await expect("document.querySelector('#mall-item-view')?.hidden===false", "退款失败咨询未打开");
  await chat("退款失败后怎么办？");
  await text("mall-dialogue", "等待店铺按原金额重试");
  await login("shop-001", number);
  await choose("refund");
  await submit("重试同一笔退款");
  await state("退款处理中");
  const retried = await loadRecord("merchant", number);
  assert.notEqual(retried.refund_attempt.id, first.refund_attempt.id);
  assert.equal(retried.refund_attempt.amount, first.refund_attempt.amount);
  assert.equal(await operationStatus("merchant", number, {
    action: "record_refund_result", refund_attempt_id: first.refund_attempt.id, refund_result: "succeeded"}), 409);
  await choose("record_refund_result");
  await evaluate("document.querySelector('#mall-refund-result').value='succeeded';true");
  await submit("模拟渠道退款成功");
  await state("退款成功");
  const paid = await loadRecord("merchant", number);
  assert.equal(Number(paid.recorded_refund), Number(first.refund_attempt.amount));
  assert.equal(Number(paid.refund_balance), Number(beforeRefund.refund_balance) - Number(paid.recorded_refund));
  assert.equal(await operationStatus("merchant", number, {
    action: "record_refund_result", refund_attempt_id: retried.refund_attempt.id, refund_result: "succeeded"}), 409);
  assert.equal(await evaluate("Boolean(document.querySelector('#case-operations'))"), false);
  await text("mall-case-next", "待店铺登记寄回运费返还");
  await text("mall-reimbursement-start", "登记寄回运费返还");
  await click("case-shortcut-history");
  await expect("document.activeElement?.id==='case-history' && !document.querySelector('#case-history').hidden",
    "最近进展未跳转到处理记录");
  await text("mall-timeline", "模拟渠道退款成功");
  await click("case-shortcut-funds");
  await expect("document.activeElement?.id==='mall-refund-progress' && !document.querySelector('#case-overview').hidden",
    "退款入口未切回详情并聚焦退款明细");
  await click("mall-reimbursement-start");
  assert.equal(await evaluate("Boolean(document.querySelector('#mall-action'))"), false);
  await submit("寄回运费已通过渠道返还");
  await text("mall-operation-confirmed", "寄回运费返还已登记");
  await expect(`(()=>{
    const toast=document.querySelector('#mall-operation-confirmed')?.getBoundingClientRect();
    const view=document.querySelector('#mall-case-view').getBoundingClientRect();
    const content=document.querySelector('.case-columns[data-layout=read-only]')?.getBoundingClientRect();
    return toast && content && Math.abs(toast.left + toast.width/2 - document.documentElement.clientWidth/2)<2 &&
      Math.abs(toast.top + toast.height/2 - document.documentElement.clientHeight/2)<2 &&
      Math.abs(view.left + view.right - content.left - content.right)<2;
  })()`,"返还成功提示或详情未居中");
  await text("mall-return-shipping", "该笔垫付已登记返还");
  assert.equal(await evaluate("Boolean(document.querySelector('#case-operations,#mall-reimbursement-start,#mall-operation'))"), false);
  assert.equal(await operationStatus("merchant", number, {action:"reimburse"}), 409);
  const withFreight = await loadRecord("merchant", number);
  assert.equal(withFreight.return_reimbursement_due, null);
  assert.equal(withFreight.recorded_refund, paid.recorded_refund);
  assert.equal(withFreight.events.filter(event=>event.action==="reimburse").length, 1);
  await expect("!document.querySelector('#mall-operation-confirmed')", "成功提示没有自动收起");
  await evaluate("window.scrollTo({top:0,behavior:'instant'});true");
  await snapshot("case-closed-wide");
  await login("shop-001");
  await expect("Boolean(document.querySelector('.record-library[data-library=cases]'))", "店铺商品售后列表未打开");
  await evaluate("[...document.querySelectorAll('.record-library .range-tabs button')].find(b=>b.textContent==='全部记录').click();true");
  await text("mall-list", "#" + number + " ·");
  await snapshot("case-queue-wide");
  await login("buyer-001");
  await go(order);
  await expect("document.querySelector('#mall-item-view')?.hidden===false", "退款咨询未打开");
  await chat("退款到账了吗？");
  await text("mall-dialogue", "退款成功，已登记商品退款");
}

/** 验证店铺未提供地址时复核可补齐，随后恢复买家寄回。 */
async function addressReview() {
  const {number} = await platformApproves(2);
  await login("buyer-001", number);
  await state("待店铺补充寄回信息");
  await expire(number);
  await click("mall-intervention-start");
  await submit("店铺未提供寄回地址");
  await state("平台复核中");
  assert.equal(await caseAction(number, "review"), 409);
  await login("staff", number);
  await text("mall-return-progress-message", "店铺逾期未补充寄回信息");
  await choose("support_buyer");
  await enterDetails();
  await submit("平台核实并补齐寄回地址");
  await state("待买家寄回");
  await login("buyer-001", number);
  await text("mall-return-progress-action", "登记寄回");
  await text("mall-return-progress-message", "本地演练园区一号收件点");
}


/** 读取当前浏览器账号的原单，或尝试受权限校验的指定操作。 */
function loadRecord(apiRole, number) {
  return evaluate("fetch('/api/" + apiRole + "/mall/cases/" + number + "').then(r=>r.json())");
}
function operationStatus(apiRole, number, data) {
  const payload = {note: "浏览器回归", ...data};
  return evaluate("fetch('/api/" + apiRole + "/mall/cases/" + number + "/actions',{method:'POST'," +
    "headers:{'Content-Type':'application/json'},body:JSON.stringify({operation_id:crypto.randomUUID(),..." +
    JSON.stringify(payload) + "})}).then(r=>r.status)");
}
function enterTracking(code) {
  return evaluate("document.querySelector('#mall-operation [name=carrier]').value='本地演练快递';" +
    "document.querySelector('#mall-operation [name=tracking]').value=" + JSON.stringify(code) + ";true");
}

/** 通过订单详情申请并同意售后，返回原单编号和咨询地址。 */
async function approveApplication(item, reason, kind, {platform = false, orderId = "CN-9878"} = {}) {
  const order = `/mall/orders?order=${orderId}&item=${item}`;
  await login("buyer-001");
  await go(order);
  await expect("document.querySelector('#mall-item-view')?.hidden===false", "售后寄出测试商品未显示");
  await text("mall-order-address", "张三");
  await evaluate("document.querySelector('#mall-reason').value=" + JSON.stringify(reason) +
    ";document.querySelector('#mall-reason').dispatchEvent(new Event('change',{bubbles:true}));true");
  await expect("document.querySelector('#mall-application-fields')?.hidden===false", "申请未打开");
  await evaluate("document.querySelector('#mall-kind').value=" + JSON.stringify(kind) +
    ";document.querySelector('#mall-application [name=description]').value='售后寄出验收';document.querySelector('#mall-application').requestSubmit();true");
  const number = await expect("location.pathname==='/mall/after-sales' && new URLSearchParams(location.search).get('case')", "售后寄出申请未生成");
  await login("shop-001", number);
  if (platform) {
    await choose("reject");
    await submit("店铺拒绝原换货申请");
    await state("店铺已拒绝");
    await login("buyer-001", number);
    await choose("dispute");
    await submit("请求平台支持原换货申请");
    await state("平台处理中");
    await login("staff", number);
    await choose("support_buyer");
    await submit("平台支持原换货申请");
    await state("待店铺补充寄回信息");
    await login("shop-001", number);
    await choose("provide_return_details");
    await enterDetails();
    await submit("补充退回原商品的地址");
  } else {
    await choose("approve");
    await submit("同意买家申请");
  }
  return {number, order};
}

/** 验证换货必须收回原件、确认最新地址、新寄出及买家收货。 */
async function exchangeDelivery() {
  const {number, order} = await approveApplication(1, "quality", "exchange", {platform: true});
  await state("待买家寄回");
  assert.equal(await operationStatus("merchant", number, {action: "replace", carrier: "演练", tracking: "EARLY123"}), 409);
  await login("buyer-001");
  await go("/mall/orders");
  await expect("document.querySelector('#mall-after-sales-link')?.hidden===false", "订单页未显示售后入口");
  await evaluate("document.querySelector('#mall-after-sales-link').click();true");
  const caseLink = "document.querySelector('#mall-list a[href$=\"?case=" + number + "\"]')";
  await expect(caseLink + "!==null", "我的售后未列出换货单");
  await evaluate(caseLink + ".click();true");
  await state("待买家寄回");
  await expect("document.querySelector('#mall-outbound')?.hidden===false", "寄回前未显示售后包裹进度");
  await text("mall-outbound-summary", "请先登记寄回");
  assert.equal(await evaluate("document.querySelector('#mall-outbound-actions').childElementCount"), 0);
  await choose("ship");
  await enterTracking("RETURN9878");
  await submit("买家寄回原商品");
  await state("待店铺确认收件");
  await text("mall-outbound-summary", "等待店铺确认已收到寄回的商品");
  await login("shop-001", number);
  await choose("receive");
  await submit("已收回原商品");
  await state("待买家确认收货信息");
  assert.equal((await loadRecord("merchant", number)).default_delivery_address, null);

  await login("buyer-001", number);
  await choose("confirm_delivery_address");
  assert.equal(await evaluate("document.querySelector('#mall-delivery-recipient').value"), "张三");
  await submit("确认默认收货信息");
  await state("待店铺发出新包裹");
  const before = (await loadRecord("buyer", number)).outbound;
  const initialAddress = {action: "confirm_delivery_address", delivery_parcel_no: before.parcel_no,
    delivery_address: before.destination, delivery_address_version: before.address_version};
  assert.equal(await operationStatus("buyer", number, initialAddress), 409, "地址未变时不能再登记一条确认");
  const afterRepeat = await loadRecord("buyer", number);
  assert.equal(afterRepeat.outbound.address_version, before.address_version);
  assert.equal(afterRepeat.events.filter(event => event.action === "confirm_delivery_address").length, 1);
  await choose("confirm_delivery_address");
  await text("mall-action", "修改收货信息");
  await expect("document.querySelector('#mall-operation button[type=submit]').disabled", "地址未改时仍可提交");
  await evaluate("const addr=document.querySelector('#mall-delivery-address-value');" +
    "addr.value='演练园区 A 区新收件点';addr.dispatchEvent(new Event('input',{bubbles:true}));true");
  await expect("!document.querySelector('#mall-operation button[type=submit]').disabled", "真正改址后没有开放保存");
  await submit("发货前更新地址");
  await text("mall-outbound-summary", "演练园区 A 区新收件点");
  await text("mall-timeline", "买家 · 修改收货信息");
  assert.equal(await operationStatus("buyer", number, initialAddress), 409, "旧页面不能把已改地址覆盖回去");
  const changed = await loadRecord("buyer", number);
  assert.equal(changed.outbound.destination.address, "演练园区 A 区新收件点");
  assert.equal(changed.outbound.address_version, before.address_version + 1);
  assert.equal(changed.events.filter(event => event.action === "confirm_delivery_address").length, 2);

  await login("shop-001", number);
  await text("mall-outbound-summary", "演练园区 A 区新收件点");
  const tracking = "EXCHANGE9878";
  assert.equal(await operationStatus("merchant", number, {action: "replace", carrier: "演练", tracking,
    delivery_parcel_no: before.parcel_no, delivery_address_version: before.address_version}), 409);
  await evaluate("window.dispatchRequest=null;const originalFetch=window.fetch;window.fetch=(url,options)=>{" +
    "if(url.endsWith('/actions')&&options?.body?.includes('\"replace\"'))window.dispatchRequest={url,options};" +
    "return originalFetch(url,options)};true");
  await choose("replace");
  await enterTracking(tracking);
  await submit("店铺寄出换货商品");
  await state("待买家确认收货");
  const repeated = await evaluate("fetch(window.dispatchRequest.url,window.dispatchRequest.options).then(r=>r.json())");
  assert.equal(repeated.outbound_history.length, 1);
  assert.equal(repeated.outbound.tracking, tracking);
  assert.equal(await operationStatus("merchant", number, {action: "confirm_delivery",
    delivery_parcel_no: before.parcel_no}), 409);

  await login("buyer-001");
  await go(order);
  await expect("document.querySelector('#mall-item-view')?.hidden===false", "订单咨询未打开");
  await chat("换货新运单是多少");
  await text("mall-dialogue", tracking);
  await go(cases[role] + "?case=" + number);
  await text("mall-case-facts", "买家寄回运单");
  await text("mall-outbound-history", tracking);
  await choose("report_delivery_problem");
  await evaluate("document.querySelector('#mall-delivery-problem').value='not_received';true");
  await submit("换货件未收到");
  await state("售后包裹异常待店铺处理");
  assert.equal(await operationStatus("buyer", number, {action: "request_delivery_review", delivery_parcel_no: before.parcel_no}), 409);
  assert.equal(await evaluate("fetch('/_test/expire/" + number + "',{method:'POST'}).then(r=>r.status)"), 200);
  await click("mall-refresh");
  await text("mall-outbound-guidance", "店铺办理已超时");
  await choose("request_delivery_review");
  await submit("换货件异常超期，请平台核对");
  await state("平台处理中 · 售后包裹");
  const reviewing = await loadRecord("buyer", number);
  assert.equal(reviewing.decision, "support_buyer");
  await login("staff", number);
  await choose("platform_continue_delivery");
  await submit("核对后保留可追踪的在途运单");
  await state("待买家确认收货");
  await login("buyer-001", number);
  await text("mall-delivery-review-note", "核对后保留可追踪的在途运单");
  await choose("confirm_delivery");
  await submit("收到换货商品");
  await state("已确认收到换货商品");
  assert.equal(await operationStatus("buyer", number, {action: "confirm_delivery",
    delivery_parcel_no: before.parcel_no}), 409, "已签收的包裹不能再次确认收货");
  assert.equal((await loadRecord("buyer", number)).events.filter(event => event.action === "confirm_delivery").length, 1);
}

/** 验证迟发与未收到处理，重寄保留原售后运单。 */
async function reshipDelivery() {
  const {number, order} = await approveApplication(2, "missing", "reship");
  await state("待买家确认收货信息");
  await login("buyer-001", number);
  await choose("confirm_delivery_address");
  await submit("确认补发地址");
  await state("待店铺发出新包裹");
  assert.equal(await evaluate("fetch('/_test/expire/" + number + "',{method:'POST'}).then(r=>r.status)"), 200);
  await click("mall-refresh");
  await choose("report_delivery_problem");
  assert.equal(await evaluate("document.querySelector('#mall-delivery-problem').value"), "not_dispatched");
  await submit("原补发方案已超时");
  await state("售后包裹异常待店铺处理");
  await text("mall-outbound-guidance", "店铺办理已超时");
  await choose("request_delivery_review");
  await submit("补发逾期，请平台要求店铺尽快发货");
  await state("平台处理中 · 售后包裹");
  const firstReview = (await loadRecord("buyer", number)).delivery_review;

  await login("staff");
  const pendingLink = `document.querySelector('#mall-list a[href$="?case=${number}"]')`;
  await expect(pendingLink + "!==null", "平台待办缺少补发逾期申请");
  await evaluate(pendingLink + ".click();true");
  await choose("platform_dispatch");
  await submit("平台要求店铺在期限内登记新运单");
  await state("待店铺按平台要求发货");

  await login("shop-001", number);
  await choose("reship");
  await enterTracking("RESHIP9878A");
  await submit("店铺登记补发");
  await state("待买家确认收货");

  await login("buyer-001", number);
  const first = (await loadRecord("buyer", number)).outbound;
  await choose("report_delivery_problem");
  await evaluate("document.querySelector('#mall-delivery-problem').value='not_received';true");
  await addPhoto();
  await submit("没收到补发件");
  await state("售后包裹异常待店铺处理");
  await login("shop-001", number);
  await text("mall-outbound-summary", "未收到售后包裹");
  await click("show-case-history");
  await expect("Boolean(document.querySelector('#mall-timeline img'))", "店铺时间线缺少买家凭证");
  await evaluate("document.querySelector('#mall-timeline img').scrollIntoView();true");
  await expect("document.querySelector('#mall-timeline img')?.naturalWidth>0", "店铺未看到买家凭证");
  await choose("resume_delivery");
  await submit("店铺联系快递核查，建议继续等一天");
  await state("待买家核对店铺回复");

  await login("buyer-001", number);
  await text("mall-outbound-guidance", "仍未收到");
  await text("mall-delivery-reply", "店铺联系快递核查，建议继续等一天");
  await go(order);
  await expect("document.querySelector('#mall-item-view')?.hidden===false", "商品咨询未打开");
  await chat("补发到哪了？");
  await expect("document.querySelector('#mall-dialogue .chat-reply:last-child p')?.innerText.includes('RESHIP9878A')","补发进度未读取新运单");
  await chat("那单号呢？");
  await expect("document.querySelector('#mall-dialogue .chat-reply:last-child p')?.innerText.includes('RESHIP9878A')","追问单号没有承接补发包裹");
  await chat("补发包裹没收到，能找平台介入吗？");
  await expect("document.querySelector('#mall-dialogue li:last-child a')?.href.includes('case=" + number +
    "&action=request_delivery_review')", "商品咨询没有提供包裹处理直达入口");
  await evaluate("document.querySelector('#mall-dialogue li:last-child a').click();true");
  await expect("document.querySelector('#mall-action')?.value==='request_delivery_review'", "申请表单未自动打开");
  await submit("补发包裹一直没收到，请平台核实");
  await state("平台处理中 · 售后包裹");
  await text("mall-delivery-review-meta", "未收到售后包裹");
  await login("shop-001", number);
  assert.equal(await operationStatus("merchant", number, {action: "reissue_delivery", delivery_parcel_no: first.parcel_no}), 409);
  await choose("evidence");
  await submit("店铺提交快递核查记录");
  await text("mall-timeline", "店铺提交快递核查记录");

  await login("staff", number);
  await click("show-case-history");
  await expect("Boolean(document.querySelector('#mall-timeline img'))", "平台时间线缺少买家凭证");
  await evaluate("document.querySelector('#mall-timeline img').scrollIntoView();true");
  await expect("document.querySelector('#mall-timeline img')?.naturalWidth>0", "平台无法读取买家凭证");
  assert.equal(await operationStatus("staff", number, {action: "platform_reissue", delivery_parcel_no: first.parcel_no,
    delivery_review_id: firstReview.request_id}), 409);
  await choose("platform_reissue");
  await submit("平台核实丢件，要求重新寄出");
  await state("待店铺按平台要求重寄");
  await text("mall-outbound-history", "已安排重寄");
  await login("shop-001", number);
  assert.equal((await loadRecord("merchant", number)).outbound_history.length, 2);
  await choose("reship");
  await enterTracking("RESHIP9878B");
  await submit("登记重寄运单");
  await state("待买家确认收货");
  await login("buyer-001", number);
  assert.equal(await operationStatus("buyer", number, {action: "confirm_delivery", delivery_parcel_no: first.parcel_no}), 409);
  await text("mall-outbound-history", "RESHIP9878A");
  await text("mall-outbound-history", "RESHIP9878B");
  await choose("confirm_delivery");
  await submit("确认收到重新补发的商品");
  await state("已确认收到补发商品");
}

/** 从买家真实表单走完整包拦截，并验证部分退回的签收分支。 */
async function transitReturn() {
  await login("buyer-001");
  await go("/mall/orders?order=CN-9875");
  await text("mall-products","咨询物流或申请在途退回");
  await snapshot("transit-order");
  const {number} = await approveApplication(1,"wrong_purchase","return_refund",{orderId:"CN-9875"});
  await state("待核对原包裹退回");
  assert.equal(await operationStatus("merchant",number,{action:"refund"}),409);
  await choose("carrier_return");
  await submit("承运方已确认站点拒收，原运单退回。");
  await state("原包裹退回中");
  await snapshot("transit-case");
  assert.equal(await operationStatus("merchant",number,{action:"refund"}),409);
  await choose("receive");
  await submit("仓库核对原包裹，商品已退回。");
  await state("待店铺处理结果");
  await choose("refund");
  await submit("对退回商品发起退款");
  await state("退款处理中");
  await choose("record_refund_result");
  await evaluate("document.querySelector('#mall-refund-result').value='succeeded';true");
  await submit("渠道确认退款成功");
  await state("退款成功");
  const partial = await approveApplication(1,"wrong_purchase","return_refund",{orderId:"CN-9874"});
  await state("部分商品待签收后寄回");
  assert.equal(await operationStatus("merchant",partial.number,{action:"carrier_return"}),409);
  await login("buyer-001",partial.number);
  await choose("buyer_received");
  await submit("包裹已签收，只退其中一件。");
  await state("待店铺补充寄回信息");
  const order = await evaluate("fetch('/api/buyer/mall/orders/CN-9874').then(r=>r.json())");
  assert.equal(order.items[0].stage, "delivered");
  await login("shop-001",partial.number);
  await choose("provide_return_details");
  await enterDetails();
  await submit("提供寄回地址");
  await state("待买家寄回");
  await login("buyer-001",partial.number);
  await choose("ship");
  await enterTracking("PART9874");
  await submit("按地址寄回申请的一件商品");
  await state("待店铺确认收件");
  await go("/mall/after-sales");
  await expect("Boolean(document.querySelector('.case-board .ticket-body'))", "售后看板未载入");
  await snapshot("service-board");
}

/** 多种商品用一张在途售后单办理，按原实付拆账，且覆盖两个商品的后续咨询。 */
async function packageReturn() {
  const orderId = "CN-9873";
  const orderPath = "/api/buyer/mall/orders/" + orderId;
  const submission = orderPath + "/packages/1/cases";
  await login("buyer-001");
  await go("/mall/orders?order=" + orderId);
  await text("mall-products", "编织充电线");
  const before = await evaluate("fetch(" + JSON.stringify(orderPath) + ").then(r=>r.json())");
  assert.deepEqual(before.items.map(item => [item.package_no,item.quantity]), [[1,2],[1,3]]);
  const total = before.items.reduce((sum,item) => sum + Math.round(Number(item.paid_unit) * 100) * item.quantity, 0);
  await evaluate("[...document.querySelectorAll('button')].find(b=>b.textContent.includes('整包在途退回')).click();true");
  await expect("document.querySelector('form[aria-label=整包在途退回申请]')?.innerText.includes('桌面集线器')", "整包表单缺少第二种商品");
  await evaluate(`const applyForm=document.querySelector('form[aria-label=整包在途退回申请]');
    const sendRequest=window.fetch;
    window.fetch=(url, options)=>{
      if(url.endsWith('/packages/1/cases')&&options?.body) sessionStorage.setItem('package-transit-test', options.body);
      return sendRequest(url,options);
    };
    applyForm.elements.description.value='两种商品都不需要，请拦截这个包裹';
    applyForm.requestSubmit();true`);
  const number = await expect("location.pathname==='/mall/after-sales' && new URLSearchParams(location.search).get('case')",
    "整包申请未打开同一张售后单");
  await text("mall-package-lines", "桌面集线器");
  await text("mall-refund-progress", "商品退款待发起");
  await text("mall-original-shipping", "原订单免配送费");
  await text("mall-return-shipping", "拦截或拒收费用待核实");
  const replay = await evaluate("fetch(" + JSON.stringify(submission) +
    ",{method:'POST',headers:{'Content-Type':'application/json'},body:sessionStorage.getItem('package-transit-test')}).then(r=>r.json())");
  assert.equal(replay.case_no, number);
  assert.equal(replay.quantity, 5);
  assert.equal(Math.round(Number(replay.refundable_amount) * 100), total);
  const post = (path, body) => evaluate("fetch(" + JSON.stringify(path) + ",{method:'POST',headers:{'Content-Type':'application/json'},body:" +
    JSON.stringify(JSON.stringify(body)) + "}).then(r=>r.status)");
  assert.equal(await post(orderPath + "/cases", {submission_id:crypto.randomUUID(), item_no:2, quantity:1,
    reason:"wrong_purchase", kind:"return_refund", description:"第二件不能另起售后"}), 409);
  assert.equal(await post(submission, {submission_id:crypto.randomUUID(), reason:"wrong_purchase", description:"再次提交整包"}), 409);
  const consult = await evaluate("fetch(" + JSON.stringify(orderPath + "/items/2/consult") +
    ",{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:'还能退吗',reason:'wrong_purchase',record:false})}).then(r=>r.json())");
  assert.equal(consult.case_no, number);
  await login("shop-001", number);
  await text("mall-package-lines", "编织充电线");
  await choose("approve");
  await submit("同意整包在途退回");
  await state("待核对原包裹退回");
  await choose("carrier_interception_failed");
  await submit("快递已在派送，未能拦截");
  await state("拦截失败 · 包裹继续配送");
  await text("mall-interception-result", "快递已在派送，未能拦截");
  assert.equal(await operationStatus("merchant", number, {action:"carrier_interception_failed"}), 409);
  assert.equal(await operationStatus("merchant", number, {action:"refund"}), 409);
  await login("buyer-001", number);
  await text("mall-return-progress-message", "可在原单改为自行寄回");
  const guidance = await evaluate("fetch(" + JSON.stringify(orderPath + "/items/2/consult") +
    ",{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:'拦截失败了怎么办',record:false})}).then(r=>r.json())");
  assert.equal(guidance.case_no, number);
  assert.match(guidance.answer, /仍在配送/);
  await login("shop-001", number);
  await choose("carrier_return");
  await submit("买家明确拒收，快递已受理整包退回");
  await state("原包裹退回中");
  assert.equal(await evaluate("Boolean(document.querySelector('#mall-interception-result'))"), false);
  await choose("receive");
  await submit("收到包裹并核对两款商品");
  await state("待店铺处理结果");
  await choose("refund");
  await submit("按原商品实付发起退款");
  await state("退款处理中");
  assert.equal(Math.round(Number((await loadRecord("merchant", number)).refund_attempt.amount) * 100), total);
  await choose("record_refund_result");
  await evaluate("document.querySelector('#mall-refund-result').value='succeeded';true");
  await submit("整包退款渠道确认成功");
  await state("退款成功");
  const record = await loadRecord("merchant", number);
  assert.equal(await evaluate("Boolean(document.querySelector('#case-operations,#mall-reimbursement-start'))"), false);
  await text("mall-refund-progress-message", "已登记商品退款");
  assert.equal(Math.round(Number(record.recorded_refund) * 100), total);
  assert.equal(record.refund_summary.product.state, "succeeded");
  assert.equal(record.refund_summary.product.lines.reduce((sum,line) => sum + Math.round(Number(line.amount) * 100), 0), total);
  assert.equal(await operationStatus("merchant", number, {action:"refund"}), 409);
  await login("buyer-001");
  const after = await evaluate("fetch(" + JSON.stringify(orderPath) + ").then(r=>r.json())");
  assert.equal(after.cases.length, 1);
  assert.equal(Math.round(Number(after.refunded_total) * 100), total);
  for (const [index,item] of after.items.entries()) {
    assert.equal(item.stage, "refunded");
    assert.equal(item.remaining_quantity, 0);
    assert.equal(Math.round(Number(item.refunded_amount) * 100),
      Math.round(Number(before.items[index].paid_unit) * 100) * item.quantity);
  }
}

/** 拦截失败后保留原截止时间，经平台介入可在原单转换为自行寄回。 */
async function signedAfterFailedInterception() {
  await login("buyer-001");
  const number = await evaluate(`fetch('/api/buyer/mall/orders/CN-9872/packages/1/cases',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({submission_id:crypto.randomUUID(),reason:'wrong_purchase',description:'申请退回整个包裹'})
  }).then(r=>r.json()).then(r=>r.case_no)`);
  assert.ok(number);
  await login("shop-001", number);
  await choose("approve");
  await submit("同意在途退回");
  await state("待核对原包裹退回");
  const before = await loadRecord("merchant", number);
  await choose("carrier_interception_failed");
  await submit("派送途中拦截未成功");
  await state("拦截失败");
  assert.equal((await loadRecord("merchant", number)).due_at, before.due_at);
  await login("buyer-001", number);
  assert.equal(await operationStatus("buyer", number, {action:"carrier_interception_failed"}), 409);
  assert.equal(await evaluate("fetch('/_test/expire/" + number + "',{method:'POST'}).then(r=>r.status)"), 200);
  await go("/mall/after-sales?case=" + number);
  await text("mall-intervention-start", "申请平台介入");
  await click("mall-intervention-start");
  await submit("店铺登记失败后一直没有后续，请平台核对");
  await state("平台处理中");
  await login("staff", number);
  await click("show-case-history");
  await text("mall-timeline", "派送途中拦截未成功");
  await choose("support_buyer");
  await submit("支持在原单继续退回，实际签收后按寄回办理");
  await state("拦截失败");
  await login("buyer-001", number);
  assert.equal(await operationStatus("buyer", number, {action:"ship", carrier:"配送", tracking:"EARLY9872"}), 409);
  const received = {operation_id:crypto.randomUUID(), action:"buyer_received", note:"买家已签收，整包自行寄回"};
  assert.equal(await operationStatus("buyer", number, received), 200);
  assert.equal(await operationStatus("buyer", number, received), 200);
  assert.equal(await operationStatus("buyer", number, {action:"buyer_received"}), 409);
  const after = await loadRecord("buyer", number);
  assert.equal(after.events.filter(event=>event.action==="buyer_received").length, 1);
  assert.equal(after.quantity, before.quantity);
  assert.deepEqual(after.package_lines, before.package_lines);
  await login("shop-001", number);
  await state("待店铺补充寄回信息");
  assert.equal(await operationStatus("merchant", number, {action:"carrier_return"}), 409);
  assert.equal(await operationStatus("merchant", number, {action:"refund"}), 409);
  await choose("provide_return_details");
  await enterDetails();
  await submit("补充本次整包寄回地址");
  await state("待买家寄回");
  await login("buyer-001", number);
  await text("mall-original-shipping", "实付 ¥6.00");
  await text("mall-original-shipping", "未包含");
  await text("mall-return-shipping", "寄回运费待核实");
  await choose("ship");
  await enterTracking("RETURN9872");
  await submit("按地址寄回原包裹");
  await state("待店铺确认收件");
  await text("mall-return-shipping", "权益抵扣 ¥5.00");
  const mailed = await loadRecord("buyer", number);
  const ship = mailed.events.find(event => event.action === "ship");
  assert.deepEqual(ship.details.credit_by_item, {"1":"2.00","2":"3.00"});
  const order = await evaluate("fetch('/api/buyer/mall/orders/CN-9872').then(r=>r.json())");
  assert.ok(order.items.every(item => Number(item.return_credit) === 0));
  const question = await evaluate(`fetch('/api/buyer/mall/orders/CN-9872/items/2/consult',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({question:'原配送费退吗？寄回运费谁出？',record:false})
  }).then(r=>r.json())`);
  assert.equal(question.case_no, number);
  assert.ok(question.answer.includes(mailed.refund_summary.original_shipping.message));
  assert.ok(question.answer.includes(mailed.refund_summary.return_shipping.message));
  await login("shop-001", number);
  await choose("receive");
  await submit("整包寄回已收件");
  await state("待店铺处理结果");
  await choose("refund");
  await submit("按确认金额发起商品退款");
  await state("退款处理中");
  await choose("record_refund_result");
  await evaluate("document.querySelector('#mall-refund-result').value='succeeded';true");
  await submit("退款渠道确认成功");
  await state("退款成功");
  const completed = await loadRecord("merchant", number);
  assert.equal(completed.recorded_refund, mailed.refund_summary.product.amount);
  assert.deepEqual(completed.refund_summary.return_shipping, mailed.refund_summary.return_shipping);
  await login("buyer-001", number);
  await text("mall-refund-progress-message", "已登记商品退款");
}

/** 待发货仅退款走同一笔资金流程，成功后商品退出待发货。 */
async function cancelAndRefund() {
  const {number} = await approveApplication(1, "cancel", "refund_only", {orderId: "CN-9876"});
  await choose("refund");
  await submit("发起未发货退款");
  await state("退款处理中");
  await choose("record_refund_result");
  await evaluate("document.querySelector('#mall-refund-result').value='succeeded';true");
  await submit("模拟渠道完成退款");
  await state("退款成功");
  await login("buyer-001");
  const order = await evaluate("fetch('/api/buyer/mall/orders/CN-9876').then(r=>r.json())");
  assert.equal(order.items[0].remaining_quantity, 0);
  assert.equal(order.items[0].stage, "refunded");
  await go("/mall/orders?order=CN-9876");
  await expect("Boolean(document.querySelector('[aria-controls=order-cases]'))", "退款单未显示关联售后入口");
  await evaluate("document.querySelector('[aria-controls=order-cases]').click();true");
  await expect("document.activeElement.matches('#order-cases h2')", "未定位到本单关联售后");
  await text("order-cases", "#" + number);
}

/** 从平台、所属店铺和所属买家查看同一笔新增订单，并办售后。 */
async function directoryJourney() {
  await login("staff");
  await go("/staff/mall/catalog?view=shops");
  await text("directory-rows", "栖禾数码");
  await text("directory-rows", "shop-001");
  await text("directory-message", "100 条记录");
  await snapshot("platform-directory");
  await evaluate("document.querySelector('.directory-toolbar .layout-toggle button:last-child').click();true");
  await expect("Boolean(document.querySelector('table #directory-rows'))","平台档案未切换为表格");
  await click("directory-next");
  await text("directory-page", "2 /");
  await go("/staff/mall/catalog?view=buyers&search=buyer-001");
  await text("directory-rows", "张三");
  await text("directory-message", "共 1 条记录");
  await go("/staff/mall/catalog?view=orders&order=CN-300001");
  await text("directory-totals", "配送费");
  await text("directory-items", "无线鼠标");
  await checkOrderHeading(".page-title", "平台订单档案");
  await checkOrderHeading("#directory-detail > header", "平台订单详情");
  const detail = await evaluate("fetch('/api/staff/mall/catalog/orders/CN-300001').then(r=>r.json())");
  assert.equal(detail.delivery_address, null);
  assert.equal(Number(detail.paid_total), Number(detail.total) + Number(detail.shipping));

  await login("shop-001", undefined, "栖禾数码");
  await text("access-identity", "栖禾数码");
  await go("/merchant/mall/orders?search=CN-300");
  await text("directory-message", "共 3 条记录");
  await checkOrderHeading(".page-title", "本店订单");
  await evaluate("[...document.querySelectorAll('#directory-rows a')].find(a=>a.textContent.includes('CN-300001')).click();true");
  await text("directory-items", "无线鼠标");
  await checkOrderHeading("#directory-detail > header", "店铺订单详情");
  await evaluate("document.querySelector('#directory-detail header button').click();true");
  await expect("!document.querySelector('#directory-detail').open", "店铺关闭订单详情后未回到列表");
  assert.equal(await evaluate("fetch('/api/merchant/mall/catalog/orders/CN-300004').then(r=>r.status)"), 404);
  await login("buyer-001", undefined, "张三");
  await text("access-identity", "张三");
  await go("/mall/orders?order=CN-300001&item=1");
  await text("mall-order-address", "原订单收货信息");
  await text("mall-order-payment", "付款方式");
  assert.equal(await evaluate("fetch('/api/buyer/mall/orders/CN-300002').then(r=>r.status)"), 404);
  assert.equal(await evaluate("fetch('/api/staff/mall/catalog/buyers').then(r=>r.status)"), 403);
  await expect("document.querySelector('#mall-item-view')?.hidden===false", "新增订单未打开商品售后");
  await evaluate("document.querySelector('#mall-reason').value='cancel';document.querySelector('#mall-reason').dispatchEvent(new Event('change',{bubbles:true}));true");
  await expect("document.querySelector('#mall-application-fields')?.hidden===false", "新增订单没有退款办理方式");
  await evaluate("document.querySelector('#mall-kind').value='refund_only';document.querySelector('#mall-application [name=description]').value='申请取消未发货商品';document.querySelector('#mall-application').requestSubmit();true");
  const number = await expect("location.pathname==='/mall/after-sales' && new URLSearchParams(location.search).get('case')", "新增订单申请未生成");
  await login("shop-001", number);
  await choose("approve");
  await submit("同意取消未发货商品");
  await state("待店铺执行方案");
}

let caseHeading;

/** 对比已验收售后页的真实配色，防止某个订单入口又落回旧页头。 */
async function checkOrderHeading(selector, label) {
  const style = await evaluate(`(() => {
    const element = document.querySelector(${JSON.stringify(selector)});
    if (!element || !element.getClientRects().length) return null;
    const css = getComputedStyle(element);
    return [css.backgroundImage, css.color];
  })()`);
  assert.deepEqual(style, caseHeading, label + "未使用售后页头配色");
}

/** 验证买家从列表、订单抬头到付款、商品咨询和已有售后的连续操作。 */
async function orderNavigation() {
  await login("buyer-001");
  await go("/mall/after-sales");
  await expect("document.querySelector('.record-library')?.dataset.library==='cases'", "售后列表未加载");
  caseHeading = await evaluate("['backgroundImage','color'].map(k=>getComputedStyle(document.querySelector('.page-title'))[k])");
  assert.match(caseHeading[0], /linear-gradient/);
  await go("/mall/orders");
  await expect("Boolean(document.querySelector('#mall-list a'))", "我的订单列表未加载");
  await checkOrderHeading(".page-title", "我的订单");
  await snapshot("order-queue-wide");
  await evaluate("document.querySelector('.range-tabs button:nth-child(2)').click();true");
  await expect("document.querySelector('#mall-list-message').textContent==='' && document.querySelector('#mall-list a[data-code=\"CN-300001\"]') && [...document.querySelectorAll('#mall-list li')].every(row=>row.textContent.includes('待发货'))","待发货筛选未生效");
  await evaluate("document.querySelector('.layout-toggle button:last-child').click();true");
  await expect("document.querySelector('#mall-list')?.dataset.layout==='ledger'", "订单清单视图未切换");
  await evaluate("document.querySelector('#mall-list a[data-code=\"CN-300001\"]').click();true");
  await text("mall-selection-title", "CN-300001");
  await checkOrderHeading(".order-overview", "买家订单详情");
  await snapshot("order-detail-wide");
  await evaluate("document.querySelector('#order-payment > summary').click();true");
  await expect("!document.querySelector('#order-payment').open", "付款信息未收起");
  await evaluate("document.querySelector('[aria-controls=order-payment]').click();true");
  await expect("document.querySelector('#order-payment').open && document.activeElement.matches('#order-payment > summary')","快捷入口未展开付款信息并定位");
  await evaluate("document.querySelector('[aria-controls=order-goods]').click();true");
  await expect("document.activeElement.id==='order-products-title'", "未定位到商品明细");
  await evaluate("document.querySelector('#mall-products .button').click();true");
  await text("mall-chat-order", "CN-300001");
  await checkOrderHeading(".chat-header", "从订单进入商品咨询");
  await evaluate("document.querySelector('.return-line button').click();true");
  await text("mall-selection-title", "CN-300001");
  await evaluate("document.querySelector('.record-tools button').click();true");
  await expect("document.querySelector('#mall-list')?.dataset.layout==='ledger' && document.querySelector('.range-tabs button[aria-pressed=true]')?.textContent==='待发货'","返回订单列表后丢失筛选或排列方式");
}

/** 从真实商品聊天检查多诉求、模型回退、同订单不同商品与历史恢复。 */
async function mallConsultJourney() {
  await login("buyer-001");
  const page = "/mall/orders?order=CN-9870&item=";
  const url = "/api/buyer/mall/orders/CN-9870/items/1/consult";
  const history = () => evaluate("fetch(" + JSON.stringify(url) + ").then(r=>r.json()).then(r=>r.turns)");
  await go(page + "1");
  await text("mall-chat-order", "CN-9870");
  await chat("退货邮费咋算");
  await expect("document.querySelector('.consult-choices')?.textContent.includes('买错了')", "未出现运费原因追问");
  await evaluate("document.querySelector('.consult-choices button').click();true");
  await text("mall-dialogue", "预计自付");
  let turns = await history();
  assert.equal(turns.at(-1).result.route, "rules");
  const detail = await evaluate("fetch('/api/buyer/mall/orders/CN-9870').then(r=>r.json())");
  const latest = turns.at(-1).result;
  assert.equal(latest.terms_version, detail.terms_version);
  assert.ok(latest.evidence.some(c=>c.id==="freight" && detail.terms.includes(c.text)));
  const earlier = await evaluate("fetch('/api/buyer/mall/orders/CN-9877/items/1/consult',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:'买错了退货运费多少',reason:'wrong_purchase',record:false})}).then(r=>r.json())");
  assert.equal(earlier.reference.shop_id, latest.reference.shop_id);
  assert.ok(earlier.terms_version < latest.terms_version);
  assert.equal(Number(latest.freight.fee) - Number(earlier.freight.fee), 7);
  assert.notEqual(earlier.evidence.find(c=>c.id==="freight").text, latest.evidence.find(c=>c.id==="freight").text);
  await expect("!document.querySelector('#mall-dialogue .consult-citation, #mall-dialogue summary')","普通回复多出了条款");
  await evaluate("document.querySelector('#mall-dialogue li:last-child .button-soft').click();true");
  await expect("document.querySelector('#mall-reason').value==='wrong_purchase' && !document.querySelector('#mall-application-fields').hidden","聊天无法带入可办的申请");
  assert.equal((await history()).length, 2, "表单预检生成了多余的咨询");
  await chat("那多少钱？");
  await expect("document.querySelector('#mall-dialogue li:last-child p')?.textContent.includes('预计自付')","省略金额的追问未回答");
  assert.ok((await history()).at(-1).result.answer.includes("预计自付"));
  await chat("按哪条规定？");
  await expect("document.querySelector('#mall-dialogue li:last-child .consult-citation')?.textContent.includes('寄回运费')","追问没有引用运费原文");
  assert.equal((await history()).at(-1).result.evidence.length,1);
  await chat("外包装破了，照片怎么拍？");
  await expect("document.querySelector('.consult-choices')?.textContent.includes('里面也坏了')", "未核实商品本体状况");
  await evaluate("document.querySelector('.consult-choices button').click();true");
  await text("mall-dialogue", "遮住手机号");
  await expect("document.querySelector('#mall-application [name=description]').value.includes('商品也有损坏')","未带入本人确认的破损情况");
  await evaluate("document.querySelector('#mall-application [name=description]').value='我自己填写的补充说明';true");
  await chat("刚看清楚，商品没坏");
  await text("mall-dialogue", "商品本身完好");
  await expect("!document.querySelector('#mall-reason').value && document.querySelector('#mall-application-fields').hidden && document.querySelector('#mall-application [name=description]').value==='我自己填写的补充说明'", "改口后仍沿用旧申请，或覆盖了买家的说明");
  await chat("那款啥样，款子回了没");
  await text("mall-dialogue", "暂时没有售后申请");
  turns = await history();
  assert.equal(turns.at(-1).result.route, "function_call");
  assert.ok(turns.at(-1).result.answer.includes(detail.items[0].variant));
  await chat("物流到哪了，再看看买了几件");
  await text("mall-dialogue", "已有签收记录");
  assert.equal((await history()).at(-1).result.route, "fallback");
  await chat("帮我查 CN-300002 的退款");
  await expect("document.querySelector('#mall-dialogue li:last-child a')?.getAttribute('href')==='/mall/orders'", "提到其他订单时应提供选择入口");
  const length = (await history()).length;
  await go(page + "2");
  await expect("document.querySelector('#mall-item-title')?.textContent===" + JSON.stringify(detail.items[1].title), "未切换到同单第二款商品");
  await chat("退货邮费咋算");
  await expect("document.querySelector('.consult-choices')?.textContent.includes('买错了')", "另一件商品借用了上件商品原因");
  await go(page + "1");
  await text("mall-dialogue", "帮我查 CN-300002 的退款");
  assert.equal((await history()).length, length, "切换商品修改了原商品的历史");
  assert.equal(await evaluate("fetch('/api/buyer/mall/orders/CN-300002/items/1/consult',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:'退多少'})}).then(r=>r.status)"),404);
}

/** 从商户页面检查底线、版本并发和店铺隔离，从买家页面核对新旧条款。 */
async function shopPolicyJourney() {
  await login("shop-001");
  await expect("Boolean(document.querySelector('nav a[href=\"/merchant/mall/policies\"]'))","商家导航缺少店铺条款入口");
  await evaluate("document.querySelector('nav a[href=\"/merchant/mall/policies\"]').click();true");
  await expect("Boolean(document.querySelector('.policy-columns form [name=return_days]'))","店铺缺少发布入口").catch(async error => {
    throw new Error(error.message + "：" + await evaluate("location.pathname+' '+document.body.innerText.slice(0,350)"));
  });
  const api = "/api/merchant/mall/policies";
  const book = await evaluate("fetch('" + api + "').then(r=>r.json())");
  const shop = book.shops.find(s=>s.shop_id === "market-001");
  assert.equal(shop.supplement_templates.length,50);
  const note = "配件少发可以申请补发，请告诉我们缺少的线材名称。";
  await evaluate(`(()=>{
    const form=document.querySelector('textarea[name=note]').form;
    const put=(element, value, type)=>{
      Object.getOwnPropertyDescriptor(type.prototype,'value').set.call(element,value);
      element.dispatchEvent(new Event('input',{bubbles:true}));
    };
    put(form.elements.title,'充电线缺件',HTMLInputElement);
    put(form.elements.note,${JSON.stringify(note)},HTMLTextAreaElement);
    return true;
  })()`);
  await evaluate("document.querySelector('textarea[name=note]').form.requestSubmit();true");
  await expect("document.body.innerText.includes('已提交平台审核')","商家未能提交补发说明");
  await login("staff");
  await go("/staff/mall/catalog");
  await expect("Array.from(document.forms).some(f=>f.elements.feedback && f.innerText.includes("+JSON.stringify(note)+"))","平台未看到商家的待审核说明");
  await evaluate("((f)=>{f.elements.feedback.value='已核对平台保障';f.elements.decision.value='approved';f.requestSubmit();return true})(Array.from(document.forms).find(f=>f.elements.feedback && f.innerText.includes("+JSON.stringify(note)+")))");
  await expect("document.body.innerText.includes('审核结论已保存')","平台审核未保存");
  await login("shop-001");
  await go("/merchant/mall/policies");
  await expect("Boolean(document.querySelector('.policy-columns form [name=return_days]'))","店铺条款编辑未恢复");
  await trialPolicy(shop, note);
  const edition = shop.editions[0];
  const body = {base_version:edition.version, effective_on:shop.first_date, return_days:20, fault_days:45,
    warranty_days:180, reply_hours:16, return_postage:8, item_credit:4, seller_advances:false, guidance_key:"packing"};
  const post = (id, payload) => evaluate("fetch('" + api + "/"+id+"',{method:'POST',headers:{'Content-Type':'application/json'},body:" +
    JSON.stringify(JSON.stringify(payload)) + "}).then(r=>r.status)");
  assert.equal(await post("market-002", body),404);
  const formPatch = values => evaluate("((f,values)=>{for(const [key,value] of Object.entries(values)){if(f.elements[key])f.elements[key].value=String(value)} f.requestSubmit();return true})(document.querySelector('.policy-columns form')," + JSON.stringify(values) + ")");
  await formPatch({...body, return_days:3, seller_advances:"no"});
  await expect("document.querySelector('.policy-book [role=alert]')?.textContent.includes('至少为')","低于底线的发布未解释原因");
  await formPatch({...body, seller_advances:"no"});
  await expect("document.querySelector('.policy-book [role=status]')?.textContent.includes('已发布')","有效版本未发布");
  assert.equal(await post(shop.shop_id,body),409);
  await login("buyer-001");
  assert.equal(await evaluate("fetch('" + api + "').then(r=>r.status)"),403);
  await go("/mall/orders?order=CN-9877&item=1");
  await text("mall-chat-order", "CN-9877");
  const historical = await timedChat("查本单条款：缺件能补发吗？");
  assert.ok(!historical.answer.includes(note),historical.answer);
  const later = await evaluate("fetch('/_test/copy-purchase-after-review',{method:'POST'}).then(r=>r.json()).then(r=>r.order_id)");
  assert.equal(later,"CN-9867");
  await go("/mall/orders?order=CN-9867&item=1");
  await text("mall-chat-order","CN-9867");
  const purchasedAfterReview = await timedChat("查本单条款：缺件能补发吗？");
  assert.ok(purchasedAfterReview.answer.includes(note) && purchasedAfterReview.answer.includes("店铺已审核条款"),purchasedAfterReview.answer);
  await go("/mall/orders?order=CN-9877&item=1");
  await text("mall-chat-order","CN-9877");
  await chat("本店最新的寄回运费是多少？");
  await expect("document.querySelector('#mall-dialogue li:last-child p')?.textContent.includes('你这单下单时用的是第 1 版')","店铺现行约定和旧单承诺混淆");
  await chat("平台最低保障是什么？");
  await expect("document.querySelector('#mall-dialogue li:last-child p')?.textContent.includes('平台第 1 版')","平台底线未单独查询");
}

/** 已审条款可从商家端按商品试查，其他店铺和 SKU 无法越权读取。 */
async function trialPolicy(shop, note) {
  await expect("Boolean(document.querySelector('.policy-trial form'))","商家试查入口缺失");
  const sku = shop.products[0].sku;
  const question = "缺件能补发吗？";
  const compare = process.env.MALL_BROWSER_JOURNEY === "policy-preview";
  const runForm = async () => evaluate(`(()=>{
    const details=document.querySelector('.policy-trial');
    details.open=true;
    const form=details.querySelector('form');
    form.elements.sku.value=${JSON.stringify(sku)};
    form.elements.question.value=${JSON.stringify(question)};
    form.elements.hybrid.checked=${compare};
    form.requestSubmit();
    return true;
  })()`);
  await runForm();
  await expect("document.querySelector('.policy-trial [aria-live]')?.innerText.includes("+JSON.stringify(note)+")",
    "商家没看到刚审核的本店条款").catch(async failure => {
      throw new Error(failure.message + " / " + await evaluate("document.querySelector('.policy-trial')?.innerText"));
    });
  if (compare) {
    await expect("document.querySelector('.policy-trial [aria-live]')?.innerText.includes('混合召回试查')",
      "本机 E5 没有返回对照结果");
    const warm = await evaluate(`fetch('/api/merchant/mall/supplements/market-001/preview',{
      method:'POST',headers:{'content-type':'application/json'},
      body:JSON.stringify({sku:${JSON.stringify(sku)},question:${JSON.stringify(question)},hybrid:true})
    }).then(r=>r.json())`);
    assert.equal(warm.hybrid_status,"ok");
    assert.ok(warm.hybrid.evidence.some(source=>source.text===note));
    console.log(JSON.stringify({candidateCount:warm.candidate_count,bm25Ms:warm.lexical.ms,
      hybridWarmMs:warm.hybrid.ms,prepareMs:warm.hybrid.prepare_ms}));
  }
  const cross = await evaluate(`fetch('/api/merchant/mall/supplements/market-002/preview',{
    method:'POST',headers:{'content-type':'application/json'},
    body:JSON.stringify({sku:${JSON.stringify(sku)},question:${JSON.stringify(question)}})
  }).then(r=>r.status)`);
  assert.equal(cross,404);
}

/** 检查窄屏登录和商品咨询，确认问题快捷键与条款展开正常。 */
async function compactLayout() {
  await cdp("Emulation.setDeviceMetricsOverride", {width: 390, height: 844, mobile: true, deviceScaleFactor: 1});
  try {
    await go("/login");
    await expect("document.querySelector('.commerce-login-art') && document.readyState==='complete'", "登录新版未加载");
    assert.equal(await evaluate("document.documentElement.scrollWidth <= innerWidth"), true);
    await login("buyer-001");
    await go("/mall/orders?order=CN-300001&item=1");
    await text("mall-chat-order", "CN-300001");
    assert.equal(await evaluate("Boolean(document.querySelector('#commerce-app .workbench-shell'))"), true);
    assert.equal(await evaluate("document.documentElement.scrollWidth <= innerWidth"), true);
    await evaluate("document.querySelector('[data-question]').click();true");
    assert.equal(await evaluate("document.querySelector('#mall-question').value"), "如果退货，运费谁承担？");
    await chat("这件商品的物流到哪了？");
    await text("mall-dialogue", "尚未发货");
    await expect("!document.querySelector('#mall-dialogue .consult-citation')", "物流记录不应引用条款");
  } finally {
    await cdp("Emulation.clearDeviceMetricsOverride");
  }
}

/** 在桌面视口记录真实组件的视觉效果。 */
async function desktopPreview() {
  if (!preview) return;
  await cdp("Emulation.setDeviceMetricsOverride", {width: 1440, height: 960, mobile: false, deviceScaleFactor: 1});
  await go("/login");
  await expect("document.readyState==='complete' && Boolean(document.querySelector('[data-preview=merchant]'))", "登录入口未载入");
  await evaluate("document.querySelector('[data-preview=merchant]').click();true");
  await text("portal-title","店铺售后");
  await click("secret-toggle");
  await expect("document.querySelector('#access-secret').type==='text'", "口令显隐未响应");
  await click("secret-toggle");
  await snapshot("login");
  await login("buyer-001");
  await go("/mall/orders");
  await expect("Boolean(document.querySelector('#mall-list a'))", "订单票夹未加载");
  await expect("document.querySelector('#mall-list .ticket-body strong')?.textContent.includes('鼠标')","订单封面缺少实际商品");
  await evaluate("document.querySelectorAll('.range-tabs button')[1].click();true");
  await expect("!document.querySelector('#mall-list-message').textContent && [...document.querySelectorAll('#mall-list li')].every(row=>row.textContent.includes('待发货'))", "状态筛选与订单结果不一致");
  await evaluate("document.querySelector('.range-tabs button').click();true");
  await expect("document.querySelectorAll('#mall-list li').length>3","全部包裹未恢复");
  await snapshot("dispatch");
  await evaluate("document.querySelector('.layout-toggle button:last-child').click();true");
  await expect("document.querySelector('#mall-list').dataset.layout==='ledger'", "清单视图未切换");
  await evaluate("document.querySelector('#mall-list a').click();true");
  await expect("Boolean(document.querySelector('#mall-order-view'))", "票夹无法打开订单详情");
  await evaluate("history.back();true");
  await expect("document.querySelector('#mall-list')?.dataset.layout==='ledger'", "返回列表后排列方式丢失");
  await go("/mall/orders?order=CN-9877");
  await text("mall-products", "栖禾数码");
  await snapshot("orders");
  await go("/mall/orders?order=CN-9877&item=1");
  await text("mall-chat-order", "CN-9877");
  await evaluate("document.querySelector('.focus-chat').click();true");
  await expect("getComputedStyle(document.querySelector('.support-application')).display==='none'", "展开咨询区后仍占用申请空间");
  await evaluate("document.querySelector('#mall-chat-order summary').click();true");
  await expect("document.querySelector('#mall-chat-order').open", "咨询中无法展开订单");
  await expect("document.querySelector('.chat-composer').getBoundingClientRect().bottom<=innerHeight","展开订单后输入框超出视口");
  await chat("请问这件商品的物流到哪了？");
  await text("mall-dialogue", "签收记录");
  await snapshot("chat");
}

try {
  const boot = await eventually(async () => stdout.match(/PORT=(\d+)\s+TEST_PASSWORD=([-\w]+)/), "验收应用未启动", 45000);
  origin = "http://127.0.0.1:" + boot[1];
  password = boot[2];
  await eventually(async () => (await fetch(origin + "/api/config")).ok, "验收应用无法连接");
  const profile = mkdtempSync(join(process.cwd(), ".local", "platform-review-browser-"));
  chrome = spawn(executable, ["--headless=new", "--no-first-run", "--disable-background-networking",
    "--remote-debugging-port=0", "--user-data-dir=" + profile, "about:blank"], {windowsHide: true, stdio: "ignore"});
  const port = await eventually(async () => readFileSync(join(profile, "DevToolsActivePort"), "utf8").split(/\r?\n/)[0],
    "Chrome 未启动");
  const targets = await (await fetch("http://127.0.0.1:" + port + "/json/list")).json();
  socket = new WebSocket(targets.find(target => target.type === "page").webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  socket.onmessage = event => {
    const reply = JSON.parse(event.data);
    if (reply.method === "Runtime.exceptionThrown") {
      const details = reply.params.exceptionDetails;
      const reason = details.exception?.description || details.text;
      pageErrors.push(reason);
      console.error(reason);
    }
    const callback = callbacks.get(reply.id);
    if (!callback) return;
    callbacks.delete(reply.id);
    if (reply.error) callback.reject(new Error(reply.error.message));
    else callback.resolve(reply.result);
  };
  await cdp("Runtime.enable");
  await cdp("Page.enable");
  await cdp("Emulation.setFocusEmulationEnabled", {enabled:true});
  if (desktopOnly) await cdp("Emulation.setDeviceMetricsOverride", {width:1440,height:960,mobile:false,deviceScaleFactor:1});
  if (process.env.MALL_BROWSER_JOURNEY === "fulfillment-chat") {
    await fulfillmentChatJourney();
  } else if (process.env.MALL_BROWSER_JOURNEY === "task-actions") {
    await taskActionsJourney();
  } else if (process.env.MALL_BROWSER_JOURNEY === "semantic") {
    await semanticJourney();
  } else if (["policies", "policy-preview"].includes(process.env.MALL_BROWSER_JOURNEY)) {
    await shopPolicyJourney();
  } else if (process.env.MALL_BROWSER_JOURNEY === "consult-performance") {
    await consultPerformance();
  } else if (process.env.MALL_BROWSER_JOURNEY === "multi-intent") {
    await multiIntentJourney();
  } else if (process.env.MALL_BROWSER_JOURNEY === "citations") {
    await citationJourney();
  } else if (process.env.MALL_BROWSER_JOURNEY === "product-experience") {
    await productExperienceJourney();
  } else if (process.env.MALL_BROWSER_JOURNEY === "conversation-scope") {
    await conversationScopeJourney();
  } else if (process.env.MALL_BROWSER_JOURNEY === "clarification") {
    await clarificationJourney();
  } else if (process.env.MALL_BROWSER_JOURNEY === "fit-exchange") {
    await fitExchangeJourney();
  } else if (process.env.MALL_BROWSER_JOURNEY === "portfolio") {
    await portfolioJourney();
  } else {
    await orderNavigation();
    await mallConsultJourney();
    await desktopPreview();
    await receiptReview();
    await addressReview();
    await exchangeDelivery();
    await reshipDelivery();
    await cancelAndRefund();
    await transitReturn();
    await packageReturn();
    await signedAfterFailedInterception();
    if (!desktopOnly) await compactLayout();
    await directoryJourney();
    await shopPolicyJourney();
  }
  assert.deepEqual(pageErrors, []);
  console.log(process.env.MALL_BROWSER_JOURNEY ? "指定浏览器验收通过。" :
    "三角色浏览器验收通过，覆盖店铺发布、咨询、订单、退款和包裹争议。");
} catch (error) {
  console.error(error);
  if (stderr) console.error(stderr.trim());
  process.exitCode = 1;
} finally {
  socket?.close();
  chrome?.kill();
  host.kill();
}
