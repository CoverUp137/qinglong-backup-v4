#!/usr/bin/env python3
# coding: utf-8
'''
qinglong-backup v4.0 —— 青龙面板备份到阿里云盘

行为：
  1. 压缩前清光本地旧包；上传成功后本地只留最新那一个
  2. 云端按天数保留（默认 30 天），列云盘目录取 file_id 精确删除
  3. 打包时递归排除指定目录，支持目录名 / 相对路径 / 前缀通配

变量（写在 /ql/data/config/config.sh，面板环境变量对本脚本不生效）：
  QLBK_PREFIX          备份文件名前缀，默认 qinglong
  QLBK_KEEP_DAYS       云端旧备份保留天数，默认 30；<=0 表示不清理云端
  QLBK_BACKUPS_PATH    备份目录（本地与云盘同名），默认 backups28，支持多级如 backups/ql
  QLBK_KEEP_LOCAL      本地留档：1=上传成功后只留最新那一个（默认）；0=上传成功后本地不留包
  QLBK_MAX_FLIES       云端数量上限兜底，默认 0（不限制）
  QLBK_EXTRA_EXCLUDE   追加排除（推荐用这个）。支持三种写法：
                         目录名  node_modules / debug        → 任意层级同名目录都排除
                         路径    scripts/debug / repo/abc    → 只排除该路径（相对 /ql/data）
                         通配    scripts/data_*              → 结尾 * 表示前缀匹配
                       分隔符：英文逗号、空格、分号、竖线，中文逗号、顿号、分号都可以
  QLBK_EXCLUDE_NAMES   覆盖默认排除名单（慎用，留空 = 使用默认名单）

默认排除名单：log .git .github node_modules backups .pnpm-store .cache tmp dep_cache syslog
              + 备份路径的每一级（如 QLBK_BACKUPS_PATH="backups/ql"，
                则 "backups" 与 "ql" 会自动排除，避免历史备份包被打进新包）

依赖：aligo          安装：pip3 install --prefix /ql/data/dep_cache/python3 aligo
任务命令：task qinglong_backup.py        定时规则：0 2 * * *
new Env('qinglong备份');
'''
import calendar
import logging
import os
import re
import sys
import tarfile
import time

from aligo import Aligo

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)
try:
    from notify import send
except Exception:
    send = None
    logger.info("无推送文件")

VERSION = '4.0'                          # 脚本版本


def env(key, default=None):
    v = os.environ.get(key)
    return default if v in (None, '') else v


def split_names(s):
    """分隔符：英文逗号/空格/分号/竖线 + 中文逗号、顿号、分号（v4 新增，兼容中文标点）"""
    return [n.strip().strip('/') for n in re.split(r'[,\s，、;；|]+', s) if n.strip()]


# ------------------------------------------------------------------ 参数
DEFAULT_EXCLUDE_NAMES = ['log', '.git', '.github', 'node_modules',
                         'backups', '.pnpm-store', '.cache', 'tmp', 'dep_cache',
                         'syslog']

# QLBK_EXCLUDE_NAMES：完全覆盖默认名单（老变量，慎用，写漏了会导致包变大）
if env("QLBK_EXCLUDE_NAMES"):
    QLBK_EXCLUDE_NAMES = split_names(env("QLBK_EXCLUDE_NAMES"))
    logger.info('检测到 QLBK_EXCLUDE_NAMES：覆盖默认排除名单')
else:
    QLBK_EXCLUDE_NAMES = list(DEFAULT_EXCLUDE_NAMES)

# QLBK_EXTRA_EXCLUDE：追加排除（推荐用这个）
#   目录名：node_modules、ddns          → 任意层级同名目录都排除
#   路径  ：scripts/ddns、repo/abc      → 只排除该路径（相对运行目录 /ql/data）
#   通配  ：data_*、*.tmp               → 尾号 * 表示前缀匹配
#   分隔符：英文逗号 / 中文逗号、顿号 / 分号 / 空格 都行
if env("QLBK_EXTRA_EXCLUDE"):
    _extra = split_names(env("QLBK_EXTRA_EXCLUDE"))
    QLBK_EXCLUDE_NAMES += [e for e in _extra if e not in QLBK_EXCLUDE_NAMES]
    logger.info(f'QLBK_EXTRA_EXCLUDE 追加排除: {_extra}')

QLBK_BACKUPS_PATH = env("QLBK_BACKUPS_PATH", 'backups28')      # 备份目录名（本地 & 云盘同名）
QLBK_KEEP_DAYS = int(env("QLBK_KEEP_DAYS", '30'))              # 云端旧备份保留天数，<=0 表示不清理云端
QLBK_MAX_FLIES = int(env("QLBK_MAX_FLIES", '0'))               # 云端数量上限兜底，0=不限制
QLBK_KEEP_LOCAL = str(env("QLBK_KEEP_LOCAL", '1')).lower()      # 1=上传后本地只留最新那一个(默认)；0=上传后本地不留
QLBK_PREFIX = env("QLBK_PREFIX", 'qinglong')                    # 备份文件名前缀

# 备份目录自身必须排除，否则会把历史 tar.gz 一起打包进新的包里
for _p in QLBK_BACKUPS_PATH.split('/'):
    if _p and _p not in QLBK_EXCLUDE_NAMES:
        QLBK_EXCLUDE_NAMES.append(_p)

# 不含 '/' 的按目录名匹配，含 '/' 的按路径匹配
NAME_EXCLUDES = [n for n in QLBK_EXCLUDE_NAMES if '/' not in n]
PATH_EXCLUDES = [n for n in QLBK_EXCLUDE_NAMES if '/' in n]

logger.info(f'qinglong-backup v{VERSION} | 备份目录: {QLBK_BACKUPS_PATH} | 云端保留: {QLBK_KEEP_DAYS} 天 | '
            f'云端数量上限: {QLBK_MAX_FLIES or "不限制"} | 本地: '
            + ('保留当次一份' if QLBK_KEEP_LOCAL in ('1', 'true', 'yes') else '上传后不留'))
logger.info(f'排除目录名: {NAME_EXCLUDES}')
if PATH_EXCLUDES:
    logger.info(f'排除路径  : {PATH_EXCLUDES}')

ali = None
run_path = '/'
_tar_prefix = ''


# ------------------------------------------------------------------ 压缩过滤
def _name_match(comp):
    """目录名匹配，支持尾部通配 data_*"""
    for n in NAME_EXCLUDES:
        if n == comp:
            return True
        if n.endswith('*') and comp.startswith(n[:-1]):
            return True
    return False


def _path_match(rel):
    """相对路径匹配，支持尾部通配 scripts/data_*"""
    for e in PATH_EXCLUDES:
        if e.endswith('*'):
            if rel.startswith(e[:-1]):
                return True
        elif rel == e or rel.startswith(e + '/'):
            return True
    return False


def _exclude_filter(tarinfo):
    """递归过滤：任意一层命中"目录名"，或相对路径命中"路径"，就跳过整棵子树"""
    name = tarinfo.name.replace('\\', '/')
    rel = name[len(_tar_prefix):] if (_tar_prefix and name.startswith(_tar_prefix)) else name
    rel = rel.strip('/')
    if not rel:
        return tarinfo

    for p in rel.split('/'):
        if _name_match(p):
            return None
    if _path_match(rel):
        return None
    return tarinfo


def make_targz(output_filename, retval):
    """压缩为 tar.gz，返回 bool"""
    global _tar_prefix
    _tar_prefix = retval.strip('/').rstrip('/') + '/'
    try:
        with tarfile.open(output_filename, "w:gz") as tar:
            for p in sorted(os.listdir(retval)):
                full = os.path.join(retval, p)
                if os.path.isdir(full) and _name_match(p):
                    logger.info(f'跳过排除目录: {p}/')
                    continue
                if p.endswith('.tar.gz'):
                    logger.info(f'跳过顶层压缩包: {p}')
                    continue
                tar.add(full, filter=_exclude_filter)
        return True
    except Exception as e:
        logger.info(f'压缩失败: {str(e)}')
        return False


# ------------------------------------------------------------------ 本地清理
def cleanup_local(backup_dir):
    """压缩前：删除本地全部旧备份包（无论 QLBK_KEEP_LOCAL 怎么配，旧包一律先清）"""
    olds = sorted(n for n in os.listdir(backup_dir) if n.endswith('.tar.gz'))
    for n in olds:
        fp = os.path.join(backup_dir, n)
        try:
            os.remove(fp)
            logger.info('已删除本地旧备份: %s' % fp)
        except Exception as e:
            logger.info(f'本地删除失败: {fp} ({e})')
    logger.info(f'本地旧备份清理完成，共删除 {len(olds)} 个')


# ------------------------------------------------------------------ 云端清理
_TS_RE = re.compile(r'_(\d{8})_(\d{6})\.tar\.gz$')


def cloud_backup_time(f):
    """云盘文件时间：优先文件名里的时间戳，其次 created_at / updated_at（UTC）"""
    m = _TS_RE.search(f.name)
    if m:
        try:
            return time.mktime(time.strptime(f'{m.group(1)}_{m.group(2)}', '%Y%m%d_%H%M%S'))
        except Exception:
            pass
    for attr in ('created_at', 'updated_at'):
        s = getattr(f, attr, None)
        if s:
            try:
                return calendar.timegm(time.strptime(s[:19], '%Y-%m-%dT%H:%M:%S'))
            except Exception:
                pass
    return time.time()


def list_cloud_backups(remote_folder):
    """列出云盘备份目录下的 tar.gz"""
    files = ali.get_file_list(parent_file_id=remote_folder.file_id)
    return [f for f in files if f.name.endswith('.tar.gz')]


def trash_cloud(f):
    try:
        ali.move_file_to_trash(file_id=f.file_id)
        logger.info('已删除云盘旧备份: %s' % f.name)
        return True
    except Exception as e:
        logger.info(f'云盘删除失败: {f.name} ({e})')
        return False


def cleanup_cloud(remote_folder):
    """按 QLBK_KEEP_DAYS 删云端旧备份；QLBK_MAX_FLIES>0 时再兜底限制数量"""
    if QLBK_KEEP_DAYS <= 0 and not QLBK_MAX_FLIES:
        logger.info('云端清理已关闭（QLBK_KEEP_DAYS<=0 且未设数量上限）')
        return

    try:
        backups = list_cloud_backups(remote_folder)
    except Exception as e:
        logger.info(f'读取云盘目录失败，跳过云端清理: {e}')
        return

    backups.sort(key=cloud_backup_time)
    logger.info(f'云盘现有备份 {len(backups)} 个')

    keep = []
    deadline = time.time() - QLBK_KEEP_DAYS * 86400
    for f in backups:
        if QLBK_KEEP_DAYS > 0 and cloud_backup_time(f) < deadline:
            logger.info(f'云盘备份已超过 {QLBK_KEEP_DAYS} 天，准备删除: {f.name}')
            trash_cloud(f)
        else:
            keep.append(f)

    if QLBK_MAX_FLIES and len(keep) > QLBK_MAX_FLIES:
        for f in keep[:len(keep) - QLBK_MAX_FLIES]:
            logger.info(f'超过云端数量上限 {QLBK_MAX_FLIES}，准备删除: {f.name}')
            trash_cloud(f)
        keep = keep[len(keep) - QLBK_MAX_FLIES:]

    logger.info(f'云端清理完成，保留 {len(keep)} 个'
                + (f'（{QLBK_KEEP_DAYS} 天内）' if QLBK_KEEP_DAYS > 0 else ''))


# ------------------------------------------------------------------ 主流程
def show(qr_link: str):
    """打印二维码链接"""
    logger.info('请手动复制以下链接，打开阿里网盘App扫描登录')
    logger.info(f'https://qr.010507.xyz/api/qrcode/code?text={qr_link}')


def start():
    """开始备份"""
    retval = os.getcwd()
    backup_dir = os.path.join(retval, QLBK_BACKUPS_PATH)
    os.makedirs(backup_dir, exist_ok=True)

    # ① 压缩前先删本地全部旧包
    cleanup_local(backup_dir)

    # ② 清云端超过保留天数的旧包
    remote_folder = ali.get_folder_by_path(QLBK_BACKUPS_PATH)
    if remote_folder is None:
        logger.info(f'云盘未找到目录 {QLBK_BACKUPS_PATH}，尝试创建...')
        remote_folder = ali.get_folder_by_path(QLBK_BACKUPS_PATH, create_folder=True)
    cleanup_cloud(remote_folder)

    # ③ 压缩
    logger.info('将所需备份目录文件进行压缩...')
    now_time = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    name = f'{QLBK_PREFIX}_{now_time}.tar.gz'
    files_name = f'{QLBK_BACKUPS_PATH}/{name}'
    package_path = os.path.join(retval, files_name)
    logger.info(f'创建备份文件: {package_path}')

    if not make_targz(package_path, retval):
        if send:
            try:
                send('【qinglong自动备份】', '备份压缩失败,请检查日志')
            except Exception:
                logger.info("通知发送失败")
        sys.exit(1)

    size_mb = os.path.getsize(package_path) / 1024 / 1024
    logger.info(f'备份文件压缩完成（{size_mb:.1f} MB）...开始上传至阿里云盘')

    # ④ 上传
    ali.sync_folder(f'{retval}/{QLBK_BACKUPS_PATH}/',
                    flag=True,
                    remote_folder=remote_folder.file_id)

    # ⑤ 上传成功后按 QLBK_KEEP_LOCAL 决定本地是否留档
    if QLBK_KEEP_LOCAL in ('1', 'true', 'yes'):
        logger.info(f'QLBK_KEEP_LOCAL=1，本地保留当次备份包: {package_path}')
    else:
        try:
            os.remove(package_path)
            logger.info(f'上传成功，已删除本地备份包（QLBK_KEEP_LOCAL=0）: {package_path}')
        except Exception as e:
            logger.info(f'本地备份包删除失败: {package_path} ({e})')

    message_up_time = time.strftime("%Y年%m月%d日 %H时%M分%S秒", time.localtime())
    text = f'已备份至阿里网盘:\n{run_path}{files_name}\n' \
           f'\n备份大小:\n{size_mb:.1f} MB\n' \
           f'\n备份完成时间:\n{message_up_time}\n' \
           f'\n云端保留最近 {QLBK_KEEP_DAYS} 天，恭喜你这个逼备份完成！'
    if send:
        try:
            send(f'【{QLBK_PREFIX}自动备份】', text)
        except Exception:
            logger.info("通知发送失败")
    logger.info('---------------------备份完成---------------------')


if __name__ == '__main__':
    nowtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    logger.info('---------' + nowtime + ' 备份程序开始执行------------')
    if os.path.exists('/ql/data/'):
        logger.info('检测到data目录，切换运行目录至 /ql/data/')
        run_path = '/ql/data/'
    else:
        run_path = '/ql/'
    os.chdir(run_path)
    logger.info('登录阿里云盘')
    try:
        ali = Aligo(level=logging.INFO, show=show)
    except Exception:
        logger.info('登录失败')
        if send:
            try:
                send('【qinglong自动备份】', '阿里网盘登录失败,请手动重新运行本脚本登录')
            except Exception:
                logger.info("通知发送失败")
        sys.exit(1)
    start()
    sys.exit(0)
