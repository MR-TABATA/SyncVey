"""
スケジューラーを起動してよいか判定する。

起動しない条件:
  - pytest / manage.py test 実行中（テストが汚染される）
  - runserver の親プロセス（RUN_MAIN が未設定の初回起動）
  - gunicorn マスタープロセス（GUNICORN_WORKER_ID が未設定）
"""

import os
import sys


_NO_SCHEDULER_COMMANDS = frozenset({
    'migrate', 'makemigrations', 'showmigrations', 'sqlmigrate',
    'collectstatic', 'shell', 'dbshell', 'check', 'inspectdb',
    'createsuperuser', 'changepassword', 'flush', 'loaddata', 'dumpdata',
})


def should_start_scheduler() -> bool:
    # テスト実行中は起動しない
    if 'pytest' in sys.modules or 'test' in sys.argv:
        return False

    # migrate 等の管理コマンドでは起動しない
    if _NO_SCHEDULER_COMMANDS.intersection(sys.argv):
        return False

    # runserver: 子プロセス(RUN_MAIN=true)だけ起動
    if 'runserver' in sys.argv:
        return os.environ.get('RUN_MAIN') == 'true'

    # gunicorn / その他の本番起動: 起動する
    return True
