# usst-rollcall

上海理工大学一网畅学课程签到提醒和自动签到工具。

它可以在后台定时检查是否有课程签到，并在发现签到时发送通知。你也可以按需开启自动签到。

## 功能

- 检查课程签到
- 有签到时推送通知
- 支持 Bark、Gotify、邮件、控制台通知
- 支持多个账号
- 支持数字签到自动提交
- 支持雷达签到自动提交，但需要自己配置坐标
- 默认只在 `07:30-20:30` 检查，减少无意义请求

二维码签到暂不支持自动提交。

## 安装

推荐使用 `uv` 安装，适合长期运行命令行工具：

```bash
uv tool install usst-rollcall
```

更新：

```bash
uv tool upgrade usst-rollcall
```

也可以使用 `pip` 安装：

```bash
pip install usst-rollcall
```

如果使用 `pip`，建议放在虚拟环境里，避免影响系统 Python。

安装后检查命令是否可用：

```bash
usst-rollcall --help
```

## 第一次使用

### 1. 生成配置文件

```bash
usst-rollcall init-config
```

查看配置文件位置：

```bash
usst-rollcall where
```

常见位置：

| 系统 | 配置文件 |
| --- | --- |
| Windows | `%LOCALAPPDATA%\usst-rollcall\config.yaml` |
| Linux / VPS | `~/.config/usst-rollcall/config.yaml` |

### 2. 填入登录凭据

你需要从已登录的一网畅学请求里获取 `X-SESSION-ID`。

保存到工具里：

```bash
usst-rollcall session-set --x-session-id "这里填你的 X-SESSION-ID"
```

如果你也拿到了 `session` cookie，可以一起保存：

```bash
usst-rollcall session-set --x-session-id "这里填你的 X-SESSION-ID" --session-cookie "这里填 session cookie"
```

查看是否保存成功：

```bash
usst-rollcall session-show
```

### 3. 测试能否查询签到

```bash
usst-rollcall poll-once
```

如果能正常输出课程签到数量，说明配置基本可用。

## 配置通知

打开配置文件，找到 `notify`。

### Bark 示例

```yaml
notify:
  bark:
    enabled: true
    server: https://api.day.app
    key: 你的 Bark key
```

测试通知：

```bash
usst-rollcall notify-test
```

如果手机能收到消息，通知配置成功。

## 开启自动监控

前台运行：

```bash
usst-rollcall watch
```

监控所有已启用账号：

```bash
usst-rollcall watch --all
```

默认情况下，程序只会在 `07:30-20:30` 之间请求签到接口。其他时间程序会保持运行，但不会检查签到。

如果部署在 VPS，建议用 `supervisor`、`systemd`、Docker 或其他进程管理工具守护运行。

## 开启自动签到

自动签到默认关闭。需要你手动打开配置文件，把 `sign.enabled` 改为 `true`：

```yaml
sign:
  enabled: true
  number_enabled: true
  radar_enabled: false
  notify_result: true
```

临时开启一次：

```bash
usst-rollcall poll-once --sign
```

监控时开启：

```bash
usst-rollcall watch --sign
```

说明：

- 数字签到：默认支持。
- 雷达签到：需要你自己配置经纬度，并开启 `radar_enabled`。
- 二维码签到：暂不支持。

雷达签到配置示例：

```yaml
sign:
  enabled: true
  radar_enabled: true
  radar_location:
    latitude: 31.000000
    longitude: 121.000000
    accuracy: 35.0
```

## 多账号

编辑配置文件里的 `accounts`：

```yaml
accounts:
  - id: main
    name: 我的账号
    enabled: true
    session_file: sessions/main.json

  - id: friend
    name: 朋友账号
    enabled: true
    session_file: sessions/friend.json
```

给不同账号保存登录凭据：

```bash
usst-rollcall session-set --account main --x-session-id "main 的 X-SESSION-ID"
usst-rollcall session-set --account friend --x-session-id "friend 的 X-SESSION-ID"
```

运行所有账号：

```bash
usst-rollcall watch --all
```

每个账号可以单独配置通知和自动签到。

## 常用命令

| 命令 | 作用 |
| --- | --- |
| `usst-rollcall where` | 查看配置文件位置 |
| `usst-rollcall accounts` | 查看账号列表 |
| `usst-rollcall session-set` | 保存登录凭据 |
| `usst-rollcall session-show` | 查看当前登录凭据状态 |
| `usst-rollcall poll-once` | 立即检查一次签到 |
| `usst-rollcall poll-once --notify` | 检查一次，有新签到就通知 |
| `usst-rollcall watch` | 持续监控默认账号 |
| `usst-rollcall watch --all` | 持续监控所有启用账号 |
| `usst-rollcall notify-test` | 测试通知 |

## 更新

如果使用 `uv tool` 安装：

```bash
uv tool upgrade usst-rollcall
```

如果使用 `pip` 安装：

```bash
pip install -U usst-rollcall
```

## 常见问题

### supervisor 里找不到 `usst-rollcall`

这是因为 supervisor 的 `PATH` 和你登录 shell 的 `PATH` 不一样。

解决方法：在 supervisor 启动命令里写完整路径，例如：

```bash
/root/.local/bin/usst-rollcall watch --all
```

### 提示 401 或查询失败

通常是登录凭据过期了。重新获取 `X-SESSION-ID` 后再执行：

```bash
usst-rollcall session-set --x-session-id "新的 X-SESSION-ID"
```

### 没收到通知

先运行：

```bash
usst-rollcall notify-test
```

如果测试通知也收不到，优先检查 Bark key、Gotify token 或邮箱配置。
