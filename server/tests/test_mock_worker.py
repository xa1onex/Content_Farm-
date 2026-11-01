import socket,threading,time
from pathlib import Path
import importlib.util
import pytest
import uvicorn
from app.main import app
from app.config import settings

spec=importlib.util.spec_from_file_location('mock_worker',Path(__file__).parents[1]/'scripts/mock_worker.py')
mock=importlib.util.module_from_spec(spec);spec.loader.exec_module(mock)

def test_real_http_mock_worker_cycle(client,seeded,monkeypatch):
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
    port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app,log_level='error'))
    thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True);thread.start()
    try:
        deadline=time.monotonic()+10
        while not server.started and time.monotonic()<deadline:time.sleep(.05)
        assert server.started
        result=mock.execute(f'http://127.0.0.1:{port}','worker-test-token',download=False)
        assert result['status']=='published'
        monkeypatch.setattr(settings(),'environment','production')
        with pytest.raises(RuntimeError,match='production'):mock.execute(f'http://127.0.0.1:{port}','worker-test-token',download=False)
    finally:
        server.should_exit=True;thread.join(timeout=10);sock.close()
