# 企业知识问答 Agent

小影校招 AI Agent 笔试 **第3题**。从合成制度和产品FAQ中查找答案，读取原文、核对冲突，返回回答、文档ID、版本日期、原文引用、资料缺口和任务状态。

**已实际验证：59项自动化测试、20个离线场景通过，并完成本机浏览器操作验证。真实 DeepSeek 接入已实现，但本地未配置密钥，真实模型决策效果尚未验证。Mock 只证明流程、检索及引用边界。**

Python 3.11+ / CLI / 本机网页 / 原生工具调用，无第三方运行或测试依赖，无 Agent 框架。

## 评审入口

- [需求与成功标准](docs/01-requirements.md)
- [调用流程、动态决策和取舍](docs/02-solution.md)
- [实际评估和逐项证据](docs/03-evaluation.md)
- [AI使用与个人贡献](docs/04-ai-usage.md)
- [复盘与用时](docs/05-retrospective.md)
- [测试原始输出](examples/test-results.txt) / [20个场景汇总](examples/mock/summary.json) / [浏览器记录](examples/web/verification.md)

## 安装与验证

```bash
git clone https://github.com/yuhang-824/enterprise-knowledge-qa-agent.git
cd enterprise-knowledge-qa-agent
python scripts/test.py
python scripts/evaluate.py --model mock
```

以上命令不需要安装依赖或配置密钥。每个场景保存自己的输入、知识库快照、实际输出和 Trace，检验引用原文及内容指纹是否与本次资料一致。

## 打开本机界面

```bash
python scripts/web.py
```

打开 [http://127.0.0.1:8766](http://127.0.0.1:8766)，保持启动窗口运行；Ctrl+C结束。端口占用时可用 `--port 8767`。页面可提问、切换资料、查看知识库、确认适用制度、追问、展开 Trace、下载JSON和查看历史。

建议依次验证：

1. **文档冲突**：D01写30天、D02写15天；应列出两份原文并请求确认，不能按日期自动选15天。
2. **确认采用D01**：点击用户确认按钮后，重新查证并依据D01回答30天。
3. **替换原文**：实际把D01原文改为45天，并停用D02；回答及引用应同步为45天。
4. **改写问题**：“报账最晚什么时候交？”首次检索无结果，扩展检索后核对报销期限。
5. **上下文追问**：先问报销，再问“那需要哪些材料？”；引用D01材料片段，历史回答本身不作为证据。
6. **工具失败 / 资料外问题**：明确失败或资料不足，不能生成无依据答案。

网页直接调用与CLI相同的Agent核心，默认使用离线Mock。历史保存在 `runtime/web/`，不提交运行时文件。服务仅绑定127.0.0.1，不是生产鉴权或企业权限系统。

## CLI示例

默认知识库故意保留报销期限冲突：

```bash
python scripts/ask.py --input examples/mock/document_conflict.input.json --output runtime/conflict.run.json
```

明确答案场景必须配套使用该场景的数据快照（其中D02停用）：

```bash
python scripts/ask.py --input examples/mock/clear_answer.input.json --data examples/mock/clear_answer.data.json --output runtime/clear.run.json
```

源包入口也可用 `python -m pip install -e .` 安装，然后执行 `knowledge-qa`、`knowledge-qa-web` 或 `python -m knowledge_qa`。

## 输入

```json
{
  "request_id": "question-001",
  "question": "那需要哪些材料？",
  "history": [{"role": "user", "content": "报销期限是多久？"}],
  "applicable_doc_id": null,
  "scope": null
}
```

`history`最多8条user/assistant消息，仅作问题理解，不能当作制度证据。`applicable_doc_id`和`scope`是用户明确提供的可信边界；模型不能自行传入它们来消除冲突。页面确认按钮会显示并提交用户选定的制度。

## 证据校验与状态

- 检索预览不足以作答，必须读取原文，再调用check_evidence。
- 若索引内相关来源没读全，拒绝宣布无冲突。日期只作元数据，明确的原文替代关系或用户选择才可限定适用依据。
- 引用必须是已读片段的source_id，且包含所问事实；错误文档、错误片段、伪造ID、未检查的证据都不能生成成功回答。
- 答案用已核验事实模板生成，避免额外无依据主张。当前支持报销期限、报销材料、年假天数和产品导出分辨率四类合成事实，不是开放域语义蕴含系统。
- 修改 `data/corpus.json` 后，CLI或网页下一次请求会读取新资料；每次任务内部使用固定快照，引用保留该快照文本及SHA-256指纹。

| 状态 | 含义 | CLI退出码 |
|---|---|---|
| answered | 有已核验、无未决冲突的引用支持答案 | 0 |
| needs_clarification | 主题、适用制度或范围需确认 | 0 |
| insufficient_evidence | 补充检索后仍缺支持资料 | 0 |
| failed | 工具失败，未形成可靠结论 | 2 |
| incomplete | 步数耗尽或模型异常，保留已有记录 | 2 |
| error | 输入、配置或资料错误 | 1 |

默认最多24步，`--max-steps 1..200`可配置。Trace记录实际参数、返回、后续动作及一句依据，不记录模型内部思维过程。

## 真实模型验证

复制 `.env.example` 为 `.env`，在本地填写 `DEEPSEEK_API_KEY`。默认 `MODEL_NAME=deepseek-flash`，可按账号修改，不要提交密钥。

```bash
python scripts/evaluate.py --model deepseek --case clear_answer --case document_conflict
python scripts/evaluate.py --model deepseek --core
```

真实运行会产生API费用，结果写入 `examples/real/`。`--core`覆盖六项必验；如失败会保留实际结果，不会自动改为Mock。真实模式的网页可用 `python scripts/web.py --model deepseek` 启动，密钥只在后端读取。

## 目录与提交

```text
src/knowledge_qa/     问题理解、检索、工具、证据核对、循环、模型、CLI、网页
tests/               单元/集成/HTTP测试
data/                带文档ID、版本日期及片段的合成制度和FAQ
examples/            实际输入、知识库快照、输出、Trace、测试与界面记录
docs/                五份要求文档
scripts/             源码启动、测试、验收与提交文本生成
.github/workflows/   Windows/Linux自动验证
```

提交题号为第3题，[GitHub仓库](https://github.com/yuhang-824/enterprise-knowledge-qa-agent)。最终SHA用 `git rev-parse HEAD` 获取；`python scripts/submission.py` 可生成当前版本的提交文本。

限制：词法检索和有限术语扩展、人工标注的主题索引、受限事实抽取、合成资料；没有企业ACL、真实文档解析、向量库或生产部署。不能把离线结果当作真实模型质量或企业通用问答准确率。
