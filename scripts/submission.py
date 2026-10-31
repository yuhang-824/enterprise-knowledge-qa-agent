"""Generate honest submission text for the actual current commit."""
import argparse
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "runtime/submission.txt")
    args = parser.parse_args()
    dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()
    if dirty:
        print("Please commit all tracked changes before generating final submission text.")
        return 2
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    summary = json.loads((ROOT / "examples/mock/summary.json").read_text(encoding="utf-8"))
    log = (ROOT / "examples/test-results.txt").read_text(encoding="utf-8")
    count = re.search(r"Ran (\d+) tests", log).group(1)
    text = f"""选择题号：第3题——企业知识问答 Agent

GitHub仓库：
https://github.com/yuhang-824/enterprise-knowledge-qa-agent

最终commit SHA：
{sha}

一、实现内容
使用Python3.11标准库实现CLI和本机验证页面，提供文档检索、扩展检索、原文读取、证据核验、引用回答、追问和停止工具。
真实模型接入使用DeepSeek原生tool_calls，实现模型选动作、执行工具、观察结果、再次决策的有界循环；另提供明确标记的离线Mock。
回答包含文档ID、版本日期、原文片段和内容指纹，以及资料缺口、冲突和任务状态。未读全相关来源或冲突未解决时不能给出唯一答案，日期不自动决定优先级。

二、运行方式
环境：Python3.11及以上；离线运行和测试无需第三方依赖或密钥。
git clone https://github.com/yuhang-824/enterprise-knowledge-qa-agent.git
cd enterprise-knowledge-qa-agent
python scripts/test.py
python scripts/evaluate.py --model mock

启动本机界面：python scripts/web.py
浏览器访问：http://127.0.0.1:8766

三、实际验证结果
{count}项自动化测试通过，{summary['passed']}/{summary['total']}个离线场景通过。
覆盖明确答案、文档冲突、资料外问题、改写相似问题、上下文追问和工具失败六项必验；额外覆盖原文替换、用户确认制度、明确替代关系、范围、步数上限和错误引用防护。
典型结果：D01=30天、D02=15天时列出冲突并请求确认；用户明确选择D01后回答30天。D01原文改为45天时答案和引用同步更新；D02改为30天后从冲突追问转为回答。资料外问题补充检索后明确资料不足，工具失败不虚报成功。
证据见examples/test-results.txt、examples/mock/summary.json、各场景input/data/output/trace，以及examples/web/verification.md。

四、模型与范围边界
当前完成的是离线工程验证。DeepSeek接入代码已实现，但未配置密钥、未执行真实模型验收；Mock不证明真实模型决策效果。
当前采用词法检索、有限术语扩展及四类合成事实的受限抽取和回答模板，不声称开放域企业知识问答或通用语义蕴含能力。真实模型验证、企业ACL、真实文档解析和生产部署未完成。

五、交付材料与AI使用
仓库包含README、src、tests、examples、data、五份要求文档、依赖文件、.env.example和CI配置。
本人提供题目要求并确定开展第三题全过程；Codex辅助仓库创建、方案、代码、数据、测试、文档和本机界面，以及实际离线验证。通用模型HTTP适配和网页传输/基础样式复用第二题代码，第三题业务逻辑独立实现。
实际AI辅助会话用时见docs/05-retrospective.md；个人实际操作时间及个人代码修改尚未另行记录，不将AI生成内容表述为独立编写。
资料全部为合成示例，未提交真实密钥、隐私业务数据或模型内部思维过程。
"""
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    print(text)
    print("Saved to:", args.output)
    return 0


if __name__ == "__main__": raise SystemExit(main())
