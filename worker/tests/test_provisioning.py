from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from shortpost_worker.provisioning import APPS, PlayInstaller, PLAY_PACKAGE, play_problem, prepare_devices
from shortpost_worker.errors import WorkerError

DRIVE = next(a for a in APPS if a.key == 'drive')
PAGE = '<hierarchy><node package="com.android.vending" text="Google Drive"/><node package="com.android.vending" text="Install"/></hierarchy>'

class Clock:
    def __init__(self): self.at = 0
    def now(self): return self.at
    def sleep(self, n): self.at += n

class Driver:
    current_package = PLAY_PACKAGE
    page_source = PAGE
    def __init__(self, *, installed=False):
        self.installed = installed
        self.button = SimpleNamespace(is_displayed=lambda: True, click=self.install)
        self.clicks = 0
        self.urls = []
    def install(self): self.clicks += 1; self.installed = True
    def is_app_installed(self, package): return package == PLAY_PACKAGE or self.installed
    def execute_script(self, script, args): self.urls.append((script, args))
    def find_elements(self, *args): return [self.button]

def installer(driver):
    clock = Clock()
    return PlayInstaller(driver, timeout=6, clock=clock.now, sleep=clock.sleep)

def test_already_installed_is_idempotent_and_does_not_open_or_update():
    driver = Driver(installed=True)
    assert installer(driver).ensure(DRIVE)['status'] == 'already_installed'
    assert not driver.urls and driver.clicks == 0

def test_check_only_never_opens_store_or_installs():
    driver = Driver()
    assert installer(driver).ensure(DRIVE, check_only=True)['status'] == 'missing'
    assert not driver.urls and driver.clicks == 0

def test_install_uses_exact_play_package_and_confirms_android_package():
    driver = Driver()
    assert installer(driver).ensure(DRIVE)['status'] == 'installed'
    assert driver.clicks == 1
    assert driver.urls == [('mobile: deepLink', {'url':'market://details?id=com.google.android.apps.docs', 'package':PLAY_PACKAGE})]

def test_install_timeout_does_not_repeat_click_or_claim_success():
    driver = Driver()
    driver.button.click = lambda: setattr(driver, 'clicks', driver.clicks + 1)
    assert installer(driver).ensure(DRIVE)['status'] == 'installation_timeout'
    assert driver.clicks == 1

def test_wrong_card_is_not_clicked():
    driver = Driver(); driver.page_source = PAGE.replace('Google Drive','Some Other App')
    assert installer(driver).ensure(DRIVE)['status'] == 'play_ui_unrecognized'
    assert driver.clicks == 0

def test_ambiguous_buttons_are_not_clicked():
    driver = Driver();driver.find_elements = lambda *args:[driver.button,driver.button]
    assert installer(driver).ensure(DRIVE)['status'] == 'ambiguous_install_button'
    assert driver.clicks == 0

@pytest.mark.parametrize('source,status', [
    ('<hierarchy><node text="Войти"/></hierarchy>', 'google_login_required'),
    ('<hierarchy><node text="This item is not available in your country"/></hierarchy>', 'play_app_unavailable'),
    ('<hierarchy><node text="Подтвердите свою личность"/></hierarchy>', 'play_verification_required'),
])
def test_intervention_does_not_click(source,status):
    driver = Driver();driver.page_source = source
    assert installer(driver).ensure(DRIVE)['status'] == status
    assert driver.clicks == 0

def test_foreign_app_is_not_clicked():
    driver = Driver();driver.current_package='com.android.chrome'
    assert installer(driver).ensure(DRIVE)['status'] == 'play_intervention_required'
    assert driver.clicks == 0

def test_missing_play_does_not_sideload():
    driver = Driver();driver.is_app_installed = lambda p:False
    assert installer(driver).ensure(DRIVE)['status'] == 'play_store_missing'
    assert not driver.urls

def test_catalog_has_five_exact_packages_including_user_proxy():
    assert len(APPS) == 5
    assert APPS[-1].package == 'com.deniyitunji.proxysocket'
    assert len({app.package for app in APPS}) == 5

def test_unresolved_publication_blocks_installer(config,journal,assignment):
    journal.save_assignment(assignment)
    with pytest.raises(WorkerError, match='unfinished_jobs_block_preparation'):
        prepare_devices(config,journal)

def test_preparation_continues_next_device_and_excludes_disabled(config,journal,monkeypatch):
    import shortpost_worker.provisioning as module
    second=config.devices[0].model_copy(update={'external_id':'mumu-02','instance_index':1,'android_id':'identity2','system_port':8201})
    disabled=second.model_copy(update={'external_id':'mumu-03','instance_index':2,'android_id':'identity3','system_port':8202,'enabled':False})
    config.devices += [second,disabled]
    instances=[]
    class DeviceAccess:
        def __init__(self,*args): pass
        def resources_ok(self): return True
        def connect(self,device,*args):return SimpleNamespace(serial=device.external_id)
    class Session:
        def __init__(self,device,serial): self.name=device.external_id;self.closed=False;instances.append(self)
        def open(self):
            if self.name=='mumu-01':raise WorkerError('appium_unavailable')
            return Driver(installed=True)
        def close(self):self.closed=True
    monkeypatch.setattr(module,'Devices',DeviceAccess)
    monkeypatch.setattr(module,'Session',Session)
    result=prepare_devices(config,journal)
    assert len(result['devices'])==2
    assert result['devices'][0]['error']=='appium_unavailable'
    assert result['devices'][1]['ready']
    assert all(s.closed for s in instances)
    assert (config.state_dir/'provisioning/latest.json').exists()

def test_stop_prevents_install_click():
    driver = Driver(); clock = Clock()
    tool=PlayInstaller(driver,timeout=6,clock=clock.now,sleep=clock.sleep,stop_requested=lambda:True)
    assert tool.ensure(DRIVE)['status']=='preparation_stopped'
    assert not driver.urls and not driver.clicks

@pytest.mark.asyncio
async def test_engine_checks_apps_before_starting_work(config,journal,monkeypatch):
    from shortpost_worker.engine import Engine
    import shortpost_worker.provisioning as module
    api=SimpleNamespace(auth_failed=SimpleNamespace(is_set=lambda:False))
    calls=[]
    def prepare(*args,**kwargs):calls.append(1);return {'devices':[{'ready':False}]}
    monkeypatch.setattr(module,'prepare_devices',prepare)
    engine=Engine(config,journal,api)
    with pytest.raises(WorkerError,match='required_apps_not_ready'):
        await engine.prepare_apps()
    assert calls==[1]

@pytest.mark.asyncio
async def test_engine_does_not_open_play_during_unfinished_job(config,journal,assignment,monkeypatch):
    from shortpost_worker.engine import Engine
    import shortpost_worker.provisioning as module
    prepare=Mock(side_effect=AssertionError('Play must not open'))
    monkeypatch.setattr(module,'prepare_devices',prepare)
    journal.save_assignment(assignment)
    await Engine(config,journal,SimpleNamespace()).prepare_apps()
    prepare.assert_not_called()

def test_play_pass_offer_is_declined_without_purchase():
    from shortpost_worker.provisioning import SKIP_PASS_SELECTOR
    driver=Driver()
    driver.page_source='<hierarchy><node text="Попробуйте Google Play Pass"/><node text="Не сейчас"/></hierarchy>'
    calls=[]
    def decline():driver.page_source=PAGE
    skip=SimpleNamespace(is_displayed=lambda:True,click=decline)
    def find(by,value):
        calls.append(value)
        return [skip] if value==SKIP_PASS_SELECTOR else [driver.button]
    driver.find_elements=find
    assert installer(driver).ensure(DRIVE)['status']=='installed'
    assert calls[0]==SKIP_PASS_SELECTOR and driver.clicks==1
