# qinglong-backup

青龙面板数据备份脚本：把 `/ql/data` 打包上传到**阿里云盘**，本地只留最新一份，云端按天数自动清理。

> 本项目针对 [whyour/qinglong](https://github.com/whyour/qinglong) 的目录结构进行了适配。

## 特性

- **递归排除目录**：任意层级的 `node_modules` 都能排掉
- 排除写法三种：目录名 `node_modules`、路径 `scripts/debug`、前缀通配 `scripts/data_*`
- 分隔符兼容中英文（英文逗号 / 空格 / 分号 + 中文逗号、顿号、分号）
- **本地**：压缩前清光旧包，上传成功后只留最新那一个
- **云端**：按天数保留（默认 30 天），列云盘目录取 `file_id` 精确删除
- 备份目录自身自动排除（不会把历史包打进新包）；云盘目录不存在会自动创建

## 安装

1. 装依赖：面板「依赖管理」→ Python3 → 添加 `aligo`
2. 把 `qinglong_backup.py` 放进 `/ql/data/scripts/`
3. 变量追加到 `/ql/data/config/config.sh`（清单见脚本头部注释）
4. 新建定时任务并每4天13点半备份：命令 `task qinglong_backup.py`，规则 `30 13 */4 * *`
5. 手动跑一次，日志里打印的链接用**阿里云盘 App** 扫码登录

## 变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `QLBK_PREFIX` | `qinglong` | 备份文件名前缀 |
| `QLBK_KEEP_DAYS` | `30` | 云端旧备份保留天数，`<=0` 表示不清理云端 |
| `QLBK_BACKUPS_PATH` | `backups` | 备份目录（本地与云盘同名），支持多级如 `backups/ql` |
| `QLBK_KEEP_LOCAL` | `1` | `1`=上传成功后本地只留最新那一个；`0`=上传成功后本地不留包 |
| `QLBK_MAX_FLIES` | `0` | 云端数量上限兜底，`0`=不限制 |
| `QLBK_EXTRA_EXCLUDE` | 空 | 追加排除（推荐） |
| `QLBK_EXCLUDE_NAMES` | 空 | 覆盖默认排除名单（慎用，留空=用默认） |

默认排除名单：`log .git .github node_modules backups .pnpm-store .cache tmp dep_cache syslog`
（备份路径的每一级也会自动加入排除）

## 排除示例

```bash
# 目录名：任意层级同名目录都排除
export QLBK_EXTRA_EXCLUDE="node_modules,debug"

# 路径：只排除该路径（相对 /ql/data）
export QLBK_EXTRA_EXCLUDE="scripts/debug,repo/abc"

# 前缀通配：结尾 * 表示前缀匹配
export QLBK_EXTRA_EXCLUDE="scripts/data_*,chrome_*"

# 分隔符随便用，中文标点也认
export QLBK_EXTRA_EXCLUDE="scripts/data_*、scripts/debug"
```

## 注意

- 变量写 `config.sh`，**不要**写面板「环境变量」：普通 `task xxx` 任务不会注入它们，而且同名变量会把 `config.sh` 里的值清掉
- 登录态在 `/home/qinglong/.aligo/aligo.json`，重建容器 / 恢复备份后需要重新扫码
- 目录被排除后不会进包，恢复时该目录也不会回来（`node_modules`、依赖缓存这类需重装）
- 登录二维码链路默认走公共 API；如需**自建**（不依赖第三方），可用 [i207M/qr-code-worker](https://github.com/i207M/qr-code-worker) 部署，再把脚本 `show()` 里的链接换成你自己的域名

## 更新日志

**v4.0**
- 递归排除，支持目录名 / 相对路径 / 前缀通配；分隔符兼容中英文标点
- 本地策略：压缩前清光旧包，上传成功后只留最新那一个
- 云端策略：按天保留，列云盘目录取 `file_id` 精确删除
- 备份目录自身自动排除；云盘目录缺失自动创建

## License

[MIT](LICENSE)
