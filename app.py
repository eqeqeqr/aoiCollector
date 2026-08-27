# -*- coding: utf-8 -*-
"""AOI 半自动采集工具 — FastAPI 后端
启动: python app.py
访问: http://127.0.0.1:9224
"""
import os, sys, json, math, glob, shutil, sqlite3, asyncio
from datetime import datetime
from contextlib import asynccontextmanager

import uvicorn
import shapefile
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from browser import BrowserController

# ================== CORS + Private Network 中间件 ==================
class CORSMiddleware(BaseHTTPMiddleware):
    """处理跨域请求，特别是 Chrome Private Network Access 预检"""
    async def dispatch(self, request, call_next):
        if request.method == 'OPTIONS':
            from starlette.responses import Response
            resp = Response(status_code=204)
        else:
            resp = await call_next(request)
        resp.headers['Access-Control-Allow-Origin'] = '*'
        resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, DELETE, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        resp.headers['Access-Control-Allow-Private-Network'] = 'true'
        return resp

# ================== 配置 ==================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, 'output')
DB_PATH = os.path.join(BASE_DIR, 'aoi.sqlite')
STATIC_DIR = os.path.join(BASE_DIR, 'static')
COLLECT_JS_PATH = os.path.join(STATIC_DIR, 'collect.js')
HOST = '127.0.0.1'
PORT = 9224

# ================== GCJ-02 → WGS-84 ==================
_pi = 3.1415926535897932384626
_x_pi = 3.14159265358979324 * 3000.0 / 180.0
_a = 6378245.0
_ee = 0.00669342162296594323

def _transformlat(lng, lat):
    r = -100.0+2.0*lng+3.0*lat+0.2*lat*lat+0.1*lng*lat+0.2*math.sqrt(abs(lng))
    r += (20.0*math.sin(6.0*lng*_pi)+20.0*math.sin(2.0*lng*_pi))*2.0/3.0
    r += (20.0*math.sin(lat*_pi)+40.0*math.sin(lat/3.0*_pi))*2.0/3.0
    r += (160.0*math.sin(lat/12.0*_pi)+320*math.sin(lat*_pi/30.0))*2.0/3.0
    return r

def _transformlng(lng, lat):
    r = 300.0+lng+2.0*lat+0.1*lng*lng+0.1*lng*lat+0.1*math.sqrt(abs(lng))
    r += (20.0*math.sin(6.0*lng*_pi)+20.0*math.sin(2.0*lng*_pi))*2.0/3.0
    r += (20.0*math.sin(lng*_pi)+40.0*math.sin(lng/3.0*_pi))*2.0/3.0
    r += (150.0*math.sin(lng/12.0*_pi)+300.0*math.sin(lng/30.0*_pi))*2.0/3.0
    return r

def gcj02towgs84(lng, lat):
    if not (72.004 <= lng <= 137.8347 and 0.8293 <= lat <= 55.8271):
        return [lng, lat]
    dlat = _transformlat(lng-105.0, lat-35.0)
    dlng = _transformlng(lng-105.0, lat-35.0)
    radlat = lat / 180.0 * _pi
    magic = math.sin(radlat)
    magic = 1 - _ee * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat*180.0)/((_a*(1-_ee))/(magic*sqrtmagic)*_pi)
    dlng = (dlng*180.0)/(_a/sqrtmagic*math.cos(radlat)*_pi)
    mglat = lat + dlat
    mglng = lng + dlng
    return [lng*2-mglng, lat*2-mglat]

# ================== 工具函数 ==================
def safe_name(s):
    for ch in '\\/:*?"<>|':
        s = s.replace(ch, '_')
    return s.strip() or 'unnamed'

def parse_ring(ring_str, sep):
    pts = [tuple(map(float, s.split(','))) for s in ring_str.split(sep)]
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    return pts

# ================== 文件输出 ==================
def build_info_text(name, poiid, address, pts):
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    return '\n'.join([
        '名称: %s | POIID: %s' % (name, poiid),
        '地址: %s' % address,
        '点数: %d (WGS-84)' % len(pts),
        '范围: 经度 %.6f ~ %.6f | 纬度 %.6f ~ %.6f' % (min(xs), max(xs), min(ys), max(ys)),
        '边界坐标串(lng,lat):',
        ';'.join('%.6f,%.6f' % (p[0], p[1]) for p in pts),
    ])

def write_geojson(fp_base, name, poiid, address, city_name, x, y, pts):
    gj = {'type': 'FeatureCollection', 'features': [{
        'type': 'Feature',
        'properties': {'NAME': name, 'POIID': poiid, 'ADDRESS': address,
                       'CITY_NAME': city_name, 'LONGITUDE': x, 'LATITUDE': y},
        'geometry': {'type': 'Polygon', 'coordinates': [[list(p) for p in pts]]}
    }]}
    with open(fp_base + '.geojson', 'w', encoding='utf-8') as f:
        f.write(json.dumps(gj, ensure_ascii=False, indent=1))

def write_aoi_outputs(name, poiid, address, city_name, x, y, wgs_pts):
    fn = safe_name(name)
    fdir = os.path.join(OUTPUT_DIR, fn)
    if os.path.exists(fdir):
        shutil.rmtree(fdir)
    os.makedirs(fdir)
    fp_base = os.path.join(fdir, fn)

    w = shapefile.Writer(fp_base, encoding='utf-8')
    w.field('NAME', 'C', size=60)
    w.field('ADDRESS', 'C', size=120)
    w.field('CITY_NAME', 'C', size=20)
    w.field('LONGITUDE', 'F', decimal=6)
    w.field('LATITUDE', 'F', decimal=6)
    w.field('POIID', 'C')
    w.poly([wgs_pts])
    w.record(name, address, city_name, x, y, poiid)
    w.close()

    prj = open(fp_base + '.prj', 'w', encoding='utf-8')
    prj.write('GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],'
              'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]]')
    prj.close()

    write_geojson(fp_base, name, poiid, address, city_name, x, y, wgs_pts)
    with open(os.path.join(fdir, '简介.txt'), 'w', encoding='utf-8') as f:
        f.write(build_info_text(name, poiid, address, wgs_pts))
    return fdir

# ================== SQLite ==================
def init_db():
    con = sqlite3.connect(DB_PATH)
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

def upsert_aoi(name, poiid, address, city, x, y, wgs_pts, fdir):
    xs = [p[0] for p in wgs_pts]; ys = [p[1] for p in wgs_pts]
    coords = ';'.join('%.6f,%.6f' % (p[0], p[1]) for p in wgs_pts)
    con = sqlite3.connect(DB_PATH)
    con.execute('''INSERT OR REPLACE INTO aoi
        (poiid,name,address,city,lng,lat,min_lng,max_lng,min_lat,max_lat,
         point_count,coords,folder,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (poiid, name, address, city, x, y, min(xs), max(xs), min(ys), max(ys),
         len(wgs_pts), coords, fdir, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    con.commit()
    con.close()

def list_aoi():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute('SELECT * FROM aoi ORDER BY created_at DESC').fetchall()
    con.close()
    return [dict(r) for r in rows]

def get_aoi(poiid):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    r = con.execute('SELECT * FROM aoi WHERE poiid=?', (poiid,)).fetchone()
    con.close()
    return dict(r) if r else None

def delete_aoi(poiid):
    con = sqlite3.connect(DB_PATH)
    row = con.execute('SELECT folder FROM aoi WHERE poiid=?', (poiid,)).fetchone()
    if row and row[0]:
        shutil.rmtree(row[0], ignore_errors=True)
    con.execute('DELETE FROM aoi WHERE poiid=?', (poiid,))
    con.commit()
    con.close()

def convert_loose_shps():
    files = glob.glob(os.path.join(OUTPUT_DIR, '*.shp'))
    if not files:
        return
    cnt = 0
    for f in files:
        try:
            r = shapefile.Reader(f[:-4])
            rec = r.record(0); sr = r.shapeRecord(0)
            name = str(rec[0]); address = str(rec[1]); city = str(rec[2])
            x = float(rec[3]); y = float(rec[4]); poiid = str(rec[5])
            wgs_pts = [list(p) for p in sr.shape.points]
            fdir = write_aoi_outputs(name, poiid, address, city, x, y, wgs_pts)
            upsert_aoi(name, poiid, address, city, x, y, wgs_pts, fdir)
            for ext in ('.shp', '.shx', '.dbf', '.prj', '.geojson'):
                fp = f[:-4] + ext
                if os.path.exists(fp) and not fp.startswith(fdir):
                    os.remove(fp)
            cnt += 1
        except Exception:
            pass
    if cnt:
        print('[迁移] 共迁移 %d 个旧文件为文件夹结构' % cnt)

# ================== 采集数据保存 ==================
def save_aoi(payload):
    if payload.lstrip().startswith('{') and '"raw"' in payload:
        obj = json.loads(payload)
        raw = obj.get('raw', '')
        rings = obj.get('rings') or {}
    else:
        raw = payload
        rings = {}
    d = json.loads(raw)
    base = (d.get('data') or {}).get('base') or {}
    name = base.get('name') or 'unnamed'
    poiid = base.get('poiid', '')
    entry = rings.get(poiid)
    if isinstance(entry, str):
        entry = {'v': entry}
    if not entry or not entry.get('v'):
        return '[未保存] %s：搜索接口未返回该建筑AOI边界，请先在搜索框搜一次该地点再点击采集' % name
    try:
        pts = parse_ring(entry['v'], '_')
    except Exception:
        return '[未保存] %s：搜索返回的AOI边界解析失败' % name
    if len(pts) < 3:
        return '[未保存] %s：边界点数不足(%d)' % (name, len(pts))
    try:
        x = float(entry.get('x')); y = float(entry.get('y'))
    except (TypeError, ValueError):
        x = float(base.get('x')); y = float(base.get('y'))
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    offm = math.hypot(cx-x, cy-y) * 111000
    warn = ''
    if offm > 800:
        warn = '[警告] 边界面与POI坐标偏差%.0f米，请人工核实位置' % offm
    address = entry.get('a', '')
    city_name = entry.get('c', '')
    wgs_pts = [gcj02towgs84(p[0], p[1]) for p in pts]
    fdir = write_aoi_outputs(name, poiid, address, city_name, x, y, wgs_pts)
    upsert_aoi(name, poiid, address, city_name, x, y, wgs_pts, fdir)
    if warn:
        return '%s，%s 已入库并保存至output文件夹（%d个边界点），建议到预览页核实或删除' % (warn, name, len(pts))
    return '%s 已入库并保存至output文件夹中（%d个边界点，来源:搜索接口）' % (name, len(pts))

# ================== 浏览器控制器 ==================
browser = BrowserController()

def _build_inject_js(base_url):
    """构建注入到高德页面的 JS: 钩子 + 浮窗"""
    with open(COLLECT_JS_PATH, 'r', encoding='utf-8') as f:
        js = f.read()
    # 注入 API 地址和预览地址
    js = 'window.__aoiApiBase="%s";window.__aoiPreviewUrl="%s/preview";\n' % (base_url, base_url) + js
    return js

# ================== FastAPI ==================
# ================== FastAPI ==================
@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    init_db()
    convert_loose_shps()
    console_url = 'http://%s:%d/' % (HOST, PORT)
    try:
        await asyncio.to_thread(browser.launch, console_url)
        print('[启动] 浏览器已连接，控制台: %s' % console_url)
    except Exception as ex:
        print('[启动] 浏览器连接失败: %s' % ex)
    yield
    browser.quit()
    print('[退出] 浏览器已关闭')

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware)
app.mount('/static', StaticFiles(directory=STATIC_DIR), name='static')

@app.get('/favicon.ico', include_in_schema=False)
async def favicon():
    from starlette.responses import Response
    return Response(status_code=204)

@app.get('/', response_class=HTMLResponse)
async def home():
    with open(os.path.join(STATIC_DIR, 'index.html'), 'r', encoding='utf-8') as f:
        return HTMLResponse(f.read())

@app.get('/preview', response_class=HTMLResponse)
async def preview():
    with open(os.path.join(STATIC_DIR, 'preview.html'), 'r', encoding='utf-8') as f:
        return HTMLResponse(f.read())

@app.post('/api/collect')
async def api_collect():
    """新开标签页打开高德地图 — Selenium 操作放线程池，不阻塞响应"""
    base_url = 'http://%s:%d' % (HOST, PORT)
    console_url = base_url + '/'
    js = _build_inject_js(base_url)
    try:
        await asyncio.to_thread(browser.open_collect_tab, js, console_url)
        return {'ok': True}
    except Exception as ex:
        err = str(ex)
        if 'invalid session' in err or 'disconnected' in err:
            try:
                await asyncio.to_thread(browser.ensure_driver, console_url)
                await asyncio.to_thread(browser.open_collect_tab, js, console_url)
                return {'ok': True}
            except Exception as ex2:
                return {'ok': False, 'err': str(ex2)[:120]}
        return {'ok': False, 'err': err[:120]}

@app.post('/api/report')
async def api_report(request: Request):
    """接收采集数据并保存"""
    try:
        body = await request.body()
        msg = await asyncio.to_thread(save_aoi, body.decode('utf-8'))
        print('[采集] ' + msg)
        return HTMLResponse(msg, media_type='text/plain; charset=utf-8')
    except Exception as ex:
        msg = '保存失败: %s' % str(ex)[:80]
        print(msg)
        return HTMLResponse(msg, media_type='text/plain; charset=utf-8')

@app.get('/api/list')
async def api_list():
    return list_aoi()

@app.get('/api/detail')
async def api_detail(poiid: str = ''):
    row = get_aoi(poiid)
    if row:
        return row
    return JSONResponse({'ok': False}, status_code=404)

@app.get('/api/delete')
async def api_delete(poiid: str = ''):
    if poiid:
        delete_aoi(poiid)
        return {'ok': True}
    return JSONResponse({'ok': False, 'err': 'missing poiid'}, status_code=400)

@app.get('/api/status')
async def api_status():
    return {'browser_alive': browser.alive}

# ================== 启动 ==================
if __name__ == '__main__':
    print('=' * 56)
    print(' AOI 半自动采集工具（FastAPI 版）')
    print(' 访问地址: http://%s:%d' % (HOST, PORT))
    print(' 数据目录: %s' % OUTPUT_DIR)
    print(' 按 Ctrl+C 退出')
    print('=' * 56)
    uvicorn.run(app, host=HOST, port=PORT, log_level='info')
