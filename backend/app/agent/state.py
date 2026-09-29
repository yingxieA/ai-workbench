"""AgentState：LangGraph 图流转的状态定义"""

from typing import TypedDict, Optional


class AgentState(TypedDict, total=False):
    # 输入
    question: str  # 当前用户问题
    history: list[dict]  # 历史消息（不含当前问题），[{role, content}]
    summary: str  # 会话摘要（P2.5：窗口外消息的 LLM 压缩）
    profile: str  # 用户画像 prompt 片段（P2.5：长期记忆注入）
    session_id: str
    user_id: str
    started_at: float  # 请求开始时间戳（耗时统计）

    # 路由结果（super_router 节点输出）
    route: str  # direct | rag | tool | agent_loop
    route_detail: str  # direct: chitchat|llm_common；rag: knowledge_qa；tool: tool_call；agent_loop: multi_step
    confidence: float  # 0.0 - 1.0
    router_model: str  # 路由器使用的模型

    # 工具分支（route=tool）
    tool_calls: list[str]  # Router 选定的工具名列表
    tool_args: dict  # 工具名 -> 参数（LLM 生成 + 校验后）
    tool_results: list[dict]  # 执行结果 [{success, tool, result, error}]
    tool_confirm_pending: bool  # 是否处于人工确认挂起状态

    # Agent Loop 多步推理（route=agent_loop）
    loop_trace: list  # 执行轨迹 [{step, tool, args, result|error, retried?, fallback_to?}]
    loop_step: int  # 已执行步数
    loop_done: bool  # 循环是否结束
    loop_pending_tool: str  # 待执行工具（interrupt 断点恢复用）
    loop_pending_args: dict  # 待执行工具参数
    loop_reasoning: str  # 最近一步决策说明
    loop_model: str  # 决策用的模型
    loop_started_at: float  # 循环起始时间戳（整轮超时熔断用）

    # 检索结果（rag_retrieve 节点输出）
    contexts: list[str]  # 过滤后的检索片段
    contexts_meta: list[dict]  # 前端引用元数据 [{index, title, content}]

    # 生成结果（generate 节点输出）
    answer: str
    model_used: str  # 实际生成使用的模型
    fallback_info: Optional[dict]  # {fallback_triggered, error_type, from_model, to_model}

    # 审计
    latency_ms: int
    token_usage: int
    cost: float  # 单次请求成本（元，P2.6 成本监控）
