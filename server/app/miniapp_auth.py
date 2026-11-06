"""Telegram initData validation and short-lived, server-only signed sessions."""
import hashlib,hmac,json,time
from urllib.parse import parse_qsl
from cryptography.fernet import Fernet,InvalidToken
from fastapi import HTTPException
from app.config import settings

def allowed_user(uid):
    config=settings()
    return config.telegram_allow_all or uid in config.telegram_admin_ids

def validate_init_data(raw):
    token=settings().telegram_bot_token.get_secret_value()
    if not token:raise HTTPException(503,'Вход через Telegram ещё не настроен')
    try:
        pairs=parse_qsl(raw,keep_blank_values=True,strict_parsing=True)
        if len(pairs)!=len(dict(pairs)):raise ValueError()
        data=dict(pairs);signature=data.pop('hash')
        secret=hmac.new(b'WebAppData',token.encode(),hashlib.sha256).digest()
        check='\n'.join(k+'='+v for k,v in sorted(data.items()))
        if not hmac.compare_digest(hmac.new(secret,check.encode(),hashlib.sha256).hexdigest(),signature):raise ValueError()
        stamp=int(data['auth_date'])
        if not -30<=time.time()-stamp<=3600:raise ValueError()
        user=json.loads(data['user']);uid=user['id']
        if type(uid) is not int or uid<=0:raise ValueError()
    except (ValueError,KeyError,TypeError):raise HTTPException(401,'Открой панель заново через Telegram') from None
    if not allowed_user(uid):raise HTTPException(403,'Нет доступа к управлению')
    return {'id':uid,'first_name':str(user.get('first_name',''))[:100]}

def issue_session(user):
    data={'kind':'miniapp','uid':user['id'],'expires':int(time.time())+43200}
    token=Fernet(settings().encryption_key.get_secret_value().encode()).encrypt(json.dumps(data).encode()).decode()
    return {'token':'tma.'+token,'user':user,'expires_at':data['expires']}

def session_user(raw):
    try:
        data=json.loads(Fernet(settings().encryption_key.get_secret_value().encode()).decrypt(raw[4:].encode(),ttl=43200))
        uid=data['uid']
        if data['kind']!='miniapp' or type(uid) is not int or uid<=0 or data['expires']<time.time():raise ValueError()
    except (InvalidToken,ValueError,KeyError,TypeError):raise HTTPException(401,'Открой панель заново через Telegram') from None
    if not allowed_user(uid):raise HTTPException(403,'Нет доступа к управлению')
    return uid
