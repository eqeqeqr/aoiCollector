# -*- coding: utf-8 -*-
# AOI半自动采集工具（独立版·Web控制台）
# 原理: 启动本地Web服务(默认127.0.0.1:9224)并自动打开控制主页；
#       主页可【新开收集窗口】(真实Edge打开高德地图并注入采集浮窗)或【进入预览】。
#       收集窗口中浏览地图点击建筑→高德自动框出区域→点浮窗[采集当前AOI]，
#       数据经本地服务保存(shp四件套+geojson+简介.txt 并写入SQLite)。
# 坐标系: 高德原始数据为GCJ-02火星坐标，保存前通过公开逆算法逐点转换为WGS-84，
#         转换精度约0.5~2米。所有落盘数据均为WGS-84。
# 部署: pip install selenium pyshp 后运行 python collectAoiTool.py
#       本文件完全自包含，可整体拷贝到任意目录/机器部署。

import os
import sys
import json
import math
import glob
import time
import shutil
import sqlite3
import threading
import subprocess
import tempfile
from datetime import datetime
from urllib.parse import urlparse, parse_qs, unquote
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import shapefile
from selenium import webdriver
from selenium.webdriver.edge.options import Options

# ================== 可配置项 ==================
DEBUG_PORT = '9223'                                   # 受控Edge调试端口
PREVIEW_PORT = 9224                                   # 本地Web服务端口
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, 'output')         # 采集结果目录
DB_PATH = os.path.join(BASE_DIR, 'aoi.sqlite')        # SQLite库文件
HOME_URL = 'https://map.gaode.com'                    # 收集窗口打开的页面
# =============================================

# ================== GCJ-02 → WGS-84 坐标转换 ==================
x_pi = 3.14159265358979324 * 3000.0 / 180.0
pi = 3.1415926535897932384626
a = 6378245.0
ee = 0.00669342162296594323

def _transformlat(lng, lat):
    ret = -100.0 + 2.0 * lng + 3.0 * lat + 0.2 * lat * lat + \
        0.1 * lng * lat + 0.2 * math.sqrt(math.fabs(lng))
    ret += (20.0 * math.sin(6.0 * lng * pi) + 20.0 *
            math.sin(2.0 * lng * pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(lat * pi) + 40.0 *
            math.sin(lat / 3.0 * pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(lat / 12.0 * pi) + 320 *
            math.sin(lat * pi / 30.0)) * 2.0 / 3.0
    return ret

def _transformlng(lng, lat):
    ret = 300.0 + lng + 2.0 * lat + 0.1 * lng * lng + \
        0.1 * lng * lat + 0.1 * math.sqrt(math.fabs(lng))
    ret += (20.0 * math.sin(6.0 * lng * pi) + 20.0 *
            math.sin(2.0 * lng * pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(lng * pi) + 40.0 *
            math.sin(lng / 3.0 * pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(lng / 12.0 * pi) + 300.0 *
            math.sin(lng / 30.0 * pi)) * 2.0 / 3.0
    return ret

def out_of_china(lng, lat):
    return not (72.004 <= lng <= 137.8347 and 0.8293 <= lat <= 55.8271)

def gcj02towgs84(lng, lat):
    """GCJ02(火星坐标系)转WGS84"""
    if out_of_china(lng, lat):
        return [lng, lat]
    dlat = _transformlat(lng - 105.0, lat - 35.0)
    dlng = _transformlng(lng - 105.0, lat - 35.0)
    radlat = lat / 180.0 * pi
    magic = math.sin(radlat)
    magic = 1 - ee * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((a * (1 - ee)) / (magic * sqrtmagic) * pi)
    dlng = (dlng * 180.0) / (a / sqrtmagic * math.cos(radlat) * pi)
    mglat = lat + dlat
    mglng = lng + dlng
    return [lng * 2 - mglng, lat * 2 - mglat]
# ==============================================================

def hook_js():
    return """
if(!window.__aoiHooked){
window.__aoiHooked=1;
window.__aoiLast=null;
// 搜索接口自带每POI的AOI环(domain_list[aoi].value)，作为mining_shape脏数据时的备用修复源
window.__aoiRings={};
(function(){
  function detailNote(t){
    try{
      var d=JSON.parse(t);
      if(d && d.status==='1' && d.data && d.data.base){
        window.__aoiLast=t;
        var el=document.getElementById('__aoiPanelInfo');
        if(el){el.textContent='已捕获: '+(d.data.base.name||'?');}
      }else if(d && d.ret){
        var el2=document.getElementById('__aoiPanelInfo');
        if(el2){el2.textContent='本次请求被风控拦截，请稍候再点击或先登录账号';}
      }
    }catch(e){}
  }
  function searchNote(t){
    try{
      var d=JSON.parse(t);
      var pl=(d && d.data && d.data.poi_list)||[];
      for(var i=0;i<pl.length;i++){
        var pp=pl[i], dl=pp.domain_list||[];
        for(var k=0;k<dl.length;k++){
          if(dl[k].name==='aoi' && dl[k].value && dl[k].value.indexOf('_')>0 && pp.id){
            window.__aoiRings[pp.id]={v:dl[k].value,
              x:pp.longitude,y:pp.latitude,
              a:pp.address||'',c:pp.cityname||''};
          }
        }
      }
    }catch(e){}
  }
  function route(url,t){
    try{
      url=String(url);
      if(url.indexOf('/detail/get/detail')>=0){detailNote(t);}
      else if(url.indexOf('poiInfo')>=0){searchNote(t);}
    }catch(e){}
  }
  // 手动触发一次搜索(点建筑采集时自动调用,补齐该POI的搜索环)
  window.__aoiFetchRing=function(kw,pid,cb){
    var url='/service/poiInfo?query_type=TQUERY&pagesize=20&pagenum=1&qii=true&cluster_state=5'
      +'&need_utd=true&utd_sceneid=1000&div=PC1000&addr_poi_merge=true&is_classify=true'
     +'&zoom=17&keywords='+encodeURIComponent(kw);
    fetch(url).then(function(r){return r.text()})
      .then(function(t){
        try{
          var j=JSON.parse(t);
          var pl=(j.data&&j.data.poi_list)||[];
          for(var i=0;i<pl.length;i++){
            if(pl[i].id===pid){
              var dl=pl[i].domain_list||[];
              for(var k=0;k<dl.length;k++){
                if(dl[k].name==='aoi'&&dl[k].value&&dl[k].value.indexOf('_')>0){
                  window.__aoiRings[pid]={v:dl[k].value,
                    x:pl[i].longitude,y:pl[i].latitude,
                    a:pl[i].address||'',c:pl[i].cityname||''};
                  cb(true);return;
                }
              }
            }
          }
          cb(false);
        }catch(e){cb(false);}
      }).catch(function(){cb(false);});
  };
  var of=window.fetch;
  window.fetch=function(u){
    var p=of.apply(this,arguments);
    try{
      // clone后读取,避免抢占页面自身的响应体流
      p.clone().text().then(function(t){route(u,t)}).catch(function(){});
    }catch(e){}
    return p;
  };
  var oo=XMLHttpRequest.prototype.open, os_=XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open=function(m,u){this.__u=String(u);return oo.apply(this,arguments)};
  XMLHttpRequest.prototype.send=function(){
    this.addEventListener('load',function(){ if(this.__u){route(this.__u,this.responseText);} });
    return os_.apply(this,arguments);
  };
})();
}
"""

def panel_js(base, preview_url):
    """采集浮窗(含跳转预览链接)，自带DOM就绪保护与重复挂载守卫"""
    return """
(function(){
  function mount(){
    // 单窗口模式: 同一标签页也会加载控制台/预览页, 仅在高德页面挂载浮窗
    if(location.host.indexOf('gaode.com')<0){return;}
    if(document.getElementById('__aoiPanel')){return;}
    if(!document.body){setTimeout(mount,200);return;}
    var panel=document.createElement('div');
    panel.id='__aoiPanel';
    panel.style.cssText='position:fixed;top:80px;right:20px;z-index:999999;background:#fff;'
      +'border:2px solid #1677ff;border-radius:8px;padding:10px 14px;font-size:13px;'
      +'font-family:Microsoft YaHei,sans-serif;box-shadow:0 2px 10px rgba(0,0,0,.25);width:230px;';
    panel.innerHTML=
      '<div id="__aoiPanelHead" style="display:flex;align-items:center;justify-content:space-between;'
      +'margin-bottom:6px;cursor:move;">'
      +'<span style="font-weight:bold;color:#1677ff;">AOI采集工具</span>'
      +'<a href="%s" style="color:#1677ff;font-size:12px;text-decoration:none;'
      +'cursor:pointer;">打开预览模式 →</a></div>'
      +'<div style="color:#999;font-size:11px;margin-bottom:6px;">GCJ-02将自动转为WGS-84保存</div>'
      +'<div id="__aoiPanelInfo" style="color:#555;margin-bottom:8px;min-height:18px;">等待点击建筑...</div>'
      +'<div id="__aoiPanelSaved" style="color:#52c41a;margin-bottom:8px;min-height:16px;"></div>'
      +'<button id="__aoiBtn" style="background:#1677ff;color:#fff;border:none;border-radius:4px;'
      +'padding:6px 14px;cursor:pointer;font-size:13px;">采集当前AOI</button>';
    document.body.appendChild(panel);
    // 自由拖动(按住面板非按钮/链接区域即可拖)
    panel.addEventListener('mousedown',function(e){
      var t=e.target;
      if(t.tagName==='BUTTON'||t.tagName==='A'||t.closest('button,a')){return;}
      var rect=panel.getBoundingClientRect();
      var sx=e.clientX,sy=e.clientY,ol=rect.left,ot=rect.top,moved=false;
      function mv(ev){
        moved=true;
        var nl=ol+ev.clientX-sx, nt=ot+ev.clientY-sy;
        nl=Math.max(0,Math.min(window.innerWidth-panel.offsetWidth,nl));
        nt=Math.max(0,Math.min(window.innerHeight-panel.offsetHeight,nt));
        panel.style.left=nl+'px';panel.style.top=nt+'px';panel.style.right='auto';
        ev.preventDefault();
      }
      function up(){
        document.removeEventListener('mousemove',mv);
        document.removeEventListener('mouseup',up);
      }
      document.addEventListener('mousemove',mv);
      document.addEventListener('mouseup',up);
      e.preventDefault();
    });
    document.getElementById('__aoiBtn').addEventListener('click',function(){
      var btn=this,saved=document.getElementById('__aoiPanelSaved'),
          info=document.getElementById('__aoiPanelInfo');
      var raw=window.__aoiLast;
      if(!raw){saved.style.color='#52c41a';saved.textContent='请先点击目标建筑';return;}
      var base={};
      try{ base=(JSON.parse(raw).data||{}).base||{}; }catch(e){}
      if(!base.poiid){saved.style.color='#e64c3c';saved.textContent='点击数据异常，请重新点击建筑';return;}
      function submit(){
        var payload=JSON.stringify({raw:raw,rings:window.__aoiRings||{}});
        fetch('%s/api/report',{method:'POST',headers:{'Content-Type':'text/plain;charset=utf-8'},body:payload})
          .then(function(r){return r.text();})
          .then(function(m){
            var isWarn=m.indexOf('[警告]')===0||m.indexOf('未保存')>=0;
            if(!isWarn&&m.indexOf('已保存')>=0){window.__aoiLast=null;info.textContent='等待点击建筑...';}
            saved.style.color=isWarn?'#e64c3c':'#52c41a';
            saved.textContent=m.length>90?m.substring(0,90)+'…':m;
            btn.textContent='采集当前AOI';
          })
          .catch(function(e){saved.textContent='上报失败:'+e.message;btn.textContent='采集当前AOI';});
      }
      // 数据源:搜索接口AOI(可靠)。缓存没有就现场按名称+poiid搜一次
      var entry=(window.__aoiRings||{})[base.poiid];
      if(entry&&entry.v){submit();return;}
      saved.style.color='#888';saved.textContent='正在通过搜索接口获取AOI...';
      btn.textContent='采集中...';
      window.__aoiFetchRing(base.name,base.poiid,function(ok){
        submit();
      });
    });
  }
  if(document.readyState==='loading'){document.addEventListener('DOMContentLoaded',mount);}
  else{mount();}
})();
""" % (preview_url, base)

_driver={'d':None}
_dlock=threading.RLock()

def kill_stale_browser():
    """清理上次残留的受控浏览器进程(仅限本工具专用profile，不影响日常Edge)"""
    out=subprocess.run(['wmic','process','where',"name='msedge.exe'",'get','ProcessId,CommandLine'],
                       capture_output=True,text=True) if os.name=='nt' else None
    if not out or not out.stdout:
        return
    for line in out.stdout.splitlines():
        if ('gaode_collect_profile' in line) or (('--remote-debugging-port=%s'%DEBUG_PORT) in line):
            pid=line.strip().split()[-1]
            if pid.isdigit():
                subprocess.run(['taskkill','/F','/PID',pid],capture_output=True)

def launch_browser(url=None):
    kill_stale_browser()
    time.sleep(1)
    edge=None
    for p in [shutil.which('msedge'),
              r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
              r'C:\Program Files\Microsoft\Edge\Application\msedge.exe']:
        if p and os.path.exists(p):
            edge=p; break
    if not edge:
        raise RuntimeError('未找到Edge浏览器，请先安装Microsoft Edge')
    profile=tempfile.gettempdir()+os.sep+'gaode_collect_profile'
    cmd=[edge,'--remote-debugging-port=%s'%DEBUG_PORT,
         '--user-data-dir='+profile,
         '--disable-blink-features=AutomationControlled']
    if url:
        cmd.append(url)
    subprocess.Popen(cmd)
    time.sleep(4)
    opts=Options()
    opts.add_experimental_option('debuggerAddress','127.0.0.1:%s'%DEBUG_PORT)
    return webdriver.Edge(options=opts)

_home_handle={'h':None}

def ensure_driver():
    with _dlock:
        d=_driver['d']
        if d is not None:
            try:
                d.window_handles   # 心跳探测: 会话已死则抛异常
            except Exception:
                _driver['d']=None
                d=None
        if d is None:
            _driver['d']=launch_browser()
        return _driver['d']

def _navigate_tab(js,url):
    """后台线程: 切换到工作标签页、注册钩子、导航到目标页面。
       必须在后台执行——主线程同步d.get会阻塞HTTP响应导致浏览器连接中断。"""
    with _dlock:
        d=_driver['d']
        if not d: return
        if not (_home_handle['h'] and _home_handle['h'] in d.window_handles):
            _home_handle['h']=d.window_handles[0] if d.window_handles else None
            if not _home_handle['h']:
                return
        try:
            d.switch_to.window(_home_handle['h'])
            d.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument',{'source':js})
        except Exception:
            pass
        try:
            d.get(url)
            d.execute_script(js)
        except Exception:
            pass

def open_collect_tab(preview_url):
    """在当前浏览器新开标签页并导航到高德地图。创建标签页+导航全部在
       后台线程执行, 主线程立刻返回HTTP响应, 避免ConnectionAbortedError。"""
    js=hook_js()+panel_js(_report_base(),preview_url)
    def nav():
        with _dlock:
            d=_driver['d']
            if not d: return
            try:
                d.switch_to.new_window('tab')
                d.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument',{'source':js})
                d.get(HOME_URL)
                d.execute_script(js)
            except Exception as ex:
                print('[open_collect] nav失败:', ex)
    try:
        ensure_driver()
        t=threading.Thread(target=nav,daemon=True)
        t.start()
        return True
    except Exception as ex:
        if 'invalid session id' not in str(ex) and 'disconnected' not in str(ex):
            raise
        print('[恢复] 检测到浏览器会话已关闭，正在重新拉起浏览器...')
        with _dlock:
            try:
                _driver['d'].quit()
            except Exception:
                pass
            _driver['d']=None
        ensure_driver()
        t=threading.Thread(target=nav,daemon=True)
        t.start()
        return True

def start_console_browser(port):
    """启动受控Edge并让初始页直接显示控制台主页(单窗口模式入口)"""
    url='http://127.0.0.1:%d/'%port
    d=launch_browser(url=url)
    with _dlock:
        _driver['d']=d
        hs=d.window_handles
        _home_handle['h']=hs[0] if hs else None
        if not _home_handle['h']:
            d.switch_to.new_window('tab')
            _home_handle['h']=d.current_window_handle
            d.get(url)
        js=hook_js()+panel_js(_report_base(),url.rstrip('/')+'/preview')
        try:
            d.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument',{'source':js})
        except Exception:
            pass

# ================== 输出与简介 ==================
def build_info_text(name,poiid,address,pts):
    xs=[p[0] for p in pts]; ys=[p[1] for p in pts]
    lines=[
        '名称: %s | POIID: %s'%(name,poiid),
        '地址: %s'%address,
        '点数: %d (WGS-84)'%len(pts),
        '范围: 经度 %.6f ~ %.6f | 纬度 %.6f ~ %.6f'%(min(xs),max(xs),min(ys),max(ys)),
        '边界坐标串(lng,lat):',
        ';'.join('%.6f,%.6f'%(p[0],p[1]) for p in pts),
    ]
    return '\n'.join(lines)

def write_geojson(fp_base,name,poiid,address,city_name,x,y,pts):
    gj={'type':'FeatureCollection','features':[{
        'type':'Feature',
        'properties':{'NAME':name,'POIID':poiid,'ADDRESS':address,
                      'CITY_NAME':city_name,'LONGITUDE':x,'LATITUDE':y},
        'geometry':{'type':'Polygon','coordinates':[[list(p) for p in pts]]}
    }]}
    with open(fp_base+'.geojson','w',encoding='utf-8') as f:
        f.write(json.dumps(gj,ensure_ascii=False,indent=1))

def write_aoi_outputs(name,poiid,address,city_name,x,y,wgs_pts):
    fn=safe_name(name)
    fdir=os.path.join(OUTPUT_DIR,fn)
    n=1
    while os.path.exists(fdir):
        n+=1
        fdir=os.path.join(OUTPUT_DIR,'%s_%d'%(fn,n))
    os.makedirs(fdir)
    fp_base=os.path.join(fdir,fn)

    w=shapefile.Writer(fp_base,encoding='utf-8')
    w.field('NAME','C',size=60)
    w.field('ADDRESS','C',size=120)
    w.field('CITY_NAME','C',size=20)
    w.field('LONGITUDE','F',decimal=6)
    w.field('LATITUDE','F',decimal=6)
    w.field('POIID','C')
    w.poly([wgs_pts])
    w.record(name,address,city_name,x,y,poiid)
    w.close()
    prj=open(fp_base+'.prj','w',encoding='utf-8')
    prj.write('GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],\
        PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]]')
    prj.close()

    write_geojson(fp_base,name,poiid,address,city_name,x,y,wgs_pts)
    with open(os.path.join(fdir,'简介.txt'),'w',encoding='utf-8') as f:
        f.write(build_info_text(name,poiid,address,wgs_pts))
    return fdir

def parse_ring(ring_str,sep):
    """解析边界串为点列表; 搜索源首尾闭合会去掉重复的收尾点"""
    pts=[tuple(map(float,s.split(','))) for s in ring_str.split(sep)]
    if len(pts)>1 and pts[0]==pts[-1]:
        pts=pts[:-1]
    return pts

def save_aoi(payload):
    # 数据源决策:AOI几何一律取自搜索接口的环(可靠);
    # detail响应仅用于识别用户点击了哪个建筑(poiid/名称)。
    if payload.lstrip().startswith('{') and '"raw"' in payload:
        obj=json.loads(payload)
        raw=obj.get('raw','')
        rings=obj.get('rings') or {}
    else:
        raw=payload
        rings={}
    d=json.loads(raw)
    base=(d.get('data') or {}).get('base') or {}
    name=base.get('name') or 'unnamed'
    poiid=base.get('poiid','')
    entry=rings.get(poiid)
    # 兼容: 缓存值可能是旧版纯字符串
    if isinstance(entry,str):
        entry={'v':entry}
    if not entry or not entry.get('v'):
        return '%s：搜索接口未返回该建筑的AOI边界(可能名称歧义)，未保存。请先在搜索框搜一次该地点再点击采集。'%name
    try:
        pts=parse_ring(entry['v'],'_')
    except Exception:
        return '%s：搜索返回的AOI边界解析失败，未保存。'%name
    if len(pts)<3:
        return '%s：搜索返回的AOI边界点数不足(%d)，未保存。'%(name,len(pts))
    # 中心坐标优先用搜索接口返回值，缺失则回退detail基准点
    try:
        x=float(entry.get('x')); y=float(entry.get('y'))
    except (TypeError,ValueError):
        x=float(base.get('x')); y=float(base.get('y'))
    cx=sum(p[0] for p in pts)/len(pts); cy=sum(p[1] for p in pts)/len(pts)
    offm=math.hypot(cx-x,cy-y)*111000
    warn=''
    if offm>800:
        warn='[警告] 边界面与POI坐标偏差%.0f米，请人工核实位置'%offm
        print(warn+' -> '+name)

    address=entry.get('a','')
    city_name=entry.get('c','')
    wgs_pts=[gcj02towgs84(p[0],p[1]) for p in pts]
    fdir=write_aoi_outputs(name,poiid,address,city_name,x,y,wgs_pts)
    upsert_aoi(name,poiid,address,city_name,x,y,wgs_pts,fdir)
    if warn:
        print('[采集] ', warn)
        return warn+'，%s 已入库并保存至output文件夹（%d个边界点），建议到预览页核实或删除'%(name,len(pts))
    print('[采集] %s -> %s'%(name,fdir))
    return '%s 已入库并保存至output文件夹中（%d个边界点，来源:搜索接口）'%(name,len(pts))

def safe_name(s):
    for ch in '\\/:*?"<>|':
        s=s.replace(ch,'_')
    return s.strip() or 'unnamed'

def convert_loose_shps():
    files=glob.glob(os.path.join(OUTPUT_DIR,'*.shp'))
    if not files:
        return None
    cnt=0
    for f in files:
        try:
            r=shapefile.Reader(f[:-4])
            rec=r.record(0)
            sr=r.shapeRecord(0)
            name=str(rec[0]); address=str(rec[1]); city=str(rec[2])
            x=float(rec[3]); y=float(rec[4]); poiid=str(rec[5])
            wgs_pts=[list(p) for p in sr.shape.points]
            fdir=write_aoi_outputs(name,poiid,address,city,x,y,wgs_pts)
            upsert_aoi(name,poiid,address,city,x,y,wgs_pts,fdir)
            for ext in ('.shp','.shx','.dbf','.prj','.geojson'):
                fp=f[:-4]+ext
                if os.path.exists(fp) and not fp.startswith(fdir):
                    os.remove(fp)
            cnt+=1
            print('[迁移%d] %s -> 文件夹结构'%(cnt,name))
        except Exception as ex:
            print('转换失败 %s: %s'%(f,str(ex)[:80]))
    if cnt:
        print('共迁移 %d 个旧文件为文件夹结构。'%cnt)
    return cnt

# ================== SQLite 存储 ==================
def init_db():
    con=sqlite3.connect(DB_PATH)
    con.execute('''CREATE TABLE IF NOT EXISTS aoi(
        poiid TEXT PRIMARY KEY,
        name TEXT, address TEXT, city TEXT,
        lng REAL, lat REAL,
        min_lng REAL, max_lng REAL, min_lat REAL, max_lat REAL,
        point_count INTEGER,
        coords TEXT,
        folder TEXT,
        created_at TEXT)''')
    con.commit()
    con.close()

def upsert_aoi(name,poiid,address,city,x,y,wgs_pts,fdir):
    xs=[p[0] for p in wgs_pts]; ys=[p[1] for p in wgs_pts]
    coords=';'.join('%.6f,%.6f'%(p[0],p[1]) for p in wgs_pts)
    con=sqlite3.connect(DB_PATH)
    con.execute('''INSERT OR REPLACE INTO aoi
        (poiid,name,address,city,lng,lat,min_lng,max_lng,min_lat,max_lat,point_count,coords,folder,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (poiid,name,address,city,x,y,min(xs),max(xs),min(ys),max(ys),
         len(wgs_pts),coords,fdir,datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    con.commit()
    con.close()

def list_aoi():
    con=sqlite3.connect(DB_PATH)
    con.row_factory=sqlite3.Row
    rows=con.execute('SELECT * FROM aoi ORDER BY created_at DESC').fetchall()
    con.close()
    return [dict(r) for r in rows]

def get_aoi(poiid):
    con=sqlite3.connect(DB_PATH)
    con.row_factory=sqlite3.Row
    r=con.execute('SELECT * FROM aoi WHERE poiid=?',(poiid,)).fetchone()
    con.close()
    return dict(r) if r else None

def delete_aoi(poiid):
    con=sqlite3.connect(DB_PATH)
    cur=con.execute('SELECT folder FROM aoi WHERE poiid=?',(poiid,))
    row=cur.fetchone()
    if row and row[0]:
        shutil.rmtree(row[0],ignore_errors=True)
    con.execute('DELETE FROM aoi WHERE poiid=?',(poiid,))
    con.commit()
    con.close()

# ================== Web页面模板 ==================
def home_html():
    rows=list_aoi()
    return """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>AOI半自动采集工具 · 控制台</title>
<style>
body{margin:0;font-family:Microsoft YaHei,sans-serif;background:#f0f4fa;
     display:flex;align-items:center;justify-content:center;height:100vh;}
.card{background:#fff;border-radius:14px;box-shadow:0 6px 24px rgba(0,0,0,.08);
      padding:42px 56px;text-align:center;max-width:560px;}
h1{margin:0 0 6px;color:#1677ff;font-size:26px;}
.sub{color:#888;font-size:13px;margin-bottom:26px;line-height:1.7;}
.btns{display:flex;gap:18px;justify-content:center;}
.big{flex:1;padding:20px 10px;border:none;border-radius:10px;cursor:pointer;
     font-size:17px;font-weight:bold;color:#fff;transition:.15s;}
.big:hover{transform:translateY(-2px);box-shadow:0 6px 14px rgba(0,0,0,.15);}
.b1{background:#1677ff;} .b2{background:#52c41a;}
.tip{margin-top:24px;color:#aaa;font-size:12px;line-height:1.8;text-align:left;}
.stat{display:inline-block;background:#e6f4ff;color:#1677ff;border-radius:20px;
      padding:3px 14px;font-size:13px;margin-bottom:18px;}
.err{color:#e64c3c;margin-top:14px;font-size:13px;min-height:16px;}
</style></head>
<body><div class="card">
  <h1>AOI 半自动采集工具</h1>
  <div class="sub">在高德地图上点击建筑即可采集其区域边界<br>GCJ-02 自动转 WGS-84 · 数据落盘 shp/geojson/SQLite</div>
  <div class="stat">已采集 __COUNT__ 个 AOI</div>
  <div class="btns">
    <button class="big b1" onclick="openCollect()">🏗️ 开始收集<br><small style="font-weight:normal;">本窗口切换到高德地图</small></button>
    <button class="big b2" onclick="location.href='/preview'">🗺️ 进入预览<br><small style="font-weight:normal;">地图查看/搜索/管理</small></button>
  </div>
  <div class="err" id="err"></div>
  <div class="tip">
    · 收集窗口右上角浮窗显示"已捕获"后，点【采集当前AOI】保存<br>
    · 若提示被风控拦截：稍等再试，或在弹出的浏览器中登录高德账号<br>
    · 登录弹窗右上角可直接关闭，请勿滑动其中的滑块
  </div>
</div>
<script>
function openCollect(){
  var e=document.getElementById('err');
  e.textContent='正在新开收集窗口...';
  fetch('/api/open_collect',{method:'POST'})
    .then(r=>r.json()).then(d=>{
      if(d.ok){e.style.color='#52c41a';e.textContent='✅ 正在切换到收集模式...';
        setTimeout(()=>{e.textContent='';},4000);}
      else{e.style.color='#e64c3c';e.textContent='❌ '+ (d.err||'失败');}
    }).catch(ex=>{e.style.color='#e64c3c';e.textContent='❌ '+ex.message;});
}
</script></body></html>""".replace('__COUNT__',str(len(rows)))

PREVIEW_TMPL="""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>AOI预览与管理</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
html,body{height:100%;margin:0;font-family:Microsoft YaHei,sans-serif;}
#app{display:flex;height:100%;flex-direction:column;}
#nav{background:#1677ff;color:#fff;display:flex;align-items:center;gap:14px;padding:8px 16px;}
#nav b{font-size:15px;}
#nav a{color:#fff;font-size:13px;text-decoration:none;background:rgba(255,255,255,.18);
       padding:4px 12px;border-radius:4px;cursor:pointer;}
#nav a:hover{background:rgba(255,255,255,.32);}
#main{flex:1;display:flex;min-height:0;}
#map{flex:1;}
#side{width:300px;background:#fafafa;border-left:1px solid #ddd;padding:10px;box-sizing:border-box;display:flex;flex-direction:column;}
.bar{display:flex;gap:5px;margin-bottom:7px;}
.bar input{flex:1;border:1px solid #ccc;border-radius:4px;padding:5px 8px;font-size:13px;}
.btn{background:#1677ff;color:#fff;border:none;border-radius:4px;padding:5px 10px;cursor:pointer;font-size:12px;white-space:nowrap;}
.btn.gray{background:#888;}
.tip{color:#aaa;font-size:12px;margin:4px 0 8px;}
#list{flex:1;overflow-y:auto;}
.item{background:#fff;border:2px solid transparent;border-radius:6px;padding:6px 9px;margin-bottom:6px;font-size:13px;user-select:none;}
.item:hover{border-color:#91caff;}
.item.sel{background:#e6f4ff;border-color:#1677ff;}
.item .hd{display:flex;align-items:center;gap:5px;}
.item .nm{flex:1;cursor:pointer;font-weight:bold;}
.item small{color:#888;display:block;margin-top:2px;}
.act{background:#e6f4ff;color:#1677ff;border:none;border-radius:4px;padding:2px 7px;cursor:pointer;font-size:11px;}
.act.del{background:#fdecec;color:#e64c3c;font-size:13px;padding:0 6px;}
.empty{color:#bbb;text-align:center;padding:30px 0;}
#mask{position:fixed;inset:0;background:rgba(0,0,0,.45);display:none;align-items:center;justify-content:center;z-index:99999;}
#modal{background:#fff;border-radius:10px;width:520px;max-width:92vw;max-height:82vh;overflow-y:auto;padding:22px 26px;}
#modal h3{margin:0 0 12px;color:#1677ff;}
#modal pre{white-space:pre-wrap;word-break:break-all;background:#f6f8fa;border-radius:6px;
           padding:12px;font-size:12px;line-height:1.8;}
#modal .x{float:right;background:none;border:none;font-size:20px;cursor:pointer;color:#888;}
</style></head>
<body><div id="app">
  <div id="nav"><b>🗺️ AOI预览与管理</b>
    <a href="/">🏠 控制台主页</a>
    <a onclick="openCollect()">➕ 返回收集模式</a>
  </div>
  <div id="main">
    <div id="map"></div>
    <div id="side">
      <div class="bar"><input id="kw" placeholder="搜索名称/地址/POIID"><button class="btn" onclick="doSearch()">搜索</button></div>
      <div class="bar">
        <button class="btn gray" onclick="showAll()">全选显示</button>
        <button class="btn gray" onclick="hideAllLayers(false)">全选隐藏</button>
      </div>
      <div class="tip">共 __COUNT__ 个 · 点击条目上图/隐藏 · 「ℹ详情」看档案 · ✕删除</div>
      <div id="list"></div>
    </div>
  </div>
</div>
<div id="mask"><div id="modal">
  <button class="x" onclick="closeModal()">×</button>
  <h3 id="mTitle"></h3>
  <pre id="mBody"></pre>
</div></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
var map=L.map('map').setView([__CLAT__,__CLNG__],__CZOOM__);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19}).addTo(map);
var AOIS=[],layers={},group=L.featureGroup().addTo(map);
var listEl=document.getElementById('list');

function load(){fetch('/api/list').then(r=>r.json()).then(d=>{AOIS=d;render();});}
function parsePts(coords){return coords.split(';').map(function(s){var t=s.split(',');return [+t[0],+t[1]];});}
function matched(){var kw=document.getElementById('kw').value.trim();
  return AOIS.filter(function(a){return !kw||a.name.indexOf(kw)>=0||(a.address||'').indexOf(kw)>=0||a.poiid.indexOf(kw)>=0;});}

function render(){
  var kw=document.getElementById('kw').value.trim();
  var f=matched();
  listEl.innerHTML='';
  if(!f.length){listEl.innerHTML='<div class="empty">'+(kw?'无匹配结果':'暂无数据，请先采集')+'</div>';return;}
  f.forEach(function(a){
    var d=document.createElement('div');
    d.className='item'+(layers[a.poiid]?' sel':'');
    d.dataset.pid=a.poiid;
    d.innerHTML='<div class="hd"><span class="nm">'+a.name+'</span>'
      +'<button class="act" title="查看详情">ℹ 详情</button>'
      +'<button class="act del" title="删除">✕</button></div>'
      +'<small>'+a.poiid+' · '+a.point_count+'点 · '+a.city+'</small>';
    // 点击条目任意位置 -> 上图/隐藏并定位到该面
    d.addEventListener('click',function(){toggleShow(a);});
    var acts=d.querySelectorAll('.act');
    acts[0].addEventListener('click',function(ev){ev.stopPropagation();showModal(a);});
    acts[1].addEventListener('click',function(ev){
      ev.stopPropagation();
      if(confirm('确认删除「'+a.name+'」？将同时删除磁盘文件夹与数据库记录'))
        fetch('/api/delete?poiid='+encodeURIComponent(a.poiid)).then(()=>{hideLayer(a);load();});
    });
    d.draggable=true;
    d.addEventListener('dragstart',function(ev){ev.dataTransfer.setData('pid',a.poiid);});
    listEl.appendChild(d);
  });
}
function showModal(a){
  var pts=parsePts(a.coords);
  var xs=pts.map(p=>p[0]),ys=pts.map(p=>p[1]);
  var cx=xs.reduce((s,v)=>s+v,0)/pts.length, cy=ys.reduce((s,v)=>s+v,0)/pts.length;
  var off=Math.round(Math.hypot(cx-a.lng,cy-a.lat)*111000);
  document.getElementById('mTitle').textContent=a.name;
  document.getElementById('mBody').textContent=
    '名称: '+a.name+' | POIID: '+a.poiid+'\\n'
    +'地址: '+(a.address||'')+'\\n'
   +'中心经纬度(WGS-84): '+a.lng.toFixed(6)+', '+a.lat.toFixed(6)+'\\n'
    +'面范围: 经度 '+Math.min.apply(null,xs).toFixed(6)+' ~ '+Math.max.apply(null,xs).toFixed(6)
    +' | 纬度 '+Math.min.apply(null,ys).toFixed(6)+' ~ '+Math.max.apply(null,ys).toFixed(6)+'\\n'
    +'边界点数: '+a.point_count+'\\n'
    +(off>800?'⚠ 面心与基准点偏差约'+off+'米，疑为高德源数据错位，建议删除！\\n':'')
    +'\\n边界坐标串(lng,lat):\\n'+a.coords;
  document.getElementById('mask').style.display='flex';
}
function closeModal(){document.getElementById('mask').style.display='none';}
document.getElementById('mask').addEventListener('click',function(e){if(e.target===this)closeModal();});

function doSearch(){
  render();
  hideAllLayers(true);
  matched().forEach(showLayer);
  fitAll();
}
document.getElementById('kw').addEventListener('keydown',function(e){if(e.key==='Enter')doSearch();});

function showLayer(a){
  if(layers[a.poiid]){return;}
  var ring=parsePts(a.coords);
  var ly=L.geoJSON({type:'Feature',properties:{},geometry:{type:'Polygon',coordinates:[ring]}},
                   {style:{color:'#1677ff',weight:2,fillOpacity:.15}}).addTo(map);
  layers[a.poiid]=ly;group.addLayer(ly);
  markItem(a.poiid,true);
}
function hideLayer(a){
  var ly=layers[a.poiid];
  if(ly){map.removeLayer(ly);group.removeLayer(ly);delete layers[a.poiid];markItem(a.poiid,false);}
}
function toggleShow(a){
  if(layers[a.poiid]){hideLayer(a);}
  else{showLayer(a);map.fitBounds(layers[a.poiid].getBounds().pad(.2));}
}
function markItem(pid,on){
  Array.from(listEl.children).forEach(function(el){
    if(el.dataset.pid===pid){el.classList.toggle('sel',on);}
  });
}
function hideAllLayers(skipMarks){
  Object.keys(layers).forEach(function(k){
    var ly=layers[k];map.removeLayer(ly);group.removeLayer(ly);delete layers[k];});
  if(!skipMarks){Array.from(listEl.children).forEach(function(el){el.classList.remove('sel');});}
}
function showAll(){AOIS.forEach(showLayer);fitAll();}
function fitAll(){if(group.getLayers().length){map.fitBounds(group.getBounds().pad(.2));}}
function openCollect(){
  fetch('/api/open_collect',{method:'POST'}).then(r=>r.json()).then(d=>{
    if(d.ok){/* 本窗口即将切换到高德地图 */}
    else{alert('失败: '+(d.err||'未知错误'));}
  });
}
var mapEl=document.getElementById('map');
mapEl.addEventListener('dragover',function(e){e.preventDefault();});
mapEl.addEventListener('drop',function(e){
  e.preventDefault();
  var pid=e.dataTransfer.getData('pid');
  var a=AOIS.find(function(x){return x.poiid===pid;});
  if(a){showLayer(a);map.fitBounds(layers[a.poiid].getBounds().pad(.2));}
});
load();
setInterval(load,5000);
</script></body></html>"""

class PreviewHandler(BaseHTTPRequestHandler):
    def log_message(self,*args):
        pass
    def _send(self,code,body,ctype='text/html; charset=utf-8'):
        data=body.encode('utf-8') if isinstance(body,str) else body
        self.send_response(code)
        self.send_header('Content-Type',ctype)
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Access-Control-Allow-Origin','*')
        self.send_header('Access-Control-Allow-Private-Network','true')
        self.end_headers()
        self.wfile.write(data)
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin','*')
        self.send_header('Access-Control-Allow-Methods','GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers','Content-Type')
        self.send_header('Access-Control-Allow-Private-Network','true')
        self.send_header('Content-Length','0')
        self.end_headers()
    def do_GET(self):
        u=urlparse(self.path)
        if u.path=='/' or u.path=='/index.html':
            self._send(200,home_html())
        elif u.path=='/preview':
            self._send(200,generate_preview_html())
        elif u.path=='/api/list':
            self._send(200,json.dumps(list_aoi(),ensure_ascii=False),'application/json; charset=utf-8')
        elif u.path=='/api/detail':
            qs=parse_qs(u.query)
            pid=unquote(qs.get('poiid',[''])[0])
            row=get_aoi(pid)
            if row:
                self._send(200,json.dumps(row,ensure_ascii=False),'application/json; charset=utf-8')
            else:
                self._send(404,json.dumps({'ok':False}),'application/json; charset=utf-8')
        elif u.path=='/api/delete':
            qs=parse_qs(u.query)
            pid=unquote(qs.get('poiid',[''])[0])
            if pid:
                delete_aoi(pid)
                self._send(200,json.dumps({'ok':True}),'application/json; charset=utf-8')
            else:
                self._send(400,json.dumps({'ok':False,'err':'missing poiid'}),'application/json; charset=utf-8')
        else:
            self._send(404,'not found')
    def do_POST(self):
        u=urlparse(self.path)
        if u.path=='/api/report':
            try:
                ln=int(self.headers.get('Content-Length') or 0)
                raw=self.rfile.read(ln).decode('utf-8')
                msg=save_aoi(raw)
                print('[采集] '+msg)
                self._send(200,msg,'text/plain; charset=utf-8')
            except Exception as ex:
                msg='保存失败: %s'%str(ex)[:80]
                print(msg)
                self._send(200,msg,'text/plain; charset=utf-8')
        elif u.path=='/api/open_collect':
            try:
                open_collect_tab(_report_base()+'/preview')
                self._send(200,json.dumps({'ok':True}),'application/json; charset=utf-8')
            except Exception as ex:
                self._send(200,json.dumps({'ok':False,'err':str(ex)[:120]}),'application/json; charset=utf-8')
        else:
            self._send(404,'not found')

_server=None
def start_preview_server():
    global _server
    if _server is None:
        port=PREVIEW_PORT
        for i in range(10):
            try:
                _server=ThreadingHTTPServer(('127.0.0.1',port+i),PreviewHandler)
                threading.Thread(target=_server.serve_forever,daemon=True).start()
                break
            except OSError:
                continue
    return _server

def _report_base():
    if _server is not None:
        return 'http://127.0.0.1:%d'%_server.server_address[1]
    return 'http://127.0.0.1:%d'%PREVIEW_PORT

def generate_preview_html():
    rows=list_aoi()
    if rows:
        lats=[];lngs=[]
        for r in rows:
            for pair in r['coords'].split(';'):
                t=pair.split(',')
                lngs.append(float(t[0]));lats.append(float(t[1]))
        clat=(min(lats)+max(lats))/2;clng=(min(lngs)+max(lngs))/2
    else:
        clat,clng=30.3151,120.05805
    html=(PREVIEW_TMPL.replace('__COUNT__',str(len(rows)))
          .replace('__CLAT__','%.6f'%clat)
          .replace('__CLNG__','%.6f'%clng)
          .replace('__CZOOM__','18'))
    return html

def drain(q):
    while not q.empty():
        try:
            q.get_nowait()
        except Exception:
            break

def main():
    os.makedirs(OUTPUT_DIR,exist_ok=True)
    init_db()
    convert_loose_shps()
    srv=start_preview_server()
    port=srv.server_address[1]
    url='http://127.0.0.1:%d/'%port
    print('='*56)
    print(' AOI 半自动采集工具（独立版 · Web控制台）')
    print(' 服务地址: %s'%url)
    print(' 数据目录: %s'%OUTPUT_DIR)
    print(' 单窗口模式: 控制台/收集/预览都在同一浏览器窗口切换')
    print(' 按 Ctrl+C 退出(将同时关闭受控浏览器)')
    print('='*56)
    start_console_browser(port)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        if _driver['d'] is not None:
            try:
                _driver['d'].quit()
            except Exception:
                pass
        print('已退出。')

if __name__=='__main__':
    main()
