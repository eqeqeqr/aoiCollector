/**
 * GCJ-02 (火星坐标系) → WGS-84 坐标转换
 * 精度约 0.5~2 米
 */
const CoordTransform = (() => {
  const pi = 3.1415926535897932384626;
  const x_pi = 3.14159265358979324 * 3000.0 / 180.0;
  const a = 6378245.0;
  const ee = 0.00669342162296594323;

  function outOfChina(lng, lat) {
    return !(72.004 <= lng && lng <= 137.8347 && 0.8293 <= lat && lat <= 55.8271);
  }

  function transformlat(lng, lat) {
    let ret = -100.0 + 2.0 * lng + 3.0 * lat + 0.2 * lat * lat +
      0.1 * lng * lat + 0.2 * Math.sqrt(Math.abs(lng));
    ret += (20.0 * Math.sin(6.0 * lng * pi) + 20.0 * Math.sin(2.0 * lng * pi)) * 2.0 / 3.0;
    ret += (20.0 * Math.sin(lat * pi) + 40.0 * Math.sin(lat / 3.0 * pi)) * 2.0 / 3.0;
    ret += (160.0 * Math.sin(lat / 12.0 * pi) + 320 * Math.sin(lat * pi / 30.0)) * 2.0 / 3.0;
    return ret;
  }

  function transformlng(lng, lat) {
    let ret = 300.0 + lng + 2.0 * lat + 0.1 * lng * lng +
      0.1 * lng * lat + 0.1 * Math.sqrt(Math.abs(lng));
    ret += (20.0 * Math.sin(6.0 * lng * pi) + 20.0 * Math.sin(2.0 * lng * pi)) * 2.0 / 3.0;
    ret += (20.0 * Math.sin(lng * pi) + 40.0 * Math.sin(lng / 3.0 * pi)) * 2.0 / 3.0;
    ret += (150.0 * Math.sin(lng / 12.0 * pi) + 300.0 * Math.sin(lng / 30.0 * pi)) * 2.0 / 3.0;
    return ret;
  }

  function gcj02towgs84(lng, lat) {
    if (outOfChina(lng, lat)) return [lng, lat];
    let dlat = transformlat(lng - 105.0, lat - 35.0);
    let dlng = transformlng(lng - 105.0, lat - 35.0);
    const radlat = lat / 180.0 * pi;
    let magic = Math.sin(radlat);
    magic = 1 - ee * magic * magic;
    const sqrtmagic = Math.sqrt(magic);
    dlat = (dlat * 180.0) / ((a * (1 - ee)) / (magic * sqrtmagic) * pi);
    dlng = (dlng * 180.0) / (a / sqrtmagic * Math.cos(radlat) * pi);
    const mglat = lat + dlat;
    const mglng = lng + dlng;
    return [lng * 2 - mglng, lat * 2 - mglat];
  }

  // 批量转换边界点串
  function convertRing(ringStr, sep) {
    const pts = ringStr.split(sep).map(s => {
      const [lng, lat] = s.split(',').map(Number);
      return gcj02towgs84(lng, lat);
    });
    // 去掉重复的首尾闭合点
    if (pts.length > 1 && pts[0][0] === pts[pts.length - 1][0] && pts[0][1] === pts[pts.length - 1][1]) {
      pts.pop();
    }
    return pts;
  }

  return { gcj02towgs84, convertRing };
})();
