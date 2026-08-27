/**
 * Popup UI - 查看/删除/导出已采集的 AOI
 */
const listEl = document.getElementById('list');
const countEl = document.getElementById('count');

function load() {
  chrome.runtime.sendMessage({ type: 'GET_ALL' }, res => {
    if (!res || !res.ok) { listEl.innerHTML = '<div class="empty">加载失败</div>'; return; }
    const aois = res.data;
    countEl.textContent = aois.length + ' 个';
    if (!aois.length) { listEl.innerHTML = '<div class="empty">暂无数据，请先采集</div>'; return; }
    listEl.innerHTML = '';
    aois.forEach(a => {
      const div = document.createElement('div');
      div.className = 'item';
      div.innerHTML = '<div class="info">'
        + '<div class="name">' + esc(a.name) + '</div>'
        + '<div class="meta">' + a.poiid + ' · ' + (a.coords_wgs84 || []).length + '点 · ' + (a.city || '') + '</div>'
        + (a.warn ? '<div class="warn">⚠ ' + esc(a.warn) + '</div>' : '')
        + '</div>'
        + '<button class="del" title="删除">✕</button>';
      div.querySelector('.del').addEventListener('click', () => {
        if (confirm('确认删除「' + a.name + '」？')) {
          chrome.runtime.sendMessage({ type: 'DELETE_AOI', poiid: a.poiid }, () => load());
        }
      });
      listEl.appendChild(div);
    });
  });
}

function esc(s) { const d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }

// ---- 导出 (在 popup 上下文构建 Blob 并下载, SW 中无 URL.createObjectURL) ----
function downloadFile(text, filename, mime) {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  chrome.downloads.download(
    { url, filename, saveAs: true },
    () => setTimeout(() => URL.revokeObjectURL(url), 60000)
  );
}

function fetchAll(cb) {
  chrome.runtime.sendMessage({ type: 'GET_ALL' }, res => {
    if (!res || !res.ok) { alert('读取数据失败: ' + ((res && res.err) || '无响应')); return; }
    if (!res.data.length) { alert('暂无数据，请先采集'); return; }
    cb(res.data);
  });
}

document.getElementById('btnExportGJ').addEventListener('click', () => {
  fetchAll(aois => {
    const geojson = {
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
        geometry: { type: 'Polygon', coordinates: [a.coords_wgs84] }
      }))
    };
    downloadFile(JSON.stringify(geojson, null, 2), `aoi_${Date.now()}.geojson`, 'application/json;charset=utf-8');
  });
});

// 预览: 打开在线 GeoJSON 编辑器, 用户导入导出的文件即可预览
document.getElementById('btnPreview').addEventListener('click', () => {
  chrome.tabs.create({ url: 'https://ky-gis.com/zh/geojson-editor' });
});

load();
