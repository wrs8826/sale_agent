---
name: policy-skill-maker
description: Use when creating or updating a policy knowledge skill under skills/ for the 销售 Agent project (F:\销售agent\) — typically when new policy material/documents are uploaded and the matching policy skill (SKILL.md triggers + 文档地图 + references/*.md) must be authored or refreshed. Covers skill structure, trigger design to avoid cross-skill collisions, document-map conventions, references splitting, and verification. Trigger when the user says things like 上传了新政策 / 更新XX政策skill / 新建一个政策skill / 把这份政策做成skill. NOT for code changes (use write_skill) and NOT a runtime capability — the developer authors the files; the agent never self-edits skills.
---

# 政策 Skill 设计指南（开发态）

把"上传的政策材料"沉淀成 / 更新为 `skills/<地区>政策/` 下的一个**运行时政策 skill**。
本 skill 面向**开发者（即你，编码助手）**：由你按本方法论手工编辑文件，agent 不自改技能目录。

> 配套关系：改**代码**看 [write_skill](../write_skill/SKILL.md)；本 skill 只管**政策知识 skill 的内容编排**。
> 运行机制详见 `write_skill/references/architecture.md` 的「Skill 系统（三层披露）」。

## 何时使用

- 用户上传了某地**新政策文件**，要把它做成 / 并入政策 skill
- 已有政策有**更新**（新公告、金额/条件变化），要刷新对应 skill
- 要为**新地区/新政策**从零新建一个政策 skill

不该用：改 Flask/图/前端代码（→ write_skill）；通用编程问题；非政策类 skill。

## 政策 skill 的固定结构（锚定现有约定）

```
skills/<地区>人才政策/
├── SKILL.md                 ← frontmatter(triggers) + body(角色 + 文档地图 + 回答原则 + 边界)
└── references/
    ├── 申报条件_制造业.md     ← L3 政策原文，一文件一主题
    ├── 资金政策.md
    └── …
```

三层披露（Path 2，**references 不进 RAG 向量库**）：
- **L1** `build_skill_table()`：所有 skill 的 name+description 常驻注入，让模型知道有哪些知识领域。
- **L2** `SKILL.md` body：`detect_skill(query)` 命中后整段注入，激活该政策专家角色 + 文档地图。
- **L3** `references/*.md`：模型按文档地图调 `load_policy_file(skill_name, filename)` 按需读原文。

关键事实（来自 `agent_service/skill_loader.py`）：
- `skill_loader` 启动时扫描 `skills/*/SKILL.md` 自动加载，**无需在任何代码里注册**。
- `skill_name`（调 `load_policy_file` 用）= **目录名**；body 文档地图里的文件名必须与 `references/` 实际文件名**逐字一致**（含 `.md`）。
- `detect_skill` 对 query 做**子串匹配**，遍历 `load_skills()`（按目录名排序）返回**第一个**命中的 skill → triggers 必须地域专属、互不串味。
- 改完 SKILL.md 后需 `load_skills(force=True)` 或**重启服务**才生效（启动期已缓存）。

## 工作流 A：更新已有政策 skill（最常见）

上传了某地新政策材料、且该 skill 已存在时：

1. **定位 skill**：按地区找到 `skills/<地区>人才政策/`。
2. **拆分新材料到 references**：按"问题类型"切分，一文件一主题（申报条件 / 资金政策 / 申报流程 / 注意事项 / 系统操作…）。
   - 新增主题 → 新建 `references/<主题>.md`；同主题更新 → 覆写对应文件。
   - 正文用 Markdown 标题分节 + 表格承载金额/比例/年龄等结构化数字（见现有 `资金政策.md` 范式）。
   - **数字、日期、金额照搬原文**，不改写、不估算。
3. **更新文档地图**：在 body 的「文档地图」表里，为新增/变更的 references 增改一行 `| 问题类型描述（触发条件） | references/<文件名>.md |`。删文件就删行。
4. **校对 triggers**：若新政策引入新别名/系统名/编号（如"甬才通""3315"），补进 frontmatter `triggers`。
5. **更新边界/年度**：body 的「边界」里把覆盖年度、未覆盖项、官方兜底说明刷新到最新。
6. **验证**（见下）。

## 工作流 B：新建政策 skill（新地区/新政策）

1. `skills/<地区>人才政策/` + `references/` 建目录。
2. 写 `SKILL.md`，照下方模板填 frontmatter + body。
3. 把政策材料拆成多个 `references/*.md`，逐一在文档地图登记。
4. 验证；确认 `detect_skill("<地区>申报…")` 命中新 skill 且不抢占其它地区。

## triggers 设计规则（避免跨 skill 串味）

- **地域/专名前缀优先**：每个词尽量含地区或专有系统名（"无锡人才""飞凤人才""甬才通"），避免裸用"人才""申报""政策"这类会命中多地的泛词。
- 纯数字/编号要加引号（YAML）：如 `- "3315"`。
- 覆盖用户常见说法：地区简称、工程名、系统名、申报动作（"<地区>申报"）、官网域名等。
- 自检：`detect_skill` 是"首个命中即返回"，新 skill 的任何 trigger **不应**是别的政策 query 的子串，反之亦然。新增后务必跑下方验证逐条核。

## body 写作模板

```markdown
---
name: <地区>人才政策
description: <一句话定位，进 L1 表，例：宁波市甬江人才工程政策顾问（2026）与甬才通系统操作>
triggers:
  - <地区>人才
  - <工程/系统名>
  - <地区>申报
  - …
---

# <地区>人才政策顾问

你是<地区>人才政策专家（如涉系统，附"并熟悉<系统名>操作"）。
**核心职责**：判断申报资格、解读政策细节、指引系统操作。

## 文档地图

根据问题类型读取对应文件；跨类型问题并行读取后汇总。

| 问题类型 | 读取文件 |
|---|---|
| <某类申报条件> | `references/申报条件_xxx.md` |
| 资金标准、拨付比例和时间 | `references/资金政策.md` |
| 申报截止、受理部门、评审流程 | `references/申报流程与受理.md` |
| 注意事项、不得申报人员、咨询电话 | `references/注意事项与限制.md` |
| <系统>登录注册与申报 | `references/<系统>_登录注册与申报.md` |

## 回答原则

- 先结论后依据；数字/金额/日期直接引用原文，不估算
- 用户描述自身情况时逐项核查，明确满足/不满足/可破格
- 涉及系统操作主动给入口链接

## 边界

- 覆盖：<年度/公告范围>；其它年度差异提示以官方最新发布为准
- 资格最终以主管部门审核为准，本 skill 提供参考性解读
```

## references 拆分原则

- **一文件一主题**：load_policy_file 返回整篇，文件过大/混主题会稀释回答精度。
- 主题粒度对齐文档地图的"问题类型"：申报条件按行业再拆（制造业/高校院所/现代服务业），系统操作按阶段拆（登录注册申报 / 入选完善 / 变更立项经费 / 评估结题）。
- 文件名用中文短主题，与文档地图逐字一致。

## 验证（改完必跑）

在应用实际环境（conda `agent`，非 base）跑：

```powershell
$env:PYTHONIOENCODING='utf-8'; $env:PYTHONPATH='F:\销售agent'
& E:\miniconda\envs\agent\python.exe -c "from agent_service.skill_loader import load_skills, detect_skill; [print(s.name, s._keywords[:6]) for s in load_skills(force=True)]; print('命中:', getattr(detect_skill('<地区>申报条件是什么'),'name',None))"
```

逐项核对：
- [ ] 新/改 skill 出现在 `load_skills(force=True)` 列表，triggers 正确。
- [ ] 典型 query `detect_skill(...)` 命中目标 skill，且不抢占其它地区的 query。
- [ ] 文档地图每个文件名都能在 `references/` 找到（逐字一致，含 `.md`）。
- [ ] 金额/年龄/比例/日期与原文一致，无改写。
- [ ] 运行时生效需重启服务或 `load_skills(force=True)`。

## 不要做的事

- 不要把本开发 skill 放进 `skills/`（会被 `skill_loader` 注入 agent）。
- 不要让 references 进 RAG（`all_refs_dirs()` 恒返回 `[]`，保持 Path 2）。
- 不要在文档地图里写不存在的文件名（`load_policy_file` 会返回"文件不存在 + 可用列表"，污染回答）。
- 不要改运行时代码来"自动更新 skill"——本方案就是开发者手工编排；要自动化是另一套（运行态写入工具）。
