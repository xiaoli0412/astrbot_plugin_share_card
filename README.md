# 卡片分享

> 用 NapCat get_mini_app_ark 发 B站 小程序卡片，配 1-2 句文案

## 这是什么

主动分享哔哩哔哩视频，以 QQ 小程序卡片（NapCat get_mini_app_ark + json 段）推送并配 1-2 句文案

## 安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/xiaoli0412/astrbot_plugin_share_card.git
```

然后在 WebUI 插件管理里启用，或重启 AstrBot。

## 配置

全部配置都能在 **WebUI → 插件 → 卡片分享 → 配置** 里改，不用手改文件。

| 键 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `enabled` | bool | `False` | 关掉后 LLM 工具 share_video 与主动分享都不会有任何动作。 |
| `proactive_enabled` | bool | `False` | 开启后按 proactive_interval_hours 从最近一次对话里提取话题，搜视频并推卡片。依赖 enabled=true。 |
| `proactive_interval_hours` | int | `6` | 默认 6 小时一次，最小 1 小时。 |
| `max_per_day` | int | `3` | 主动分享每天最多推几次，超过就跳过，防刷屏。 |
| `search_source` | string | `bilibili` | 目前只支持 bilibili（小红书暂无公开搜索接口，留作后续扩展位）。 |
| `bili_plugin_path` | string | `/AstrBot/data/plugins/astrbot_plugin_bilibili` | 用来复用它的搜索客户端（WBI 签名）。容器内默认 /AstrBot/data/plugins/astrbot_plugin_bilibil |
| `fallback_to_text` | bool | `True` | 卡片发送失败时退化成「标题 + 链接」的纯文本，保证用户至少能收到东西。 |
| `log_ark_debug` | bool | `True` | 把 get_mini_app_ark 的请求参数与返回结构写进日志，排障时很有用。 |

## 依赖

无第三方依赖（纯标准库）

## 兼容性

- 平台：aiocqhttp
- AstrBot：`>=4.24,<5`

## 隐私说明

插件只在本机运行，不上传任何数据到第三方。配置与运行时数据存在 `AstrBot/data/` 下。

## 许可

MIT，见 [LICENSE](LICENSE)。
