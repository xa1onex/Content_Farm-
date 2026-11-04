from appium import webdriver
from appium.options.android import UiAutomator2Options
from appium.webdriver.client_config import AppiumClientConfig
from .errors import WorkerError

class Session:
    def __init__(self,device,serial):self.device=device;self.serial=serial;self.driver=None
    def open(self):
        options=UiAutomator2Options().load_capabilities({'platformName':'Android',
            'appium:automationName':'UiAutomator2','appium:udid':self.serial,
            'appium:noReset':True,'appium:fullReset':False,'appium:systemPort':self.device.system_port,
            'appium:newCommandTimeout':120,'appium:adbExecTimeout':30000,
            'appium:uiautomator2ServerInstallTimeout':60000})
        config=AppiumClientConfig(remote_server_addr=self.device.appium_url,timeout=60,
            init_args_for_pool_manager={'init_args_for_pool_manager':{'retries':0}})
        try:
            self.driver=webdriver.Remote(options=options,client_config=config)
            self.driver.implicitly_wait(0)
            self.driver.update_settings({'waitForIdleTimeout':1000,'waitForSelectorTimeout':1000})
            return self.driver
        except Exception:raise WorkerError('appium_unavailable') from None
    def close(self):
        if self.driver:
            try:self.driver.quit()
            except Exception:pass
