"""Install the reviewed extension only after approval to modify the existing bot."""
import json,os,shutil,subprocess
from pathlib import Path
from datetime import datetime,timezone
root=Path(__file__).resolve().parents[1]
target=Path('/root/tg-video-clipper')
marker="install_autopost_admin(dp, '/etc/shortpost/telegram.json')"
if __name__=='__main__':
    for state_path in (target/'jobs').glob('*/state.json'):
        state=json.loads(state_path.read_text())
        if state.get('status') in ('receiving','queued','processing','interrupted'):
            raise SystemExit('Content farm has unfinished jobs. Wait before restarting its bot.')
    bot_path=target/'bot.py';text=bot_path.read_text()
    if marker in text:raise SystemExit('Extension already installed')
    old='dp = Dispatcher()'
    if text.count(old)!=1:raise SystemExit('Unexpected bot.py structure; review manually')
    addition="\n\nfrom autopost_admin_extension import install as install_autopost_admin\n"+marker
    updated=text.replace(old,old+addition,1)
    compile(updated,str(bot_path),'exec')
    module=(root/'app/telegram_admin/extension.py').read_text()
    compile(module,'autopost_admin_extension.py','exec')
    credentials=json.loads((root/'secrets/bootstrap.json').read_text())
    config=Path('/etc/shortpost');config.mkdir(mode=0o700,exist_ok=True)
    path=config/'telegram.json'
    path.write_text(json.dumps({'api_url':'http://127.0.0.1:8108','api_token':credentials['admin_token'],
        'allow_all':True,'admin_ids':[]}));path.chmod(0o600)
    shutil.copyfile(root/'app/telegram_admin/extension.py',target/'autopost_admin_extension.py')
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup=target/'backups'/('autopost-'+stamp);backup.mkdir(mode=0o700,parents=True)
    shutil.copy2(bot_path,backup/'bot.py')
    temp=bot_path.with_suffix('.autopost.tmp');temp.write_text(updated)
    shutil.copymode(bot_path,temp);os.replace(temp,bot_path)
    try:
        subprocess.run(['systemctl','restart','tg-video-clipper.service'],check=True)
        subprocess.run(['systemctl','is-active','tg-video-clipper.service'],check=True)
    except subprocess.CalledProcessError:
        shutil.copy2(backup/'bot.py',bot_path)
        subprocess.run(['systemctl','restart','tg-video-clipper.service'],check=True)
        raise SystemExit('Extension startup failed. Original bot.py restored and original service restarted.')
    print('Extension installed. Existing bot handles /ap and /autopost. Backup:',backup)
