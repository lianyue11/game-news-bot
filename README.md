# 全球游戏行业情报推送器

定时汇集游戏公司、新游戏与重大更新、电竞赛事、创作比赛、政策及行业动态，去重后生成中文简报，并同时推送到企业微信群和飞书群。

## 快速开始

1. 安装 Python 3.11+。
2. 复制 `.env.example` 为 `.env`，填入至少一个群机器人 Webhook。
3. 安装依赖并试跑：

```powershell
python -m pip install -r requirements.txt
python -m gameintel run --dry-run
python -m gameintel run
```

持续定时运行：

```powershell
python -m gameintel daemon
```

默认每天北京时间 09:00 推送。信息源、分类词、排除词和条数可在 `config.yaml` 中调整。

## Webhook 获取

- 企业微信：进入企业微信群 → 群设置 → 群机器人 → 添加机器人，复制 Webhook。
- 飞书：进入群聊 → 设置 → 群机器人 → 添加自定义机器人，复制 Webhook。若启用了签名校验，同时填写 `FEISHU_SECRET`。

Webhook 是发送凭证，不要提交到 Git。程序用 SQLite 保留已处理链接，避免重复推送。

## AI 摘要（免费云端方案）

未配置模型时，程序仍可采集、去重、分类并生成简易摘要。推荐用 Gemini API 免费层；它提供 OpenAI 兼容接口，因此无需额外 SDK：

```env
LLM_API_KEY=你的_Gemini_API_Key
LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
LLM_MODEL=gemini-2.5-flash
```

## GitHub Actions 云端部署（电脑关机也运行）

1. 将项目上传到 GitHub 仓库。
2. 打开仓库 `Settings → Secrets and variables → Actions`。
3. 添加以下 Repository secrets：
   - `GEMINI_API_KEY`
   - `WECOM_WEBHOOK`
   - `FEISHU_WEBHOOK`
   - `FEISHU_SECRET`（飞书未启用签名时可不填）
4. 打开仓库的 `Actions` 页面，启用工作流。
5. 可通过 `Run workflow` 手动测试；之后每天北京时间 09:00 自动执行。

工作流会缓存 `gameintel.db`，用于跨次运行记录已推送链接。所有密钥仅从 GitHub Secrets 注入，不写入仓库或日志。

## 其他部署

可部署到一台长期在线的 Windows/Linux 主机或云服务器。正式使用建议用 Docker/系统服务托管，并为日志、SQLite 数据库和 `.env` 做持久化与备份。
