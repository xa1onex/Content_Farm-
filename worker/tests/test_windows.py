import os,sys,subprocess
from pathlib import Path
import pytest
from shortpost_worker.crypto import DPAPI,TokenStore

@pytest.mark.skipif(os.name!='nt',reason='Windows DPAPI requires a real Windows user')
def test_windows_dpapi_and_token_store(tmp_path):
    cipher=DPAPI();secret='local-test-secret-not-a-real-worker-token'
    encrypted=cipher.encrypt(secret)
    assert secret not in encrypted and cipher.decrypt(encrypted)==secret
    store=TokenStore(tmp_path/'token.dpapi',cipher);store.save(secret)
    assert store.read()==secret and secret not in store.path.read_text()

@pytest.mark.skipif(os.name=='nt',reason='Non-Windows fail-closed check')
def test_linux_has_no_plaintext_dpapi_fallback():
    with pytest.raises(RuntimeError,match='DPAPI requires Windows'):DPAPI()

@pytest.mark.skipif(os.name!='nt',reason='PowerShell syntax parser runs on Windows CI')
def test_powershell_scripts_parse():
    root=Path(__file__).resolve().parents[2]
    for script in (root/'scripts').glob('*.ps1'):
        code='$t=$null;$e=$null;[System.Management.Automation.Language.Parser]::ParseFile($env:SHORTPOST_PS_FILE,[ref]$t,[ref]$e)|Out-Null;if($e.Count){$e|ForEach-Object{$_.Message};exit 1}'
        result=subprocess.run(['powershell','-NoProfile','-Command',code],capture_output=True,text=True,env=dict(os.environ,SHORTPOST_PS_FILE=str(script)))
        assert result.returncode==0,result.stdout+result.stderr
