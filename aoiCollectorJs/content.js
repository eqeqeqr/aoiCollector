/**
 * Content Script - 注入高德地图页面 (隔离环境)
 * 接收 hook.js (MAIN world) 通过 postMessage 发来的 AOI 数据，显示采集面板
 * AOI 边界获取策略: 纯被动捕获页面自身的搜索响应 (手动补发请求会被风控419拦截)
 */
(() => {
  console.log('[AOI采集] === Content script 启动 ===', location.host, new Date().toLocaleTimeString());
  if (window.__aoiPanelReady) { console.log('[AOI采集] 已挂载，跳过'); return; }
  window.__aoiPanelReady = 1;

  let aoiLast = null;       // 最近一次 detail/search 捕获的 base 原始JSON
  let aoiRings = {};        // poiid -> { v, x, y, a, c } 由页面搜索响应累积
  let pendingSave = null;   // 挂起的保存任务 { base, raw, until }

  // ---- 保存到 background ----
  function submitSave(base, raw) {
    const saved = document.getElementById('__aoiPanelSaved');
    const info = document.getElementById('__aoiPanelInfo');
    const btn = document.getElementById('__aoiBtn');
    const payload = { raw, rings: aoiRings };
    console.log('[AOI采集] 发送保存请求:', base.name);
    try {
      chrome.runtime.sendMessage({ type: 'SAVE_AOI', data: payload }, res => {
        console.log('[AOI采集] 收到响应:', res);
        if (chrome.runtime.lastError) {
          console.error('[AOI采集] 消息错误:', chrome.runtime.lastError);
          if (saved) { saved.style.color = '#e64c3c'; saved.textContent = '通信失败: ' + chrome.runtime.lastError.message; }
          if (btn) btn.textContent = '采集当前AOI';
          return;
        }
        if (res && res.ok) {
          if (saved) { saved.style.color = '#52c41a'; saved.textContent = res.msg || '已保存'; }
          aoiLast = null;
          if (info) info.textContent = '等待搜索或点击建筑...';
        } else {
          if (saved) { saved.style.color = '#e64c3c'; saved.textContent = (res && res.msg) || '保存失败'; }
        }
        if (btn) btn.textContent = '采集当前AOI';
      });
    } catch(e) {
      console.error('[AOI采集] sendMessage异常:', e);
      if (saved) { saved.style.color = '#e64c3c'; saved.textContent = '异常: ' + e.message; }
      if (btn) btn.textContent = '采集当前AOI';
    }
  }

  // ---- 接收 hook.js (MAIN world) 数据 ----
  window.addEventListener('message', (e) => {
    if (e.source !== window) return;
    const el = document.getElementById('__aoiPanelInfo');
    if (e.data && e.data.type === '__AOI_DETAIL') {
      aoiLast = e.data.data;
      try {
        const d = JSON.parse(aoiLast);
        if (el) el.textContent = '已捕获: ' + (d.data?.base?.name || '?');
      } catch {}
      console.log('[AOI采集] 收到 detail 数据');
    }
    if (e.data && e.data.type === '__AOI_SEARCH') {
      aoiRings = { ...aoiRings, ...e.data.rings };
      if (e.data.first) {
        aoiLast = JSON.stringify({ status: '1', data: { base: {
          poiid: e.data.first.id, name: e.data.first.name,
          x: e.data.first.longitude, y: e.data.first.latitude
        }}});
        if (el) el.textContent = '已捕获: ' + e.data.first.name;
      }
      console.log('[AOI采集] 收到 search 数据, AOI数:', Object.keys(aoiRings).length);
      // 挂起任务获得边界 → 自动继续
      if (pendingSave && Date.now() < pendingSave.until) {
        const pid = pendingSave.base.poiid;
        if (aoiRings[pid] && aoiRings[pid].v) {
          console.log('[AOI采集] 挂起任务获得AOI边界，自动提交:', pendingSave.base.name);
          const p = pendingSave;
          pendingSave = null;
          submitSave(p.base, p.raw);
        }
      }
    }
  });

  // ---- 触发页面自身的搜索 (填入搜索框并回车, 请求带页面风控签名) ----
  function triggerPageSearch(name) {
    try {
      const input = document.getElementById('searchipt');
      if (!input) return false;
      input.focus();
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
      setter.call(input, name);
      input.dispatchEvent(new Event('input', { bubbles: true }));
      for (const t of ['keydown', 'keypress', 'keyup']) {
        input.dispatchEvent(new KeyboardEvent(t, {
          key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true
        }));
      }
      console.log('[AOI采集] 已自动触发搜索:', name);
      return true;
    } catch(e) {
      console.error('[AOI采集] 自动搜索失败:', e);
      return false;
    }
  }

  // ---- 浮窗 UI ----
  function mount() {
    try {
      if (location.host.indexOf('gaode.com') < 0) return;
      if (document.getElementById('__aoiPanel')) return;
      if (!document.body) { setTimeout(mount, 200); return; }
      console.log('[AOI采集] 开始挂载面板');

      const panel = document.createElement('div');
      panel.id = '__aoiPanel';
      panel.style.cssText = 'position:fixed;top:80px;right:20px;z-index:999999;background:#fff;'
        + 'border:2px solid #1677ff;border-radius:8px;padding:10px 14px;font-size:13px;'
        + 'font-family:Microsoft YaHei,sans-serif;box-shadow:0 2px 10px rgba(0,0,0,.25);width:230px;';

      panel.innerHTML =
        '<div id="__aoiPanelHead" style="display:flex;align-items:center;justify-content:space-between;'
        + 'margin-bottom:6px;cursor:move;">'
        + '<span style="font-weight:bold;color:#1677ff;">AOI采集工具</span>'
        + '<span style="font-size:11px;color:#888;">插件版</span></div>'
        + '<div style="color:#999;font-size:11px;margin-bottom:6px;">GCJ-02将自动转为WGS-84</div>'
        + '<div id="__aoiPanelInfo" style="color:#555;margin-bottom:8px;min-height:18px;">等待搜索或点击建筑...</div>'
        + '<div id="__aoiPanelSaved" style="color:#52c41a;margin-bottom:8px;min-height:16px;"></div>'
        + '<button id="__aoiBtn" style="background:#1677ff;color:#fff;border:none;border-radius:4px;'
        + 'padding:6px 14px;cursor:pointer;font-size:13px;">采集当前AOI</button>'
        + '<div style="margin-top:8px;border-top:1px solid #eee;padding-top:6px;">'
        + '<button id="__aoiTest" style="background:#888;color:#fff;border:none;border-radius:4px;'
        + 'padding:3px 10px;cursor:pointer;font-size:11px;">测试连接</button>'
        + '<span id="__aoiTestResult" style="font-size:11px;margin-left:6px;"></span></div>';
      document.body.appendChild(panel);

      // 拖动
      panel.addEventListener('mousedown', function(e) {
        const t = e.target;
        if (t.tagName === 'BUTTON' || t.tagName === 'A' || t.closest('button,a')) return;
        const rect = panel.getBoundingClientRect();
        const sx = e.clientX, sy = e.clientY, ol = rect.left, ot = rect.top;
        function mv(ev) {
          const nl = Math.max(0, Math.min(window.innerWidth - panel.offsetWidth, ol + ev.clientX - sx));
          const nt = Math.max(0, Math.min(window.innerHeight - panel.offsetHeight, ot + ev.clientY - sy));
          panel.style.left = nl + 'px'; panel.style.top = nt + 'px'; panel.style.right = 'auto';
          ev.preventDefault();
        }
        function up() { document.removeEventListener('mousemove', mv); document.removeEventListener('mouseup', up); }
        document.addEventListener('mousemove', mv);
        document.addEventListener('mouseup', up);
        e.preventDefault();
      });

      // 采集按钮
      document.getElementById('__aoiBtn').addEventListener('click', function() {
        const saved = document.getElementById('__aoiPanelSaved');
        const raw = aoiLast;
        if (!raw) { saved.style.color = '#52c41a'; saved.textContent = '请先搜索或点击目标建筑'; return; }
        let base = {};
        try { base = (JSON.parse(raw).data || {}).base || {}; } catch {}
        if (!base.poiid) { saved.style.color = '#e64c3c'; saved.textContent = '数据异常，请重新点击'; return; }

        const entry = aoiRings[base.poiid];
        if (entry && entry.v) { submitSave(base, raw); return; }

        // 无边界: 自动触发页面自身搜索 (走页面签名管线, 避免风控419), 60秒窗口内等结果
        this.textContent = '采集中...';
        saved.style.color = '#888';
        const kw = base.name.replace(/\(.*\)$/, '').trim();
        pendingSave = { base, raw, until: Date.now() + 60000 };
        if (triggerPageSearch(kw)) {
          saved.textContent = '正在搜索「' + kw + '」获取边界...';
        } else {
          saved.textContent = '未捕获边界：请在搜索框搜「' + kw + '」后自动继续';
        }
        setTimeout(() => {
          if (pendingSave && Date.now() >= pendingSave.until) {
            pendingSave = null;
            const s2 = document.getElementById('__aoiPanelSaved');
            const b2 = document.getElementById('__aoiBtn');
            if (s2) { s2.style.color = '#e64c3c'; s2.textContent = '获取边界超时，请稍后重试'; }
            if (b2) b2.textContent = '采集当前AOI';
          }
        }, 61000);
      });

      // 测试连接按钮
      document.getElementById('__aoiTest').addEventListener('click', function() {
        const el = document.getElementById('__aoiTestResult');
        el.style.color = '#888'; el.textContent = '测试中...';
        try {
          chrome.runtime.sendMessage({ type: 'GET_COUNT' }, res => {
            if (chrome.runtime.lastError) {
              el.style.color = '#e64c3c';
              el.textContent = '❌ ' + chrome.runtime.lastError.message;
              return;
            }
            if (res && res.ok) {
              el.style.color = '#52c41a';
              el.textContent = '✅ 连接正常，已采集 ' + res.count + ' 个';
            } else {
              el.style.color = '#e64c3c';
              el.textContent = '❌ ' + ((res && res.err) || '无响应');
            }
          });
        } catch(e) {
          el.style.color = '#e64c3c';
          el.textContent = '❌ ' + e.message;
        }
      });
    } catch(e) { console.error('[AOI采集] mount异常:', e); }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
  else mount();

  // DOM 变化监控: 面板被清除时自动重建
  const observer = new MutationObserver(() => {
    if (location.host.indexOf('gaode.com') < 0) return;
    if (!document.getElementById('__aoiPanel') && document.body) {
      console.log('[AOI采集] 面板丢失，重新挂载');
      mount();
    }
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });

  // 监听 background 发来的保存结果
  chrome.runtime.onMessage.addListener((msg) => {
    if (msg.type === 'SAVED') {
      const saved = document.getElementById('__aoiPanelSaved');
      if (saved) {
        saved.style.color = '#52c41a';
        saved.textContent = msg.msg;
        setTimeout(() => { saved.textContent = ''; }, 5000);
      }
    }
  });
})();
