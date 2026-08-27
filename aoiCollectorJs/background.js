/**
 * Background Service Worker - 管理数据库 + 消息通信
 * MV3: 使用 IndexedDB 存储 AOI 数据
 */
const DB_NAME = 'aoi_collector';
const DB_VERSION = 1;
const STORE_NAME = 'aois';

console.log('[AOI BG] Service Worker 已启动', new Date().toLocaleTimeString());

// ================== IndexedDB ==================
let db = null;

function openDB() {
  return new Promise((resolve, reject) => {
    if (db) { resolve(db); return; }
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = (e) => {
      const d = e.target.result;
      if (!d.objectStoreNames.contains(STORE_NAME)) {
        const store = d.createObjectStore(STORE_NAME, { keyPath: 'poiid' });
        store.createIndex('name', 'name', { unique: false });
        store.createIndex('created_at', 'created_at', { unique: false });
      }
    };
    req.onsuccess = (e) => { db = e.target.result; resolve(db); };
    req.onerror = (e) => reject(e.target.error);
  });
}

async function dbPut(aoi) {
  const d = await openDB();
  return new Promise((resolve, reject) => {
    const tx = d.transaction(STORE_NAME, 'readwrite');
    tx.objectStore(STORE_NAME).put(aoi);
    tx.oncomplete = () => resolve();
    tx.onerror = (e) => reject(e.target.error);
  });
}

async function dbGetAll() {
  const d = await openDB();
  return new Promise((resolve, reject) => {
    const tx = d.transaction(STORE_NAME, 'readonly');
    const req = tx.objectStore(STORE_NAME).getAll();
    req.onsuccess = () => resolve(req.result);
    req.onerror = (e) => reject(e.target.error);
  });
}

async function dbGet(poiid) {
  const d = await openDB();
  return new Promise((resolve, reject) => {
    const tx = d.transaction(STORE_NAME, 'readonly');
    const req = tx.objectStore(STORE_NAME).get(poiid);
    req.onsuccess = () => resolve(req.result);
    req.onerror = (e) => reject(e.target.error);
  });
}

async function dbDelete(poiid) {
  const d = await openDB();
  return new Promise((resolve, reject) => {
    const tx = d.transaction(STORE_NAME, 'readwrite');
    tx.objectStore(STORE_NAME).delete(poiid);
    tx.oncomplete = () => resolve();
    tx.onerror = (e) => reject(e.target.error);
  });
}

async function dbCount() {
  const d = await openDB();
  return new Promise((resolve, reject) => {
    const tx = d.transaction(STORE_NAME, 'readonly');
    const req = tx.objectStore(STORE_NAME).count();
    req.onsuccess = () => resolve(req.result);
    req.onerror = (e) => reject(e.target.error);
  });
}

// ================== 坐标转换 ==================
function convertRing(ringStr, sep) {
  const pts = ringStr.split(sep).map(s => {
    const [lng, lat] = s.split(',').map(Number);
    // 内联坐标转换 (content script 已加载 coord.js, 这里独立实现)
    return gcj02towgs84(lng, lat);
  });
  if (pts.length > 1 && pts[0][0] === pts[pts.length - 1][0] && pts[0][1] === pts[pts.length - 1][1]) {
    pts.pop();
  }
  return pts;
}

function gcj02towgs84(lng, lat) {
  const pi = 3.1415926535897932384626;
  const a = 6378245.0;
  const ee = 0.00669342162296594323;
  if (!(72.004 <= lng && lng <= 137.8347 && 0.8293 <= lat && lat <= 55.8271)) return [lng, lat];
  let dlat = -100 + 2*lng + 3*lat + 0.2*lat*lat + 0.1*lng*lat + 0.2*Math.sqrt(Math.abs(lng));
  dlat += (20*Math.sin(6*lng*pi) + 20*Math.sin(2*lng*pi)) * 2/3;
  dlat += (20*Math.sin(lat*pi) + 40*Math.sin(lat/3*pi)) * 2/3;
  dlat += (160*Math.sin(lat/12*pi) + 320*Math.sin(lat*pi/30)) * 2/3;
  let dlng = 300 + lng + 2*lat + 0.1*lng*lng + 0.1*lng*lat + 0.1*Math.sqrt(Math.abs(lng));
  dlng += (20*Math.sin(6*lng*pi) + 20*Math.sin(2*lng*pi)) * 2/3;
  dlng += (20*Math.sin(lng*pi) + 40*Math.sin(lng/3*pi)) * 2/3;
  dlng += (150*Math.sin(lng/12*pi) + 300*Math.sin(lng/30*pi)) * 2/3;
  const radlat = lat / 180 * pi;
  let magic = Math.sin(radlat);
  magic = 1 - ee * magic * magic;
  const sqrtmagic = Math.sqrt(magic);
  dlat = (dlat*180) / ((a*(1-ee))/(magic*sqrtmagic)*pi);
  dlng = (dlng*180) / (a/sqrtmagic*Math.cos(radlat)*pi);
  return [lng*2 - (lng+dlng), lat*2 - (lat+dlat)];
}

// ================== GeoJSON 导出 ==================
function buildGeoJSON(aois) {
  return {
    type: 'FeatureCollection',
    features: aois.map(a => ({
      type: 'Feature',
      properties: {
        NAME: a.name,
        POIID: a.poiid,
        ADDRESS: a.address,
        CITY_NAME: a.city,
        LONGITUDE: a.lng,
        LATITUDE: a.lat
      },
      geometry: {
        type: 'Polygon',
        coordinates: [a.coords_wgs84]
      }
    }))
  };
}

function buildTextExport(a) {
  const xs = a.coords_wgs84.map(p => p[0]);
  const ys = a.coords_wgs84.map(p => p[1]);
  return [
    `名称: ${a.name} | POIID: ${a.poiid}`,
    `地址: ${a.address || ''}`,
    `城市: ${a.city || ''}`,
    `点数: ${a.coords_wgs84.length} (WGS-84)`,
    `范围: 经度 ${Math.min(...xs).toFixed(6)} ~ ${Math.max(...xs).toFixed(6)} | 纬度 ${Math.min(...ys).toFixed(6)} ~ ${Math.max(...ys).toFixed(6)}`,
    `边界坐标串(lng,lat):`,
    a.coords_wgs84.map(p => `${p[0].toFixed(6)},${p[1].toFixed(6)}`).join(';')
  ].join('\n');
}

// ================== 消息处理 ==================
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  console.log('[AOI BG] 收到消息:', msg.type);
  try {
    if (msg.type === 'SAVE_AOI') {
      handleSaveAOI(msg.data).then(r => {
        console.log('[AOI BG] 保存结果:', r);
        sendResponse(r);
      }).catch(e => {
        console.error('[AOI BG] 保存失败:', e);
        try { sendResponse({ ok: false, msg: e.message }); } catch {}
      });
      return true;
    }
    if (msg.type === 'GET_COUNT') {
      dbCount().then(n => sendResponse({ ok: true, count: n }))
        .catch(e => { try { sendResponse({ ok: false, err: e.message }); } catch {} });
      return true;
    }
    if (msg.type === 'GET_ALL') {
      dbGetAll().then(list => sendResponse({ ok: true, data: list }))
        .catch(e => { try { sendResponse({ ok: false, err: e.message }); } catch {} });
      return true;
    }
    if (msg.type === 'DELETE_AOI') {
      dbDelete(msg.poiid).then(() => sendResponse({ ok: true }))
        .catch(e => { try { sendResponse({ ok: false, err: e.message }); } catch {} });
      return true;
    }
    if (msg.type === 'EXPORT_GEOJSON') {
      dbGetAll().then(list => {
        const geojson = buildGeoJSON(list);
        const blob = new Blob([JSON.stringify(geojson, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        chrome.downloads.download({ url, filename: `aoi_${Date.now()}.geojson`, saveAs: true });
        sendResponse({ ok: true });
      }).catch(e => { try { sendResponse({ ok: false, err: e.message }); } catch {} });
      return true;
    }
    if (msg.type === 'EXPORT_SQLITE') {
      dbGetAll().then(list => {
        exportAsSQLite(list).then(blob => {
          const url = URL.createObjectURL(blob);
          chrome.downloads.download({ url, filename: `aoi_${Date.now()}.db`, saveAs: true });
          sendResponse({ ok: true });
        });
      }).catch(e => { try { sendResponse({ ok: false, err: e.message }); } catch {} });
      return true;
    }
  } catch(e) {
    console.error('[AOI BG] 消息处理异常:', e);
    try { sendResponse({ ok: false, msg: e.message }); } catch {}
  }
});

async function handleSaveAOI(data) {
  // data: { raw, rings }
  let raw, rings;
  if (typeof data === 'string') {
    const obj = JSON.parse(data);
    raw = obj.raw;
    rings = obj.rings || {};
  } else {
    raw = data.raw;
    rings = data.rings || {};
  }

  const d = JSON.parse(raw);
  const base = (d.data || {}).base || {};
  const name = base.name || 'unnamed';
  const poiid = base.poiid || '';
  if (!poiid) return { ok: false, msg: '无POIID' };

  let entry = rings[poiid];
  if (typeof entry === 'string') entry = { v: entry };
  if (!entry || !entry.v) {
    return { ok: false, msg: `${name}：搜索接口未返回AOI边界，请先在搜索框搜一次` };
  }

  let pts;
  try {
    pts = entry.v.split('_').map(s => {
      const [lng, lat] = s.split(',').map(Number);
      return gcj02towgs84(lng, lat);
    });
    if (pts.length > 1 && pts[0][0] === pts[pts.length-1][0] && pts[0][1] === pts[pts.length-1][1]) {
      pts.pop();
    }
  } catch (e) {
    return { ok: false, msg: `${name}：边界解析失败` };
  }
  if (pts.length < 3) return { ok: false, msg: `${name}：边界点数不足(${pts.length})` };

  let x, y;
  try { x = parseFloat(entry.x); y = parseFloat(entry.y); }
  catch { x = parseFloat(base.x); y = parseFloat(base.y); }

  // 偏差检测
  const cx = pts.reduce((s, p) => s + p[0], 0) / pts.length;
  const cy = pts.reduce((s, p) => s + p[1], 0) / pts.length;
  const offm = Math.hypot(cx - x, cy - y) * 111000;
  const warn = offm > 800 ? `[警告] 偏差${Math.round(offm)}米，建议核实` : '';

  const aoi = {
    poiid,
    name,
    address: entry.a || base.address || '',
    city: entry.c || base.cityname || '',
    lng: x,
    lat: y,
    coords_wgs84: pts,
    coords_gcj02: entry.v,
    created_at: new Date().toISOString(),
    warn
  };

  await dbPut(aoi);

  const msg = warn
    ? `${warn}，${name} 已保存（${pts.length}点）`
    : `${name} 已保存（${pts.length}点）`;

  // 通知 content script 更新面板
  try {
    const tabs = await chrome.tabs.query({ url: 'https://map.gaode.com/*' });
    for (const tab of tabs) {
      chrome.tabs.sendMessage(tab.id, { type: 'SAVED', msg }).catch(() => {});
    }
  } catch {}

  return { ok: true, msg };
}

// ================== SQLite 导出 (简化版) ==================
async function exportAsSQLite(aois) {
  // 使用简化的 SQLite 文件格式 (纯 SQL 导出为 .sql 文件)
  const lines = [
    'CREATE TABLE IF NOT EXISTS aoi(',
    '  poiid TEXT PRIMARY KEY, name TEXT, address TEXT, city TEXT,',
    '  lng REAL, lat REAL, point_count INTEGER,',
    '  coords TEXT, created_at TEXT);',
    ''
  ];
  for (const a of aois) {
    const coords = a.coords_wgs84.map(p => `${p[0].toFixed(6)},${p[1].toFixed(6)}`).join(';');
    const name = (a.name || '').replace(/'/g, "''");
    const addr = (a.address || '').replace(/'/g, "''");
    const city = (a.city || '').replace(/'/g, "''");
    lines.push(`INSERT OR REPLACE INTO aoi VALUES('${a.poiid}','${name}','${addr}','${city}',${a.lng},${a.lat},${a.coords_wgs84.length},'${coords}','${a.created_at}');`);
  }
  return new Blob([lines.join('\n')], { type: 'text/sql;charset=utf-8 });
}

// 扩展安装时初始化数据库 + 保活定时器
chrome.runtime.onInstalled.addListener(() => {
  openDB();
  // 每25秒触发一次 alarm 保持 Service Worker 存活
  chrome.alarms.create('keepalive', { periodInMinutes: 0.5 });
  console.log('[AOI采集] 扩展已安装，数据库就绪');
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === 'keepalive') {
    // 仅保活，不做任何操作
  }
});
