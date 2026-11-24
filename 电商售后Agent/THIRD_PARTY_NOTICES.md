# 第三方资料与模型

- `scripts/fixtures/tau-retail-zh.json` 是基于 Sierra Research [τ-bench 2.0.0 中文零售包](https://github.com/sierra-research/tau2-bench/tree/v2.0.0/data/tau2/multilingual/zh/regional)整理的中文检索题和规则译写；上游仓库采用 MIT 许可，原版权及许可文本见 [`third_party/tau2-bench-LICENSE.txt`](third_party/tau2-bench-LICENSE.txt)。完整上游数据包不在本仓库。
- 主聊天可选的 E5 ONNX 文件来自 [Xenova/multilingual-e5-small](https://huggingface.co/Xenova/multilingual-e5-small)，基础模型来自 [intfloat/multilingual-e5-small](https://huggingface.co/intfloat/multilingual-e5-small)。模型权重不在 Git 源码里，Docker 构建或本机脚本按 SHA-256 下载。
- [QingshanAI 电商售后合成语料](https://www.modelscope.cn/datasets/QingshanAI/ecom-after-sale-synthetic)、Olist、ShopTC 等原始下载语料留在开发者本机 `.local`，不随源码上传。仓库中的少量题目和评测汇总注明了各自数据来源；它们不能当作独立的真实用户盲测。

本文件记录来源，不改变上游资料的许可，也不替项目源码自动授予开源许可。
