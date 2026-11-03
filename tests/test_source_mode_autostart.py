# -*- coding: utf-8 -*-
"""源码/测试版启动不得写开机自启注册表（回归守卫，对应 0.10.106）。

`app/shell_integration.sync_autostart()` 只在打包版（`sys.frozen`）里动
`HKCU\\...\\Run`；设置页点「确定」已经走它了，但 `run.py` 的启动入口在
0.10.107 合并后又退回成直接 `set_autostart()`，源码版启动仍可能把
`pythonw` 写进「启动应用」，下次登录自动拉起测试版。

这里**不导入 Qt / winreg**（CI 的 ubuntu source-check 里也能跑），只做静态检查：
入口必须调用 `sync_autostart(settings)`，且不允许退回带 `True` 默认值的旧写法。
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class SourceModeAutostartTests(unittest.TestCase):
    def test_run_py_uses_sync_autostart(self):
        src = (ROOT / "run.py").read_text(encoding="utf-8")
        self.assertIn("shell_integration.sync_autostart(settings)", src)
        self.assertNotIn('settings.get("autostart", True)', src)

    def test_sync_autostart_is_frozen_only(self):
        src = (ROOT / "app" / "shell_integration.py").read_text(encoding="utf-8")
        self.assertIn("def sync_autostart(", src)
        self.assertIn('if not getattr(sys, "frozen", False):', src)


if __name__ == "__main__":
    unittest.main()
