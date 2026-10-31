# 公开源码发布操作（Windows 开发 AI / GitHub CLI）

目标：官方仓库 `https://github.com/king1054-maker/baifan-downloader`

**只发布 `baifan-downloader/` 项目源码，勿上传 `github.zip` 旧安装包或旧绿色版。**

1. 使用 GitHub 已授权账号 `king1054-maker` 创建新仓库 `baifan-downloader`，建议初始设为 **Private**。
2. 在仓库根目录上传本包中的全部项目文件，默认分支 `main`；不得包含任何 `aria2c.exe`、本地 API Key、日志、`data/` 等文件。
3. 核实 `LICENSE`（PolyForm Noncommercial 1.0.0）和 README 中的来源说明及联系邮箱。
4. 浏览 `assets/backgrounds`，应仅有蓝鲸原创主题的两张 JPG。确认 `assets/sounds` 不含 `ciallo.wav`、`ciallo_alt2.wav`。
5. 完成下方检查后，在仓库设置中把可见性切换为 **Public**；如你没有账号或仓库创建权限，请让所有者本人完成公开操作。
6. 第一个源码版本可打 tag `v0.10.103-source`；暂不创建带 EXE 安装包的正式发行附件。

检查清单：
- `python -m compileall -q app run.py tools packaging` 语法通过
- 核对 `requirements.txt` 指定 PySide6 而非 PyQt6
- 扫描公开仓库有无 API Key、令牌和个人数据
- 核实 LICENSE、ASSET_NOTICE、NOTICE、THIRD_PARTY_NOTICES、SECURITY.md
- Windows 11 真实运行测试未完成的必须明确在 README 中提示

说明：公开非商业源码与发布可执行安装包是两件事。后者必须先满足 `RELEASE_GATES.md`。
