"""
Telegram 核心服务 - 使用 Telethon 实现群组操作
"""
import asyncio
import re
import os
from pathlib import Path
from telethon import TelegramClient
from telethon.tl.functions.messages import ImportChatInviteRequest, CheckChatInviteRequest, AddChatUserRequest
from telethon.tl.functions.channels import JoinChannelRequest, InviteToChannelRequest
from telethon.tl.types import Channel, Chat
from telethon.errors import (
    InviteHashExpiredError, UserAlreadyParticipantError,
    ChannelPrivateError, FloodWaitError, UserPrivacyRestrictedError,
    UserNotMutualContactError, UserChannelsTooMuchError
)

import config


def parse_group_link(link: str) -> tuple[str | None, bool]:
    """
    解析群组链接，返回 (value, is_private_hash)
    - 私有链接(joinchat/+): 返回 (hash, True)
    - 公开链接(t.me/用户名): 返回 (username, False)
    """
    link = link.strip()
    # 私有: https://t.me/joinchat/xxx 或 https://t.me/+xxx
    match = re.search(r'(?:joinchat/|\+)([A-Za-z0-9_-]+)', link)
    if match:
        return (match.group(1), True)
    # 公开: https://t.me/channelname 或 https://t.me/f4444444444444444444444444
    match = re.search(r't\.me/([A-Za-z0-9_]+)', link)
    if match:
        return (match.group(1), False)
    return (None, False)


async def get_client(session_name: str) -> TelegramClient:
    """获取已登录的 Telegram 客户端"""
    session_path = Path(config.SESSIONS_DIR) / session_name
    client = TelegramClient(
        str(session_path),
        config.API_ID,
        config.API_HASH
    )
    await client.connect()
    if not await client.is_user_authorized():
        raise ValueError(f"会话 {session_name} 未授权，请先登录")
    return client


async def join_group_with_account(session_name: str, group_link: str) -> dict:
    """
    使用指定账号通过链接加入群组
    返回: {success: bool, message: str}
    """
    client = None
    try:
        client = await get_client(session_name)
        value, is_private = parse_group_link(group_link)
        if not value:
            return {"success": False, "message": "无效的群组链接"}

        if is_private:
            # 私有邀请链接
            try:
                await client(ImportChatInviteRequest(value))
                return {"success": True, "message": "成功加入群组"}
            except UserAlreadyParticipantError:
                return {"success": True, "message": "已在群组中"}
            except InviteHashExpiredError:
                return {"success": False, "message": "邀请链接已过期"}
        else:
            # 公开群组/频道
            try:
                entity = await client.get_entity(value)
                await client(JoinChannelRequest(entity))
                return {"success": True, "message": "成功加入群组"}
            except ChannelPrivateError:
                return {"success": False, "message": "该群组为私有，请使用邀请链接"}
            except UserAlreadyParticipantError:
                return {"success": True, "message": "已在群组中"}
    except FloodWaitError as e:
        return {"success": False, "message": f"请求过于频繁，请等待 {e.seconds} 秒后重试"}
    except ValueError as e:
        return {"success": False, "message": str(e)}
    except Exception as e:
        return {"success": False, "message": str(e)}
    finally:
        if client:
            await client.disconnect()


async def batch_join_group(session_names: list[str], group_link: str) -> list[dict]:
    """批量使用多个账号加入群组"""
    results = []
    for session_name in session_names:
        result = await join_group_with_account(session_name, group_link)
        results.append({"session": session_name, **result})
        # 避免触发限流
        await asyncio.sleep(2)
    return results


async def get_active_users_from_group(
    session_name: str,
    group_link: str,
    limit: int | None = None,
    progress_callback=None,
) -> dict:
    """
    获取群组中发送过消息的用户（活人）
    返回: {success: bool, users: list, message: str}
    """
    client = None
    try:
        client = await get_client(session_name)
        value, is_private = parse_group_link(group_link)
        if not value:
            return {"success": False, "users": [], "messages": [], "message": "无效的群组链接"}

        if is_private:
            # 私有群：先检查是否已在群中
            try:
                check = await client(CheckChatInviteRequest(value))
                if hasattr(check, 'chat'):
                    chat = check.chat  # ChatInviteAlready - 已在群中
                else:
                    updates = await client(ImportChatInviteRequest(value))
                    chat = updates.chats[0]
            except Exception as e:
                return {"success": False, "users": [], "messages": [], "message": f"无法访问群组: {e}"}
        else:
            chat = await client.get_entity(value)

        # 遍历消息历史，收集发送过消息的用户 ID 及消息内容（不限制数量，扫描全部）
        # iter_messages 从新到旧，最后一条即最早的消息
        active_user_ids = set()
        scanned_messages = []
        count = 0
        earliest_date = None
        async for message in client.iter_messages(chat):
            if message.sender_id:
                active_user_ids.add(message.sender_id)
            if message.date:
                earliest_date = message.date
            text = (message.text or "").replace("\n", " ").replace("\t", " ")[:500]
            scanned_messages.append({
                "date": message.date.isoformat() if message.date else "",
                "sender_id": message.sender_id or "",
                "text": text,
            })
            count += 1

        # 逐个获取完整用户信息（message.sender 可能缺少 username，需 get_entity 补全）
        active_users = []
        total = len(active_user_ids)
        if progress_callback:
            progress_callback(0, 0, total, message_count=count, earliest_date=earliest_date)  # 扫描完成，开始采集
        for i, uid in enumerate(active_user_ids):
            try:
                user = await client.get_entity(uid)
                username = (getattr(user, 'username', None) or "").strip()
                if not username:
                    if progress_callback:
                        progress_callback(len(active_users), i + 1, total)
                    continue  # 没有用户名的（如 @xxx 格式）不采集
                active_users.append({
                    "id": uid,
                    "username": username,
                    "first_name": getattr(user, 'first_name', None) or "",
                    "last_name": getattr(user, 'last_name', None) or ""
                })
            except Exception:
                pass  # 获取失败或没有用户名则跳过
            if progress_callback:
                progress_callback(len(active_users), i + 1, total)
            await asyncio.sleep(0.5)  # 避免限流

        return {
            "success": True,
            "users": active_users,
            "messages": scanned_messages,
            "message": f"找到 {len(active_users)} 个活跃用户（共扫描 {count} 条消息）"
        }
    except FloodWaitError as e:
        return {"success": False, "users": [], "messages": [], "message": f"请求过于频繁，请等待 {e.seconds} 秒"}
    except ValueError as e:
        return {"success": False, "users": [], "messages": [], "message": str(e)}
    except Exception as e:
        return {"success": False, "users": [], "messages": [], "message": str(e)}
    finally:
        if client:
            await client.disconnect()


async def add_users_to_group(session_name: str, target_group_link: str, user_ids: list[int]) -> dict:
    """
    将用户添加到目标群组
    需要管理员权限
    返回: {success: int, failed: int, details: list}
    """
    client = None
    try:
        client = await get_client(session_name)
        value, is_private = parse_group_link(target_group_link)
        if not value:
            return {"success": 0, "failed": len(user_ids), "details": [], "message": "无效的目标群组链接"}

        if is_private:
            try:
                check = await client(CheckChatInviteRequest(value))
                if hasattr(check, 'chat'):
                    target_chat = check.chat
                else:
                    updates = await client(ImportChatInviteRequest(value))
                    target_chat = updates.chats[0]
            except Exception as e:
                return {"success": 0, "failed": len(user_ids), "details": [], "message": str(e)}
        else:
            target_chat = await client.get_entity(value)

        success_count = 0
        failed_count = 0
        details = []
        is_channel = isinstance(target_chat, Channel)

        def do_invite(user_entity):
            if is_channel:
                return client(InviteToChannelRequest(target_chat, [user_entity]))
            else:
                return client(AddChatUserRequest(target_chat, user_entity, fwd_limit=10))

        for uid in user_ids:
            try:
                user_entity = await client.get_entity(uid)
                await do_invite(user_entity)
                success_count += 1
                details.append({"user_id": uid, "status": "success"})
            except UserPrivacyRestrictedError:
                failed_count += 1
                details.append({"user_id": uid, "status": "privacy_restricted"})
            except UserNotMutualContactError:
                failed_count += 1
                details.append({"user_id": uid, "status": "not_mutual_contact"})
            except UserChannelsTooMuchError:
                failed_count += 1
                details.append({"user_id": uid, "status": "user_in_too_many_groups"})
            except FloodWaitError as e:
                details.append({"user_id": uid, "status": f"flood_wait_{e.seconds}s"})
                await asyncio.sleep(e.seconds)
                # 重试
                try:
                    user_entity = await client.get_entity(uid)
                    await do_invite(user_entity)
                    success_count += 1
                except Exception:
                    failed_count += 1
            except Exception as e:
                failed_count += 1
                details.append({"user_id": uid, "status": str(e)[:50]})
            await asyncio.sleep(1)  # 避免限流

        return {
            "success": success_count,
            "failed": failed_count,
            "details": details,
            "message": f"成功添加 {success_count} 人，失败 {failed_count} 人"
        }
    except Exception as e:
        return {"success": 0, "failed": len(user_ids), "details": [], "message": str(e)}
    finally:
        if client:
            await client.disconnect()


async def pull_active_users_to_group(
    session_name: str,
    source_group_link: str,
    target_group_link: str,
    message_limit: int | None = None,
) -> dict:
    """
    从群B获取活人，拉入群A
    """
    # 1. 获取群B的活跃用户
    result = await get_active_users_from_group(session_name, source_group_link, limit=message_limit or None)
    if not result["success"]:
        return result

    users = result["users"]
    if not users:
        return {"success": True, "message": "群B中没有找到活跃用户", "added": 0, "failed": 0}

    user_ids = [u["id"] for u in users]
    # 2. 添加到群A
    add_result = await add_users_to_group(session_name, target_group_link, user_ids)
    add_result["active_users_count"] = len(users)
    add_result["success"] = True
    return add_result
