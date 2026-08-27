/**
 * Hook.js - 运行在页面主环境 (MAIN world)
 * 拦截 fetch/XHR 响应，提取 AOI 数据
 * 通过 postMessage 发送给隔离环境的 content.js
 * 注意: 纯被动捕获页面自身请求，不主动补发 (手动请求会被风控419拦截)
 */
(() => {
  if (window.__aoiHooked) return;
  window.__aoiHooked = 1;

  function detailNote(t) {
    try {
      const d = JSON.parse(t);
      if (d && d.status === '1' && d.data && d.data.base) {
        window.postMessage({ type: '__AOI_DETAIL', data: t }, '*');
      }
    } catch {}
  }

  function searchNote(t) {
    try {
      const d = JSON.parse(t);
      const pl = (d && d.data && d.data.poi_list) || [];
      const rings = {};
      for (let i = 0; i < pl.length; i++) {
        const pp = pl[i], dl = pp.domain_list || [];
        for (let k = 0; k < dl.length; k++) {
          if (dl[k].name === 'aoi' && dl[k].value && dl[k].value.indexOf('_') > 0 && pp.id) {
            rings[pp.id] = {
              v: dl[k].value,
              x: pp.longitude, y: pp.latitude,
              a: pp.address || '', c: pp.cityname || ''
            };
          }
        }
      }
      if (Object.keys(rings).length > 0) {
        const first = pl[0];
        window.postMessage({ type: '__AOI_SEARCH', rings, first }, '*');
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

  // Hook fetch
  const of = window.fetch;
  window.fetch = function() {
    const p = of.apply(this, arguments);
    try { p.clone().text().then(t => route(arguments[0], t)).catch(() => {}); } catch {}
    return p;
  };

  // Hook XHR
  const oo = XMLHttpRequest.prototype.open;
  const os_ = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function(m, u) {
    this.__u = String(u);
    return oo.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function() {
    this.addEventListener('load', function() {
      if (this.__u) route(this.__u, this.responseText);
    });
    return os_.apply(this, arguments);
  };

  console.log('[AOI Hook] fetch/XHR hook 已安装 (MAIN world)');
})();
