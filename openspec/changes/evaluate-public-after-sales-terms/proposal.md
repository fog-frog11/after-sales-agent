## Why

售后条款问答需要用来源可核对的公开文本评估召回，还需要观察索引扩容时的表现。

## What Changes

- 将工作台的主要入口统一称为「售后条款」。
- 增加公开零售条款检索评测，用公开任务的动作标注关联目标章节。
- 支持加入真实网页售后条款，比较扩容前后的召回和并发延迟。

## Capabilities

### New Capabilities

- `public-terms-retrieval-evaluation`：隔离的公开条款检索和本机负载评测。

### Modified Capabilities

无。

## Impact

新增离线评测代码、测试、公开数据来源与运行说明，以及独立的 Milvus 实验集合。
