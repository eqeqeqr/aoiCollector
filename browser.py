# -*- coding: utf-8 -*-
"""Selenium 浏览器控制器 - 管理 Edge 生命周期与标签页操作"""
import os, time, shutil, subprocess, tempfile, threading
from selenium import webdriver
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.chrome.options import Options as ChromeOptions

DEBUG_PORT = '9223'
HOME_URL = 'https://map.gaode.com'
_BROWSER_PATH = None  # 缓存找到的浏览器路径


def _find_browser():
    """自动探测系统上的 Chromium 浏览器: Edge > Chrome > 其他 (跨平台)"""
    global _BROWSER_PATH
    if _BROWSER_PATH and os.path.exists(_BROWSER_PATH):
        return _BROWSER_PATH
    import platform
    system = platform.system()
    candidates = []
    if system == 'Windows':
        candidates = [
            shutil.which('msedge'),
            r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
            r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
            shutil.which('chrome'),
            r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
            r'C:\Program Files\Google\Chrome\Application\chrome.exe',
        ]
    elif system == 'Darwin':  # macOS
        candidates = [
            '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
            '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
            shutil.which('microsoft-edge'),
            shutil.which('google-chrome'),
        ]
    else:  # Linux
        candidates = [
            shutil.which('microsoft-edge-stable'),
            shutil.which('microsoft-edge'),
            shutil.which('google-chrome-stable'),
            shutil.which('google-chrome'),
            shutil.which('chromium-browser'),
            shutil.which('chromium'),
            '/usr/bin/microsoft-edge-stable',
            '/usr/bin/google-chrome-stable',
            '/usr/bin/chromium-browser',
            '/usr/bin/chromium',
        ]
    for p in candidates:
        if p and os.path.exists(p):
            _BROWSER_PATH = p
            name = 'Edge' if 'edge' in p.lower() else 'Chrome' if 'chrome' in p.lower() else 'Chromium'
            print('[浏览器] 检测到 %s: %s' % (name, p))
            return p
    raise RuntimeError('未找到 Chromium 内核浏览器（Edge/Chrome/Chromium），请先安装其中一个')


def _kill_stale():
    """清理残留的受控浏览器进程(跨平台)"""
    import platform
    system = platform.system()
    if system == 'Windows':
        for exe in ('msedge.exe', 'chrome.exe'):
            out = subprocess.run(
                ['wmic', 'process', 'where', "name='%s'" % exe, 'get', 'ProcessId,CommandLine'],
                capture_output=True, text=True)
            for line in (out.stdout or '').splitlines():
                if 'gaode_collect_profile' in line or ('--remote-debugging-port=%s' % DEBUG_PORT) in line:
                    pid = line.strip().split()[-1]
                    if pid.isdigit():
                        subprocess.run(['taskkill', '/F', '/PID', pid], capture_output=True)
    else:
        # macOS / Linux: 用 pkill 匹配 profile 路径
        subprocess.run(['pkill', '-f', 'gaode_collect_profile'], capture_output=True)
        subprocess.run(['pkill', '-f', 'remote-debugging-port=%s' % DEBUG_PORT], capture_output=True)


class BrowserController:
    """管理一个受控 Chromium 浏览器实例 (Edge/Chrome)，所有 Selenium 操作
       通过 asyncio.to_thread 在后台线程执行，FastAPI 层不会被阻塞。"""

    def __init__(self):
        self._d = None          # webdriver.Edge 实例
        self._lock = threading.RLock()
        self._base_url = None   # 运行时由 app.py 注入

    # ---------- 生命周期 ----------

    def launch(self):
        """启动受控浏览器并连接 Selenium"""
        _kill_stale()
        time.sleep(1)
        browser_path = _find_browser()
        is_edge = 'Edge' in browser_path
        profile = os.path.join(tempfile.gettempdir(), 'gaode_collect_profile')
        cmd = [browser_path,
               '--remote-debugging-port=%s' % DEBUG_PORT,
               '--user-data-dir=' + profile,
               '--disable-blink-features=AutomationControlled']
        subprocess.Popen(cmd)
        time.sleep(4)
        opts = (EdgeOptions() if is_edge else ChromeOptions())
        opts.add_experimental_option('debuggerAddress', '127.0.0.1:%s' % DEBUG_PORT)
        with self._lock:
            self._d = (webdriver.Edge if is_edge else webdriver.Chrome)(options=opts)

    def ensure_driver(self, console_url=None):
        """确保浏览器可用，不可用则重新拉起。
           如果传入 console_url 且当前页面不在控制台，则主动导航过去。"""
        with self._lock:
            if self._d is not None:
                try:
                    self._d.window_handles
                    # 浏览器活着——检查是否在控制台页面
                    if console_url and console_url not in self._d.current_url:
                        self._d.get(console_url)
                    return self._d
                except Exception:
                    self._d = None
            self.launch()
            return self._d

    def quit(self):
        with self._lock:
            if self._d:
                try:
                    self._d.quit()
                except Exception:
                    pass
                self._d = None

    @property
    def alive(self) -> bool:
        with self._lock:
            if not self._d:
                return False
            try:
                self._d.window_handles
                return True
            except Exception:
                return False

    def set_base_url(self, url: str):
        self._base_url = url

    # ---------- 标签页操作 ----------

    def open_collect_tab(self, inject_js, console_url=None):
        """新开标签页并导航到高德地图，注入采集钩子+浮窗。
           此方法同步执行，调用方应放到线程池。"""
        d = self.ensure_driver(console_url)
        with self._lock:
            d.switch_to.new_window('tab')
            d.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument',
                              {'source': inject_js})
            d.get(HOME_URL)
            d.execute_script(inject_js)

    def inject_js_on_all_tabs(self, inject_js: str):
        """对所有已有标签页注入钩子（用于启动时预注入）"""
        d = self.ensure_driver()
        with self._lock:
            for h in d.window_handles:
                try:
                    d.switch_to.window(h)
                    d.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument',
                                      {'source': inject_js})
                except Exception:
                    pass

    def eval_on_active_tab(self, script: str):
        """在当前激活的标签页执行 JS"""
        d = self.ensure_driver()
        with self._lock:
            return d.execute_script(script)
