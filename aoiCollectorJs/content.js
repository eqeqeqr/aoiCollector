/**
 * Content Script - 注入高德地图页面
 * 拦截 fetch/XHR 响应，捕获 AOI 数据，显示采集面板
 */
(() => {
  if (window.__aoiHooked) return;
  window.__aoiHooked = 1;
  window.__aoiLast = null;
  window.__aoiRings = {};

  // ---- 拦截接口响应 ----
  function detailNote(t) {
    try {
      const d = JSON.parse(t);
      if (d && d.status === '1' && d.data && d.data.base) {
        window.__aoiLast = t;
        const el = document.getElementById('__aoiPanelInfo');
        if (el) el.textContent = '已捕获: ' + (d.data.base.name || '?');
      } else if (d && d.ret) {
        const el2 = document.getElementById('__aoiPanelInfo');
        if (el2) el2.textContent = '被风控拦截，请稍候再试或登录账号';
      }
    } catch {}
  }

  function searchNote(t) {
    try {
      const d = JSON.parse(t);
      const pl = (d && d.data && d.data.poi_list) || [];
      for (let i = 0; i < pl.length; i++) {
        const pp = pl[i], dl = pp.domain_list || [];
        for (let k = 0; k < dl.length; k++) {
          if (dl[k].name === 'aoi' && dl[k].value && dl[k].value.indexOf('_') > 0 && pp.id) {
            window.__aoiRings[pp.id] = {
              v: dl[k].value,
              x: pp.longitude, y: pp.latitude,
              a: pp.address || '', c: pp.cityname || ''
            };
          }
        }
      }
      // 搜索命中后直接设为"已捕获"
      if (pl.length > 0) {
        const first = pl[0];
        window.__aoiLast = JSON.stringify({ status: '1', data: { base: {
          poiid: first.id, name: first.name, x: first.longitude, y: first.latitude
        }}});
        const el = document.getElementById('__aoiPanelInfo');
        if (el) el.textContent = '已捕获: ' + first.name;
      }
    } catch {}
  }

  function route(url, t) {
    try {
      url = String(url);
      if (url.indexOf('/detail/get/detail') >= 0) detailNote(t);
      else if (url.indexOf('poiInfo') >= 0) searchNote(t);
    } catch {}
  }

  // 手动触发搜索
  window.__aoiFetchRing = function(kw, pid, cb) {
    const url = '/service/poiInfo?query_type=TQUERY&pagesize=20&pagenum=1&qii=true&cluster_state=5'
      + '&need_utd=true&utd_sceneid=1000&div=PC1000&addr_poi_merge=true&is_classify=true'
      + '&zoom=17&keywords=' + encodeURIComponent(kw);
    fetch(url).then(r => r.text()).then(t => {
      try {
        const j = JSON.parse(t);
        const pl = (j.data && j.data.poi_list) || [];
        for (let i = 0; i < pl.length; i++) {
          if (pl[i].id === pid) {
            const dl = pl[i].domain_list || [];
            for (let k = 0; k < dl.length; k++) {
              if (dl[k].name === 'aoi' && dl[k].value && dl[k].value.indexOf('_') > 0) {
                window.__aoiRings[pid] = {
                  v: dl[k].value,
                  x: pl[i].longitude, y: pl[i].latitude,
                  a: pl[i].address || '', c: pl[i].cityname || ''
                };
                cb(true); return;
              }
            }
          }
        }
        cb(false);
      } catch { cb(false); }
    }).catch(() => cb(false));
  };

  // ---- Hook fetch / XHR ----
  const of = window.fetch;
  window.fetch = function(u) {
    const p = of.apply(this, arguments);
    try { p.clone().text().then(t => route(u, t)).catch(() => {}); } catch {}
    return p;
  };
  const oo = XMLHttpRequest.prototype.open, os_ = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function(m, u) { this.__u = String(u); return oo.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function() {
    this.addEventListener('load', function() { if (this.__u) route(this.__u, this.responseText); });
    return os_.apply(this, arguments);
  };

  // ---- 浮窗 UI ----
  function mount() {
    if (location.host.indexOf('gaode.com') < 0) return;
    if (document.getElementById('__aoiPanel')) return;
    if (!document.body) { setTimeout(mount, 200); return; }

    const panel = document.createElement('div');
    panel.id = '__aoiPanel';
    panel.style.cssText = 'position:fixed;top:80px;right:20px;z-index:999999;background:#fff;'
      + 'border:2px solid #1677ff;border-radius:8px;padding:10px 14px;font-size:13px;'
      + 'font-family:Microsoft YaHei,sans-serif;box-shadow:0 2px 10px rgba(0,0,0,.25);width:230px;';

    panel.innerHTML =
      '<div id="__aoiPanelHead" style="display:flex;align-items:center;justify-content:space-between;'
      + 'margin-bottom:6px;cursor:move;">'
      + '<span style="font-weight:bold;color:#1677ff;">AOI采集工具</span>'
      + '<span style="font-size:11px;color:#888;">浏览器插件版</span></div>'
      + '<div style="color:#999;font-size:11px;margin-bottom:6px;">GCJ-02将自动转为WGS-84</div>'
      + '<div id="__aoiPanelInfo" style="color:#555;margin-bottom:8px;min-height:18px;">等待搜索或点击建筑...</div>'
      + '<div id="__aoiPanelSaved" style="color:#52c41a;margin-bottom:8px;min-height:16px;"></div>'
      + '<button id="__aoiBtn" style="background:#1677ff;color:#fff;border:none;border-radius:4px;'
      + 'padding:6px 14px;cursor:pointer;font-size:13px;">采集当前AOI</button>';
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
      const raw = window.__aoiLast;
      if (!raw) { saved.style.color = '#52c41a'; saved.textContent = '请先搜索或点击目标建筑'; return; }
      let base = {};
      try { base = (JSON.parse(raw).data || {}).base || {}; } catch {}
      if (!base.poiid) { saved.style.color = '#e64c3c'; saved.textContent = '数据异常，请重新点击'; return; }

      function submit() {
        const payload = JSON.stringify({ raw, rings: window.__aoiRings || {} });
        chrome.runtime.sendMessage({ type: 'SAVE_AOI', data: JSON.parse(payload) }, res => {
          if (res && res.ok) {
            saved.style.color = '#52c41a';
            saved.textContent = res.msg || '已保存';
            window.__aoiLast = null;
            info.textContent = '等待搜索或点击建筑...';
          } else {
            saved.style.color = '#e64c3c';
            saved.textContent = (res && res.msg) || '保存失败';
          }
          btn.textContent = '采集当前AOI';
        });
      }

      const entry = (window.__aoiRings || {})[base.poiid];
      if (entry && entry.v) { submit(); return; }
      saved.style.color = '#888'; saved.textContent = '正在获取AOI...';
      btn.textContent = '采集中...';
      window.__aoiFetchRing(base.name, base.poiid, () => submit());
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
  else mount();

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
