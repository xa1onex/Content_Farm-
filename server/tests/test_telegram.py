import asyncio,json
from aiogram import types,Dispatcher
from app.telegram_admin.extension import AdminExtension,AdminMiddleware,install
from app.db import now

def message(text='/ap',uid=123):
    return types.Message(message_id=1,date=now(),chat=types.Chat(id=uid,type='private'),
        from_user=types.User(id=uid,is_bot=False,first_name='Test'),text=text)

def test_explicit_all_users_and_allowlist():
    assert AdminExtension({'allow_all':True}).allowed(message(uid=456))
    assert not AdminExtension({'admin_ids':[123]}).allowed(message(uid=456))
    assert AdminExtension({'admin_ids':[123]}).allowed(message())

def test_extension_preserves_existing_bot_handlers():
    extension=AdminExtension({'allow_all':True})
    middleware=AdminMiddleware(extension)
    called=[]
    async def legacy(event,data):called.append(event.text);return 'legacy'
    result=asyncio.run(middleware(legacy,message('https://youtube.com/watch?v=123'),{}))
    assert result=='legacy' and called

def test_mutation_requires_confirmation(monkeypatch):
    extension=AdminExtension({'allow_all':True})
    calls=[];answers=[]
    async def api(*args,**kwargs):calls.append(args);return {}
    async def answer(self,text,**kwargs):answers.append((text,kwargs))
    monkeypatch.setattr(extension,'api',api)
    monkeypatch.setattr(types.Message,'answer',answer)
    asyncio.run(extension.message(message('/ap worker_add {"name":"Worker A"}')))
    assert calls==[] and len(extension.pending)==1
    pending=next(iter(extension.pending.values()))
    assert pending['user']==123 and pending['path']=='/api/v1/workers'
    assert 'Подтвердить' in str(answers[0][1]['reply_markup'])

def test_install_does_not_add_polling_consumer(tmp_path):
    config=tmp_path/'telegram.json';config.write_text(json.dumps({'api_url':'http://127.0.0.1:8108','api_token':'test','allow_all':True}))
    dispatcher=Dispatcher();install(dispatcher,str(config))
    assert dispatcher.startup.handlers and dispatcher.shutdown.handlers
