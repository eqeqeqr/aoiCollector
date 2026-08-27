# -*- coding: utf-8 -*-
"""Selenium 浏览器控制器 - 管理 Edge 生命周期与标签页操作"""
import os, time, shutil, subprocess, tempfile, threading
from selenium import webdriver
from selenium.webdriver.edge.options import Options

DEBUG_PORT = '9223'
HOME_URL = 'https://map.gaode.com'


def _find_edge():
    for p in [shutil.which('msedge'),
              r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
              r'C:\Program Files\Microsoft\Edge\Application\msedge.exe']:
        if p and os.path.exists(p):
            return p
    raise RuntimeError('未找到 Edge 浏览器，请先安装 Microsoft Edge')


def _kill_stale():
    """清理残留的受控 Edge 进程"""
    if os.name != 'nt':
        return
    out = subprocess.run(
        ['wmic', 'process', 'where', "name='msedge.exe'", 'get', 'ProcessId,CommandLine'],
        capture_output=True, text=True)
    for line in (out.stdout or '').splitlines():
        if 'gaode_collect_profile' in line or ('--remote-debugging-port=%s' % DEBUG_PORT) in line:
            pid = line.strip().split()[-1]
            if pid.isdigit():
                subprocess.run(['taskkill', '/F', '/PID', pid], capture_output=True)


class BrowserController:
    """管理一个受控 Edge 实例，所有 Selenium 操作通过 run_in_thread 在
       后台线程执行，FastAPI 层不会被阻塞。"""

    def __init__(self):
        self._d = None          # webdriver.Edge 实例
        self._lock = threading.RLock()
        self._base_url = None   # 运行时由 app.py 注入

    # ---------- 生命周期 ----------

    def launch(self):
        """启动受控 Edge 并连接 Selenium"""
        _kill_stale()
        time.sleep(1)
        edge = _find_edge()
        profile = os.path.join(tempfile.gettempdir(), 'gaode_collect_profile')
        cmd = [edge,
               '--remote-debugging-port=%s' % DEBUG_PORT,
               '--user-data-dir=' + profile,
               '--disable-blink-features=AutomationControlled']
        subprocess.Popen(cmd)
        time.sleep(4)
        opts = Options()
        opts.add_experimental_option('debuggerAddress', '127.0.0.1:%s' % DEBUG_PORT)
        with self._lock:
            self._d = webdriver.Edge(options=opts)

    def ensure_driver(self):
        """确保浏览器可用，不可用则重新拉起"""
        with self._lock:
            if self._d is not None:
                try:
                    self._d.window_handles
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

    def open_collect_tab(self, inject_js: str):
        """新开标签页并导航到高德地图，注入采集钩子+浮窗。
           此方法同步执行，调用方应放到线程池。"""
        d = self.ensure_driver()
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
