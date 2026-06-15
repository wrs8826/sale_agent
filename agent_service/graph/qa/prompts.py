"""QA 主图使用的系统提示词。

三层 Skill 披露架构：
  L1  build_skill_table()   → 常驻注入：让 LLM 知道所有可用知识领域
  L2  skill.system_prompt   → 关键词命中后注入：覆盖默认提示，激活专家角色
  L3  references/*.md       → 按需工具读取：load_policy_file(skill_name, filename) 返回政策原文

生成节点的 system prompt 由 build_generate_system() 动态组装，优先级：
  skill + RAG 命中  →  skill body + 检索片段
  skill + RAG 未命中 →  skill body + 降级说明
  无 skill + RAG 命中 →  通用提示 + 检索片段 + L1 Skill 表
  无 skill + RAG 未命中 →  通用降级 + L1 Skill 表（引导用户聚焦）
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


# ─── 生成节点模板（内部用，不对外暴露） ──────────────────────────────────────

# 情形 1：skill 命中 + RAG 有结果 → skill body 作为角色定位，片段补充细节
_TMPL_SKILL_WITH_HITS = """\
{skill_body}

──── 检索片段 ────
{context}
──── 片段结束 ────\
"""

# 情形 2：skill 命中 + RAG 无结果 → 保留 skill 角色，但提示知识库未命中
_TMPL_SKILL_NO_HITS = """\
{skill_body}

> 注：本次知识库检索未命中相关片段。请结合对话历史和通用知识回答；涉及具体政策条款数字，\
建议用户以官方最新发布为准。\
"""

# 情形 3：无 skill + RAG 有结果 → 通用顾问角色，片段（能力清单由统一块追加）
_TMPL_GENERIC_WITH_HITS = """\
# 角色设定
你是一位专业的政策顾问助手。请基于检索片段回答用户问题。

# 行为原则
- **先结论后依据**：先给出明确结论，再列出条文或依据
- **数字直接引用**：金额、日期、比例等直接引用原文，不估算
- **边界清晰**：无法确认的信息如实告知，建议查阅官方渠道

──── 检索片段 ────
{context}
──── 片段结束 ────\
"""

# 情形 4：无 skill + RAG 无结果 → 降级兜底（能力清单由统一块追加，引导用户聚焦）
_TMPL_GENERIC_NO_HITS = """\
# 角色设定
你是一位专业的政策顾问助手。本次知识库检索未找到直接相关内容。

# 处理策略
1. 优先参考历史对话上下文回答
2. 若有部分相关信息，给出参考性解读并提示以官方发布为准
3. 若完全无法回答，告知用户并建议查阅官网或联系主管部门；可引导用户就下方"可咨询的知识领域"提问
4. 不编造数据、条款或具体事实\
"""

# 系统可用能力块（所有情形常驻追加）：skill_list + tool_list
CAPABILITIES_PREFIX = """\


──── 系统可用能力 ────
【可咨询的知识领域 / Skill】
{skill_table}

【可调用的工具 / Tools】
{tool_table}
当用户需求匹配某个工具用途时，按其参数要求调用；生成类工具返回的下载链接需原样呈现给用户。\
"""

# 工具结果追加前缀
TOOL_RESULTS_PREFIX = """\


──── 工具查询结果 ────
以下是本轮工具调用返回的内容（可能包含政策文件原文、当前时间等），请优先依据这些内容回答用户：
"""


# ─── 组装函数（供 generate_node 调用） ───────────────────────────────────────

def build_generate_system(
    skill_prompt: str,
    context: str,
    tool_results: str,
    skill_table: str,
    has_hits: bool,
    tool_table: str = "",
) -> str:
    """根据当前运行状态组装 generate 节点的 system prompt。

    Args:
        skill_prompt:  detect_skill() 命中后的 L2 系统提示；未命中时为空串。
        context:       RAG 命中片段格式化后的文本。
        tool_results:  call_tools_node 执行结果；无工具调用时为空串。
        skill_table:   L1 Skill 注册表（Markdown 表格）；从 skill_loader 获取。
        has_hits:      RAG 检索是否有高于阈值的命中片段。
        tool_table:    内置工具清单（Markdown 表格）；从 builtin_tools.build_tool_table 获取。

    Returns:
        完整的 system prompt 字符串。组装顺序：角色/片段 → 系统可用能力（skill+tool）→ 工具结果。
    """
    _skill_table = skill_table or "（暂无已加载的知识领域）"
    _tool_table = tool_table or "（暂无可用工具）"

    if skill_prompt and has_hits:
        base = _TMPL_SKILL_WITH_HITS.format(skill_body=skill_prompt, context=context)
    elif skill_prompt and not has_hits:
        base = _TMPL_SKILL_NO_HITS.format(skill_body=skill_prompt)
    elif not skill_prompt and has_hits:
        base = _TMPL_GENERIC_WITH_HITS.format(context=context)
    else:
        base = _TMPL_GENERIC_NO_HITS

    # 所有情形常驻追加「系统可用能力」块（skill_list + tool_list）
    base += CAPABILITIES_PREFIX.format(skill_table=_skill_table, tool_table=_tool_table)

    if tool_results:
        base += TOOL_RESULTS_PREFIX + tool_results

    return base
