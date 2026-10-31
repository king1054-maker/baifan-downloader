# 白饭下载器 · AI 调用说明

> 版本 @VERSION@ ｜ 接口地址 `http://127.0.0.1:@PORT@`
> 本文就是给 AI 看的：读完可以直接写代码，不需要再问人。

## 0. 三句话版本

1. 读 token：`@TOKEN_FILE@`
2. 建任务：`POST /api/v1/tasks`，body 里放 `urls` 数组（中文路径直接写，别转义）
3. 等结果：`GET /api/v1/wait/<gid>` 会阻塞到下载结束并返回完整性结论，`verify.ok == true` 才算真的下好了

---

## 1. 认证

每个请求带一个头：

```
X-Auth-Token: <token>
```

token 存在 `@TOKEN_FILE@`（纯文本，24 位以上）。
也支持 `?token=xxx` 或 `Authorization: Bearer xxx`，但推荐用头。

`GET /health` 不需要 token（存活检查）。

---

## 2. 一次下多个文件（这是重点）

**不要循环发请求。** 一个请求里塞多个链接就行。

### 2.1 结构化写法（最稳，推荐）

```json
POST /api/v1/tasks
{
  "urls": [
    "http://example.com/a.mkv",
    "http://example.com/b.zip",
    "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567"
  ],
  "dir": "@DOWNLOAD_DIR@"
}
```

返回：

```json
{ "ok": true, "count": 3, "gid": "第一个任务的id", "gids": ["id1", "id2", "id3"] }
```

- `count` = 这次真正建出来的任务数
- `gids` = 全部任务 id，后面逐个 wait 用
- 重复链接会自动去重
- 所有链接共用同一套参数（dir / 限速 / 是否暂停）

### 2.2 纯文本写法（用户给你一段话的时候用）

```json
POST /api/v1/tasks
{
  "text": "http://example.com/a.mkv https://pan.example.com/s/xyz\nmagnet:?xt=urn:btih:...",
  "dir": "@DOWNLOAD_DIR@"
}
```

服务端拆分规则（很重要，别自作聪明在客户端先切）：

| 分隔符 | 是否算 |
|---|---|
| 1 个及以上空格 | 算 |
| 换行 `\n`、回车 `\r\n` | 算 |
| 制表符 `\t` | 算 |
| 中文标点（，。）紧跟链接 | 自动剥掉，不算链接的一部分 |

然后逐条识别：`magnet:?xt=urn:btih:...`、`http(s)://`、`ftp://`、以及本机磁盘上的 `X:\...\xxx.torrent`。
认不出来的片段会被忽略，不会报错中断。

**所以用户粘一整段「复制打开抖音…」或者「资源：magnet:?xt=… 提取码 1234」的文案，直接原样丢进 text 就行。**

### 2.3 `urls` 里的字符串也会再切一次

```json
{ "urls": ["http://a/1.mkv http://a/2.mkv"] }
```

等价于两个任务。所以从别处拿到的「空格分隔的一长串」可以直接塞进来。

---

## 3. 参数表（POST /api/v1/tasks）

| 字段 | 类型 | 说明 |
|---|---|---|
| `urls` | string[] 或 string | 链接列表；字符串会按空白再切一次 |
| `text` | string | 整段文本，按空白切分后识别 |
| `torrent_base64` | string | .torrent 文件内容的 base64 |
| `dir` | string | 保存目录，不存在会自动创建。省略则用程序默认目录 |
| `pause` | bool | true = 建好但先不开始 |
| `download_limit` | int | 本任务下载限速，Byte/s，0 = 不限 |
| `upload_limit` | int | 本任务上传限速，Byte/s |
| `memo` | string | 备注，只在**建出 1 个任务**时生效（批量时每碗饭不一样，别硬套） |
| `options` | object | 透传给 aria2 的额外选项，例如 `{"select-file": "1,3"}` |

---

## 4. 等下载 + 验收

```
GET /api/v1/wait/<gid>?timeout=1800
```

会**阻塞**到任务结束（或超时），返回里带 `task.verify`：

| 字段 | 含义 |
|---|---|
| `verify.ok` | true = 文件完整；false = 不完整（截断、损坏、大小不符） |
| `verify.bad_count` | 有问题的文件数 |
| `verify.files` | 每个文件的结论明细 |

**只有 `verify.ok == true` 才能告诉用户「下好了」。** 下载进度 100% 不等于文件完整——这是这个程序存在的主要理由。

批量的时候对每个 gid 并发调 wait（或者串行，看你的时间预算），全部 `ok` 才交付。

其他常用：

```
GET    /api/v1/tasks                  # 列表，?status=all|active|waiting|stopped&verify=1
GET    /api/v1/tasks/<gid>            # 单任务详情
POST   /api/v1/tasks/<gid>/pause
POST   /api/v1/tasks/<gid>/resume
POST   /api/v1/tasks/<gid>/verify     # 强制重算完整性
DELETE /api/v1/tasks/<gid>?files=1    # 删任务，files=1 连文件一起（进回收站）
POST   /api/v1/tasks/<gid>/memo  {"text": "芙莉莲 第 8 集"}   # 写备注
POST   /api/v1/limit  {"download_kb": 2048, "upload_kb": 512}
GET    /api/v1/status                 # 引擎版本、全局速度、计数
```

---

## 5. 备注（文件名看不懂时的解药）

用户常遇到的情况：文件名是 `[VCB-Studio] Sousou no Frieren [08][Ma10p_2160p][x265_FLAC].mkv`，
或者一串纯日文假名，看完不知道这是啥。**不要改文件名**（做种、做种校验、后续播放都可能依赖它），
写一条备注就好——备注会直接显示在界面任务行的「备注」列里，用户一眼能看懂。

### 写 / 改 / 清

```
POST /api/v1/tasks/<gid>/memo
{ "text": "芙莉莲 第 8 集 4K" }
```

- `text` 为空串 `""` = 清除备注
- 备注会被压成一行、最多 120 字（换行会变空格）
- 只存在本机 `data/tasks.json`，不上传、不改文件、不影响下载和校验

### 读

`GET /api/v1/tasks` 和 `GET /api/v1/tasks/<gid>` 的每个任务里都有 `"memo"` 字段（没有就是 `""`）。

### 什么时候该写

| 场景 | 建议备注 |
|---|---|
| 番剧 / 电影 | 中文名 + 集数，例如「芙莉莲 第 8 集」 |
| 一堆同系列文件 | 系列名 + 序号，例如「XX 教程 03」 |
| 看不懂的压缩包 | 里面大概是什么，例如「显卡驱动 551.86」 |
| 用户自己交代过用途 | 原话照抄，例如「留着剪视频用」 |

**别自作聪明把猜测当事实写进去**（比如把 `S01E03` 猜成具体剧情），写不准就不写。

---

## 6. 可直接跑的示例

### Python

```python
import json, urllib.request, pathlib

PORT = @PORT@
TOKEN = pathlib.Path(r"@TOKEN_FILE@").read_text(encoding="utf-8").strip()

def call(path, payload=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}{path}",
        data=None if payload is None else json.dumps(payload).encode(),
        headers={"X-Auth-Token": TOKEN, "Content-Type": "application/json"},
        method="GET" if payload is None else "POST")
    return json.load(urllib.request.urlopen(req, timeout=3600))

# 批量：一段文本里三个链接，空格和换行混着
r = call("/api/v1/tasks", {
    "text": "https://a.com/1.mkv https://a.com/2.mkv\nmagnet:?xt=urn:btih:...",
    "dir": "@DOWNLOAD_DIR@"})
print("建了", r["count"], "个任务")

for gid in r["gids"]:
    w = call(f"/api/v1/wait/{gid}?timeout=1800")
    print(gid, w["task"]["status"], (w["task"].get("verify") or {}).get("ok"))
    # 下完之后给看不懂的文件名写一句人话（不满意就改成你自己的判断）
    if (w["task"].get("verify") or {}).get("ok"):
        call(f"/api/v1/tasks/{gid}/memo", {"text": "某番 第 3 集"})
```

### curl

```bash
TOKEN=$(cat "@TOKEN_FILE@")
curl -s -X POST "http://127.0.0.1:@PORT@/api/v1/tasks" \
  -H "X-Auth-Token: $TOKEN" -H "Content-Type: application/json" \
  -d '{"urls":["http://a/1.mkv","http://a/2.mkv"],"dir":"@DOWNLOAD_DIR@"}'
```

---

## 7. 状态与字段对照

- `status`：`active`（下载中）/ `waiting`（排队）/ `paused` / `complete` / `error` / `removed`
- `kind`：`video` / `image` / `audio` / `folder` / `exec` / `archive` / `other`
- 速度单位一律 Byte/s；`percent` 为 0–100
- `path`：单文件任务指向文件；`kind == "folder"` 的多文件任务指向任务根目录
- `memo`：备注，可能为空串；写它用 `POST /api/v1/tasks/<gid>/memo`
- `is_metadata`：**磁力链接的"先下种子"阶段**。这种任务只有几十 KB，`complete` 得很快，
  但它不是你要的文件 —— 真正的下载任务会在它结束后自动出现（另一个 gid）。
  轮询时**不要把 `is_metadata == true` 的 `complete` 当成下载完成**，等新任务出现再说。
- 本程序的界面用语是「饭」：干饭中 = 下载中，吃饱了 = 完成。仅仅是文案，字段值仍是上面这些英文。

## 8. 常见错误

| 返回 | 原因 |
|---|---|
| `401 unauthorized` | 少了或写错 `X-Auth-Token` |
| `400 bad_request` | `urls` / `text` / `torrent_base64` 一个都没有 |
| `404 not_found` | 路径写错，或 gid 已被删除 |
| wait 里 `ok=false, done=true` | 任务结束了但出错，看 `task.error` |

