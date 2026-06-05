"""Skills 加载器：解析 skills/ 目录下的 SKILL.md，提供关键词匹配与动态系统提示。

目录约定：
    skills/<技能名>/SKILL.md             — frontmatter(name/description) + body(系统提示)
    skills/<技能名>/references/*.md      — 参考文档（由 RAG 索引）

触发匹配：
    description 的 frontmatter 是 YAML 字符串，其中含有触发关键词列表。
    detect_skill(query) 对 query 做简单的 in 子串匹配，返回第一个命中的 SkillDef。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

import yaml

from . import SKILLS_ROOT


class SkillDef:
    """单个 skill 的元数据 + 系统提示。"""

    def __init__(
        self,
        name: str,
        description: str,
        system_prompt: str,
        refs_dir: Path,
    ) -> None:
        self.name = name
        self.description = description
        self.system_prompt = system_prompt.strip()
        self.refs_dir = refs_dir
        self._keywords: List[str] = _extract_keywords(description)

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
    refs_dir = path.parent / "references"
    return SkillDef(name=name, description=description, system_prompt=body, refs_dir=refs_dir)


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
    """返回所有 skill 的 references 目录（存在的才返回）。"""
    return [s.refs_dir for s in load_skills() if s.refs_dir.exists()]
