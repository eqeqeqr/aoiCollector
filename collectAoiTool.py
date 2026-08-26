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
import webbrowser
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
(function(){
  function note(t){
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
  var of=window.fetch;
  window.fetch=function(u){
    var p=of.apply(this,arguments);
    try{ if(String(u).indexOf('/detail/get/detail')>=0){ p.then(function(r){return r.text()}).then(note); } }catch(e){}
    return p;
  };
  var oo=XMLHttpRequest.prototype.open, os_=XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open=function(m,u){this.__u=String(u);return oo.apply(this,arguments)};
  XMLHttpRequest.prototype.send=function(){
    this.addEventListener('load',function(){ if(this.__u&&this.__u.indexOf('/detail/get/detail')>=0){note(this.responseText);} });
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
    if(document.getElementById('__aoiPanel')){return;}
    if(!document.body){setTimeout(mount,200);return;}
    var panel=document.createElement('div');
    panel.id='__aoiPanel';
    panel.style.cssText='position:fixed;top:80px;right:20px;z-index:999999;background:#fff;'
      +'border:2px solid #1677ff;border-radius:8px;padding:10px 14px;font-size:13px;'
      +'font-family:Microsoft YaHei,sans-serif;box-shadow:0 2px 10px rgba(0,0,0,.25);width:230px;';
    panel.innerHTML=
      '<div style="font-weight:bold;color:#1677ff;margin-bottom:6px;">AOI采集工具</div>'
      +'<div style="color:#999;font-size:11px;margin-bottom:6px;">GCJ-02将自动转为WGS-84保存</div>'
      +'<div id="__aoiPanelInfo" style="color:#555;margin-bottom:8px;min-height:18px;">等待点击建筑...</div>'
      +'<div id="__aoiPanelSaved" style="color:#52c41a;margin-bottom:8px;min-height:16px;"></div>'
      +'<button id="__aoiBtn" style="background:#1677ff;color:#fff;border:none;border-radius:4px;'
      +'padding:6px 14px;cursor:pointer;font-size:13px;">采集当前AOI</button>'
      +'<a href="%s" target="_blank" style="display:block;margin-top:8px;color:#1677ff;'
      +'font-size:12px;text-decoration:none;">打开预览模式 →</a>';
    document.body.appendChild(panel);
    document.getElementById('__aoiBtn').addEventListener('click',function(){
      var btn=this,saved=document.getElementById('__aoiPanelSaved'),
          info=document.getElementById('__aoiPanelInfo');
      btn.textContent='采集中...';
      var raw=window.__aoiLast;
      if(!raw){saved.textContent='未捕获到数据，请先点击建筑';btn.textContent='采集当前AOI';return;}
      fetch('%s/api/report',{method:'POST',headers:{'Content-Type':'text/plain;charset=utf-8'},body:raw})
        .then(function(r){return r.text();})
        .then(function(m){
          if(m.indexOf('已保存')>=0){window.__aoiLast=null;info.textContent='等待点击建筑...';}
          saved.textContent=m.length>44?m.substring(0,44)+'…':m;
          btn.textContent='采集当前AOI';
        })
        .catch(function(e){saved.textContent='上报失败:'+e.message;btn.textContent='采集当前AOI';});
    });
  }
  if(document.readyState==='loading'){document.addEventListener('DOMContentLoaded',mount);}
  else{mount();}
})();
""" % (preview_url, base)

_driver={'d':None}
_dlock=threading.RLock()

def launch_browser():
    edge=None
    for p in [shutil.which('msedge'),
              r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
              r'C:\Program Files\Microsoft\Edge\Application\msedge.exe']:
        if p and os.path.exists(p):
            edge=p; break
    if not edge:
        raise RuntimeError('未找到Edge浏览器，请先安装Microsoft Edge')
    profile=tempfile.gettempdir()+os.sep+'gaode_collect_profile'
    subprocess.Popen([edge,'--remote-debugging-port=%s'%DEBUG_PORT,
                      '--user-data-dir='+profile,
                      '--disable-blink-features=AutomationControlled'])
    time.sleep(4)
    opts=Options()
    opts.add_experimental_option('debuggerAddress','127.0.0.1:%s'%DEBUG_PORT)
    return webdriver.Edge(options=opts)

def ensure_driver():
    with _dlock:
        if _driver['d'] is None:
            _driver['d']=launch_browser()
        return _driver['d']

def open_collect_tab(preview_url):
    """新开一个收集窗口(独立标签页)，并针对该标签页注册钩子+立即挂载浮窗。
       修复点: CDP的addScriptToEvaluateOnNewDocument仅对注册时的标签页生效，
       因此每个新窗口都必须单独注册，否则浮窗不会显示。"""
    d=ensure_driver()
    with _dlock:
        d.switch_to.new_window('tab')
        h=d.current_window_handle
        d.get(HOME_URL)
        js=hook_js()+panel_js(_report_base(),preview_url)
        # 注册到本标签页: 手动刷新后仍自动挂载
        try:
            d.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument',{'source':js})
        except Exception:
            pass
        # 立即挂载(脚本自带body就绪保护)
        d.execute_script(js)
    return h

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

def save_aoi(raw):
    d=json.loads(raw)
    base=(d.get('data') or {}).get('base') or {}
    spec=(d.get('data') or {}).get('spec') or {}
    ms=spec.get('mining_shape') or {}
    shape=ms.get('shape')
    name=base.get('name') or 'unnamed'
    if not shape:
        return '%s 无边界数据(mining_shape缺失)，未保存。'%name
    x=float(base.get('x')); y=float(base.get('y'))
    pts=[tuple(map(float,s.split(','))) for s in shape.split(';')]
    cx=sum(p[0] for p in pts)/len(pts); cy=sum(p[1] for p in pts)/len(pts)
    offm=math.hypot(cx-x,cy-y)*111000
    warn=''
    if offm>800:
        warn='[警告] 边界面与POI坐标偏差%.0f米，疑为服务端脏数据，仍按你的指令保存。'%offm

    address=base.get('address','')
    city_name=base.get('city_name','')
    poiid=base.get('poiid','')
    wgs_pts=[gcj02towgs84(p[0],p[1]) for p in pts]
    fdir=write_aoi_outputs(name,poiid,address,city_name,x,y,wgs_pts)
    upsert_aoi(name,poiid,address,city_name,x,y,wgs_pts,fdir)
    if warn:
        print(warn)
    return '%s 已保存并入库 -> %s （%d个边界点）'%(name,fdir,len(pts))

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
    <button class="big b1" onclick="openCollect()">🏗️ 开始收集<br><small style="font-weight:normal;">新开高德地图窗口</small></button>
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
      if(d.ok){e.style.color='#52c41a';e.textContent='✅ 已新开收集窗口，请切换到浏览器中的高德标签页';
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
    <a onclick="openCollect()">➕ 新开收集窗口</a>
  </div>
  <div id="main">
    <div id="map"></div>
    <div id="side">
      <div class="bar"><input id="kw" placeholder="搜索名称/地址/POIID"><button class="btn" onclick="doSearch()">搜索</button></div>
      <div class="bar">
        <button class="btn gray" onclick="showAll()">全选显示</button>
        <button class="btn gray" onclick="hideAllLayers(false)">全选隐藏</button>
      </div>
      <div class="tip">共 __COUNT__ 个 · 点名称看详情 · 「上图」蓝框显示 · ✕删除</div>
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
      +'<button class="act" title="在地图显示/隐藏">上图</button>'
      +'<button class="act" title="查看详情">ℹ 详情</button>'
      +'<button class="act del" title="删除">✕</button></div>'
      +'<small>'+a.poiid+' · '+a.point_count+'点 · '+a.city+'</small>';
    // 点击条目主体 -> 居中弹窗详情
    d.addEventListener('click',function(){showModal(a);});
    var acts=d.querySelectorAll('.act');
    acts[0].addEventListener('click',function(ev){ev.stopPropagation();toggleShow(a);});
    acts[1].addEventListener('click',function(ev){ev.stopPropagation();showModal(a);});
    acts[2].addEventListener('click',function(ev){
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
  document.getElementById('mTitle').textContent=a.name;
  document.getElementById('mBody').textContent=
    '名称: '+a.name+' | POIID: '+a.poiid+'\\n'
    +'地址: '+(a.address||'')+'\\n'
   +'中心经纬度(WGS-84): '+a.lng.toFixed(6)+', '+a.lat.toFixed(6)+'\\n'
    +'面范围: 经度 '+Math.min.apply(null,xs).toFixed(6)+' ~ '+Math.max.apply(null,xs).toFixed(6)
    +' | 纬度 '+Math.min.apply(null,ys).toFixed(6)+' ~ '+Math.max.apply(null,ys).toFixed(6)+'\\n'
    +'边界点数: '+a.point_count+'\\n\\n'
    +'边界坐标串(lng,lat):\\n'+a.coords;
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
    if(d.ok){alert('已新开收集窗口，请切换到浏览器的高德标签页');}
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
    print(' 按 Ctrl+C 退出(将同时关闭受控浏览器)')
    print('='*56)
    webbrowser.open(url)
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
