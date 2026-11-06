import hashlib,json,logging,time,uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI,Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError,OperationalError
from app.db import engine
from app.api.routes import router
from app.integrations.drive import DriveError

class JsonFormatter(logging.Formatter):
    def format(self,record):
        result={'timestamp':self.formatTime(record),'level':record.levelname,'logger':record.name,'message':record.getMessage()}
        if record.exc_info:result['exception']=self.formatException(record.exc_info)
        return json.dumps(result,ensure_ascii=False)
handler=logging.StreamHandler();handler.setFormatter(JsonFormatter())
logging.basicConfig(level=logging.INFO,handlers=[handler],force=True)
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('google.auth').setLevel(logging.WARNING)
log=logging.getLogger('autopost')

@asynccontextmanager
async def lifespan(app):
    from app.config import settings
    with engine.connect() as s:
        db=s.scalar(text('SELECT current_database()'))
        if settings().environment=='test' and db!='shortpost_test':
            raise RuntimeError('Test environment must use shortpost_test')
        s.execute(text('SELECT version_num FROM alembic_version'))
    yield
    engine.dispose()
app=FastAPI(title='Autopost Server',version='1.0.0',lifespan=lifespan,
    description='Server protocol only. No social media publication is performed by this server.')
app.include_router(router)
from app.miniapp import router as miniapp_router
app.include_router(miniapp_router)
from app.editorial import router as editorial_router
app.include_router(editorial_router)
from app.review import router as review_router
app.include_router(review_router)

@app.middleware('http')
async def request_context(request:Request,call_next):
    request_id=uuid.uuid4().hex;started=time.monotonic()
    if request.method in ('POST','PUT','PATCH','DELETE'):
        body=bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body)>1024*1024:return JSONResponse({'detail':'Request too large'},413)
        request._body=bytes(body)
        request.state.body_hash=hashlib.sha256(body).hexdigest()
    try:
        response=await call_next(request)
    except Exception:
        log.error('unhandled_request_error request_id=%s',request_id)
        from app.db import transaction
        from app.errors import event
        try:
            with transaction() as s:event(s,'server_error',f'server:{request_id}',{'request_id':request_id},'critical')
        except Exception:pass
        response=JSONResponse({'detail':'Internal server error','request_id':request_id},500)
    response.headers['X-Request-ID']=request_id
    response.headers['Cache-Control']='no-store'
    log.info('request method=%s path=%s status=%s duration_ms=%d request_id=%s',
        request.method,request.url.path,response.status_code,int((time.monotonic()-started)*1000),request_id)
    return response

@app.exception_handler(IntegrityError)
async def integrity(request,exc):
    return JSONResponse({'detail':'Database constraint conflict: duplicate identity, invalid relation or unsafe state'},409)
@app.exception_handler(OperationalError)
async def unavailable(request,exc):
    return JSONResponse({'detail':'Database temporarily unavailable; retry using the same Idempotency-Key'},503)
@app.exception_handler(DriveError)
async def drive_error(request,exc):return JSONResponse({'detail':exc.code},503)

# Header usage is enforced by @idempotent and exposed explicitly in OpenAPI.
from fastapi.openapi.utils import get_openapi

def openapi_schema():
    if app.openapi_schema:return app.openapi_schema
    schema=get_openapi(title=app.title,version=app.version,description=app.description,routes=app.routes)
    for path in schema['paths'].values():
        for method,operation in path.items():
            if method in ('post','patch','put','delete'):
                operation.setdefault('parameters',[]).append({'name':'Idempotency-Key','in':'header',
                    'required':True,'description':'Reuse the same key and identical body to retry a network operation.',
                    'schema':{'type':'string','minLength':8,'maxLength':100}})
    app.openapi_schema=schema
    return schema
app.openapi=openapi_schema
