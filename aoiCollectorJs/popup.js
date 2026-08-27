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

document.getElementById('btnExportGJ').addEventListener('click', () => {
  chrome.runtime.sendMessage({ type: 'EXPORT_GEOJSON' }, res => {
    if (res && res.ok) alert('导出成功');
    else alert('导出失败: ' + ((res && res.err) || '未知'));
  });
});

document.getElementById('btnExportSQL').addEventListener('click', () => {
  chrome.runtime.sendMessage({ type: 'EXPORT_SQLITE' }, res => {
    if (res && res.ok) alert('导出成功');
    else alert('导出失败: ' + ((res && res.err) || '未知'));
  });
});

load();
