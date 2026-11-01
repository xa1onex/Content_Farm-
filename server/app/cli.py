"""Local-only credential provisioning. Redirect stdout to a private file."""
import argparse,json,sys
from sqlalchemy import select
from app.db import transaction
from app.models import ApiToken
from app.security import new_token,digest

def main():
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['bootstrap'])
    args=parser.parse_args()
    if args.command=='bootstrap':
        with transaction() as s:
            if s.scalar(select(ApiToken.id).where(ApiToken.label=='bootstrap-admin')):
                raise SystemExit('Bootstrap already completed. Use authenticated token API for rotation.')
            result={}
            for role in ('admin','farm'):
                raw=new_token();s.add(ApiToken(token_hash=digest(raw),role=role,label='bootstrap-'+role))
                result[role+'_token']=raw
        print(json.dumps(result))
if __name__=='__main__':main()
