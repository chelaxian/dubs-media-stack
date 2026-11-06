"""Interactive entry point around the existing, tested deployment commands."""
import argparse
import os
import sys
from pathlib import Path

from . import cli, system
from .deployment import read_env


def preflight():
    if Path('/etc/pve').exists() or system.have('pveversion'):
        raise ValueError('Запустите установщик внутри Debian/Ubuntu LXC или VM, не на хосте Proxmox.')
    if not system.supported_distro():
        raise ValueError('Поддерживаются Debian 12/13 и Ubuntu 24.04.')
    if not system.is_root():
        raise ValueError('Для изменений запустите sudo ./install.sh; просмотр: ./install.sh --plan.')


def answer(label, default=''):
    value = input('%s%s: ' % (label, ' [%s]' % default if default else '')).strip()
    return value or default


def confirm(label):
    return answer(label + ' (введите ДА)', '').casefold() == 'да'


def write_initial_env(path, media, data, uid, gid, timezone):
    """Create a private env once; existing operator configuration is never overwritten."""
    for value in (media, data):
        if not value.startswith('/') or any(c in value for c in '\n\r\x00\'"$`#'):
            raise ValueError('Нужны абсолютные пути без кавычек, переноса строки и подстановок.')
    if not Path(media).is_dir():
        raise ValueError('Каталог медиадиска должен существовать и быть смонтирован заранее.')
    if not uid.isdecimal() or not gid.isdecimal() or len(uid)>9 or len(gid)>9:
        raise ValueError('UID/GID должны быть числовыми.')
    from zoneinfo import ZoneInfo
    ZoneInfo(timezone)
    template = (system.repo_root()/'compose'/'.env.example').read_text()
    values = {'MEDIA_ROOT':media, 'DUBS_DATA_ROOT':data, 'PUID':uid,
              'PGID':gid, 'TZ':timezone}
    text = '\n'.join(k+'='+values[k] if (k := line.partition('=')[0]) in values else line
                     for line in template.splitlines())+'\n'
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd,'w') as f:
        os.fchmod(f.fileno(),0o600)
        f.write(text)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Dubs: интерактивная установка и обновление')
    parser.add_argument('--env-file', type=Path, help='Путь compose/.env (не перезаписывается)')
    parser.add_argument('--plan', action='store_true', help='Только диагностика, без изменений')
    parser.add_argument('--update', action='store_true', help='Визард обновления с резервной копией')
    args = parser.parse_args(argv)
    source_env = system.repo_root()/'compose'/'.env'
    installed = Path(read_env(source_env).get('DUBS_INSTALL_ROOT','/opt/dubs'))/'compose'/'.env'
    env = args.env_file or (source_env if source_env.exists() and not installed.exists() else installed)
    prefix = ['--env-file', str(env)]
    if args.plan:
        return cli.main(prefix+['plan'])
    try:
        preflight()
        if not sys.stdin.isatty():
            raise ValueError('Нужен интерактивный терминал. Для автоматизации: python3 -m installer --help.')
        print('Dubs — мастер установки. Диски не форматируются; существующие настройки сохраняются.')
        if not env.exists():
            if args.update:
                raise ValueError('Конфигурация не найдена. Укажите --env-file установленного стека.')
            media = answer('Уже смонтированный каталог медиа', '/mnt/media')
            data = answer('Каталог данных приложений', '/srv/dubs-data')
            uid = answer('UID владельца медиа', '1000')
            gid = answer('GID владельца медиа', '1000')
            timezone = answer('Часовой пояс', 'Europe/Moscow')
            if not confirm('Создать приватный файл параметров %s?' % env):
                return 0
            write_initial_env(env,media,data,uid,gid,timezone)
        # The normal installed env is selected by Deployment when no explicit
        # override is provided. Retain an explicitly supplied operator file.
        installed = Path(read_env(env).get('DUBS_INSTALL_ROOT','/opt/dubs'))/'compose'/'.env'
        if args.env_file is None and installed.exists():
            env = installed
            prefix = ['--env-file',str(env)]
        cli.main(prefix+['plan'])
        if args.update:
            if not confirm('Создать резервную копию конфигурации и обновить работающие компоненты?'):
                return 0
            if cli.main(prefix+['backup']) != 0:
                return 1
            return cli.main(prefix+['update'])
        while True:
            print('\n1 — Установить зависимости, сервисы и каталоги\n'
                  '2 — Создать недостающие настройки (существующие сохраняются)\n'
                  '3 — Проверить настройки и связать сервисы (bootstrap)\n'
                  '4 — Проверить готовность\n5 — Диагностика\n0 — Выход')
            selected = answer('Шаг','0')
            if selected == '0':
                return 0
            commands = {'1':'install','2':'configure','3':'bootstrap','4':'check','5':'doctor'}
            if selected not in commands:
                continue
            command = commands[selected]
            if selected in ('1','2','3') and not confirm('Выполнить шаг «%s»?' % command):
                continue
            if command == 'install' and env.resolve() != installed.resolve() and not installed.exists():
                # The lower-level installer reads its installed compose env.
                # Carry the operator's choices there, once and privately.
                if not confirm('Скопировать параметры в %s?' % installed):
                    continue
                installed.parent.mkdir(parents=True,exist_ok=True)
                fd=os.open(installed,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
                with os.fdopen(fd,'wb') as f:f.write(env.read_bytes())
            result = cli.main(prefix+[command])
            if command == 'install' and args.env_file is None and installed.exists():
                prefix = ['--env-file',str(installed)]
            if result:
                print('Шаг не завершён. Исправьте указанные проблемы; успешная установка не подтверждена.')
            if command == 'configure':
                print('Заполните созданные JSON-настройки и ключи API по инструкции docs/quickstart.md.\n'
                      'Пароли/токены не передавайте в аргументах команд. Затем повторите шаги 3 и 4.')
    except (ValueError, KeyError, OSError, EOFError, KeyboardInterrupt) as exc:
        print('Визард остановлен: %s' % exc, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
