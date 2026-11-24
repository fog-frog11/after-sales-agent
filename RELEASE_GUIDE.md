# 公开源码与本机部署

本仓库是使用虚构商城数据展示三角色售后与 Agent 查询、检索、确认办理的求职作品。启动命令只监听 `127.0.0.1`。订单、金额、退款和物流办理结果都由工作台记录，不连接真实电商平台或支付、快递系统。

## Docker Compose 启动整套应用

安装 Docker Desktop，在项目根目录运行以下 PowerShell 命令。第一条只在缺少 `.env` 时生成随机数据库口令，不覆盖现有配置。

```powershell
pwsh -File scripts/prepare_docker.ps1
docker pull postgres:17-alpine
docker pull registry-1.docker.io/milvusdb/milvus:v3.0.0
docker compose --profile workbench up -d --build --wait
```

打开 `http://127.0.0.1:8766/login`。用下面的命令分别查看一个买家、一个店铺和平台客服的随机口令；容器中的账号及业务记录由 Docker 卷持久保存。

```powershell
docker compose exec workbench python -X utf8 -B -m scripts.show_demo_login buyer-001
docker compose exec workbench python -X utf8 -B -m scripts.show_demo_login shop-001
docker compose exec workbench python -X utf8 -B -m scripts.show_demo_login staff
```

首次镜像构建会安装依赖并下载约 135 MB 的 E5 ONNX 模型，脚本核对 SHA-256；镜像内的模型不会因账号数据卷遮蔽。Compose 内部连接 PostgreSQL 和 Milvus，对宿主机只开放 `127.0.0.1:8766` 的工作台端口。已有 `8766` 端口占用时在 `.env` 修改 `AFTER_SALES_DOCKER_PORT`。启用每轮模型规划与回复还需在 `.env` 填入自己的 `DEEPSEEK_API_KEY`，然后重建工作台容器；留空时使用规则降级。第一次条款查询按购买范围建立索引，可能较慢。

源码中的 [`Dockerfile`](Dockerfile) 构建 React 和 Python 服务；[`compose.yaml`](compose.yaml) 编排三项服务；[`scripts/prepare_docker.ps1`](scripts/prepare_docker.ps1) 准备 Docker 配置。不会连接真实电商平台、支付或快递系统。

## 不使用应用容器，从源码启动

准备 Python 3.14、Node.js 22、Docker Desktop 和 Chrome。在项目根目录运行以下 PowerShell 命令：

```powershell
python -X utf8 -B -m scripts.init_workbench --prepare-config
python -m pip install -r requirements-dev.txt
npm ci --ignore-scripts
npm run build
docker pull postgres:17-alpine
docker compose up -d --wait --pull never postgres
python -X utf8 -B -m scripts.init_workbench
python -X utf8 -B server.py
```

打开 `http://127.0.0.1:8765/login`。账号索引在 `.local/mall-logins.json`，也可运行 `python -X utf8 -B -m scripts.show_demo_login buyer-001` 查看指定账号。首次初始化生成虚构的 50 位买家、100 家店铺、300 笔订单以及 4 笔可按步骤验收的案例订单。重复初始化会保留已有办理记录。

这条基础路径不要求 DeepSeek API 密钥或 Milvus。聊天会使用规则降级，适合查看三角色流程；**不能用它代表完整的模型规划、依据生成和混合检索效果**。Docker Compose 路径会准备 Milvus 和模型，但同样需要自己的 DeepSeek API 密钥才能启用模型对话。

## 打开完整 Agent 与检索链路

1. 在本机 `.env` 填入自己的 `DEEPSEEK_API_KEY`。不要提交 `.env`、账号文件或日志。真实模型调用产生的费用由该 API 账号承担。
2. 安装 CPU 检索依赖，并下载项目已验收的 E5 ONNX 模型。模型下载脚本会核对 SHA-256；已有不同版本不会被覆盖。使用上面的 Docker Compose 路径时，这一步由镜像构建完成。

   ```powershell
   python -m pip install --target .local/retrieval-deps -r requirements-retrieval.txt
   python -X utf8 -B -m scripts.prepare_retrieval --download
   ```

3. 启动可选的 Milvus，确认 `.env` 具有下面三行，再重启 `server.py`：

   ```powershell
   docker pull registry-1.docker.io/milvusdb/milvus:v3.0.0
   docker compose --profile policy-search up -d --wait --pull never
   ```

   ```text
   MILVUS_URL=http://127.0.0.1:19531
   MALL_PERSISTENT_DENSE=1
   MALL_WINDOW_RERANK=1
   ```

   新工作区的初始化脚本会写入后两项；旧 `.env` 不会被脚本覆盖。首次查询会按订单适用范围建索引，因此首轮可能较慢。模型或 Milvus 暂不可用时会降级，聊天气泡里的“查看本轮查询”可核对实际走过的路径。

模型文件来自 [Xenova/multilingual-e5-small](https://huggingface.co/Xenova/multilingual-e5-small)，原模型见 [intfloat/multilingual-e5-small](https://huggingface.co/intfloat/multilingual-e5-small)。模型和下载的公开研究数据不在源码包内；本项目没有微调模型权重。所用模型文件版本以 `scripts/prepare_retrieval.py` 中的 SHA-256 为准。

## 验收与边界

```powershell
python -X utf8 -B -m scripts.check_public_release
python -X utf8 -B -m unittest discover -s tests
python -X utf8 -B -m scripts.check_business_release
```

最后一个脚本串行检查确认事务、三角色浏览器和小规模只读并发，运行时需要数据库、Chrome，浏览器测试会使用回滚事务。真实模型履约聊天可另运行 `python -X utf8 -B -m scripts.check_business_release --live`。公开题集、检索分母和已知失误见 [README](README.md)；作者自编或已用于回归的题不能当作外部盲测。

2026-11-24 已实际构建 Linux 工作台镜像，并在独立的新 Docker 卷从空库启动三项服务；三角色 HTTP 登录、304 笔订单、604 条商品、50 位买家、100 家店铺、Milvus 健康和 E5 384 维推理均通过。[验收记录](scripts/fixtures/mall_docker_delivery_results.json)保留了范围与限制。此前也在同一台电脑的新解包目录和独立空库完成三角色浏览器验收；这些测试不等于跨操作系统、公网环境或真实外部服务已验证。项目没有真实支付退款、快递接口、外部商家身份认证、备份与运维监控。不要把本机服务直接暴露到公网或输入真实顾客资料。

## 发布文件

`scripts/package_release.py` 只打包列明的源码、虚构种子数据生成器和公开验收汇总；`.env`、`.local`、`node_modules`、构建产物和下载模型不进入 ZIP。打包后用 `scripts.check_public_release --archive <ZIP路径>` 检查文件清单、内容一致性与高置信度密钥模式。

GitHub Actions 的默认 `verify.yml` 跑 Python、事务和浏览器回归；`docker-smoke.yml` 可手动触发，使用全新容器卷构建并验收三角色和检索依赖。它不调用收费模型 API。

目前仓库未附项目级 `LICENSE`。公开可见、可供阅读，不代表已经授予他人复制、修改或再分发许可；若要把它定位为供他人改造部署的开源项目，需由项目作者确定授权方式后再发布许可文件。
