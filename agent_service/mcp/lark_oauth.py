"""飞书 OAuth 2.0 授权码流程工具函数。

流程：
    1. get_auth_url()  → 生成授权链接，发给飞书用户点击
    2. 用户同意后，飞书回调 /lark/oauth/callback?code=xxx&state=<open_id>
    3. exchange_code() → 用 code 换取 user_access_token + refresh_token
    4. refresh_user_token() → token 过期时自动续签（有效期通常 2 小时）

所有函数均为同步 HTTP 调用（用于 Flask 路由和 bot 线程，不涉及 asyncio）。
"""
from __future__ import annotations

import urllib.parse
from typing import Dict

import requests

FEISHU_HOST = "https://open.feishu.cn"

# ── 应用已申请的 tenant（应用身份）权限 ──────────────────────────────────────
# 仅作记录，实际生效由飞书开发者后台控制，代码无需读取此列表。
TENANT_SCOPES = [
    "contact:department.base:readonly",
    "contact:user.base:readonly",
    "contact:user.email:readonly",
    "contact:user.employee_id:readonly",
    "contact:user.employee_number:read",
    "contact:user.id:readonly",
    "contact:work_city:readonly",
    "im:chat",
    "im:chat.access_event.bot_p2p_chat:read",
    "im:chat.announcement:read",
    "im:chat.announcement:write_only",
    "im:chat.managers:write_only",
    "im:chat.members:bot_access",
    "im:chat.members:read",
    "im:chat.members:write_only",
    "im:chat.menu_tree:read",
    "im:chat.menu_tree:write_only",
    "im:chat.moderation:read",
    "im:chat.tabs:read",
    "im:chat.tabs:write_only",
    "im:chat.top_notice:write_only",
    "im:chat.widgets:read",
    "im:chat.widgets:write_only",
    "im:chat:create",
    "im:chat:delete",
    "im:chat:moderation:write_only",
    "im:chat:operate_as_owner",
    "im:chat:read",
    "im:chat:readonly",
    "im:chat:update",
    "im:message",
    "im:message.group_at_msg.include_bot:readonly",
    "im:message.group_at_msg:readonly",
    "im:message.group_msg",
    "im:message.p2p_msg:readonly",
    "im:message.pins:read",
    "im:message.pins:write_only",
    "im:message.reactions:read",
    "im:message.reactions:write_only",
    "im:message.urgent",
    "im:message.urgent.status:write",
    "im:message:readonly",
    "im:message:recall",
    "im:message:send_as_bot",
    "im:message:send_multi_depts",
    "im:message:send_multi_users",
    "im:message:send_sys_msg",
    "im:message:update",
    "im:resource",
    "im:url_preview.update",
    "im:user_agent:read",
]

# ── OAuth 用户授权 scope（user 身份）────────────────────────────────────────
# 生成授权 URL 时实际请求的权限列表，用户点击同意后 token 包含这些权限。
USER_SCOPES = [
    "contact:contact",
    "contact:contact.base:readonly",
    "contact:department.organize:readonly",
    "im:message",
    "im:message.group_msg:get_as_user",
    "im:message.p2p_msg:get_as_user",
    "im:message.pins:read",
    "im:message.pins:write_only",
    "im:message.reactions:read",
    "im:message.reactions:write_only",
    "im:message.send_as_user",
    "im:message.urgent.status:write",
    "im:message:readonly",
    "im:message:recall",
    "im:message:update",
]

# get_auth_url 默认使用的 scope 字符串（空格分隔）
DEFAULT_SCOPES = " ".join(USER_SCOPES)


def get_auth_url(
    app_id: str,
    redirect_uri: str,
    state: str,
    scopes: str = DEFAULT_SCOPES,
) -> str:
    """生成飞书 OAuth 授权 URL。

    state 用于回调时识别是哪个用户触发的授权（通常填 open_id）。
    """
    params = {
        "app_id": app_id,
        "redirect_uri": redirect_uri,
        "scope": scopes,
        "state": state,
    }
    return f"{FEISHU_HOST}/open-apis/authen/v1/authorize?" + urllib.parse.urlencode(params)


def _get_app_access_token(app_id: str, app_secret: str) -> str:
    """获取应用级 app_access_token（用于换 user token）。"""
    resp = requests.post(
        f"{FEISHU_HOST}/open-apis/auth/v3/app_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取 app_access_token 失败: {data.get('msg')}")
    return data["app_access_token"]


def exchange_code(
    code: str,
    app_id: str,
    app_secret: str,
    redirect_uri: str,
) -> Dict:
    """用授权码换取 user_access_token。

    返回字典包含：
        access_token, refresh_token, expires_in, token_type,
        open_id, union_id, name, avatar_url 等。
    """
    app_token = _get_app_access_token(app_id, app_secret)
    resp = requests.post(
        f"{FEISHU_HOST}/open-apis/authen/v1/oidc/access_token",
        headers={"Authorization": f"Bearer {app_token}"},
        json={"grant_type": "authorization_code", "code": code},
        timeout=10,
    )
    resp.raise_for_status()
    result = resp.json()
    if result.get("code") != 0:
        raise RuntimeError(f"换取 user token 失败: {result.get('msg')}")
    return result.get("data", {})


def refresh_user_token(refresh_token: str, app_id: str, app_secret: str) -> Dict:
    """用 refresh_token 续签 user_access_token（refresh_token 有效期 30 天）。

    返回与 exchange_code 相同结构的字典。
    """
    app_token = _get_app_access_token(app_id, app_secret)
    resp = requests.post(
        f"{FEISHU_HOST}/open-apis/authen/v1/oidc/refresh_access_token",
        headers={"Authorization": f"Bearer {app_token}"},
        json={"grant_type": "refresh_token", "refresh_token": refresh_token},
        timeout=10,
    )
    resp.raise_for_status()
    result = resp.json()
    if result.get("code") != 0:
        raise RuntimeError(f"刷新 user token 失败: {result.get('msg')}")
    return result.get("data", {})
