"""以 user_access_token 调用飞书 API 的 LangChain 工具集。

这些工具作为 lark-mcp（app token）的补充，专门处理需要用户授权的操作。
通过工厂函数 build_user_tools(token) 创建，绑定具体的 user_access_token。

当前工具：
    list_contacts  — 列出企业通讯录成员
    search_contacts — 按关键字搜索成员
"""
from __future__ import annotations

from typing import List

import requests
from langchain_core.tools import tool

FEISHU_HOST = "https://open.feishu.cn"


def build_user_tools(user_access_token: str) -> List:
    """工厂函数：根据 user_access_token 创建绑定该 token 的工具列表。"""

    _headers = {"Authorization": f"Bearer {user_access_token}"}

    @tool
    def list_contacts(department_id: str = "0", page_size: int = 20) -> str:
        """列出企业通讯录中指定部门的成员（需要用户授权）。

        Args:
            department_id: 部门 ID，默认 "0" 表示根部门。
            page_size: 返回条数，最多 50。
        """
        try:
            resp = requests.get(
                f"{FEISHU_HOST}/open-apis/contact/v3/users",
                headers=_headers,
                params={
                    "department_id": department_id,
                    "page_size": min(int(page_size), 50),
                },
                timeout=10,
            )
            data = resp.json()
            if data.get("code") != 0:
                return f"接口错误 {data.get('code')}: {data.get('msg')}"
            users = data.get("data", {}).get("items", [])
            if not users:
                return "该部门下暂无成员。"
            lines = []
            for u in users:
                name = u.get("name", "（未知）")
                email = u.get("enterprise_email") or u.get("email") or "无邮箱"
                mobile = u.get("mobile", "")
                dept_ids = u.get("department_ids", [])
                lines.append(f"- {name}  邮箱:{email}  手机:{mobile}  部门ID:{dept_ids}")
            return "\n".join(lines)
        except Exception as exc:
            return f"调用失败: {exc}"

    @tool
    def search_contacts(query: str) -> str:
        """按关键字搜索企业通讯录用户（姓名/邮箱/手机号，需要用户授权）。

        Args:
            query: 搜索关键字，支持姓名、邮箱、手机号模糊匹配。
        """
        try:
            resp = requests.get(
                f"{FEISHU_HOST}/open-apis/contact/v3/users/find",
                headers=_headers,
                params={"query": query},
                timeout=10,
            )
            data = resp.json()
            if data.get("code") != 0:
                return f"接口错误 {data.get('code')}: {data.get('msg')}"
            users = data.get("data", {}).get("items", [])
            if not users:
                return f"未找到与「{query}」匹配的联系人。"
            lines = []
            for u in users:
                name = u.get("name", "（未知）")
                email = u.get("enterprise_email") or u.get("email") or "无邮箱"
                open_id = u.get("open_id", "")
                lines.append(f"- {name}  邮箱:{email}  open_id:{open_id}")
            return "\n".join(lines)
        except Exception as exc:
            return f"调用失败: {exc}"

    return [list_contacts, search_contacts]
