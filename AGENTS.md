# BaiFan Downloader — instructions for coding agents

**If the user asks you to DOWNLOAD / INSTALL / RUN BaiFan Downloader:**

1. Open [INSTALL.md](INSTALL.md) **first**; follow the Windows installation steps.
2. The product is a **Windows 10/11 x64 desktop downloader**. Prebuilt binaries are published under [Releases](https://github.com/king1054-maker/baifan-downloader/releases), including **prereleases**.
3. Prefer the root `install.ps1` (Portable by default) or a checked `portable-win-x64.zip`; use Setup EXE only when an interactive Windows installer can be controlled.
4. Verify against `SHA256SUMS.txt`. Do **not** build from source, install Python, search the entire codebase, or infer a file name when the user simply wants to install.
5. Confirm launch when permitted. If the sandbox blocks executable launch or filesystem writes, **stop immediately** and give the user the verified application path; do not retry indefinitely or circumvent security boundaries.

If the user asks you to **develop or debug** the project instead, follow the task and normal GitHub development workflow. Historical migration docs are **not installation instructions**.
