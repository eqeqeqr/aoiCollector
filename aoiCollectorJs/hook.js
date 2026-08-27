/**
 * Hook.js - 运行在页面主环境 (MAIN world)
 * 拦截 fetch/XHR 响应，提取 AOI 数据
 * 通过 postMessage 发送给隔离环境的 content.js
 * 接收 content.js 发来的 __AOI_FETCH_RING 请求，用页面的 fetch 搜索 AOI
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
      if (pl.length > 0) {
        window.postMessage({ type: '__AOI_SEARCH', rings, first: pl[0] }, '*');
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

  // 接收 content.js 发来的搜索 AOI 请求
  window.addEventListener('message', (e) => {
    if (e.source !== window) return;
    if (e.data && e.data.type === '__AOI_FETCH_RING') {
      const { name, poiid } = e.data;
      console.log('[AOI Hook] 收到搜索请求:', name, poiid);
      const url = '/service/poiInfo?query_type=TQUERY&pagesize=20&pagenum=1&qii=true&cluster_state=5'
        + '&need_utd=true&utd_sceneid=1000&div=PC1000&addr_poi_merge=true&is_classify=true'
        + '&zoom=17&keywords=' + encodeURIComponent(name);
      of.call(window, url).then(r => r.text()).then(t => {
        try {
          const j = JSON.parse(t);
          const pl = (j.data && j.data.poi_list) || [];
          for (let i = 0; i < pl.length; i++) {
            if (pl[i].id === poiid) {
              const dl = pl[i].domain_list || [];
              for (let k = 0; k < dl.length; k++) {
                if (dl[k].name === 'aoi' && dl[k].value && dl[k].value.indexOf('_') > 0) {
                  const rings = {};
                  rings[poiid] = {
                    v: dl[k].value,
                    x: pl[i].longitude, y: pl[i].latitude,
                    a: pl[i].address || '', c: pl[i].cityname || ''
                  };
                  window.postMessage({ type: '__AOI_SEARCH', rings, first: pl[i] }, '*');
                  console.log('[AOI Hook] 搜索到 AOI:', name);
                  return;
                }
              }
            }
          }
          console.log('[AOI Hook] 未找到 AOI:', name);
        } catch {}
      }).catch(() => {});
    }
  });

  console.log('[AOI Hook] fetch/XHR hook 已安装 (MAIN world)');
})();
