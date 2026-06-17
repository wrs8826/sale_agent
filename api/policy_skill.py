"""政策 Skill 更新蓝图（管理员专用，与正常 RAG/检索隔离）。

流程：
    1. 管理员上传「政策材料」→ 走 /upload?kind=policy 进 POLICY_STAGING_DIR（不进 RAG）
    2. POST /admin/policy-skill/draft（SSE）：解析暂存文件 → 复用清洗子图做一次 LLM 变换，
       按 policy_skill_maker 方法论产出**结构化草稿**（目标 skill + SKILL.md + references），
       写入 POLICY_DRAFTS_DIR。agent 只产草稿，不碰 live skills/。
    3. GET  /admin/policy-skill/draft/<id>：管理员审核草稿全文
    4. POST /admin/policy-skill/publish：人工确认后由后端落盘到 skills/（备份旧件）→ 热重载 →
       删除暂存源文件与草稿
    5. POST /admin/policy-skill/discard：丢弃草稿（保留暂存文件，可重生成）

隔离要点：本蓝图只在 app_admin 注册 + 每路由校验 admin 角色；草稿生成方法论
（policy_skill_maker）不在 skills/ 下，正常用户对话的 detect_skill / L1 表永不含它。
"""
from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Dict, List, Tuple

from flask import Blueprint, Response, jsonify, request, session, stream_with_context

from agent_service import (
    POLICY_DRAFTS_DIR,
    POLICY_SKILL_MAKER,
    POLICY_STAGING_DIR,
    SKILL_BACKUPS_DIR,
    SKILLS_ROOT,
)
from agent_service.graph import build_cleaning_graph
from agent_service.mcp.builtin_tools import extract_text_from_file

from . import services

bp = Blueprint("policy_skill", __name__)


# ── 权限 / 路径安全 ────────────────────────────────────────────────────────────

def _is_admin() -> bool:
    return session.get("role") == "admin"


def _safe_skill_name(name: str) -> str:
    """校验 skill 目录名：非空、无路径分隔符 / .. / 前导点。返回规整名或抛 ValueError。"""
    name = (name or "").strip()
    if not name or name in (".", ".."):
        raise ValueError("skill_name 为空或非法")
    if any(c in name for c in ("/", "\\")) or ".." in name or name.startswith("."):
        raise ValueError(f"skill_name 含非法字符: {name!r}")
    return name


def _safe_ref_filename(name: str) -> str:
    """校验 references 文件名：必须 *.md、无路径分隔符 / ..。"""
    name = (name or "").strip()
    if any(c in name for c in ("/", "\\")) or ".." in name or not name.endswith(".md"):
        raise ValueError(f"references 文件名非法（需 *.md 且无路径）: {name!r}")
    return name


# ── 草稿生成上下文 ────────────────────────────────────────────────────────────

def _maker_methodology() -> str:
    """读取 policy_skill_maker 的 body（去 frontmatter）作为方法论系统提示。"""
    try:
        text = POLICY_SKILL_MAKER.read_text(encoding="utf-8")
    except Exception:
        return ""
    m = re.match(r"^---\s*\n.*?\n---\s*\n(.*)", text, re.DOTALL)
    return (m.group(1) if m else text).strip()


def _existing_skills_context() -> str:
    """汇总现有政策 skill：名称 + triggers + references 文件名 + SKILL.md 全文，供 LLM 匹配/合并。"""
    from agent_service.skill_loader import load_skills

    blocks: List[str] = []
    for s in load_skills(force=True):
        refs_dir = s.refs_dir
        ref_files = (
            sorted(f.name for f in refs_dir.iterdir() if f.suffix == ".md")
            if refs_dir.exists() else []
        )
        skill_md = ""
        md_path = SKILLS_ROOT / s.name / "SKILL.md"
        if md_path.exists():
            skill_md = md_path.read_text(encoding="utf-8", errors="ignore")
        blocks.append(
            f"### 现有 skill 目录名：{s.name}\n"
            f"triggers: {s._keywords}\n"
            f"references 文件: {ref_files}\n"
            f"SKILL.md 原文:\n{skill_md}"
        )
    return "\n\n----------\n\n".join(blocks) if blocks else "（当前无任何 skill）"


_DRAFT_SCHEMA_INSTRUCTION = """\

──────────────────────────────────────
你现在的任务不是回答用户，而是把下方【待解析的政策材料】沉淀为 / 合并进一个政策 skill，
**只输出一个 JSON 对象**（不要任何解释、不要 markdown 代码围栏），schema：

{
  "action": "update" | "create",            // 命中某个现有 skill 则 update，否则 create
  "skill_name": "目录名，如 甬江人才政策",     // update 必须等于现有目录名；create 用新目录名（地区+人才政策）
  "region": "地区名",
  "reason": "为何匹配到该 skill / 为何新建（给管理员看）",
  "skill_md": "完整 SKILL.md 文本，含 --- frontmatter(triggers) --- 与 body（角色/文档地图表/回答原则/边界）",
  "references": [
    {"filename": "资金政策.md", "op": "create"|"update", "content": "完整 markdown 正文"}
  ],
  "delete_references": [],
  "notes": "变更点摘要（新增/修改了哪些文件、triggers、文档地图行）"
}

硬性规则：
- action=update 时，skill_md 必须是**合并后的完整 SKILL.md**：保留原有文档地图行与 triggers，再并入新增项；
  references 只列**新增或需更新**的文件，未提及的原 references 文件保持不变。
- 文档地图里每个文件名必须与 references[].filename 或现有 references 文件逐字一致（含 .md）。
- triggers 要地域/专名专属，避免与其它地区 skill 串味（参考上方现有 skill 的 triggers）。
- 金额/年龄/比例/日期照搬政策原文，不估算、不杜撰。
- 只输出 JSON，从 { 开始、到 } 结束。\
"""


def _build_draft_prompt(policy_filename: str, policy_text: str) -> Tuple[str, str]:
    """返回 (system_prompt, user_raw_text) 供清洗子图调用。"""
    system_prompt = _maker_methodology() + "\n\n" + _DRAFT_SCHEMA_INSTRUCTION
    user_raw = (
        "【现有政策 skill 上下文】\n"
        + _existing_skills_context()
        + f"\n\n──────────────────────────────────────\n【待解析的政策材料：{policy_filename}】\n"
        + policy_text
        + "\n\n请按上面的 JSON schema 输出草稿。"
    )
    return system_prompt, user_raw


def _parse_draft_json(text: str) -> Dict:
    """从 LLM 输出里抽取 JSON 对象并校验关键字段。"""
    s = (text or "").strip()
    # 去掉可能的 ```json ... ``` 围栏
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s).strip()
    # 容错：截取第一个 { 到最后一个 }
    if not s.startswith("{"):
        i, j = s.find("{"), s.rfind("}")
        if i != -1 and j != -1 and j > i:
            s = s[i : j + 1]
    data = json.loads(s)  # 失败抛 JSONDecodeError，由调用方捕获
    if data.get("action") not in ("update", "create"):
        raise ValueError("草稿 action 非法（应为 update/create）")
    _safe_skill_name(data.get("skill_name", ""))
    if not (data.get("skill_md") or "").strip().startswith("---"):
        raise ValueError("草稿 skill_md 缺少 frontmatter")
    refs = data.get("references") or []
    for r in refs:
        _safe_ref_filename(r.get("filename", ""))
    for d in (data.get("delete_references") or []):
        _safe_ref_filename(d)
    return data


# ── 暂存文件管理 ──────────────────────────────────────────────────────────────

@bp.route("/admin/policy-staging", methods=["GET"])
def list_staging():
    if not _is_admin():
        return jsonify({"error": "仅管理员可访问"}), 403
    if not POLICY_STAGING_DIR.exists():
        return jsonify([])
    items = []
    for f in sorted(POLICY_STAGING_DIR.iterdir()):
        if f.is_file():
            st = f.stat()
            items.append({"filename": f.name, "size": st.st_size, "mtime": int(st.st_mtime)})
    return jsonify(items)


@bp.route("/admin/policy-staging/<path:filename>", methods=["DELETE"])
def delete_staging(filename):
    if not _is_admin():
        return jsonify({"error": "仅管理员可访问"}), 403
    name = Path(filename).name
    target = POLICY_STAGING_DIR / name
    if not target.is_file():
        return jsonify({"error": "文件不存在"}), 404
    target.unlink()
    return jsonify({"ok": True})


# ── 草稿生成（SSE）────────────────────────────────────────────────────────────

@bp.route("/admin/policy-skill/draft", methods=["POST"])
def make_draft():
    if not _is_admin():
        return jsonify({"error": "仅管理员可访问"}), 403
    data = request.get_json(silent=True) or {}
    filename = Path((data.get("filename") or "").strip()).name
    if not filename:
        return jsonify({"error": "filename 不能为空"}), 400
    src = POLICY_STAGING_DIR / filename
    if not src.is_file():
        return jsonify({"error": f"暂存文件不存在: {filename}"}), 404

    chat_cfg = services.load_chat_settings()
    if not chat_cfg.get("api_key"):
        return jsonify({"error": "未配置 Chat API Key"}), 400

    def generate():
        try:
            yield _sse({"type": "status", "message": "读取政策材料…"})
            policy_text = extract_text_from_file(src).strip()
            if not policy_text:
                yield _sse({"type": "error", "message": "文件为空或未提取到文本（扫描版 PDF 需 OCR）"})
                return

            yield _sse({"type": "status", "message": "分析现有政策 skill 并生成草稿（按 policy_skill_maker 方法论）…"})
            system_prompt, user_raw = _build_draft_prompt(filename, policy_text)
            out = build_cleaning_graph().invoke({
                "raw_text": user_raw,
                "system_prompt": system_prompt,
                "cleaner_cfg": chat_cfg,
            })
            if out.get("error"):
                yield _sse({"type": "error", "message": f"草稿生成失败：{out['error']}"})
                return

            try:
                draft = _parse_draft_json(out.get("cleaned_text") or "")
            except Exception as exc:
                yield _sse({"type": "error", "message": f"草稿解析失败（模型未按 JSON 输出）：{exc}"})
                return

            draft_id = uuid.uuid4().hex
            record = {
                "draft_id": draft_id,
                "source": filename,
                "created_at": int(time.time()),
                "draft": draft,
            }
            POLICY_DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
            (POLICY_DRAFTS_DIR / f"{draft_id}.json").write_text(
                json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            yield _sse({
                "type": "done",
                "draft_id": draft_id,
                "action": draft.get("action"),
                "skill_name": draft.get("skill_name"),
                "region": draft.get("region"),
                "reason": draft.get("reason"),
                "notes": draft.get("notes"),
                "ref_count": len(draft.get("references") or []),
            })
        except Exception as exc:
            yield _sse({"type": "error", "message": str(exc)})

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── 草稿审核 ──────────────────────────────────────────────────────────────────

@bp.route("/admin/policy-skill/drafts", methods=["GET"])
def list_drafts():
    if not _is_admin():
        return jsonify({"error": "仅管理员可访问"}), 403
    if not POLICY_DRAFTS_DIR.exists():
        return jsonify([])
    items = []
    for f in sorted(POLICY_DRAFTS_DIR.glob("*.json")):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
            d = rec.get("draft", {})
            items.append({
                "draft_id": rec.get("draft_id"),
                "source": rec.get("source"),
                "created_at": rec.get("created_at"),
                "action": d.get("action"),
                "skill_name": d.get("skill_name"),
                "region": d.get("region"),
            })
        except Exception:
            continue
    return jsonify(items)


@bp.route("/admin/policy-skill/draft/<draft_id>", methods=["GET"])
def get_draft(draft_id):
    if not _is_admin():
        return jsonify({"error": "仅管理员可访问"}), 403
    rec = _load_draft(draft_id)
    if rec is None:
        return jsonify({"error": "草稿不存在"}), 404
    return jsonify(rec)


# ── 发布 / 丢弃 ───────────────────────────────────────────────────────────────

@bp.route("/admin/policy-skill/publish", methods=["POST"])
def publish_draft():
    if not _is_admin():
        return jsonify({"error": "仅管理员可访问"}), 403
    data = request.get_json(silent=True) or {}
    draft_id = (data.get("draft_id") or "").strip()
    rec = _load_draft(draft_id)
    if rec is None:
        return jsonify({"error": "草稿不存在"}), 404

    draft = rec.get("draft", {})
    try:
        skill_name = _safe_skill_name(draft.get("skill_name", ""))
        skill_md = draft.get("skill_md") or ""
        refs = draft.get("references") or []
        dels = draft.get("delete_references") or []
        for r in refs:
            _safe_ref_filename(r.get("filename", ""))
        for d in dels:
            _safe_ref_filename(d)
    except ValueError as exc:
        return jsonify({"error": f"草稿校验失败：{exc}"}), 400

    skill_dir = SKILLS_ROOT / skill_name
    refs_dir = skill_dir / "references"

    # 1) 备份将被覆盖/删除的旧件
    backup_dir = SKILL_BACKUPS_DIR / f"{skill_name}_{int(time.time())}"
    touched = [skill_dir / "SKILL.md"] + [refs_dir / r["filename"] for r in refs] \
              + [refs_dir / d for d in dels]
    for p in touched:
        if p.exists():
            rel = p.relative_to(SKILLS_ROOT)
            dst = backup_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dst)

    # 2) 落盘
    refs_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")
    written: List[str] = []
    for r in refs:
        (refs_dir / r["filename"]).write_text(r.get("content") or "", encoding="utf-8")
        written.append(r["filename"])
    removed: List[str] = []
    for d in dels:
        fp = refs_dir / d
        if fp.exists():
            fp.unlink()
            removed.append(d)

    # 3) 热重载 skill 缓存
    from agent_service.skill_loader import load_skills
    load_skills(force=True)

    # 4) 删除暂存源文件 + 草稿（决策④：转成 skill 后删除暂存）
    src = POLICY_STAGING_DIR / (rec.get("source") or "")
    if src.is_file():
        src.unlink()
    (POLICY_DRAFTS_DIR / f"{draft_id}.json").unlink(missing_ok=True)

    return jsonify({
        "ok": True,
        "skill_name": skill_name,
        "action": draft.get("action"),
        "written_references": written,
        "removed_references": removed,
        "backup_dir": str(backup_dir) if backup_dir.exists() else None,
    })


@bp.route("/admin/policy-skill/discard", methods=["POST"])
def discard_draft():
    if not _is_admin():
        return jsonify({"error": "仅管理员可访问"}), 403
    data = request.get_json(silent=True) or {}
    draft_id = (data.get("draft_id") or "").strip()
    fp = POLICY_DRAFTS_DIR / f"{draft_id}.json"
    if not fp.is_file():
        return jsonify({"error": "草稿不存在"}), 404
    fp.unlink()
    return jsonify({"ok": True})


# ── 工具 ──────────────────────────────────────────────────────────────────────

def _load_draft(draft_id: str) -> Dict | None:
    draft_id = Path((draft_id or "").strip()).name  # 防穿越
    fp = POLICY_DRAFTS_DIR / f"{draft_id}.json"
    if not fp.is_file():
        return None
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return None


def _sse(payload: Dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
