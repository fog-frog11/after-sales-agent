## Why

需要用同一条款范围和题集确认语义召回、融合及二阶段重排的收益，再确定工作台的检索策略。

## What Changes

- 增加本机多语言 E5 语义召回。
- 采用倒数排名融合 BM25 和语义候选。
- 评测用局部证据窗口重新排序前八个融合候选的效果。
- 输出逐题候选、召回、首次命中位置和延迟。

## Capabilities

### New Capabilities

- `hybrid-terms-evaluation`：售后条款混合检索与重排对照。

## Impact

复用公开数据与 Milvus 只读实验集合。E5 及其 CPU 依赖安装在项目实验目录。
