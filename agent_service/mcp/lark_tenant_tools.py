"""以 app_access_token（tenant 身份）调用飞书 API 的 LangChain 工具集。

这些工具绕过 lark-mcp 的 token 模式限制，直接调飞书 REST API，
适用于已在开发者后台申请了 tenant 级权限的接口（如通讯录查询）。

通过工厂函数 build_tenant_tools(app_id, app_secret) 创建，
在 mcp_manager 初始化完成后由 lark_bot 自动注入。
"""
from __future__ import annotations

import time
import threading
from typing import List, Optional

import requests
from langchain_core.tools import tool

FEISHU_HOST = "https://open.feishu.cn"

# app_access_token 本地缓存（有效期 2 小时，留 5 分钟余量）
_token_cache: dict = {"token": "", "expires_at": 0}
_token_lock = threading.Lock()


def _get_app_token(app_id: str, app_secret: str) -> str:
    """获取并缓存 app_access_token。"""
    with _token_lock:
        if _token_cache["expires_at"] > time.time() + 300:
            return _token_cache["token"]
        resp = requests.post(
            f"{FEISHU_HOST}/open-apis/auth/v3/app_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"获取 app_access_token 失败: {data.get('msg')}")
        token = data["app_access_token"]
        expires_in = int(data.get("expire", 7200))
        _token_cache["token"] = token
        _token_cache["expires_at"] = time.time() + expires_in
        return token


def build_tenant_tools(app_id: str, app_secret: str) -> List:
    """工厂函数：创建绑定应用身份的通讯录工具列表。"""

    def _headers() -> dict:
        return {"Authorization": f"Bearer {_get_app_token(app_id, app_secret)}"}

    def _get_authorized_scope() -> dict:
        """查询 app 被授权的通讯录访问范围（department_ids + user_ids）。"""
        resp = requests.get(
            f"{FEISHU_HOST}/open-apis/contact/v3/scopes",
            headers=_headers(),
            params={"user_id_type": "open_id", "department_id_type": "open_department_id"},
            timeout=10,
        )
        data = resp.json()
        if data.get("code") != 0:
            return {}
        return data.get("data", {})

    def _batch_get_users(user_ids: list) -> list:
        """批量查询用户详情。"""
        if not user_ids:
            return []
        resp = requests.get(
            f"{FEISHU_HOST}/open-apis/contact/v3/users/batch",
            headers=_headers(),
            params=[("user_ids", uid) for uid in user_ids[:50]] + [("user_id_type", "open_id")],
            timeout=10,
        )
        data = resp.json()
        if data.get("code") != 0:
            return []
        return data.get("data", {}).get("items", [])

    @tool
    def list_contacts(department_id: str = "", page_size: int = 20) -> str:
        """列出企业通讯录成员（应用身份）。

        优先按部门查询；若 app 未获得部门权限，则列出管理员已授权给 app 的所有用户。

        Args:
            department_id: 部门 ID，留空时自动查询授权范围内的所有用户。
            page_size: 返回条数，最多 50。
        """
        try:
            h = _headers()

            # 尝试按部门查询
            if department_id:
                resp = requests.get(
                    f"{FEISHU_HOST}/open-apis/contact/v3/users",
                    headers=h,
                    params={
                        "department_id": department_id,
                        "department_id_type": "open_department_id",
                        "page_size": min(int(page_size), 50),
                        "user_id_type": "open_id",
                    },
                    timeout=10,
                )
                data = resp.json()
                if data.get("code") == 0:
                    users = data.get("data", {}).get("items", [])
                else:
                    print(f"[lark_tenant] 按部门查询失败 {data.get('code')}: {data.get('msg')}")
                    users = []
            else:
                users = []

            # 按部门查无结果 → 降级到授权范围查询
            if not users:
                scope = _get_authorized_scope()
                dept_ids = scope.get("department_ids", [])
                user_ids = scope.get("user_ids", [])

                # 有部门权限 → 查根部门（dept_ids 已是 open_department_id 格式）
                if dept_ids:
                    root = dept_ids[0]
                    resp = requests.get(
                        f"{FEISHU_HOST}/open-apis/contact/v3/users",
                        headers=h,
                        params={
                            "department_id": root,
                            "department_id_type": "open_department_id",
                            "page_size": min(int(page_size), 50),
                            "user_id_type": "open_id",
                        },
                        timeout=10,
                    )
                    data = resp.json()
                    users = data.get("data", {}).get("items", []) if data.get("code") == 0 else []

                # 只有 user_ids → 批量查用户详情
                if not users and user_ids:
                    users = _batch_get_users(user_ids)
                    if users:
                        users_note = "（注意：当前 app 通讯录授权范围有限，仅显示部分成员。如需查看全员，请管理员在飞书管理后台扩大 app 的通讯录授权范围。）\n"
                    else:
                        return "未能获取通讯录成员，请确认管理员已在飞书管理后台授权 app 访问通讯录。"
                else:
                    users_note = ""

            else:
                users_note = ""

            if not users:
                return "暂无成员或无访问权限。"

            lines = []
            for u in users:
                name   = u.get("name", "（未知）")
                email  = u.get("enterprise_email") or u.get("email") or "无邮箱"
                mobile = u.get("mobile", "")
                title  = u.get("job_title", "")
                lines.append(f"- {name}  职位:{title}  邮箱:{email}  手机:{mobile}")
            return (users_note or "") + "\n".join(lines)

        except Exception as exc:
            print(f"[lark_tenant] list_contacts 异常: {exc}")
            return f"调用失败: {exc}"

    @tool
    def search_contacts(query: str) -> str:
        """按关键字搜索企业通讯录用户（应用身份，支持姓名/邮箱/手机号）。

        若 app 通讯录授权范围有限，则在已授权用户中本地过滤。

        Args:
            query: 搜索关键字（姓名、邮箱、手机号）。
        """
        try:
            h = _headers()
            query_lower = query.lower()

            # 先尝试 API 搜索
            resp = requests.get(
                f"{FEISHU_HOST}/open-apis/contact/v3/users/find",
                headers=h,
                params={"query": query, "user_id_type": "open_id"},
                timeout=10,
            )
            data = resp.json()

            if data.get("code") == 0:
                users = data.get("data", {}).get("items", [])
            else:
                print(f"[lark_tenant] search 失败 {data.get('code')}: {data.get('msg')}，降级到授权范围搜索")
                # 降级：在授权范围内批量拉取后本地过滤
                scope = _get_authorized_scope()
                user_ids = scope.get("user_ids", [])
                all_users = _batch_get_users(user_ids)
                users = [
                    u for u in all_users
                    if query_lower in (u.get("name") or "").lower()
                    or query_lower in (u.get("enterprise_email") or u.get("email") or "").lower()
                    or query_lower in (u.get("mobile") or "")
                ]

            if not users:
                return f"未找到与「{query}」匹配的联系人。"

            lines = []
            for u in users:
                name   = u.get("name", "（未知）")
                email  = u.get("enterprise_email") or u.get("email") or "无邮箱"
                title  = u.get("job_title", "")
                mobile = u.get("mobile", "")
                lines.append(f"- {name}  职位:{title}  邮箱:{email}  手机:{mobile}")
            return "\n".join(lines)

        except Exception as exc:
            print(f"[lark_tenant] search_contacts 异常: {exc}")
            return f"调用失败: {exc}"

    @tool
    def list_departments(parent_department_id: str = "0") -> str:
        """列出指定父部门下的子部门（应用身份，需 contact:department.base:readonly 权限）。

        Args:
            parent_department_id: 父部门 ID，默认 "0" 为根部门。
        """
        try:
            resp = requests.get(
                f"{FEISHU_HOST}/open-apis/contact/v3/departments",
                headers=_headers(),
                params={
                    "parent_department_id": parent_department_id,
                    "fetch_child": False,
                    "page_size": 50,
                },
                timeout=10,
            )
            data = resp.json()
            if data.get("code") != 0:
                return f"接口错误 {data.get('code')}: {data.get('msg')}"
            depts = data.get("data", {}).get("items", [])
            if not depts:
                return "该部门下没有子部门。"
            lines = [
                f"- {d.get('name')}  ID:{d.get('department_id')}  成员数:{d.get('member_count', '?')}"
                for d in depts
            ]
            return "\n".join(lines)
        except Exception as exc:
            return f"调用失败: {exc}"

    return [list_contacts, search_contacts, list_departments]
