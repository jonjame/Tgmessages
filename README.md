# Telegram 群组管理后台

基于 Python + Telethon 的 Telegram 群组管理网站，支持批量导入账号到群组、从源群拉活人到目标群。

## 功能

1. **批量导入到群 C**：输入群 C 的邀请链接，将已登录的多个 Telegram 账号批量加入该群
2. **从群 B 拉活人到群 A**：输入群 B（源群）和群 A（目标群）链接，将群 B 中发送过消息的用户拉入群 A  
   - 活人 = 在群 B 中发过消息的用户  
   - 操作账号需在群 A 拥有管理员权限

## 环境要求

- Python 3.10+
- Telegram API 凭证（API ID、API Hash）

## 安装

```bash
cd d:\php_project\tg
pip install -r requirements.txt
```

## 配置

1. 打开 [https://my.telegram.org](https://my.telegram.org)，登录后创建应用获取 **API ID** 和 **API Hash**
2. 编辑 `config.py`，填入：

```python
API_ID = 你的API_ID
API_HASH = "你的API_Hash"
```

## 使用步骤

### 1. 登录 Telegram 账号

每个要使用的账号需要先登录一次，生成会话文件：

```bash
python login_session.py
```

按提示输入会话名称（如 `account1`）、手机号、验证码等。会话文件会保存在 `sessions/` 目录。

### 2. 启动 Web 服务

```bash
python main.py
```

或使用 uvicorn：

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

浏览器访问：`http://localhost:8000`

### 3. 功能说明

- **批量导入到群 C**：选择要使用的账号，粘贴群 C 的邀请链接，点击「开始导入」
- **拉活人到群 A**：选择操作账号，填写群 B（源群）和群 A（目标群）链接，可调整扫描消息数量，点击「开始拉人」

## 链接格式

支持的链接格式：

- 私有群：`https://t.me/joinchat/xxxx` 或 `https://t.me/+xxxx`
- 公开群/频道：`https://t.me/channelname`

## 注意事项

1. 拉人功能需要操作账号在**群 A** 拥有**添加成员**的管理员权限
2. 部分用户因隐私设置可能无法被添加，属正常情况
3. 频繁操作可能触发 Telegram 限流，请合理控制频率
4. 请遵守 Telegram 服务条款和当地法律法规
