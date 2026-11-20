# 更新日志 / Change Log

本仓库是 [trojanpanel/install-script](https://github.com/trojanpanel/install-script) 的自定义分支（fork），
镜像与面板前端/后端/Core 均由 `ghcr.io/silin12` 构建发布。

- 镜像前缀：`ghcr.io/silin12`
- 默认镜像标签：`v1.0.4`
- 一键脚本：`https://raw.githubusercontent.com/silin12/install-script/main/install_script.sh`

---

## v1.0.4（2026-11-19）

### 一键安装脚本（盲选，推荐）

不用记版本号，脚本自己去 GHCR 取本 fork 已发布的最高版本（当前为 `v1.0.4`）：

```bash
TP_IMAGE_TAG=auto bash <(curl -fsSL https://raw.githubusercontent.com/silin12/install-script/main/install_script.sh)
```

- `auto` 与 `latest` 等价，只在本系列 `v1.0.x` 内取最高 tag
- 读不到时自动回退 `v1.0.4`，不会中断安装
- 只用一条命令，升级老机器也是它（菜单照样进 8 / 9 / 10）

### 固定版本安装

```bash
TP_IMAGE_REPO=ghcr.io/silin12 TP_IMAGE_TAG=v1.0.4 \
bash <(curl -fsSL https://raw.githubusercontent.com/silin12/install-script/main/install_script.sh)
```

不写环境变量时默认就是 `ghcr.io/silin12` + `v1.0.4`：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/silin12/install-script/main/install_script.sh)
```

### 安装顺序（重要）

| 角色 | 菜单项 | 说明 |
| --- | --- | --- |
| 面板机 | **1 → 2 → 3** | 1 装前端（会自动带 Docker、反代、证书、DB Guard），2 装后端，3 装 Core |
| 节点子机 | **仅 3** | 只装 Core，用于连接面板机 |

> 菜单里的 **6（MariaDB）/ 7（Redis）已经被 2 包含**，不需要再单独选，重复执行会报“已安装”。

### 本次更新内容

1. **镜像统一到 v1.0.4**（ui / panel / core 三件套），脚本默认标签同步为 `v1.0.4`。
2. **订阅链接恢复旧格式**：`/api/auth/subscribe/<base64(pass)>`。
   - 兼容 v2rayN / v2rayNG / Clash Meta 等老客户端，迁移面板时用户订阅链接保持一致。
   - 新版订阅仍可按 `?format=` 与 UA 分流，属可选增强。
3. **服务器列表新增白名单状态列**：
   - 已在通讯防火墙白名单 → 绿色勾
   - 尚未同步 → 红色叉
   - 「导出」按钮右侧新增**手动刷新白名单**按钮，点一次即重新识别并下发。
4. **DB Guard 密码不再硬编码**（修复重装后白名单为空）：
   - `db-guard-sync.py` 改为从 `trojan-panel` / `trojan-panel-core` 容器的环境变量 `mariadb_pas` 实时读取密码，读取失败才回退旧值。
   - 修复现象：新机安装 DB Guard 报 `ERROR 1045 (28000): Access denied`，导致自动白名单为空、子机连不上 9507/6378。
   - 同步机制：`db-guard-sync.timer` 每 30 秒按 `node_server.ip` + `node.node_server_ip` 刷新一次白名单。
5. **菜单 25「版本查询」改为实时读取**：同时显示 GHCR 最新 tag 与已安装镜像的实际 tag，不再写死。
6. **菜单 27「安装 BBRv3 内核」**：使用 XanMod 内核，走清华镜像源；不再提供 BBRv1。
7. **通讯防火墙范围**：仅管控 **MariaDB 9507 / Redis 6378** 两个内部端口，面板 UI 8888 与节点端口不受影响。
8. **新增盲选安装 + 修正版本误判**：
   - 支持 `TP_IMAGE_TAG=auto`（等同 `latest`），安装时自动取本系列最高 tag。
   - 修复菜单 25「版本查询」把 GHCR 上的上游 `v2.4.0` 误判为最新、导致提示升级到不兼容版本的问题；现在只认 `v1.0.x` 系列，与已装 `v1.0.4` 一致。

### 升级方式

已安装的机器不需要重装，执行：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/silin12/install-script/main/install_script.sh)
# 菜单选 8（更新前端）、9（更新后端）、10（更新 Core）
```

已经装过 DB Guard 的机器，需要重新执行一次菜单 26，让同步脚本换成动态读取密码的版本。

### v1.0.4 追加（2026-11-20）

- 前端镜像标签独立为 `TP_UI_IMAGE_TAG`（默认 `v1.0.10`），panel / core 仍用 `TP_IMAGE_TAG`（`v1.0.4`）；GHCR 拉不到时先用本机同 tag，再退 `TP_IMAGE_TAG`。
- nginx 两套模板新增 `/backup/`（上传 200m、超时 1800s）与 `/backup-pull/`（反代 8898）反代。
- 新增 `install_tp_backup`：安装 `tpbackup-api.py` + systemd `tpbackup-api`，装前端时自动执行；菜单 28 可单独安装/更新。
- 前端升级改为先拉镜像再删容器，避免回退时本地镜像已被删掉。

---

## 历史版本

| 版本 | 说明 |
| --- | --- |
| v1.0.3 | 面板三件套初始自定义构建；订阅短链与 UA 适配；到期与流量提醒 |
| v1.0.4 | 订阅恢复旧格式；白名单状态与手动刷新；DB Guard 动态密码；菜单 25/27 改进（当前版本） |
