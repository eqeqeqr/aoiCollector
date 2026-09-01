/**
 * Background Service Worker - 管理数据库 + 消息通信
 * MV3: 使用 IndexedDB 存储 AOI 数据
 */
importScripts('libs/typecode_map.js', 'libs/xlsx.full.min.js');

const DB_NAME = 'aoi_collector';
const DB_VERSION = 1;
const STORE_NAME = 'aois';

let TYPECODE_READY = false;

async function initTypecodeMap() {
  try {
    const url = chrome.runtime.getURL('excel/gaode_typecode.xlsx');
    const resp = await fetch(url);
    if (!resp.ok) throw new Error('file not found');
    const buf = await resp.arrayBuffer();
    const wb = XLSX.read(buf, { type: 'array' });
    const sheet = wb.Sheets[wb.SheetNames[0]];
    const rows = XLSX.utils.sheet_to_json(sheet);
    const ext = {};
    for (const row of rows) {
      const code = String(row['NEW_TYPE']);
      ext[code] = { big: row['大类'], mid: row['中类'], sub: row['小类'] };
    }
    Object.assign(TYPECODE_MAP, ext);
    TYPECODE_READY = true;
    console.log('[AOI BG] 从Excel加载类型映射:', rows.length, '条');
  } catch (e) {
    TYPECODE_READY = true;
    console.log('[AOI BG] 未找到Excel，使用内置映射');
  }
}

function lookupType(typeStr) {
  const code = String(typeStr || '');
  const entry = TYPECODE_MAP[code];
  if (entry) return { typecode: code, big_category: entry.big, mid_category: entry.mid, sub_category: entry.sub };
  return { typecode: code, big_category: '', mid_category: '', sub_category: '' };
}

initTypecodeMap();

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

async function dbClear() {
  const d = await openDB();
  return new Promise((resolve, reject) => {
    const tx = d.transaction(STORE_NAME, 'readwrite');
    tx.objectStore(STORE_NAME).clear();
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

function transformlat(lng, lat) {
  const pi = 3.1415926535897932384626;
  let ret = -100.0 + 2.0 * lng + 3.0 * lat + 0.2 * lat * lat +
    0.1 * lng * lat + 0.2 * Math.sqrt(Math.abs(lng));
  ret += (20.0 * Math.sin(6.0 * lng * pi) + 20.0 * Math.sin(2.0 * lng * pi)) * 2.0 / 3.0;
  ret += (20.0 * Math.sin(lat * pi) + 40.0 * Math.sin(lat / 3.0 * pi)) * 2.0 / 3.0;
  ret += (160.0 * Math.sin(lat / 12.0 * pi) + 320 * Math.sin(lat * pi / 30.0)) * 2.0 / 3.0;
  return ret;
}

function transformlng(lng, lat) {
  const pi = 3.1415926535897932384626;
  let ret = 300.0 + lng + 2.0 * lat + 0.1 * lng * lng +
    0.1 * lng * lat + 0.1 * Math.sqrt(Math.abs(lng));
  ret += (20.0 * Math.sin(6.0 * lng * pi) + 20.0 * Math.sin(2.0 * lng * pi)) * 2.0 / 3.0;
  ret += (20.0 * Math.sin(lng * pi) + 40.0 * Math.sin(lng / 3.0 * pi)) * 2.0 / 3.0;
  ret += (150.0 * Math.sin(lng / 12.0 * pi) + 300.0 * Math.sin(lng / 30.0 * pi)) * 2.0 / 3.0;
  return ret;
}

function gcj02towgs84(lng, lat) {
  const pi = 3.1415926535897932384626;
  const a = 6378245.0;
  const ee = 0.00669342162296594323;
  if (!(72.004 <= lng && lng <= 137.8347 && 0.8293 <= lat && lat <= 55.8271)) return [lng, lat];
  let dlat = transformlat(lng - 105.0, lat - 35.0);
  let dlng = transformlng(lng - 105.0, lat - 35.0);
  const radlat = lat / 180.0 * pi;
  let magic = Math.sin(radlat);
  magic = 1 - ee * magic * magic;
  const sqrtmagic = Math.sqrt(magic);
  dlat = (dlat * 180.0) / ((a * (1 - ee)) / (magic * sqrtmagic) * pi);
  dlng = (dlng * 180.0) / (a / sqrtmagic * Math.cos(radlat) * pi);
  return [lng * 2 - (lng + dlng), lat * 2 - (lat + dlat)];
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
        .catch(e => { try { sendResponse({ ok: false, err: String(e) }); } catch {} });
      return true;
    }
    if (msg.type === 'GET_ALL') {
      dbGetAll().then(list => sendResponse({ ok: true, data: list }))
        .catch(e => { try { sendResponse({ ok: false, err: String(e) }); } catch {} });
      return true;
    }
    if (msg.type === 'DELETE_AOI') {
      dbDelete(msg.poiid).then(() => sendResponse({ ok: true }))
        .catch(e => { try { sendResponse({ ok: false, err: String(e) }); } catch {} });
      return true;
    }
    if (msg.type === 'CLEAR_ALL') {
      dbClear().then(() => sendResponse({ ok: true }))
        .catch(e => { try { sendResponse({ ok: false, err: String(e) }); } catch {} });
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

  const tc = lookupType(entry.t || base.newtype || base.typecode);

  const aoi = {
    poiid,
    name,
    address_poiinfo: entry.a || '',
    address_detail: base.address || '',
    city: entry.c || base.cityname || '',
    lng: x,
    lat: y,
    coords_wgs84: pts,
    coords_gcj02: entry.v,
    typecode: tc.typecode,
    big_category: tc.big_category,
    mid_category: tc.mid_category,
    sub_category: tc.sub_category,
    created_at: new Date().toISOString(),
    warn
  };

  await dbPut(aoi);

  const msg = warn
    ? `${warn}，${name} 已保存（${pts.length}点）`
    : `${name} 已保存（${pts.length}点）`;

  // 通知 content script 更新面板
  try {
    const tabs = await chrome.tabs.query({ url: ['https://gaode.com/*', 'https://*.gaode.com/*', 'https://amap.com/*', 'https://*.amap.com/*'] });
    for (const tab of tabs) {
      chrome.tabs.sendMessage(tab.id, { type: 'SAVED', msg }).catch(() => {});
    }
  } catch {}

  return { ok: true, msg };
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
