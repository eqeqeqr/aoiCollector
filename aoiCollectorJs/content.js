/**
 * Content Script - 注入高德地图页面 (隔离环境)
 * 接收 hook.js (MAIN world) 通过 postMessage 发来的 AOI 数据，显示采集面板
 */
(() => {
  console.log('[AOI采集] === Content script 启动 ===', location.host, new Date().toLocaleTimeString());
  if (window.__aoiPanelReady) { console.log('[AOI采集] 已挂载，跳过'); return; }
  window.__aoiPanelReady = 1;

  // 隔离环境的本地变量，接收 hook.js 发来的数据
  let aoiLast = null;
  let aoiRings = {};

  // 监听 hook.js (MAIN world) 通过 postMessage 发来的数据
  window.addEventListener('message', (e) => {
    if (e.source !== window) return;
    if (e.data && e.data.type === '__AOI_DETAIL') {
      aoiLast = e.data.data;
      const el = document.getElementById('__aoiPanelInfo');
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
        const el = document.getElementById('__aoiPanelInfo');
        if (el) el.textContent = '已捕获: ' + e.data.first.name;
      }
      console.log('[AOI采集] 收到 search 数据, AOI数:', Object.keys(aoiRings).length);
    }
  });

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
        const btn = this, saved = document.getElementById('__aoiPanelSaved'),
              info = document.getElementById('__aoiPanelInfo');
        const raw = aoiLast;
        if (!raw) { saved.style.color = '#52c41a'; saved.textContent = '请先搜索或点击目标建筑'; return; }
        let base = {};
        try { base = (JSON.parse(raw).data || {}).base || {}; } catch {}
        if (!base.poiid) { saved.style.color = '#e64c3c'; saved.textContent = '数据异常，请重新点击'; return; }

        btn.textContent = '采集中...';
        saved.style.color = '#888'; saved.textContent = '正在保存...';

        function submit() {
          const payload = { raw, rings: aoiRings };
          console.log('[AOI采集] 发送保存请求:', base.name);
          try {
            chrome.runtime.sendMessage({ type: 'SAVE_AOI', data: payload }, res => {
              console.log('[AOI采集] 收到响应:', res);
              if (chrome.runtime.lastError) {
                console.error('[AOI采集] 消息错误:', chrome.runtime.lastError);
                saved.style.color = '#e64c3c';
                saved.textContent = '通信失败: ' + chrome.runtime.lastError.message;
                btn.textContent = '采集当前AOI';
                return;
              }
              if (res && res.ok) {
                saved.style.color = '#52c41a';
                saved.textContent = res.msg || '已保存';
                aoiLast = null;
                info.textContent = '等待搜索或点击建筑...';
              } else {
                saved.style.color = '#e64c3c';
                saved.textContent = (res && res.msg) || '保存失败';
              }
              btn.textContent = '采集当前AOI';
            });
          } catch(e) {
            console.error('[AOI采集] sendMessage异常:', e);
            saved.style.color = '#e64c3c';
            saved.textContent = '异常: ' + e.message;
            btn.textContent = '采集当前AOI';
          }
        }

        const entry = aoiRings[base.poiid];
        if (entry && entry.v) { submit(); return; }
        saved.style.color = '#888'; saved.textContent = '正在获取AOI...';
        btn.textContent = '采集中...';
        window.postMessage({ type: '__AOI_FETCH_RING', name: base.name, poiid: base.poiid }, '*');
        // 等 hook.js 回传数据再 submit
        let tries = 0;
        const wait = setInterval(() => {
          tries++;
          if (aoiRings[base.poiid] && aoiRings[base.poiid].v) { clearInterval(wait); submit(); }
          else if (tries > 20) { clearInterval(wait); submit(); }
        }, 500);
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
