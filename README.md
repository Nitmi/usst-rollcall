# usst-rollcall

上海理工大学一网畅学课程签到提醒和自动签到工具。

它可以在后台定时检查是否有课程签到，并在发现签到时发送通知。你也可以按需开启自动签到。

## 功能

- 检查课程签到
- 有签到时推送通知
- 自动登录
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

安装后检查命令是否可用：

```bash
usst-rollcall --help
usst-rollcall version
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

### 2. 填入账号密码

在配置文件中填写账号密码登录信息：

```yaml
accounts:
  - id: main
    name: Main
    enabled: true
    session_file: sessions/main.json
    login:
      enabled: true
      username: 你的学号或账号
      password: 你的密码
```

可以先主动测试一次登录：

```bash
usst-rollcall login
usst-rollcall login-status
```

程序会自动维护内部 session 缓存，你不需要手动处理。

### 3. 测试能否查询签到

```bash
usst-rollcall poll-once
```

如果能正常输出课程签到数量，说明配置基本可用。

## 配置通知

打开配置文件，找到对应账号下面的 `notify`。

### Bark 示例

```yaml
accounts:
  - id: main
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

## 自动登录说明

如果 `watch` 或 `poll-once` 遇到 `401`，工具会自动尝试重新登录，再继续请求。
如果本地还没有 session 缓存，程序也会先自动登录，再开始查询。

## 开启自动监控

推荐监控所有已启用账号：

```bash
usst-rollcall watch --all
```

只监控默认账号：

```bash
usst-rollcall watch
```

默认情况下，程序只会在 `07:30-20:30` 之间请求签到接口。其他时间程序会保持运行，但不会检查签到。

默认按 `Asia/Shanghai` 时区判断活跃时间。如果你的运行环境时区特殊，可以在配置文件里修改：

```yaml
watch:
  active_start: "07:30"
  active_end: "20:30"
  timezone: Asia/Shanghai
```

启动后会先显示运行摘要，你可以看到当前是否启用了 `--all`、是否启用了 `--sign`、正在监控哪些账号。

如果部署在 VPS，建议用 `supervisor`、`systemd`、Docker 或其他进程管理工具守护运行。

## 开启自动签到

自动签到默认关闭。需要你手动打开配置文件，把对应账号的 `sign.enabled` 改为 `true`：

```yaml
accounts:
  - id: main
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
usst-rollcall watch --all --sign
```

说明：

- 数字签到：默认支持。
- 雷达签到：需要你自己配置经纬度，并开启 `radar_enabled`。
- 二维码签到：暂不支持。

雷达签到配置示例：

```yaml
accounts:
  - id: main
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
    login:
      enabled: true
      username: "2023000001"
      password: "你的密码1"

  - id: friend
    name: 朋友账号
    enabled: true
    session_file: sessions/friend.json
    login:
      enabled: true
      username: "2023000002"
      password: "你的密码2"
```

运行所有账号：

```bash
usst-rollcall watch --all
```

每个账号可以单独配置通知和自动签到。

每个账号都配置自己的登录方式、通知方式和自动签到策略。

## 常用命令

| 命令 | 作用 |
| --- | --- |
| `usst-rollcall where` | 查看配置文件位置 |
| `usst-rollcall version` | 查看当前安装版本 |
| `usst-rollcall --version` | 查看当前安装版本 |
| `usst-rollcall accounts` | 查看账号列表 |
| `usst-rollcall login` | 使用配置中的账号密码重新登录并刷新 session |
| `usst-rollcall login-status` | 查看当前账号是否已配置自动登录、是否已有缓存 session |
| `usst-rollcall poll-once` | 立即检查一次签到 |
| `usst-rollcall poll-once --notify` | 检查一次，有新签到就通知 |
| `usst-rollcall watch --all` | 持续监控所有启用账号 |
| `usst-rollcall watch --all --sign` | 持续监控所有启用账号，并临时开启自动签到 |
| `usst-rollcall watch` | 只持续监控默认账号 |
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

## 联系方式

有问题或建议可以联系：

```text
a.oxidizing172@aleeas.com
```

## 常见问题

### supervisor 里找不到 `usst-rollcall`

这是因为 supervisor 的 `PATH` 和你登录 shell 的 `PATH` 不一样。

解决方法：在 supervisor 启动命令里写完整路径，例如：

```bash
/root/.local/bin/usst-rollcall watch --all --sign
```

### 提示 401 或查询失败

通常是缓存 session 过期了，或者账号密码登录配置不完整。

先执行：

```bash
usst-rollcall login
usst-rollcall login-status
```

如果还是失败，再检查对应账号下面的 `login.enabled`、`username` 和 `password`。

### 没收到通知

先运行：

```bash
usst-rollcall notify-test
```

如果测试通知也收不到，优先检查 Bark key、Gotify token 或邮箱配置。
