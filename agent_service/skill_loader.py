"""Skills 加载器：解析 skills/ 目录下的 SKILL.md，提供关键词匹配与动态系统提示。

目录约定：
    skills/<技能名>/SKILL.md             — frontmatter(name/description/triggers) + body(L2 系统提示)
    skills/<技能名>/references/*.md      — L3 参考文档（由 RAG 索引）

三层披露：
    L1  build_skill_table()   → 所有 skill 的 name + description，用于常驻注入
    L2  SkillDef.system_prompt → 命中后注入的完整系统提示（SKILL.md body）
    L3  references/*.md        → RAG 按需检索的深层文档

触发匹配（优先级）：
    1. frontmatter 的 triggers 列表（显式关键词，精确）
    2. description 文本中用引号/列表条目抽取的关键词（兜底）
    detect_skill(query) 对 query 做 in 子串匹配，返回第一个命中的 SkillDef。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

import yaml

from . import SKILLS_ROOT


class SkillDef:
    """单个 skill 的元数据 + 系统提示。

    Attributes:
        name:          skill 唯一名称（frontmatter name 或目录名）
        description:   一行简介，用于 L1 Skill 表（frontmatter description 首行）
        system_prompt: L2 系统提示（SKILL.md body），命中时注入 generate_node
        refs_dir:      L3 references 目录，由 RAG 索引
    """

    def __init__(
        self,
        name: str,
        description: str,
        system_prompt: str,
        refs_dir: Path,
        triggers: Optional[List[str]] = None,
    ) -> None:
        self.name = name
        # 取 description 第一行作为 L1 摘要，去除多余空白
        self.description = description.strip().splitlines()[0].strip() if description.strip() else ""
        self.system_prompt = system_prompt.strip()
        self.refs_dir = refs_dir
        # triggers 优先用 frontmatter 显式列表，降级到正则抽取
        if triggers:
            self._keywords: List[str] = [str(t).strip() for t in triggers if str(t).strip()]
        else:
            self._keywords = _extract_keywords(description)

    def matches(self, query: str) -> bool:
        """query 中包含任意一个关键词时命中。"""
        for kw in self._keywords:
            if kw and kw in query:
                return True
        return False

    def __repr__(self) -> str:
        return f"<SkillDef name={self.name!r} keywords={self._keywords[:5]}>"


# ── 关键词提取 ─────────────────────────────────────────────────────────────────

def _extract_keywords(description: str) -> List[str]:
    """从 description 文本中提取触发关键词。

    策略（按优先级）：
    1. 提取所有「」/ "" 引号包裹的内容（通常是专有名词）
    2. 提取以"- "开头的条目里最前面的中文短语（最多 8 字）
    去重后返回。
    """
    kws: List[str] = []

    # 规则 1：引号内的词（「...」或 "..."）
    kws += re.findall(r'[「""]([^「""\n]{1,16})[」""]', description)

    # 规则 2：- 开头条目的首个中文短语（取第一个顿号/逗号/冒号前的部分）
    for line in description.splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        text = line[2:].strip()
        # 取第一个标点前的片段
        m = re.match(r'^([一-鿿\w]{2,10})', text)
        if m:
            kws.append(m.group(1))

    # 去重，过滤过短的词
    seen: set = set()
    result: List[str] = []
    for kw in kws:
        kw = kw.strip()
        if len(kw) >= 2 and kw not in seen:
            seen.add(kw)
            result.append(kw)
    return result


# ── 解析 SKILL.md ──────────────────────────────────────────────────────────────

def _parse_skill_md(path: Path) -> Optional[SkillDef]:
    """解析单个 SKILL.md，失败时返回 None。"""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        print(f"[skill_loader] 读取 {path} 失败: {exc}")
        return None

    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", text, re.DOTALL)
    if m:
        try:
            meta = yaml.safe_load(m.group(1)) or {}
        except Exception:
            meta = {}
        body = m.group(2)
    else:
        meta = {}
        body = text

    name = str(meta.get("name") or path.parent.name)
    description = str(meta.get("description") or "")
    # triggers 支持 YAML list；若缺失则传 None，由 SkillDef 降级到正则抽取
    raw_triggers = meta.get("triggers")
    triggers: Optional[List[str]] = list(raw_triggers) if isinstance(raw_triggers, list) else None
    refs_dir = path.parent / "references"
    return SkillDef(
        name=name,
        description=description,
        system_prompt=body,
        refs_dir=refs_dir,
        triggers=triggers,
    )


# ── 全局缓存 ──────────────────────────────────────────────────────────────────

_skills: Optional[List[SkillDef]] = None


def load_skills(force: bool = False) -> List[SkillDef]:
    """加载（并缓存）所有 skill。force=True 时强制重新解析。"""
    global _skills
    if _skills is not None and not force:
        return _skills

    if not SKILLS_ROOT.exists():
        _skills = []
        return _skills

    result: List[SkillDef] = []
    for skill_dir in sorted(SKILLS_ROOT.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        sd = _parse_skill_md(skill_md)
        if sd is not None:
            result.append(sd)
            print(f"[skill_loader] 已加载 skill: {sd.name}  关键词: {sd._keywords}")

    _skills = result
    return _skills


def detect_skill(query: str) -> Optional[SkillDef]:
    """返回第一个关键词命中当前 query 的 skill；无匹配返回 None。"""
    for s in load_skills():
        if s.matches(query):
            return s
    return None


def all_refs_dirs() -> List[Path]:
    """返回所有 skill 的 references 目录（存在的才返回）。

    Path 2 架构：references/ 文件由 load_policy_file 工具按需读取，
    不再纳入 RAG 向量索引，避免不同政策文档的语义污染。
    """
    return []


def build_skill_table(skills: Optional[List[SkillDef]] = None) -> str:
    """生成 L1 Skill 注册表（Markdown 表格），供 generate_node 常驻注入。

    Args:
        skills: 可选；默认使用全局已加载的 skill 列表。

    Returns:
        Markdown 表格字符串，例如：
          | 知识领域 | 覆盖范围 |
          |---|---|
          | 甬江人才政策 | 宁波市甬江人才工程（2026年度）与甬才通系统操作 |
          ...
        若无任何 skill，返回空字符串。
    """
    if skills is None:
        skills = load_skills()
    if not skills:
        return ""
    lines = ["| 知识领域 | 覆盖范围 |", "|---|---|"]
    for s in skills:
        desc = s.description.replace("|", "｜")  # 防止破坏表格
        lines.append(f"| {s.name} | {desc} |")
    return "\n".join(lines)
