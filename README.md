# diarysync

把 **Garmin 运动记录** 和 **本机 agent 工作记录** 自动写进 Obsidian 日记
（`diary/YYYY-MM-DD.md`），并且保证 **同一条记录不会被写两次**。

既是命令行工具，也是一个可以 `import` 的 Python 库。

它复刻的是这样一件手工活：从 <https://connect.garmin.cn/app/activities> 点
「导出为csv文献」，把 `Activities.csv` 里的每一条活动按

```markdown
# 打卡

- [x] 运动

# 日程

- [x] 16:14 - 16:47 #运动 跑步 衢州市 - 基础训练
```

写进对应日期的日记；同时把 `~/.codex`、`~/.dsh` 里的会话历史整理成

```markdown
- [x] 13:53 - 14:36 #科研 对照原始三狼代码，核查两狼 RS 评估与 shared/individ checkpoint
```

## 安装

分两种用法，按需选择：

| 用法 | 需要的 Python | 能做什么 |
| --- | --- | --- |
| 库 + CLI + 离线 CSV + 工作记录 | ≥ 3.9 | 全部功能，除了在线拉 Garmin |
| 在线同步 Garmin | ≥ 3.12 | 多出 `--csv` 免手工的在线路径 |

在线同步的上限来自上游 `garminconnect` 要求 Python ≥ 3.12，不是本项目的要求。

### 装到当前 Python（`python3 -m diarysync` 直接可用）

```bash
cd ~/Documents/research/projects/diarysync
python3 -m pip install --user .
python3 -m diarysync doctor
```

macOS 上 `pip install --user` 会把脚本放进
`$(python3 -m site --user-base)/bin`，该目录默认不在 `PATH`；
用 `python3 -m diarysync ...` 就不受影响（`diarysync ...` 也可以，前提是 PATH 配好）。

### 开发安装（含在线同步与测试）

```bash
cd ~/Documents/research/projects/diarysync

# 推荐：uv 会自动准备 Python 3.12
uv venv --python 3.12 .venv
uv pip install -e ".[garmin,sessions,dev]"
.venv/bin/python -m diarysync doctor

# 或者 pip
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[garmin,sessions]"
```

只要离线 CSV 与工作记录时，可以省掉 `garmin` extra：

```bash
pip install -e .
```

## 认证：Garmin 中国站

代码走的是和手机 App 相同的 SSO 流程，**不需要浏览器**，也**不需要手动导出 CSV**。

```bash
# 1) 先用参数把账号密码传进去（推荐用环境变量，避免进入 shell history）
export DIARYSYNC_GARMIN_EMAIL='you@example.com'
export DIARYSYNC_GARMIN_PASSWORD='********'

# 2) 确认能登录（会缓存 token 到 ~/.garminconnect）
python3 -m diarysync login --vault /path/to/vault

# 3) 同步最近 30 天的运动
python3 -m diarysync exercise --vault /path/to/vault
```

三种传参方式，优先级从高到低：

| 方式 | 写法 |
| --- | --- |
| 命令行 | `--email you@example.com --password '***'` |
| 环境变量 | `DIARYSYNC_GARMIN_EMAIL` / `DIARYSYNC_GARMIN_PASSWORD` |
| 配置文件 | `<vault>/.diarysync/config.toml` 里的 `garmin_email` / `garmin_password` |

都不提供时，会交互式 `getpass` 提示输入密码。登录成功后 token 存在
`~/.garminconnect/garmin_tokens.json`（权限 0600），后续运行不必再输密码。
用 `--token-store` 可以改位置。账号需要 MFA 时，在交互终端里会提示输入验证码。

默认连中国站 `connect.garmin.cn`；国际站加 `--global-account`。

### 登录失败时：离线 CSV 兜底

如果网络、验证码或 Garmin 风控导致登录失败，就沿用原来的手工路径：

1. 打开 <https://connect.garmin.cn/app/activities>
2. 点「导出为csv文献」，下载 `Activities.csv`
3. 运行：

```bash
python3 -m diarysync exercise --csv ~/Downloads/Activities.csv --vault /path/to/vault
```

CSV 路径和在线路径产出完全相同的记录，两条路径互为备份，去重逻辑也互相认得
（同一条活动无论来自 API 还是 CSV，都只会写一次）。

## 去重：同一条记录不会写两次

这是本工具的核心，分三层，任何一层命中就跳过：

1. **Ledger（跨运行幂等）** — 每次写入都会把记录的身份键记到
   `<vault>/.diarysync/ledger.json`。重复运行、换窗口重跑，都不会重复写。
2. **整行比对** — 已存在的日程行与待写入行完全相同则跳过。
3. **时间窗重叠** — 对 `#运动` 行做重叠度计算，用较短的窗口做分母：

   ```
   overlap = 交集分钟数 / min(新记录时长, 已有行时长)
   overlap >= overlap_ratio（默认 0.6）  =>  视为同一条，跳过
   ```

   这一层专门对付**手写记录**。你手写过
   `- [x] 14:30 - 16:00 #运动 有氧`，Garmin 里的 `14:42 - 15:36 跑步机`
   会被判定为同一条而跳过；而同一天上午 `11:08-11:15` 和下午 `18:59-19:10`
   两段互不重叠，都会保留。

工作记录同样走第 1、2 层，第 3 层则有两档：

- **同 tag 重叠**：已有一行 `#科研 13:53 - 14:36 …`，新记录也是 `#科研`
  且窗口覆盖它 → 跳过。
- **窗口孪生**：tag 不同（人写 `#审查`、规则猜成 `#科研`）但窗口几乎重合
  （重叠 ≥ 90% 且时长比 ≤ 1.6）→ 也跳过。时长比这一条是必要的，否则
  `- [x] 08:35 - 23:59 #实验室工作 huawei` 这种整日块会把里面每一段短会话
  全部吞掉。

**运动与工作互不抑制**：`#科研` 的会话和同一时段的跑步是两条记录，都会保留。

`# 打卡` 的处理是另一个维度：只要某一天有运动记录，就确保存在 `- [x] 运动`;
已经存在（含 `- [ ] 运动` 未勾选）就只做勾选，不会重复添加一行。

想要覆盖去重时：

```bash
python3 -m diarysync exercise --dry-run        # 先看会写什么
python3 -m diarysync exercise --force          # 忽略全部去重规则
python3 -m diarysync exercise --overlap-ratio 0.9   # 放宽"算作同一条"的门槛
python3 -m diarysync ledger                    # 查看已记录的身份键
```

## 命令一览

```bash
python3 -m diarysync exercise [--csv PATH] [--since 2026-08-01] [--until 2026-09-16] [--days 30]
python3 -m diarysync work     [--source all|codex|dsh] [--since ...] [--min-minutes 5]
python3 -m diarysync sync     [--work-only | --exercise-only]
python3 -m diarysync login
python3 -m diarysync ledger   [--forget KEY...]
python3 -m diarysync doctor
```

公共开关：`--vault`、`--dry-run`、`--force`、`--json`、`--overlap-ratio`。

`--vault` 省略时会从当前目录向上找第一个含 `diary/` 的目录。

典型输出：

```
=== 新增 3 条 ===
  2026-09-14  - [x] 19:46 - 20:19 #运动 跑步 杭州市 - 基础训练
=== 跳过 2 条（去重） ===
  2026-09-12  - [x] 18:02 - 18:41 #运动 跑步机 基础训练
      原因: time window already covered by existing line (100% overlap): - [x] 18:00 - 18:45 #运动 有氧
=== 补充运动打卡 ===
  2026-09-14  - [x] 运动
=== 写入文件 ===
  /vault/diary/2026-09-14.md（新建）
```

## 作为 Python 库使用

命令行里的每一步都是可调用的 API。最省事的是 `DiarySync` 门面：

```python
import diarysync

sync = diarysync.DiarySync(
    vault="/path/to/vault",
    email="you@example.com",     # 也可以走 DIARYSYNC_GARMIN_EMAIL / 配置文件
    password="***",              # 或者 prompt_for_password=True 交互输入
    token_store="~/.garminconnect",
)

report = sync.sync(since="2026-09-01")          # 运动 + 工作，一次写进去
print(len(report.inserted), "新增", len(report.skipped), "去重跳过")

for record in report.inserted:
    print(record.day, record.schedule_line())
```

常用变体：

```python
sync.exercise(days=7)                            # 最近 7 天运动
sync.exercise(csv="~/Downloads/Activities.csv")  # 离线，不需要登录
sync.work(since="2026-08-25", source="codex", min_minutes=5)
sync.login()                                     # 只验证凭据
sync.activities(since="2026-09-01", until="2026-09-16")   # 只取数据，不写日记
sync.work_entries(since="2026-09-01", source="dsh")       # ditto

dry = diarysync.DiarySync(vault="/vault", dry_run=True)   # 构造带默认值
loud = dry.configure(force=True)                          # 返回副本，不改原对象
```

`DiarySync.open()` 会从当前工作目录向上找 vault。构造对象本身没有任何副作用
（不会弹密码框），凭据只在真正需要联网时才解析。

想自己拼流水线的话，底层零件都在顶层导出：

```python
from pathlib import Path

import diarysync

settings = diarysync.load_settings(vault="/path/to/vault")

records = diarysync.collect_activities(settings, since="2026-09-01", until="2026-09-16")
records += diarysync.collect_work(settings, since="2026-09-01", until="2026-09-16",
                                  source="all", min_minutes=5)

# 只看会写什么
report = diarysync.run(records, settings, dry_run=True)

# 自己决定去重口径
policy = diarysync.DedupPolicy(overlap_ratio=0.8, use_ledger=False)
diary = diarysync.Diary.load(Path("/path/to/vault/diary/2026-09-14.md"))
decision = diarysync.decide(records[0], diary, diarysync.Ledger(settings.ledger_path), policy)
print(decision.action, decision.reason)
```

顶层可用的名字：

| 名字 | 用途 |
| --- | --- |
| `DiarySync` | 门面：`activities` / `work_entries` / `exercise` / `work` / `sync` / `apply` / `login` |
| `collect_activities` / `collect_work` | 只收集记录，不写文件 |
| `run` / `SyncReport` | 写入并拿到 `inserted` / `skipped` / `files_written` / `checkins_added` |
| `load_settings` / `Settings` / `find_vault` | 解析配置（含 token store、ledger 路径、overlap_ratio） |
| `Activity` / `WorkEntry` / `Record` | 记录值对象，都有 `.day` / `.schedule_line()` / `.identity_keys()` |
| `Diary` / `Ledger` / `DedupPolicy` / `decide` | 单独使用日记写入与去重规则 |
| `load_csv` / `GarminAuthError` / `CsvFormatError` | 离线 CSV 与异常类型 |
| `parse_day` / `resolve_window` | 日期与窗口换算 |

`collect_work` 的摘要规则可以用 `build_summarizer(cmd)` 换成外部 LLM：

```python
entries = diarysync.collect_work(
    settings, since="2026-09-01", until="2026-09-16",
    summarizer=diarysync.build_summarizer("llm -m gpt-4o-mini '压缩成一句中文摘要'"),
)
```

## 工作记录从哪里来

`python3 -m diarysync work` 读取本机 agent 会话，按 **（来源, 会话, 本地日期）** 切分成时间段：

| 来源 | 路径 | 说明 |
| --- | --- | --- |
| `codex` | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` | 逐行 JSON |
| `dsh` | `~/.dsh/sessions/<cwd>/session-*/session.jsonl.zstd` | zstd 压缩，需要 `sessions` extra 或系统 `zstd` |

**只保留人真正驱动的会话。** Codex 在每条 rollout 的 `session_meta` 里记了
`thread_source`：`subagent`（子 agent）、`guardian_review`（自动审查）会被整条丢弃，
因为它们的 prompt 是模板化的（"You are a Senior Code Reviewer …"），
写进日记只会盖住真实工作。没有该字段的旧 transcript 会退回文本规则，
继续过滤审查模板、`<system-reminder>`、`TRANSCRIPT START` 这类注入内容。

每一段的起止时间取当天事件时间戳的最小/最大值，摘要取该段第一条真实用户输入
（如果 prompt 里有 `## goal` 段就优先取它，短的章节标题如 "Purpose" 会被跳过
而取下面的正文），`#tag` 由关键词规则推理：

`#服务器`、`#故障`、`#审查`、`#配置`、`#文档`、`#科研`、`#开发`、`#整理`，兜底 `#工作`。

摘要相同且时间窗互相包含的条目会合并成一条（同一会话派生的多个子进程不至于
在日记里炸成十几行）。

想要更聪明的摘要，可以外挂一个 LLM：

```bash
python3 -m diarysync work --summarizer "llm -m gpt-4o-mini '把下面这段话压缩成一句中文工作摘要'"
# 或写进 <vault>/.diarysync/config.toml: summarizer_cmd = "..."
```

命令从 stdin 收到原文，向 stdout 输出一行摘要；失败会退回内置规则。

## 日记文件怎么被修改

- 只碰 `# 日程` 和 `# 打卡` 两个一级标题段，其它内容逐字节不动。
- `# 日程` 里按时间插入新行：插到第一条起始时间更晚的行之前，
  没有时间信息的旧行保持原位置。
- 段不存在时在文件末尾新建；文件不存在时按 `# 打卡` → `# 日程` 的顺序新建。
- Frontmatter（`--- weather: 晴 ---`）永远保留在最前面。
- `## 组会记录` 这类二级标题不会被当成段落边界。

## 配置文件

项目里带了模板 [`config.example.toml`](config.example.toml)，复制到
`<vault>/.diarysync/config.toml`（或 `~/.config/diarysync/config.toml`）后按需修改：

```toml
diary_dir     = "diary"          # 相对 vault 或绝对路径
garmin_email  = "you@example.com"
# garmin_password = "..."        # 不推荐，优先用环境变量
garmin_is_cn  = true
token_store   = "~/.garminconnect"
overlap_ratio = 0.6
checkin_label = "运动"
complete_unchecked_checkin = true
work_sources  = ["codex", "dsh"]
# summarizer_cmd = "llm -m gpt-4o-mini '压缩成一句中文摘要'"
```

## 定时运行

```bash
# crontab -e，每天 23:50 同步
50 23 * * * cd /path/to/vault && DIARYSYNC_GARMIN_PASSWORD='***' \
  /path/to/venv/bin/diarysync sync --days 7 >> /tmp/diarysync.log 2>&1
```

建议先用 `--days 7` 的小窗口 + `--dry-run` 观察一轮输出，确认无误再交给定时任务。

## 测试

```bash
pytest -q
```

## 已知限制

- Garmin 官方没有公开 API，`garminconnect` 是社区逆向实现；Garmin 改接口或
  触发风控时在线同步会失败，此时用 `--csv` 离线路径。
- `python3 -m diarysync work` 的摘要来自本地规则，不是真正的语义理解；需要质量更高的
  摘要请配置 `--summarizer`。
- 时间窗重叠判断是启发式的。`overlap_ratio` 调小会更激进地去重（可能漏记），
  调大会更容易产生重复；`--dry-run` 是唯一可靠的验证方式。

## License

MIT — 见 [LICENSE](LICENSE)。
