## ADDED Requirements

### Requirement: 导入带来源的 Olist 物流样本
导入脚本 SHALL 读取 Olist 订单 CSV 并校验必填列、编号和日期，按场景固定抽样，默认预览，显式应用时将样本写入独立表。

#### Scenario: 预览分场景样本
- **WHEN** 用户运行导入命令且未指定 `--apply`
- **THEN** 导入脚本 SHALL 显示每类数量及示例编号并跳过数据库操作

#### Scenario: 再次导入同一批数据
- **WHEN** 用户用 `--apply` 重复导入同一文件与样本配置
- **THEN** 已存在的源订单 SHALL 保持原样，新增数量为零

### Requirement: 按 Olist 编号查询历史物流
买家工作台 SHALL 按 Olist 编号返回物流状态、相关日期和历史快照来源。

#### Scenario: 已发货但超出预计送达日
- **WHEN** 样本显示已发货、未签收且预计送达日早于历史查询日
- **THEN** 页面 SHALL 显示超出预计送达时间和对应日期

#### Scenario: 未提供订单编号
- **WHEN** 买家询问物流且未提供编号
- **THEN** 工作台 SHALL 追问编号，补充 Olist 编号后继续查询
