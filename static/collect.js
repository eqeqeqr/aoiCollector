// AOI 采集钩子 + 浮窗 —— 注入高德地图页面
// 由 browser.py 通过 CDP addScriptToEvaluateOnNewDocument 注入
(function(){
  if(window.__aoiHooked) return;
  window.__aoiHooked = 1;
  window.__aoiLast = null;
  window.__aoiRings = {};

  // ---- 拦截接口响应，提取 AOI 数据 ----
  function detailNote(t){
    try{
      var d = JSON.parse(t);
      if(d && d.status==='1' && d.data && d.data.base){
        window.__aoiLast = t;
        var el = document.getElementById('__aoiPanelInfo');
        if(el) el.textContent = '已捕获: ' + (d.data.base.name||'?');
      } else if(d && d.ret){
        var el2 = document.getElementById('__aoiPanelInfo');
        if(el2) el2.textContent = '本次请求被风控拦截，请稍候再点击或先登录账号';
      }
    }catch(e){}
  }

  function searchNote(t){
    try{
      var d = JSON.parse(t);
      var pl = (d && d.data && d.data.poi_list) || [];
      for(var i=0; i<pl.length; i++){
        var pp = pl[i], dl = pp.domain_list || [];
        for(var k=0; k<dl.length; k++){
          if(dl[k].name==='aoi' && dl[k].value && dl[k].value.indexOf('_')>0 && pp.id){
            window.__aoiRings[pp.id] = {
              v: dl[k].value,
              x: pp.longitude, y: pp.latitude,
              a: pp.address||'', c: pp.cityname||''
            };
          }
        }
      }
      // 搜索命中后直接设为"已捕获"，不用再点地图
      if(pl.length > 0){
        var first = pl[0];
        window.__aoiLast = JSON.stringify({status:'1',data:{base:{
          poiid:first.id, name:first.name, x:first.longitude, y:first.latitude
        }}});
        var el = document.getElementById('__aoiPanelInfo');
        if(el) el.textContent = '已捕获: ' + first.name;
      }
    }catch(e){}
  }

  function route(url, t){
    try{
      url = String(url);
      if(url.indexOf('/detail/get/detail') >= 0) detailNote(t);
      else if(url.indexOf('poiInfo') >= 0) searchNote(t);
    }catch(e){}
  }

  // 手动触发搜索（点建筑采集时自动调用，补齐该 POI 的搜索环）
  window.__aoiFetchRing = function(kw, pid, cb){
    var url = '/service/poiInfo?query_type=TQUERY&pagesize=20&pagenum=1&qii=true&cluster_state=5'
      + '&need_utd=true&utd_sceneid=1000&div=PC1000&addr_poi_merge=true&is_classify=true'
      + '&zoom=17&keywords=' + encodeURIComponent(kw);
    fetch(url).then(function(r){ return r.text(); })
      .then(function(t){
        try{
          var j = JSON.parse(t);
          var pl = (j.data && j.data.poi_list) || [];
          for(var i=0; i<pl.length; i++){
            if(pl[i].id === pid){
              var dl = pl[i].domain_list || [];
              for(var k=0; k<dl.length; k++){
                if(dl[k].name==='aoi' && dl[k].value && dl[k].value.indexOf('_')>0){
                  window.__aoiRings[pid] = {
                    v: dl[k].value,
                    x: pl[i].longitude, y: pl[i].latitude,
                    a: pl[i].address||'', c: pl[i].cityname||''
                  };
                  cb(true); return;
                }
              }
            }
          }
          cb(false);
        }catch(e){ cb(false); }
      }).catch(function(){ cb(false); });
  };

  // ---- Hook fetch / XHR ----
  var of = window.fetch;
  window.fetch = function(u){
    var p = of.apply(this, arguments);
    try{ p.clone().text().then(function(t){ route(u,t); }).catch(function(){}); }catch(e){}
    return p;
  };
  var oo = XMLHttpRequest.prototype.open, os_ = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function(m,u){ this.__u = String(u); return oo.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function(){
    this.addEventListener('load', function(){ if(this.__u) route(this.__u, this.responseText); });
    return os_.apply(this, arguments);
  };

  // ---- 浮窗 UI ----
  function mount(){
    if(location.host.indexOf('gaode.com') < 0) return;
    if(document.getElementById('__aoiPanel')) return;
    if(!document.body){ setTimeout(mount, 200); return; }

    var API = window.__aoiApiBase || '';
    var PREVIEW = window.__aoiPreviewUrl || (API + '/preview');

    var panel = document.createElement('div');
    panel.id = '__aoiPanel';
    panel.style.cssText = 'position:fixed;top:80px;right:20px;z-index:999999;background:#fff;'
      + 'border:2px solid #1677ff;border-radius:8px;padding:10px 14px;font-size:13px;'
      + 'font-family:Microsoft YaHei,sans-serif;box-shadow:0 2px 10px rgba(0,0,0,.25);width:230px;';

    panel.innerHTML =
      '<div id="__aoiPanelHead" style="display:flex;align-items:center;justify-content:space-between;'
      + 'margin-bottom:6px;cursor:move;">'
      + '<span style="font-weight:bold;color:#1677ff;">AOI采集工具</span>'
      + '<a href="' + PREVIEW + '" style="color:#1677ff;font-size:12px;text-decoration:none;'
      + 'cursor:pointer;">打开预览模式 →</a></div>'
      + '<div style="color:#999;font-size:11px;margin-bottom:6px;">GCJ-02将自动转为WGS-84保存</div>'
      + '<div id="__aoiPanelInfo" style="color:#555;margin-bottom:8px;min-height:18px;">等待点击建筑...</div>'
      + '<div id="__aoiPanelSaved" style="color:#52c41a;margin-bottom:8px;min-height:16px;"></div>'
      + '<button id="__aoiBtn" style="background:#1677ff;color:#fff;border:none;border-radius:4px;'
      + 'padding:6px 14px;cursor:pointer;font-size:13px;">采集当前AOI</button>';
    document.body.appendChild(panel);

    // 自由拖动
    panel.addEventListener('mousedown', function(e){
      var t = e.target;
      if(t.tagName==='BUTTON' || t.tagName==='A' || t.closest('button,a')) return;
      var rect = panel.getBoundingClientRect();
      var sx=e.clientX, sy=e.clientY, ol=rect.left, ot=rect.top;
      function mv(ev){
        var nl = Math.max(0, Math.min(window.innerWidth-panel.offsetWidth, ol+ev.clientX-sx));
        var nt = Math.max(0, Math.min(window.innerHeight-panel.offsetHeight, ot+ev.clientY-sy));
        panel.style.left = nl+'px'; panel.style.top = nt+'px'; panel.style.right = 'auto';
        ev.preventDefault();
      }
      function up(){
        document.removeEventListener('mousemove', mv);
        document.removeEventListener('mouseup', up);
      }
      document.addEventListener('mousemove', mv);
      document.addEventListener('mouseup', up);
      e.preventDefault();
    });

    // 采集按钮
    document.getElementById('__aoiBtn').addEventListener('click', function(){
      var btn = this, saved = document.getElementById('__aoiPanelSaved'),
          info = document.getElementById('__aoiPanelInfo');
      var raw = window.__aoiLast;
      if(!raw){ saved.style.color='#52c41a'; saved.textContent='请先点击目标建筑'; return; }
      var base = {};
      try{ base = (JSON.parse(raw).data||{}).base||{}; }catch(e){}
      if(!base.poiid){ saved.style.color='#e64c3c'; saved.textContent='点击数据异常，请重新点击建筑'; return; }

      function submit(){
        var payload = JSON.stringify({raw: raw, rings: window.__aoiRings||{}});
        fetch(API + '/api/report', {method:'POST', headers:{'Content-Type':'text/plain;charset=utf-8'}, body: payload})
          .then(function(r){ return r.text(); })
          .then(function(m){
            var isWarn = m.indexOf('[警告]')===0 || m.indexOf('未保存')>=0;
            if(!isWarn && m.indexOf('已保存')>=0){ window.__aoiLast=null; info.textContent='等待点击建筑...'; }
            saved.style.color = isWarn ? '#e64c3c' : '#52c41a';
            saved.textContent = m.length>90 ? m.substring(0,90)+'…' : m;
            btn.textContent = '采集当前AOI';
          })
          .catch(function(e){ saved.textContent='上报失败:'+e.message; btn.textContent='采集当前AOI'; });
      }

      var entry = (window.__aoiRings||{})[base.poiid];
      if(entry && entry.v){ submit(); return; }
      saved.style.color='#888'; saved.textContent='正在通过搜索接口获取AOI...';
      btn.textContent='采集中...';
      window.__aoiFetchRing(base.name, base.poiid, function(ok){ submit(); });
    });
  }

  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded', mount);
  else mount();
})();
