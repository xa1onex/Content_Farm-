"""Run tooling with a loopback PostgreSQL URL. Never prints credentials."""
import os,subprocess,sys
from pathlib import Path
from dotenv import dotenv_values
from sqlalchemy.engine import make_url
root=Path(__file__).resolve().parents[1]
env=os.environ.copy();env.update({k:v for k,v in dotenv_values(root/'.env').items() if v is not None})
args=sys.argv[1:];production=False
if args and args[0]=='--production':production=True;args=args[1:]
url=make_url(env['DATABASE_URL']).set(host='127.0.0.1',port=55432,database='shortpost' if production else 'shortpost_test')
env['DATABASE_URL']=url.render_as_string(hide_password=False)
env['TEST_DATABASE_URL']=env['DATABASE_URL']
env['ENVIRONMENT']='production' if production else 'test'
if not args:raise SystemExit('Usage: python scripts/dev.py [--production] <executable> [arguments]')
raise SystemExit(subprocess.call(args,cwd=root,env=env))
