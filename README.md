# 白饭下载器 · BaiFan Downloader

> **v0.10.103 · 已导入源码 · 私有预发布仓库（Private）**  
> Python 源码与必要素材已上传，GitHub Actions 源码完整性检查通过。**尚未公开发布 Windows 安装包，且未完成独立 Windows 11 实机验收。**

白饭下载器是一款面向 Windows 的可定制下载工具，以 **aria2** 为下载引擎、**PySide6** 为界面框架，支持 HTTP/FTP 下载、BT / 磁力链接、AI 辅助改名、本地 API、蓝鲸桌宠、背景裁切和自定义提示音。

## 从源码运行（Windows 11）

1. 安装 **Python 3.12**，获取本仓库源码。
2. 在源码根目录建立虚拟环境并安装依赖：
   ```powershell
   py -3.12 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```
3. 从可信的 aria2 项目发行渠道自行获取 Windows 版 `aria2c.exe`，放到 `bin/aria2c.exe`（项目源码仓库不捆绑该可执行文件）。
4. 启动：
   ```powershell
   .\.venv\Scripts\python.exe run.py
   ```
5. 软件的 AI API Key 由用户在本地设置，不得提交至仓库。

这是源码运行指南，**不代表所有功能已经完成实机测试**。安装包构建所需事项见 `PUBLISH_GUIDE.md` 和 `RELEASE_GATES.md`。

## 版权与使用许可

- **作者及商业授权联系**：meetmusk@icloud.com
- **作者原创代码**：PolyForm Noncommercial 1.0.0。允许符合许可的非商业使用、个人修改和非商业再分发；商业用途需另行取得许可。
- **这属于 source-available（源码可见），不是 OSI 定义的开源许可证。**
- **第三方代码与组件**：PySide6/Qt、aria2 等遵循各自的许可证，不能通过本项目 LICENSE 改写其条款。

## 示例素材

- **16:9 横版**：原创蓝鲸主题演示背景。
- **9:16 竖版**：作者在日本亲自拍摄的红叶鸟居照片（由原图裁切），用于展示竖图与可选区域。
- 桌宠与图标：作者设计和 ChatGPT 辅助生成的原创角色素材。
- **Ciallo 默认提示音**：代码保留了 `ciallo.wav` 和 `ciallo_alt2.wav` 的选择及默认音量 60%，本次仓库导入**未包含这两段录音文件**，因为底层采样的再分发权尚待确认；缺少时按程序逻辑退回自带提示音。原开发工程与私有候选包保有原始调配版本。

本仓库未导入旧安装包、EXE/DLL、用户配置、密钥、历史同人背景，也暂未导入需要单独复核的旧文档截图。

## 当前验收状态

- [x] v0.10.103 Python 源码及必要原素材导入 GitHub
- [x] GitHub Actions `Source integrity` 通过
- [x] 非商业许可、版权、安全与贡献说明已建立
- [ ] 确认提示音底层采样的公发授权
- [ ] 独立 Windows 11 构建、安装与功能测试
- [ ] 完成第三方组件及二进制分发合规复核
- [ ] 仓库所有者批准后从 Private 改为 Public

查看 [发布核验任务](../../issues/1)、[版权声明](ASSET_NOTICE.md)、[第三方许可](THIRD_PARTY_NOTICES.md)、[安全报告](SECURITY.md)。
