# 预期、实际结果、失败和限制

## 已实际运行

2026-10-31，Windows / Python3.11.9：

```bash
python scripts/test.py
python scripts/evaluate.py --model mock
```

[测试输出](../examples/test-results.txt)记录59项测试通过；[验收汇总](../examples/mock/summary.json)记录20/20场景通过。每组的input/data/output/trace均自动保存，引用文本与内容指纹还会对照本次数据快照检查。

当前没有配置DeepSeek密钥，**没有真实模型运行记录**；原生协议的替身测试、Mock实际执行和代码存在不能证明真实模型决策质量。真实验证命令已提供，未运行的项目明确保留为缺口。

## 六项必验

| 场景 | 实际结果 | 输出与Trace |
|---|---|---|
| 明确答案 | answered，30天，引用D01原文 | [输出](../examples/mock/clear_answer.output.json) / [Trace](../examples/mock/clear_answer.trace.json) |
| 文档冲突 | needs_clarification，同时引用30/15，不自动选较新文档 | [输出](../examples/mock/document_conflict.output.json) / [Trace](../examples/mock/document_conflict.trace.json) |
| 资料外问题 | 补充检索仍无依据，insufficient_evidence | [输出](../examples/mock/out_of_corpus.output.json) / [Trace](../examples/mock/out_of_corpus.trace.json) |
| 改写相似问题 | 首次无命中，expanded后读原文并回答30天 | [输出](../examples/mock/paraphrase.output.json) / [Trace](../examples/mock/paraphrase.trace.json) |
| 上下文追问 | 按先前用户问题识别报销领域，引用材料片段C02 | [输出](../examples/mock/context_followup.output.json) / [Trace](../examples/mock/context_followup.trace.json) |
| 工具失败 | D02读取失败，failed；不能把D01当作唯一有效制度 | [输出](../examples/mock/tool_failure.output.json) / [Trace](../examples/mock/tool_failure.trace.json) |

## 不同工具结果驱动的路径

1. clear_answer：inspect → search → read原文 → check(supported) → answer。
2. document_conflict：相同问题，读取D01和D02 → check(conflict) → request_clarification。
3. paraphrase：search(keyword)无结果 → search(expanded)有结果 → read → check → answer。
4. tool_failure：读取D01成功、D02失败 → stop(tool_failure)，保留已读来源。

这些是确定性Mock的真实运行路径，**不是实际模型推理效果证据**。

## 更换资料后的行为变化

- [D01改为45天](../examples/mock/changed_source.output.json)：答案、支持片段和内容指纹同步变化，不沿用30天。
- [D02也改为30天](../examples/mock/consistent_documents.output.json)：由冲突追问转为answered。
- [用户确认D01](../examples/mock/user_selected_policy.output.json)：按用户选择回答30天，仅引用D01。
- [原文明确替代](../examples/mock/explicit_precedence.output.json)：D02明确替代D01，回答15天并引用含替代句的原文。
- [来源无法核验](../examples/mock/unparseable_related.output.json)：即便另一文档可抽取15天，也不忽略未知来源后给唯一答案。

其他场景见汇总：范围缺失/明确、无效指定制度、步数上限、检索失败、不可信资料指令、缺少上下文、年假与产品FAQ。

## 工程与浏览器验证

59项测试覆盖伪造来源ID、错误片段、未读全来源、未核验证据、任意模型答案、擅改适用制度、日期偏置、循环替代、助手历史污染、固定快照、原文变更、失败和步数上限。HTTP测试覆盖同一核心、用户确认、源文件更新、历史快照和本机访问边界。

复查发现仅按领域选事实会答非所问，例如把“报销多久到账”当成“报销提交期限”。已收紧问题属性识别，并增加到账时长、年假申请、导出故障、年假后的材料追问4项回归测试；这些问题现在请求澄清，不套用30天/10天/1080p。

本机页面的实际点击记录与截图见 [浏览器验证](../examples/web/verification.md)。截图只补充可视检查，不替代源代码、输入输出或Trace。页面默认离线Mock，所有浏览器验证仍属于离线工程验证。

## 真实模型验证与未完成项

配置本地.env后：

```bash
python scripts/evaluate.py --model deepseek --case clear_answer --case document_conflict
python scripts/evaluate.py --model deepseek --core
```

必须核对examples/real/的实际输出和失败，不能把Mock文件改名作真实记录。默认每轮24步，达到上限incomplete；真实模型可能选择不同查证路径，验收脚本不符时会退出2并记录差异，不修改实际输出。

尚未评估真实模型稳定性、延迟、token费用或大规模问答准确率。词法与句式规则覆盖有限，主题索引由人工维护；没有企业ACL、任意PDF解析、向量库、复杂法规层级或语义蕴含模型。对资料注入的测试只证明Mock/工具边界，不证明真实模型的完整抗注入能力。
