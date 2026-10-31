# 第三方组件与许可证核验

本仓库计划公开的是**应用源码**，并非已经验证的 Windows 二进制发行包。

| 组件 | 用途 | 许可与待核查事项 |
| --- | --- | --- |
| PySide6 / Qt for Python | Windows 图形界面 | 可选择适用的 LGPL/GPL/商业授权路线；应按最终采用的确切版本、Qt 模块和动态库逐一确认条款。 |
| aria2 | 独立下载引擎进程 | GPL 许可软件。源码包可以不捆绑 aria2c.exe；发布安装包时需核实实际二进制版本、对应源码及 GPL 分发义务。 |
| PyInstaller | Windows 构建工具 | 核实 Bootloader 许可例外与最终打包内容；这不是运行源码的直接依赖。 |
| QtMultimedia / FFmpeg 等 | 音视频及底层解码组件（若最终包中包含） | 以实际 DLL 和构建产物为准审查许可证，不能仅凭 Python requirements 判断合规。 |

官方资料：

- https://doc.qt.io/qtforpython-6/
- https://www.qt.io/licensing/
- https://github.com/aria2/aria2
- https://pyinstaller.org/

**重要：** 项目自己的 PolyForm Noncommercial 许可证**不改变**这些第三方组件的许可证。此文档是核验清单，不是对第三方许可完全合规的证明。

发布 Windows 安装包或绿色版前，应列出准确版本、文件、许可证正文及所需的对应源代码或源码获取方式。
