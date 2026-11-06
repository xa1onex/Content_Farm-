"""Idempotent preparation through the official Google Play UI; never sideloads APKs."""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import xml.etree.ElementTree as ET

from .appium_session import Session
from .devices import Devices
from .errors import WorkerError

PLAY_PACKAGE = 'com.android.vending'

@dataclass(frozen=True)
class PlayApp:
    key: str
    package: str
    titles: tuple[str, ...]

APPS = (
    PlayApp('youtube', 'com.google.android.youtube', ('YouTube',)),
    PlayApp('tiktok', 'com.zhiliaoapp.musically', ('TikTok', 'TikTok - Videos, Shop & LIVE')),
    PlayApp('drive', 'com.google.android.apps.docs', ('Google Диск', 'Google Drive')),
    PlayApp('instagram', 'com.instagram.android', ('Instagram',)),
    PlayApp('proxysocket', 'com.deniyitunji.proxysocket', ('ProxySocket',)),
)
INSTALL_SELECTOR = (
    '//*[@package="com.android.vending" and '
    '(@text="Установить" or @content-desc="Установить" or '
    '@text="Install" or @content-desc="Install")]/'
    'ancestor-or-self::*[@clickable="true"][1]'
)

SKIP_PASS_SELECTOR = (
    '//*[@package="com.android.vending" and '
    '(@text="Не сейчас" or @content-desc="Не сейчас" or '
    '@text="Not now" or @content-desc="Not now" or '
    '@text="No thanks" or @content-desc="No thanks")]/'
    'ancestor-or-self::*[@clickable="true"][1]'
)

class PreparationGuard:
    def __init__(self, seconds):
        self.until = time.monotonic() + seconds
    def check(self):
        if time.monotonic() >= self.until:
            raise WorkerError('preparation_timeout')


def play_problem(source):
    root = ET.fromstring(source)
    labels = {n.get(k, '').strip().casefold() for n in root.iter()
              for k in ('text', 'content-desc')}
    if labels & {'войти', 'sign in', 'войдите в аккаунт google', 'sign in to your google account'}:
        return 'google_login_required'
    unavailable = ('недоступно в вашей стране', 'недоступно в вашем регионе',
                   "not available in your country", 'не поддерживается на вашем устройстве',
                   "isn’t compatible with your device", "is not compatible with your device")
    if any(word in label for label in labels for word in unavailable):
        return 'play_app_unavailable'
    if any(word in label for label in labels
           for word in ('подтвердите, что вы человек', 'verify you are human', 'подтвердите свою личность')):
        return 'play_verification_required'
    return None


class PlayInstaller:
    def __init__(self, driver, *, timeout=600, sleep=time.sleep, clock=time.monotonic, stop_requested=lambda: False):
        self.driver = driver
        self.stop_requested = stop_requested
        self.timeout = timeout
        self.sleep = sleep
        self.clock = clock

    def ensure(self, app, *, check_only=False):
        result = {'app': app.key, 'package': app.package}
        if self.stop_requested():
            return result | {'status': 'preparation_stopped'}
        if self.driver.is_app_installed(app.package):
            return result | {'status': 'already_installed'}
        if check_only:
            return result | {'status': 'missing'}
        if not self.driver.is_app_installed(PLAY_PACKAGE):
            return result | {'status': 'play_store_missing'}
        self.driver.execute_script('mobile: deepLink', {
            'url': 'market://details?id=' + app.package, 'package': PLAY_PACKAGE})
        deadline = self.clock() + self.timeout
        clicked = False
        pass_offer_dismissed = False
        while self.clock() < deadline:
            if self.stop_requested():
                return result | {'status': 'preparation_stopped'}
            if self.driver.is_app_installed(app.package):
                return result | {'status': 'installed'}
            if self.driver.current_package != PLAY_PACKAGE:
                return result | {'status': 'play_intervention_required'}
            source = self.driver.page_source
            problem = play_problem(source)
            if problem:
                return result | {'status': problem}
            labels = {n.get('text', '').strip() for n in ET.fromstring(source).iter()}
            if labels.intersection({'Попробуйте Google Play Pass', 'Try Google Play Pass'}):
                skip = self.driver.find_elements('xpath', SKIP_PASS_SELECTOR)
                if pass_offer_dismissed or len(skip) != 1 or not skip[0].is_displayed():
                    return result | {'status': 'play_intervention_required'}
                if self.stop_requested():
                    return result | {'status': 'preparation_stopped'}
                skip[0].click()  # Decline the subscription offer; never click a purchase.
                pass_offer_dismissed = True
                self.sleep(2)
                continue
            if not clicked:
                buttons = self.driver.find_elements('xpath', INSTALL_SELECTOR)
                if len(buttons) > 1:
                    return result | {'status': 'ambiguous_install_button'}
                # The deep link pins the package; title and active app pin the displayed card.
                if buttons and labels.intersection(app.titles) and buttons[0].is_displayed():
                    if self.stop_requested():
                        return result | {'status': 'preparation_stopped'}
                    buttons[0].click()
                    clicked = True  # A single click; wait for Android package confirmation.
            self.sleep(2)
        return result | {'status': 'installation_timeout' if clicked else 'play_ui_unrecognized'}


def prepare_devices(config, journal, *, external_id=None, check_only=False, stop_requested=lambda: False):
    if journal.jobs(unfinished=True):
        raise WorkerError('unfinished_jobs_block_preparation')
    devices = Devices(config, journal)
    selected = ([devices.configured(external_id)] if external_id else
                [d for d in config.devices if d.enabled])
    if not selected:
        raise WorkerError('no_configured_devices')
    report = {'at': datetime.now(timezone.utc).isoformat(), 'devices': []}
    target = config.state_dir / 'provisioning' / 'latest.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    def save():
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(target)
    for device in selected:
        item = {'device': device.external_id, 'apps': [], 'ready': False}
        report['devices'].append(item)
        session = None
        try:
            if stop_requested():
                raise WorkerError('preparation_stopped')
            if not devices.resources_ok():
                raise WorkerError('insufficient_resources')
            if config.sequential_emulators:devices.exclusive(device)
            adb = devices.connect(device, PreparationGuard(config.play_install_timeout_seconds + 120))
            session = Session(device, adb.serial)
            driver = session.open()
            installer = PlayInstaller(driver, timeout=config.play_install_timeout_seconds, stop_requested=stop_requested)
            for app in APPS:
                entry = installer.ensure(app, check_only=check_only)
                item['apps'].append(entry)
                print(device.external_id, app.key, entry['status'], flush=True)
                save()
                # Login / store problems affect every app; continue with next device.
                if entry['status'] in {'google_login_required', 'play_store_missing',
                                       'play_verification_required', 'play_intervention_required', 'preparation_stopped'}:
                    break
            item['ready'] = (len(item['apps']) == len(APPS) and all(
                a['status'] in {'installed', 'already_installed'} for a in item['apps']))
        except WorkerError as exc:
            item['error'] = exc.code
        except Exception:
            item['error'] = 'play_preparation_failed'
        finally:
            if session:
                session.close()
            if config.sequential_emulators:
                try:devices.finish(device)
                except WorkerError:item['error']='emulator_shutdown_failed';item['ready']=False
            save()
    print('Preparation report:', target)
    return report
