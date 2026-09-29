# RAG Agent 测评使用指南

> 对标生产级：路由正确性 → 检索相关性 → 生成质量 → 幻觉检测 → 引用合规，五维打分。
> 位置：`backend/scripts/eval/`，Python 解释器用 `C:\Users\18701\.conda\envs\ai-workbench\python.exe`

---

## 一、文件清单

| 文件 | 作用 |
|---|---|
| `dataset.json` | 测评用例集（30 条，10 大类别，可自定义） |
| `eval_runner.py` | 测评主程序（两种模式） |
| `seed_eval_docs.py` | 注入测试知识库（4 份业务文档） |
| `cleanup_eval_docs.py` | 清理测试文档 |
| `eval_report_*.json` | 每次运行生成的报告 |

## 用例体系（30 条，10 类）

| 类别 | 用例 | 验证点 | 路由判定 |
|---|---|---|---|
| `chitchat` | 001/030 | 闲聊不误入 RAG | 严格 direct |
| `common_sense` | 002/027/028 | 常识/代码任务走 direct | 严格 direct |
| `rag_factual` | 003/004/007/017~022 | 业务文档事实（退货/隐私/考勤/API/工资） | 严格 rag |
| `rag_summary` | 005 | 摘要类 RAG | 严格 rag |
| `rag_factual_ai` | 009~014/016 | **真实知识库回归**（FastAPI/LangGraph/Jupyter） | 严格 rag |
| `rag_summary_ai` | 015 | 真实知识库摘要 | 严格 rag |
| `out_of_scope` | 006 | 超边界（天气）不产生幻觉 | 宽容（direct/rag） |
| `ambiguous` | 008/029 | 歧义/低信息 → 澄清，不臆测 | 宽容 |
| `security` | 023/024/025 | Prompt 注入/越权输出/违规内容 → 拒绝 | 宽容 + 拒绝措辞检查 |
| `chitchat_negative` | 026 | 情绪化输入 → 礼貌不反击 | 宽容 |

> `rag_factual_ai` / `rag_summary_ai` 是**真实知识库回归**：问题来自你库里真实存在的 AI 学习文档（FastAPI 流式开发、LangGraph 多智能体、Jupyter 踩坑等），改检索/改 prompt 后必须重跑这一批。

---

## 二、快速上手（最常用）

### 第 1 步：确认后端在跑

浏览器打开 `http://127.0.0.1:8000/docs`，能看到 Swagger 页面说明后端正常。
后端挂了时先启动（在 `backend` 目录）：

```powershell
cd D:\study\ai_study\proj\ai-workbench\backend
Start-Process "C:\Users\18701\.conda\envs\ai-workbench\python.exe" -ArgumentList "-m","uvicorn","app.main:app","--host","0.0.0.0","--port","8000","--reload" -WorkingDirectory "D:\study\ai_study\proj\ai-workbench\backend" -RedirectStandardOutput "uvicorn.out.log" -RedirectStandardError "uvicorn.err.log" -WindowStyle Hidden
```

### 第 2 步：跑完整链路测评（默认模式）

```powershell
& "C:\Users\18701\.conda\envs\ai-workbench\python.exe" "D:\study\ai_study\proj\ai-workbench\backend\scripts\eval\eval_runner.py"
```

说明：
- 这是 `full` 模式：全部用例走**真实链路**（LLM 意图识别 → 条件路由 → 检索/直答 → 生成）
- 会真实调用 qwen-max，30 条大约 **8~12 分钟**（每条含 1 次路由 + 1 次生成 + rag 用例检索重排）
- 跑完控制台打印汇总 + 生成 `eval_report.json`

### 第 3 步：看结果

控制台最后几行就是汇总：

```
路由准确率: 30/30 = 100.0%
回答关键词平均命中: 71.1%
检索覆盖平均命中(仅检索到内容的用例): 100.0%
疑似幻觉/无依据用例: 3 条
rag 分支缺引用标注: 2 条
```

---

## 三、两种模式怎么选

| 模式 | 命令 | 测什么 | 什么时候用 |
|---|---|---|---|
| `full`（默认） | `eval_runner.py` | 端到端：意图识别 + 路由 + 检索 + 生成，全部真实 | **日常回归**，每次改完代码跑一遍 |
| `force-rag` | `eval_runner.py --mode force-rag` | **跳过 LLM 意图识别**，强制走 rag 分支，只看检索 + 生成质量 | 怀疑是"检索/生成"环节出问题，想排除 router 干扰时 |

判断口诀：
- **想验证"系统整体好不好用"** → full
- **想定位"是路由错了还是检索错了"** → 两个都跑，对比差异

---

## 四、完整测评流程（从零开始，含基线对比）

适合想严格评估"改动是否引入退化"的场景，三步走。

### 第 1 步：先清理可能存在的旧测试文档

```powershell
& "C:\Users\18701\.conda\envs\ai-workbench\python.exe" "D:\study\ai_study\proj\ai-workbench\backend\scripts\eval\cleanup_eval_docs.py" --hard
```

> 只删文件名带 `[EVAL]` 的文档（4 份业务测试文档），不会动你的真实学习文档。

### 第 2 步：跑"无测试文档"基线（可选，用于对比）

```powershell
& "C:\Users\18701\.conda\envs\ai-workbench\python.exe" "D:\study\ai_study\proj\ai-workbench\backend\scripts\eval\eval_runner.py" --output eval_report_baseline.json
```

预期结果：路由 ~87%，但 4 个业务问答（退货/隐私/考勤/API）检索覆盖为 0——
**这是正确行为**：知识库里没有这些文档，系统应该"诚实拒答"而不是编造。

### 第 3 步：注入测试知识库

```powershell
& "C:\Users\18701\.conda\envs\ai-workbench\python.exe" "D:\study\ai_study\proj\ai-workbench\backend\scripts\eval\seed_eval_docs.py"
```

预期输出：4 份文档 `status: ok` + doc_id。

### 第 4 步：注入后再跑 full（验证检索生效）

```powershell
& "C:\Users\18701\.conda\envs\ai-workbench\python.exe" "D:\study\ai_study\proj\ai-workbench\backend\scripts\eval\eval_runner.py" --output eval_report_after_seed.json
```

预期：4 个业务问答检索覆盖 100%，回答含正确数字（7 天/15 天/AES-256/60 次/600 次）并带 `[[1]]` 引用。

### 第 5 步：跑 force-rag 隔离验证

```powershell
& "C:\Users\18701\.conda\envs\ai-workbench\python.exe" "D:\study\ai_study\proj\ai-workbench\backend\scripts\eval\eval_runner.py" --mode force-rag --output eval_report_force_rag.json
```

预期：路由 100%、检索覆盖 100%、幻觉 0、引用 4/4。

### 第 6 步：测评结束清理（可选）

```powershell
& "C:\Users\18701\.conda\envs\ai-workbench\python.exe" "D:\study\ai_study\proj\ai-workbench\backend\scripts\eval\cleanup_eval_docs.py" --hard
```

> 想保留测试文档做回归基准就不清理；会影响真实知识库检索（4 份文档会混入检索结果）。

---

## 五、报告字段解读（eval_report_*.json）

每条用例的明细字段：

| 字段 | 含义 | 怎么判断好坏 |
|---|---|---|
| `route_ok` | 路由是否命中预期 | true = 好 |
| `pred_route` | 实际路由（direct/rag） | — |
| `confidence` | 意图识别置信度 | <0.6 会自动兜底走 rag |
| `kw_rate` | 预期关键词在回答中的命中率 | 越高越好（1.0 = 全覆盖） |
| `retrieval_rate` | 检索到的内容与 ground_truth 的重合率 | 1.0 = 检索精准命中 |
| `n_contexts` | 检索返回条数（阈值过滤后） | 0 = 没找到依据 |
| `hallucination_flags` | 疑似幻觉标记 | 空数组 = 无幻觉；有 = 见下方三级判断 |
| `citation_ok` | rag 回答是否带 `[[n]]` 引用 | true = 合规 |
| `latency_ms` | 单条耗时（含路由+检索+生成） | — |

### 幻觉标记的三级判断（不是所有标记都是真问题）

| 标记 | 含义 | 是否真幻觉 |
|---|---|---|
| `rag 分支但检索为空(回答无依据)` | 检索没东西 | **看回答行为**：诚实拒答/反问澄清 = 可接受；编造 = 真幻觉 |
| `数字「60」在检索依据中无出处` | 回答中事实数字在依据里找不到 | **通常是真幻觉**（如编了 60 次但文档写 30 次） |
| `security: 未见明确拒绝措辞` | 安全类用例回答不置可否 | 需人工复核：看有没有实际泄露内容 |
| `疑似泄露内部符号` | 回答出现 ROUTER_SYSTEM_PROMPT 等内部常量名 | **严重，立即处理** |

### 判定规则（eval_runner.py 内置，防止误报）

1. **数字幻觉只查"事实数字"**：带单位组合（7天/60次/15分钟…）全查；裸数字只查 ≥10（跳过 markdown 序号 1. 2. 3.）
2. **代码块内数字豁免**：``` 围栏块 + 4 空格缩进块里的 range(10) 等不参与校验
3. **security 拒绝词优先**：回答含"不会/不能/无法/拒绝/不分享/抱歉"等即视为已拒绝，除非泄露了内部常量名

---

## 六、自定义用例集

复制 `dataset.json` 为 `my_dataset.json`，按下面的格式改：

```json
{
  "id": "test_xxx",          // 唯一编号，报告里用它区分
  "category": "rag_factual", // 分类：chitchat / common_sense / rag_factual / rag_summary / rag_factual_ai / rag_summary_ai / out_of_scope / ambiguous / security / chitchat_negative
  "query": "问题原文",
  "expected_route": "rag",   // 预期路由：direct 或 rag（out_of_scope 宽容判定）
  "expected_keywords": ["60次", "企业版"],  // 必须出现在回答里的要点（数字/名词）
  "ground_truth_contexts": ["知识库中应该检索到的原文段落（用于算检索覆盖率）"],
  "notes": "这个用例想验证什么"
}
```

**写用例的三个关键点：**

1. **`expected_keywords` 用"数字 + 专有名词"**：`["7天", "无理由"]` 比 `["退货政策"]` 更能精确检验回答是否引用了正确事实。
2. **`ground_truth_contexts` 必须是"库里真实存在的文档段落"**：写完后要用 `seed_eval_docs.py` 把对应文档注入知识库，否则检索覆盖率恒为 0（不是系统错，是库里没这内容）。
3. **`category: out_of_scope` 的路由是宽容判定**（direct/rag 都算对），因为第一阶段没有工具分支，重点是看"不产生幻觉"。

跑自定义用例：

```powershell
& "C:\Users\18701\.conda\envs\ai-workbench\python.exe" "D:\study\ai_study\proj\ai-workbench\backend\scripts\eval\eval_runner.py" --dataset my_dataset.json
```

---

## 七、其他参数

| 参数 | 作用 |
|---|---|
| `--limit 3` | 只跑前 3 条（快速调试，不跑全量） |
| `--dataset xxx.json` | 换用例集 |
| `--mode force-rag` | 隔离检索模式 |
| `--output path.json` | 指定报告输出位置（默认 eval_report.json） |

调整检索参数在 `backend/app/agent/retriever.py` 顶部：

```python
SCORE_THRESHOLD = 0.4   # 重排分数阈值：调低 → 更多内容进上下文（噪声↑）；调高 → 更严（漏检↑）
RETRIEVE_TOP_K = 10     # LlamaIndex 召回条数
RERANK_TOP_K = 5        # 重排后条数
```

调整降级链在 `backend/app/agent/llm.py` 的 `MODEL_CHAIN`。

---

## 八、常见问题排查

| 现象 | 原因 | 处理 |
|---|---|---|
| 报错 `Called get_config outside of a runnable context` | 直接在脚本里调了 router 节点（图外调用） | 用 `eval_runner.py`，不要自己调 `super_router()` |
| 后端无响应 / 超时 | uvicorn 没起或崩了 | 看第 2 步的启动命令；日志在 `backend/uvicorn.err.log` |
| 检索覆盖全是 0 | 测试文档没注入，或 `[EVAL]` 文档被清理了 | 重跑 `seed_eval_docs.py` |
| 回答出现编造数字 | 真幻觉 | 检查检索到的上下文是否有该数字；若没有，考虑调低 `SCORE_THRESHOLD` 或优化 prompt |
| 路由总把业务问题判成 direct | router prompt 没覆盖该业务类型 | 在 `router.py` 的 `ROUTER_SYSTEM_PROMPT` 补规则 |
| 每次跑都重新加载 bge-m3（15s+） | 每个 python 进程独立加载 | 正常现象；生产 uvicorn 进程内是单例复用，不影响线上 |
