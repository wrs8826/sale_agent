"""QA 主图使用的系统提示词。

三层 Skill 披露架构：
  L1  build_skill_table()   → 常驻注入：让 LLM 知道所有可用知识领域
  L2  skill.system_prompt   → 关键词命中后注入：覆盖默认提示，激活专家角色
  L3  references/*.md       → 按需工具读取：load_policy_file(skill_name, filename) 返回政策原文

生成节点的 system prompt 由 build_generate_system() 动态组装为 **key:value 分段** 结构，
每段形如 `<key>:\n<内容>`，段间空行分隔，便于模型定位与人工排查。段与内容来源：
  identity   →  命中 skill 时用 skill body（L2）作角色；未命中用通用政策顾问角色 + 行为原则
  knowledge  →  L1 Skill 表（可咨询的知识领域），常驻
  tools      →  内置工具清单 + 调用约定（下载按钮等），常驻
  workspace  →  本轮 RAG 检索片段；未命中时给降级策略
  memory     →  本轮工具调用返回结果（仅 single 模式有；react 走循环内 ToolMessage）
  plan       →  （仅 react + enable_planning）由 agent_react 追加的既定执行方案
"""

from __future__ import annotations

# ─── 关键词提取节点 ───────────────────────────────────────────────────────────

EXTRACT_SYSTEM = """\
你是一个查询改写助手。请从用户最新问题提取核心信息，改写成适合知识库检索的查询语句。

规则：
- 仅输出检索查询，不加任何解释、引号或前缀
- 保留人名、机构名、政策名、技术术语等关键实体
- 删除「请问」「能否」「怎么样」等疑问词和冗余修饰
- 若含「他/她/那个/这个」等代词，结合历史对话补全为具体实体
- 中文输出，长度通常不超过 30 个字符\
"""


# ─── 规划节点（plan_node） ────────────────────────────────────────────────────

# 执行方案生成提示词：让模型在动手执行前，先把用户问题拆成可执行的子任务清单。
# 仅在 react 模式 + enable_planning 时启用；产出的方案会注入 agent_react 作执行指令。
PLAN_SYSTEM = """\
你是一名任务规划助手。在正式回答用户之前，请先把"如何解决这个问题"拆解成一份简洁的执行方案。

要求：
- 用 Markdown 有序列表列出 3~6 个**可执行的子步骤**，尽可能拆细、每步只做一件事
- 每步说明要做什么；若该步需要检索知识库或调用某个工具，注明工具名（参考下方可用能力）
- 步骤之间体现先后/依赖关系，由浅入深
- 只输出方案本身，不要给出最终答案，不要寒暄或解释
- 简明扼要，整体控制在 200 字以内

下面是当前可用的能力与已预检索资料（key:value 分段），供你规划时参考：

knowledge:
{skill_table}

tools:
{tool_table}

workspace:
{context}\
"""

# 注入 agent_react 系统提示的执行方案前缀（作为 plan: 段追加到 system prompt 末尾）
PLAN_INJECT_PREFIX = """\


plan:
请严格按以下已制定的方案逐步执行（按需调用工具/检索），不要重新规划；若执行中发现方案不适用可微调，但应朝着完成用户需求推进：
"""


# ─── 生成节点：key:value 分段系统提示 ─────────────────────────────────────────
# system prompt 按命名段组织（identity / knowledge / tools / workspace / memory），
# 每段渲染为 "<key>:\n<内容>"，段间以空行分隔，便于模型定位与人工排查。

# identity：未命中 skill 时的通用角色 + 行为原则；命中 skill 时由 skill body 充当 identity。
_IDENTITY_GENERIC = """\
你是一位专业的政策顾问助手，基于 workspace 段的检索片段与对话历史回答用户问题。
行为原则：
- 先结论后依据：先给出明确结论，再列出条文或依据
- 数字直接引用：金额、日期、比例等直接引用原文，不估算
- 边界清晰：无法确认的信息如实告知，建议查阅官方渠道
- 不编造数据、条款或具体事实\
"""

# knowledge：可咨询的知识领域（L1 Skill 表，常驻）
_KNOWLEDGE_BODY = """\
以下是你可咨询的知识领域（Skill）。当用户问题匹配某领域时，参考其专长作答：
{skill_table}\
"""

# tools：可调用工具清单 + 调用约定（常驻）
_TOOLS_BODY = """\
以下是你可调用的工具。当用户需求匹配某工具用途时，按其参数要求调用：
{tool_table}
对于生成可下载文件的工具（如 generate_word_document），系统会自动在你的回答下方显示「下载」按钮，\
因此你**不要自行编造、改写、猜测或重复粘贴下载链接**；工具调用成功后，只需简要告知用户\
「文档已生成，请点击下方按钮下载」即可。\
"""

# workspace：本轮检索到的资料片段；未命中时给降级策略
_WORKSPACE_NO_HITS = """\
本次知识库检索未命中相关片段。处理策略：
1. 优先参考对话历史上下文回答
2. 若有部分相关信息，给出参考性解读，并提示以官方最新发布为准
3. 若完全无法回答，告知用户并建议查阅官网或联系主管部门；可引导用户就 knowledge 段所列领域提问
4. 不编造数据、条款或具体事实\
"""

# memory：本轮通过工具获取的信息（仅 single 模式；react 走循环内 ToolMessage）
_MEMORY_BODY = """\
以下是本轮工具调用返回的内容（可能包含政策文件原文、当前时间等），请优先依据这些内容回答用户：
{tool_results}\
"""


# ─── 组装函数（供 generate_node / agent_react_node 调用） ─────────────────────

def _section(key: str, body: str) -> str:
    """渲染一个 key:value 段：键名 + 冒号换行 + 去除首尾空白的内容。"""
    return f"{key}:\n{body.strip()}"


def build_generate_system(
    skill_prompt: str,
    context: str,
    tool_results: str,
    skill_table: str,
    has_hits: bool,
    tool_table: str = "",
) -> str:
    """组装 generate 节点的 system prompt，输出 key:value 分段结构。

    段顺序：identity → knowledge → tools → workspace →（memory，仅当有工具结果）。

    Args:
        skill_prompt:  detect_skill() 命中后的 L2 提示；命中时作 identity，未命中用通用角色。
        context:       RAG 命中片段格式化文本，填入 workspace 段。
        tool_results:  call_tools_node 执行结果；非空时追加 memory 段。
        skill_table:   L1 Skill 注册表（Markdown），填入 knowledge 段。
        has_hits:      RAG 是否有高于阈值的命中片段。
        tool_table:    内置工具清单（Markdown），填入 tools 段。

    Returns:
        完整的 system prompt 字符串。
    """
    _skill_table = skill_table or "（暂无已加载的知识领域）"
    _tool_table = tool_table or "（暂无可用工具）"

    sections = [
        _section("identity", skill_prompt.strip() if skill_prompt else _IDENTITY_GENERIC),
        _section("knowledge", _KNOWLEDGE_BODY.format(skill_table=_skill_table)),
        _section("tools", _TOOLS_BODY.format(tool_table=_tool_table)),
        _section("workspace", context.strip() if has_hits else _WORKSPACE_NO_HITS),
    ]
    if tool_results:
        sections.append(_section("memory", _MEMORY_BODY.format(tool_results=tool_results.strip())))

    return "\n\n".join(sections)
