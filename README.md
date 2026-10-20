# diarysync

把 Garmin 运动记录和本机 agent 工作记录写进 Obsidian 日记
（`diary/YYYY-MM-DD.md`），重复运行不会写出重复行。既可以作为命令行工具，
也可以作为 Python 库调用。

```markdown
# 打卡

- [x] 运动

# 日程

- [x] 16:14 - 16:47 #运动 跑步 基础训练
- [x] 13:53 - 14:36 #科研 分析实验数据
```

## 安装

离线 CSV 和工作记录需要 Python ≥ 3.9；在线同步 Garmin 需要 Python ≥ 3.12，
因为上游 `garminconnect` 要求该版本。

```bash
cd /path/to/diarysync
python3 -m pip install --user .                 # 基础功能

uv venv --python 3.12 .venv                     # 开发安装
uv pip install -e ".[garmin,sessions,dev]"
.venv/bin/python -m diarysync doctor
```

`garmin` extra 提供在线同步，`sessions` extra 提供 zstd 读取能力。只用离线 CSV
时可以执行 `pip install -e .`。

## 快速开始

```bash
export DIARYSYNC_GARMIN_EMAIL='you@example.com'
export DIARYSYNC_GARMIN_PASSWORD='...'

python3 -m diarysync login    --vault /path/to/vault
python3 -m diarysync sync     --vault /path/to/vault --days 30
python3 -m diarysync exercise --csv ~/Downloads/Activities.csv --vault /path/to/vault
```

`--vault` 省略时会从当前目录向上寻找含 `diary/` 或 `.obsidian/` 的目录，
找不到就报错，不会把日记写进当前目录。可以用 `DIARYSYNC_VAULT` 指定。

## 命令

```bash
python3 -m diarysync exercise [--csv PATH] [--since DATE] [--until DATE] [--days N]
python3 -m diarysync work     [--source all|claude|codex|dsh] [--min-minutes N]
                              [--summarizer CMD | --no-summarizer]
python3 -m diarysync sync     [--work-only | --exercise-only]
python3 -m diarysync login
python3 -m diarysync ledger   [--forget KEY...]
python3 -m diarysync doctor
```

`exercise`、`work`、`sync` 支持 `--vault`、`--dry-run`、`--force`、`--json`
和 `--overlap-ratio`。`work` 与 `sync` 还支持 `--summarizer CMD` 和
`--no-summarizer`。

## Garmin 认证

凭据优先级从高到低依次为命令行、环境变量、配置文件。都不提供时会交互式输入密码。
登录成功后 token 缓存在 `~/.garminconnect`（权限 0600），可用 `--token-store`
修改位置。默认连接中国站 `connect.garmin.cn`，国际站加 `--global-account`。
账号需要 MFA 时会在终端提示输入验证码。

网络、验证码或风控导致登录失败时，改用离线 CSV：

1. 打开 <https://connect.garmin.cn/app/activities>。
2. 导出 `Activities.csv`。
3. 运行 `python3 -m diarysync exercise --csv ~/Downloads/Activities.csv`。

CSV 和在线 API 产出的记录可以互相去重。

## 去重

三层检查，任意一层命中就跳过：

1. **Ledger**：写入成功的记录记进 `<vault>/.diarysync/ledger.json`，
   重复运行或更换时间窗口都不会重复写。
2. **整行相同**：待写入行与已有日程行完全一致。
3. **时间窗重叠**：同一类别、同一 tag 的记录，较短窗口的重叠比例达到
   `overlap_ratio`（默认 0.6）时视为重复。工作记录还有一条孪生规则：
   tag 不同但窗口重叠 ≥ 90% 且较长与较短时长之比 ≤ 1.6 时，也视为重复。

`#运动` 与工作记录互不抑制。只要某天有运动记录，就确保存在
`- [x] 运动` 打卡行。

```bash
python3 -m diarysync sync --dry-run                 # 只预览
python3 -m diarysync sync --force                   # 忽略全部去重
python3 -m diarysync sync --overlap-ratio 0.9       # 放宽重复判定
python3 -m diarysync ledger                         # 查看身份键
```

## 工作记录

从三种本地 transcript 读取：

| 来源 | 路径 |
| --- | --- |
| `claude` | `~/.claude/projects/<slug>/<session-uuid>.jsonl` |
| `codex` | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` |
| `dsh` | `~/.dsh/sessions/<cwd>/session-*/session.jsonl.zstd` |

路径可以分别用 `CLAUDE_CONFIG_DIR`、`CODEX_HOME`、`DSH_HOME` 覆盖。
只保留人真正输入的 prompt；子 agent、注入内容、工具结果和后台通知会被丢弃。

每个（来源, session, 本地日期）生成一段记录，时间取当天事件的最小值和最大值。
摘要默认调用本机 `claude` CLI 生成：把该段的用户请求、agent 文本回复和工具调用
（文件、命令等）一起交给模型，输出一行中文摘要。没有配置命令且找不到 `claude`
时，或者摘要命令执行失败时，退回第一条用户输入。摘要相同且时间窗重叠的条目会合并。

换用其他摘要命令：

```bash
python3 -m diarysync work --summarizer "llm -m gpt-4o-mini"
```

命令从 stdin 读取完整工作记录，向 stdout 输出一行摘要。写入
`<vault>/.diarysync/config.toml` 的 `summarizer_cmd` 同样生效；
`--no-summarizer` 关闭自动摘要。摘要按上下文哈希缓存在
`<vault>/.diarysync/summary_cache.json`，重复运行不会重新调用模型，
`--dry-run` 不写缓存；ledger 里已有的记录不会再次调用摘要命令。启用摘要后，
上述工作记录会发送给摘要命令对应的服务。

`#tag` 只从 vault 里已有的 inline tag 中选择：prompt 直接提到的 tag 优先；
否则走关键词规则；都不命中时使用出现次数最多的非运动 tag。vault 里没有 tag
时，该行不写 tag。扫描会跳过代码块和隐藏目录。

## 日记文件

- 只修改 `# 日程` 和 `# 打卡` 两个一级标题下的内容，其他内容保持不变。
- `# 日程` 里按起始时间插入新行。
- 缺少段落时在文件末尾创建；新文件按 `# 打卡`、`# 日程` 的顺序创建。
- Frontmatter 和 `## ` 二级标题保持原样。

## 配置

把 [`config.example.toml`](config.example.toml) 复制到
`<vault>/.diarysync/config.toml` 或 `~/.config/diarysync/config.toml`：

```toml
diary_dir = "diary"
garmin_email = "you@example.com"
# garmin_password = "..."
garmin_is_cn = true
token_store = "~/.garminconnect"
overlap_ratio = 0.6
use_ledger = true
checkin_label = "运动"
complete_unchecked_checkin = true
# summarizer_cmd = "llm -m gpt-4o-mini"   # 留空时自动使用 claude
```

## Python 库

```python
import diarysync

sync = diarysync.DiarySync(vault="/path/to/vault")
report = sync.sync(since="2026-01-01")     # 运动 + 工作
print(report.inserted, report.skipped)

activities = sync.activities(days=7)       # 只取数据，不写日记
entries = sync.work_entries(days=7, source="claude")
```

`DiarySync(vault=..., auto_summarize=False)` 可以关闭自动摘要。

需要自己拼装流水线时，可以使用 `load_settings`、`collect_activities`、
`collect_work`、`run`、`DedupPolicy` 等顶层函数。`DiarySync` 构造时没有副作用，
凭据只在真正联网时读取。

## 定时运行

```bash
# 每天 23:50 同步最近 7 天
50 23 * * * cd /path/to/vault && DIARYSYNC_GARMIN_PASSWORD='...' \
  /path/to/venv/bin/diarysync sync --days 7 >> /path/to/diarysync.log 2>&1
```

先用 `--dry-run --days 7` 确认输出，再交给定时任务。

## 已知限制

- Garmin 没有公开 API，`garminconnect` 是社区实现；接口变化或风控会导致
  在线同步失败，此时使用 `--csv`。
- 工作摘要来自本地规则；需要更好的摘要请配置 `--summarizer`。
- 时间窗去重是启发式规则，`--dry-run` 是确认结果的可靠方式。

## License

MIT，见 [LICENSE](LICENSE)。
