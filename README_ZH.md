[English](README.md)

<div align="center">
<a href="https://github.com/trojanpanel"><img src="https://github.com/trojanpanel/install-script/assets/46235235/bfc4f96a-e8b6-499d-956f-a9c212059294" alt="Trojan Panel" width="150" /></a>
<h1>Trojan Panel</h1>
<p>
<a href="https://github.com/trojanpanel/install-script/stargazers"><img src="https://img.shields.io/github/stars/trojanpanel/install-script" alt="GitHub stars"></a>
<a href="https://github.com/trojanpanel/install-script/forks"><img src="https://img.shields.io/github/forks/trojanpanel/install-script" alt="GitHub forks"></a>
<a href="https://github.com/trojanpanel/install-script/issues"><img src="https://img.shields.io/github/issues/trojanpanel/install-script" alt="GitHub issues"></a>
<a href="https://github.com/trojanpanel/install-script/releases"><img src="https://img.shields.io/github/v/release/trojanpanel/install-script" alt="GitHub release"></a>
<a href="https://hub.docker.com/r/jonssonyan/trojan-panel"><img src="https://img.shields.io/docker/pulls/jonssonyan/trojan-panel" alt="Docker pulls"></a>
</p>
<h3>支持Xray/Trojan-Go/Hysteria/NaiveProxy的多用户Web管理面板</h3>
<a href="https://github.com/trojanpanel/install-script/assets/46235235/7ac2bba1-b442-442d-b48e-b52f92e0bad8"><img src="https://github.com/trojanpanel/install-script/assets/46235235/7ac2bba1-b442-442d-b48e-b52f92e0bad8" alt="Trojan Panel"/></a>
</div>

## 特点

- 极速搭建: 一键安装脚本，降低部署门槛，快速搭建系统
- 国际化: 系统语言支持中文/English/한국인/فارسی
- 多代理支持: 节点类型支持Xray/Trojan-Go/Hysteria/NaiveProxy
- 分布式: 前后端分离开发，减少模块之间耦合度，可以自由组合部署在多个服务器
- 功能强大: 支持登录注册/用户管理/节点管理/邮件管理/黑名单管理/自定义伪装网站/系统看板等
- 所见即所得: 支持多节点管理，自动化管理远程节点，自动化申请/续签证书，面板内编辑节点，远程服务实时修改节点配置

## 系统要求

系统: CentOS 7+ / Ubuntu 18+ / Debian 10+

CPU: linux/amd64 / linux/arm/v6 / linux/arm/v7 / linux/arm64 / linux/s390x / linux/ppc64le / linux/386

内存: ≥ 1G

## 本仓库说明

- 镜像与面板三件套由 `ghcr.io/silin12` 构建，默认标签 `v1.0.4`
- 安装脚本入口：`https://raw.githubusercontent.com/silin12/install-script/main/install_script.sh`
- 详细更新记录见 [CHANGELOG.md](CHANGELOG.md)

## 安装

- 联机（推荐，默认使用 ghcr.io/silin12 + v1.0.4）

    ```shell
    bash <(curl -fsSL https://raw.githubusercontent.com/silin12/install-script/main/install_script.sh)
    ```

- 指定镜像来源与标签

    ```shell
    TP_IMAGE_REPO=ghcr.io/silin12 TP_IMAGE_TAG=v1.0.4 \
    bash <(curl -fsSL https://raw.githubusercontent.com/silin12/install-script/main/install_script.sh)
    ```

- 单机

    ```shell
    source <(curl -L https://github.com/silin12/install-script/raw/main/install_script_standalone.sh)
    ```

- [安装旧版本](README_ARCHIVE_ZH.md)

### 安装顺序

| 角色 | 菜单项 | 说明 |
| --- | --- | --- |
| 面板机 | 1 → 2 → 3 | 1 前端（含 Docker / 反代 / 证书 / DB Guard），2 后端，3 Core |
| 节点子机 | 仅 3 | 只装 Core，连接面板机 |

> 菜单 6（MariaDB）、7（Redis）已被 2 包含，无需单独安装。

## 其他

Telegram Channel: https://t.me/jonssonyan_channel

You can subscribe to my channel on YouTube: https://www.youtube.com/@jonssonyan

## 文档

访问 [https://trojanpanel.github.io](https://trojanpanel.github.io) 查看完整文档

## 更新日志

见 [CHANGELOG.md](CHANGELOG.md)（含 v1.0.4 安装脚本、订阅格式、白名单状态列、DB Guard 动态密码、BBRv3 等改动）

## 报告缺陷与问题

[Issues](https://github.com/trojanpanel/install-script/issues)

## 致谢

- [trojan](https://github.com/trojan-gfw/trojan)
- [trojan-go](https://github.com/p4gefau1t/trojan-go)
- [Xray-core](https://github.com/XTLS/Xray-core)
- [hysteria](https://github.com/HyNetwork/hysteria)
- [naiveproxy](https://github.com/klzgrad/naiveproxy)

## Star随时间变化

[![Stargazers over time](https://starchart.cc/trojanpanel/install-script.svg)](https://github.com/trojanpanel/install-script)
