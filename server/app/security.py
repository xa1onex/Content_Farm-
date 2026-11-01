import hashlib
import secrets
import time
from dataclasses import dataclass
from functools import wraps
import json
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from cryptography.fernet import Fernet
from app.config import settings
from app.db import db_session, transaction
from app.models import ApiToken, RateBucket, Idempotency, Audit

bearer=HTTPBearer(auto_error=False)
def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()
def new_token():
    return secrets.token_urlsafe(40)
def lock(s, key):
    s.execute(text('SELECT pg_advisory_xact_lock(:k)'),{'k':int.from_bytes(hashlib.sha256(key.encode()).digest()[:8],'big',signed=True)})
@dataclass
class Actor:
    id: object
    role: str
    worker_id: object = None

def authenticate(request: Request, credentials: HTTPAuthorizationCredentials|None=Depends(bearer)):
    raw=credentials.credentials if credentials else ''
    key=digest(raw) if raw else digest(request.client.host if request.client else 'unknown')
    minute=int(time.time()//60)
    with transaction() as s:
        def consume(bucket):
            return s.scalar(insert(RateBucket).values(key=bucket,minute=minute,count=1)
                .on_conflict_do_update(index_elements=['key','minute'],set_={'count':RateBucket.count+1})
                .returning(RateBucket.count))
        count=consume(key)
        ip_count=consume('ip:'+digest(request.client.host if request.client else 'unknown'))
        token=s.scalar(select(ApiToken).where(ApiToken.token_hash==key,ApiToken.enabled.is_(True))) if raw else None
        actor=Actor(token.id,token.role,token.worker_id) if token else None
    if count>settings().rate_limit_per_minute or ip_count>settings().rate_limit_per_minute*5:
        raise HTTPException(429,'Rate limit exceeded',headers={'Retry-After':'60'})
    if actor is None:
        raise HTTPException(401,'Invalid bearer token',headers={'WWW-Authenticate':'Bearer'})
    return actor

def roles(*allowed):
    def dependency(actor=Depends(authenticate)):
        if actor.role not in allowed:
            raise HTTPException(403,'Insufficient permissions')
        return actor
    return dependency

def idempotent(fn):
    @wraps(fn)
    def wrapper(*args,**kwargs):
        request=kwargs['request']; s=kwargs['s']; actor=kwargs['actor']
        key=request.headers.get('Idempotency-Key','')
        if not 8<=len(key)<=100:
            raise HTTPException(400,'Idempotency-Key of 8..100 characters is required')
        scope=f'{actor.id}:{request.method}:{request.url.path}'
        lock(s,f'idempotency:{scope}:{key}')
        prior=s.get(Idempotency,(scope,key))
        body_hash=request.state.body_hash
        cipher=Fernet(settings().encryption_key.get_secret_value().encode())
        if prior:
            if prior.digest!=body_hash:
                raise HTTPException(409,'Idempotency-Key reused with a different body')
            return json.loads(cipher.decrypt(prior.response.encode()))
        result=jsonable_encoder(fn(*args,**kwargs))
        s.add(Idempotency(scope=scope,key=key,digest=body_hash,
            response=cipher.encrypt(json.dumps(result).encode()).decode()))
        s.add(Audit(actor=str(actor.id),action=f'{request.method} {request.url.path}',detail={}))
        s.flush()
        return result
    return wrapper
