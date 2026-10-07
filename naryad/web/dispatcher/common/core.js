/*
 * AUTODISP, рабочее место диспетчера - общая часть двух вариантов интерфейса.
 * Загрузка дня, обращения к API движка (docs/CONTRACT.md), модель для экрана,
 * карта парков (Leaflet), диаграмма нарядов маршрута, график покрытия, карточка объяснения.
 */
(function () {
  "use strict";

  // ---------- мелочи ----------
  function $(sel, root) { return (root || document).querySelector(sel); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function toMin(t) { var p = String(t).split(":"); return +p[0] * 60 + +p[1]; }
  function hm(m) { m = Math.round(m); var h = Math.floor(m / 60), mm = m % 60; return (h < 10 ? "0" : "") + h + ":" + (mm < 10 ? "0" : "") + mm; }
  function clock(m) { return hm(m % 1440); }
  function num(n) { return Number(n).toLocaleString("ru-RU"); }
  function pct(a, b) { return b ? Math.round(1000 * a / b) / 10 : 100; }
  function plural(n, one, few, many) {
    var a = Math.abs(n) % 100, b = a % 10;
    if (a > 10 && a < 20) return many;
    if (b > 1 && b < 5) return few;
    if (b === 1) return one;
    return many;
  }

  var REASONS = {
    no_vehicle: "нет исправного автобуса нужного класса",
    no_driver: "нет водителя с допуском",
    release_limit: "выпуск парка исчерпан",
    lower_priority: "автобус отдан маршруту важнее",
    park_down: "парк не выпускает автобусы",
    manual: "решение диспетчера"
  };
  var PRIORITY = { 1: "высокая", 2: "средняя", 3: "низкая" };
  var CLASSES = { medium: "средний", big: "большой", extra_big: "особо большой" };
  var KINDS = {
    none: "Не заменять", idle: "Автобус сверх выпуска", reserve: "Резерв",
    donor: "С менее важного маршрута", free_driver: "Свободный водитель", reserve_driver: "Водитель резерва"
  };

  // Координаты парков: в данных дня их пока нет (lat/lon = null), берём примерно по адресу.
  // Уточнить у перевозчика вместе с реестрами.
  var PARK_COORDS = {
    P01: [59.8873, 30.3850], P02: [59.9905, 30.3430], P03: [59.8925, 30.4400],
    P05: [59.8540, 30.2560], P06: [59.9530, 30.4650], P07: [59.8470, 30.3010], P08: [59.7525, 30.5880]
  };

  // ---------- API ----------
  function api(method, path, body) {
    return fetch(path, {
      method: method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined
    }).then(function (r) {
      return r.json().catch(function () { return { error: "сервер ответил не JSON (" + r.status + ")" }; })
        .then(function (data) {
          if (!r.ok) {
            var e = new Error(data.error || ("ошибка " + r.status));
            e.details = data.errors || [];
            throw e;
          }
          return data;
        });
    }, function () { throw new Error("сервер не отвечает - запущен ли он?"); });
  }

  // Парки для главного экрана и реестр парка на утро дня.
  function loadParks() { return api("GET", "/x/parks").then(function (r) { return r.parks; }); }
  function loadRegistry(parkId, date) { return api("GET", "/x/registry/" + parkId + "?date=" + date); }
  // Отметки диспетчера на день (SQLite на сервере): загрузить и сохранить
  function loadMarks(parks, date) { return api("GET", "/x/marks?parks=" + parks.join(",") + "&date=" + date); }
  function saveMarks(date, items) { return api("POST", "/x/marks", { date: date, items: items }); }
  // Отдать день движку: проверка данных, затем день живёт на сервере под номером day_id.
  function submitDay(day) {
    return Promise.all([api("POST", "/api/days", day), loadGeo()]).then(function (r) { return { info: r[0], day: day }; });
  }
  // Справочник маршрутов на карте (data/geo/routes.json). Без него карта показывает только парки.
  var GEO = { routes: [], parks: {} };
  function loadGeo() {
    return api("GET", "/x/geo").then(function (g) { GEO = g; return g; }, function () { return GEO; });
  }
  function geoFor(parkId, number) {
    var n = String(number).toLowerCase();
    return GEO.routes.filter(function (g) { return g.park_id === parkId && g.number.toLowerCase() === n; })[0] || null;
  }
  // Расчёт: план + состояние дня.
  function calculate(dayId) {
    return api("POST", "/api/days/" + dayId + "/plan", {}).then(function (plan) {
      return api("GET", "/api/days/" + dayId + "/state").then(function (state) { return { plan: plan, state: state }; });
    });
  }
  function explain(dayId, kind, id, t) {
    return api("GET", "/api/days/" + dayId + "/explain/" + kind + (id ? "/" + encodeURIComponent(id) : "") + (t ? "?t=" + t : ""));
  }
  function eventOptions(dayId, event) { return api("POST", "/api/days/" + dayId + "/events/options", event); }
  function eventApply(dayId, event, option) { return api("POST", "/api/days/" + dayId + "/events/apply", { event: event, option: option }); }

  // ---------- модель для экрана ----------
  function build(day, plan, state) {
    var m = {
      day: day, plan: plan, state: state, meta: day.meta,
      parks: {}, routes: {}, duties: {}, shifts: {}, vehicles: {}, drivers: {},
      parkList: [], routeList: [], unfilled: [], transfers: (state && state.transfers) || (plan && plan.plan.transfers) || []
    };
    day.parks.forEach(function (p) {
      var gp = GEO.parks && GEO.parks[p.id];
      var c = p.lat != null && p.lon != null ? [p.lat, p.lon] : gp ? [gp.lat, gp.lon] : PARK_COORDS[p.id] || null;
      m.parks[p.id] = { id: p.id, name: p.name, address: p.address, state: p.state, coords: c, approx: p.lat == null && !gp,
        routes: [], lineTotal: 0, lineFilled: 0, resTotal: 0, resFilled: 0, shiftTotal: 0, shiftFilled: 0,
        vehOk: 0, vehAll: 0, drvWork: 0, unfilled: [], transIn: 0, transOut: 0 };
      m.parkList.push(m.parks[p.id]);
    });
    day.routes.forEach(function (r) {
      m.routes[r.id] = { id: r.id, number: r.number, park: r.park_id, priority: r.priority, turn: r.turnaround_min,
        classes: r.allowed_classes, length: r.length_km, duties: [], total: 0, filled: 0, unfilled: 0, noDriver: 0,
        geo: geoFor(r.park_id, r.number) };
      m.routeList.push(m.routes[r.id]);
      m.parks[r.park_id] && m.parks[r.park_id].routes.push(m.routes[r.id]);
    });
    day.vehicles.forEach(function (v) {
      m.vehicles[v.id] = v;
      var p = m.parks[v.park_id]; if (!p) return;
      p.vehAll++; if (v.condition === "ok") p.vehOk++;
    });
    day.drivers.forEach(function (d) {
      m.drivers[d.id] = d;
      var p = m.parks[d.park_id]; if (p && d.schedule === "work" && d.medical !== "failed") p.drvWork++;
    });
    day.duties.forEach(function (d) {
      m.duties[d.id] = { id: d.id, park: d.park_id, route: d.route_id, type: d.type, cls: d.vehicle_class,
        start: toMin(d.start), end: toMin(d.end), shiftCount: d.shift_count, segs: [], shifts: [], unfilled: null };
      if (d.route_id && m.routes[d.route_id]) m.routes[d.route_id].duties.push(m.duties[d.id]);
    });
    day.shifts.forEach(function (s) {
      m.shifts[s.id] = { id: s.id, duty: s.duty_id, order: s.order, start: toMin(s.start), end: toMin(s.end), segs: [], unfilled: null };
      m.duties[s.duty_id] && m.duties[s.duty_id].shifts.push(m.shifts[s.id]);
    });
    if (state) {
      state.duties.forEach(function (d) {
        var x = m.duties[d.duty_id]; if (!x) return;
        x.segs = d.vehicles.map(function (s) { return { from: toMin(s.from), to: toMin(s.to), id: s.vehicle_id }; });
      });
      state.shifts.forEach(function (s) {
        var x = m.shifts[s.shift_id]; if (!x) return;
        x.segs = s.drivers.map(function (g) { return { from: toMin(g.from), to: toMin(g.to), id: g.driver_id }; });
      });
      state.unfilled.forEach(function (u) {
        var item = { id: u.id, reason: u.reason, kind: m.shifts[u.id] ? "shift" : "duty" };
        var duty = item.kind === "shift" ? m.duties[m.shifts[u.id].duty] : m.duties[u.id];
        if (item.kind === "shift") m.shifts[u.id].unfilled = u.reason; else if (duty) duty.unfilled = u.reason;
        if (!duty) return;
        item.duty = duty; item.park = duty.park; item.route = duty.route ? m.routes[duty.route] : null;
        item.start = item.kind === "shift" ? m.shifts[u.id].start : duty.start;
        item.end = item.kind === "shift" ? m.shifts[u.id].end : duty.end;
        item.type = duty.type;
        m.unfilled.push(item);
        m.parks[duty.park] && m.parks[duty.park].unfilled.push(item);
      });
    }
    Object.keys(m.duties).forEach(function (id) {
      var d = m.duties[id], p = m.parks[d.park], on = d.segs.length > 0;
      d.shifts.sort(function (a, b) { return a.order - b.order; });
      if (!p) return;
      if (d.type === "line") { p.lineTotal++; if (on) p.lineFilled++; } else { p.resTotal++; if (on) p.resFilled++; }
      d.shifts.forEach(function (s) { if (d.type === "line") { p.shiftTotal++; if (s.segs.length) p.shiftFilled++; } });
      var r = d.route && m.routes[d.route];
      if (r) { r.total++; if (on) r.filled++; else r.unfilled++; d.shifts.forEach(function (s) { if (on && !s.segs.length) r.noDriver++; }); }
    });
    m.transfers.forEach(function (t) {
      var v = m.vehicles[t.vehicle_id];
      if (v && m.parks[v.park_id]) m.parks[v.park_id].transOut++;
      if (m.parks[t.to_park]) m.parks[t.to_park].transIn++;
    });
    m.routeList.forEach(function (r) {
      r.duties.sort(function (a, b) { return a.start - b.start || (a.id < b.id ? -1 : 1); });
      r.worst = worstInterval(r);
    });
    m.unfilled.sort(function (a, b) {
      var pa = a.type === "reserve" ? 9 : (a.route ? a.route.priority : 5), pb = b.type === "reserve" ? 9 : (b.route ? b.route.priority : 5);
      return pa - pb || a.start - b.start;
    });
    m.totals = m.parkList.reduce(function (t, p) {
      ["lineTotal", "lineFilled", "resTotal", "resFilled", "shiftTotal", "shiftFilled", "vehOk", "vehAll", "drvWork"].forEach(function (k) { t[k] += p[k]; });
      return t;
    }, { lineTotal: 0, lineFilled: 0, resTotal: 0, resFilled: 0, shiftTotal: 0, shiftFilled: 0, vehOk: 0, vehAll: 0, drvWork: 0 });
    m.span = span(m);
    return m;
  }

  function span(m) {
    var a = Infinity, b = -Infinity;
    Object.keys(m.duties).forEach(function (id) { var d = m.duties[id]; if (d.start < a) a = d.start; if (d.end > b) b = d.end; });
    if (!isFinite(a)) { a = 240; b = 1560; }
    return [Math.floor(a / 60) * 60, Math.ceil(b / 60) * 60];
  }

  function running(duty, t) { return duty.segs.some(function (s) { return s.from <= t && t < s.to; }); }

  // Покрытие маршрута (или всех маршрутов) по времени: по плану и фактически.
  function coverage(duties, from, to, step) {
    var out = [];
    for (var t = from; t < to; t += step) {
      var plan = 0, run = 0;
      for (var i = 0; i < duties.length; i++) {
        var d = duties[i];
        if (d.type !== "line" || d.start > t || t >= d.end) continue;
        plan++; if (running(d, t)) run++;
      }
      out.push({ t: t, plan: plan, run: run });
    }
    return out;
  }

  // Худший рост интервала на маршруте за день.
  function worstInterval(r) {
    if (!r.duties.length) return null;
    var a = r.duties[0].start, b = Math.max.apply(null, r.duties.map(function (d) { return d.end; }));
    var worst = null;
    coverage(r.duties, a, b, 10).forEach(function (c) {
      if (!c.plan) return;
      var planned = r.turn / c.plan, actual = c.run ? r.turn / c.run : Infinity;
      var grow = actual / planned - 1;
      if (grow > 0.001 && (!worst || grow > worst.grow)) worst = { t: c.t, grow: grow, planned: planned, actual: actual };
    });
    return worst;
  }

  // «11 мин вместо 10»; если после округления числа равны - с десятыми
  function intervalText(w) {
    var a = Math.round(w.actual), p = Math.round(w.planned);
    if (a === p) { a = (Math.round(w.actual * 10) / 10).toLocaleString("ru-RU"); p = (Math.round(w.planned * 10) / 10).toLocaleString("ru-RU"); }
    return a + " мин вместо " + p;
  }

  function lineDuties(m) { return Object.keys(m.duties).map(function (k) { return m.duties[k]; }).filter(function (d) { return d.type === "line"; }); }

  // ---------- представление ----------
  function vehicleName(m, id) { var v = m.vehicles[id]; return v ? v.board_number : id; }
  function driverName(m, id) {
    var d = m.drivers[id]; if (!d) return id;
    var p = d.full_name.split(" ");
    return p[0] + " " + (p[1] ? p[1][0] + "." : "") + (p[2] ? p[2][0] + "." : "");
  }
  function dutyLabel(m, d) {
    var r = d.route && m.routes[d.route];
    return (r ? "Маршрут " + r.number : "Резерв") + ", наряд " + d.id.split("-").pop();
  }
  function parkShort(m, id) { var p = m.parks[id]; return p ? p.name.replace("Автобусный парк ", "Парк ") : id; }

  function explainHtml(x) {
    if (!x) return "";
    var reasons = (x.reasons || []).map(function (r) { return "<li>" + esc(r) + "</li>"; }).join("");
    return '<div class="xp"><div class="xp__q">' + esc(x.question) + '</div><div class="xp__a">' + esc(x.answer) + "</div>" +
      (reasons ? '<ul class="xp__r">' + reasons + "</ul>" : "") + "</div>";
  }

  // Диаграмма нарядов маршрута. opts: {onDuty(duty, tMin, evt)}
  function gantt(el, m, duties, opts) {
    opts = opts || {};
    if (!duties.length) { el.innerHTML = '<p class="muted gantt__empty">Нет нарядов</p>'; return; }
    var a = Math.min.apply(null, duties.map(function (d) { return d.start; })), b = Math.max.apply(null, duties.map(function (d) { return d.end; }));
    a = Math.floor(a / 60) * 60; b = Math.ceil(b / 60) * 60;
    var W = b - a;
    function x(t) { return (100 * (t - a) / W).toFixed(3) + "%"; }
    function w(f, t) { return (100 * (t - f) / W).toFixed(3) + "%"; }
    var hours = "";
    var stepH = W > 14 * 60 ? 120 : 60;
    for (var h = a; h <= b; h += stepH) hours += '<span class="gantt__h" style="left:' + x(h) + '">' + clock(h).slice(0, 2) + ":00</span>";
    var rows = duties.map(function (d) {
      var bars = '<span class="gantt__duty" style="left:' + x(d.start) + ";width:" + w(d.start, d.end) + '"></span>';
      if (d.unfilled || !d.segs.length) {
        bars += '<span class="gantt__seg gantt__seg--empty" style="left:' + x(d.start) + ";width:" + w(d.start, d.end) +
          '"><b>нет автобуса</b></span>';
      }
      d.segs.forEach(function (s, i) {
        bars += '<span class="gantt__seg' + (i > 0 ? " gantt__seg--swap" : "") + '" style="left:' + x(s.from) + ";width:" + w(s.from, s.to) +
          '" title="' + esc("Автобус " + vehicleName(m, s.id) + " " + hm(s.from) + "-" + hm(s.to)) + '"><b>' + esc(vehicleName(m, s.id)) + "</b></span>";
      });
      d.shifts.forEach(function (s) {
        var cls = s.segs.length ? "" : (d.segs.length ? " gantt__shift--empty" : " gantt__shift--off");
        bars += '<span class="gantt__shift' + cls + '" style="left:' + x(s.start) + ";width:" + w(s.start, s.end) + '" title="' +
          esc("Смена " + s.order + " " + hm(s.start) + "-" + hm(s.end) + (s.segs.length ? ": " + s.segs.map(function (g) { return driverName(m, g.id); }).join(", ") : ": нет водителя")) + '"></span>';
      });
      var mark = d.unfilled ? " is-bad" : (d.shifts.some(function (s) { return d.segs.length && !s.segs.length; }) ? " is-warn" : "");
      return '<div class="gantt__row' + mark + '" data-duty="' + esc(d.id) + '"><span class="gantt__label">' + esc(d.id.split("-").pop()) +
        '<small>' + hm(d.start) + "</small></span><span class=\"gantt__track\">" + bars + "</span></div>";
    }).join("");
    el.innerHTML = '<div class="gantt"><div class="gantt__axis"><span class="gantt__label"></span><span class="gantt__track">' + hours + "</span></div>" + rows + "</div>";
    el.querySelectorAll(".gantt__row").forEach(function (row) {
      row.addEventListener("click", function (e) {
        var track = row.querySelector(".gantt__track").getBoundingClientRect();
        var t = a + Math.round((e.clientX - track.left) / track.width * W);
        opts.onDuty && opts.onDuty(m.duties[row.getAttribute("data-duty")], t, e);
        el.querySelectorAll(".gantt__row.is-selected").forEach(function (r) { r.classList.remove("is-selected"); });
        row.classList.add("is-selected");
      });
    });
  }

  // График покрытия: столбики «по плану» и «на линии».
  function coverageChart(el, series, opts) {
    opts = opts || {};
    var Wd = 640, H = opts.height || 120, pad = 22;
    var max = Math.max(1, Math.max.apply(null, series.map(function (s) { return s.plan; })));
    var bw = (Wd - 8) / series.length;
    var bars = series.map(function (s, i) {
      var hp = (H - pad) * s.plan / max, hr = (H - pad) * s.run / max, xx = 4 + i * bw;
      var gap = s.plan - s.run;
      var wdt = Math.max(1, bw - 1).toFixed(1), X = xx.toFixed(1), base = H - pad;
      return '<g><title>' + clock(s.t) + ": на линии " + s.run + " из " + s.plan + "</title>" +
        '<rect x="' + X + '" y="' + (base - hr).toFixed(1) + '" width="' + wdt + '" height="' + hr.toFixed(1) + '" class="cv__run"/>' +
        (gap > 0 ? '<rect x="' + X + '" y="' + (base - hp).toFixed(1) + '" width="' + wdt + '" height="' + Math.max(2, hp - hr).toFixed(1) + '" class="cv__gap"/>' : "") + "</g>";
    }).join("");
    var ticks = "", n = series.length;
    series.forEach(function (s, i) {
      if (s.t % 120 === 0) ticks += '<span style="left:' + (100 * i / n).toFixed(2) + '%">' + clock(s.t).slice(0, 2) + ":00</span>";
    });
    el.innerHTML = '<div class="cvw"><svg class="cv" viewBox="0 0 ' + Wd + " " + (H - pad) + '" preserveAspectRatio="none" style="height:' + (H - pad) + 'px" role="img" aria-label="Автобусы на линии по времени">' +
      bars + '</svg><div class="cv__ticks">' + ticks + "</div></div>";
  }

  // ---------- плавная карта (как в PROJECT POLET) ----------
  var REDUCE = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  // Масштаб дробный и без затухания плиток: жест идёт непрерывно, карта не мутнеет.
  // Штатные кнопки и подпись Leaflet выключены - свои, в стиле AUTODISP (mapControls ниже).
  var MAP_OPTIONS = { zoomControl: false, attributionControl: false, scrollWheelZoom: false,
    zoomSnap: 0, zoomDelta: 1, fadeAnimation: false, zoomAnimation: !REDUCE, markerZoomAnimation: !REDUCE };
  // Плитки тянем на ходу, с запасом в шесть плиток по краю: при резком сдвиге не видно серых дыр.
  // Во время масштабирования плитки не перестраиваются - старые просто растягиваются.
  var ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/";
  var BASEMAPS = [
    { name: "Светлая", url: ESRI + "Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
      labels: ESRI + "Canvas/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}", native: 16 },
    { name: "Улицы", url: ESRI + "World_Street_Map/MapServer/tile/{z}/{y}/{x}", native: 19 }
  ];
  function baseLayers() {
    var out = {};
    BASEMAPS.forEach(function (b) {
      var common = { maxZoom: 19, minZoom: 7, detectRetina: true, maxNativeZoom: b.native - (L.Browser.retina ? 1 : 0),
        updateWhenIdle: false, updateWhenZooming: false, keepBuffer: 6 };
      var layers = [L.tileLayer(b.url, common)];
      if (b.labels) layers.push(L.tileLayer(b.labels, common));
      out[b.name] = L.layerGroup(layers);
    });
    return out;
  }

  // Своё колесо вместо штатного. Штатное копит прокрутку и проигрывает анимированный прыжок -
  // на тачпаде это рывки. Здесь, пока жест идёт, слой карты масштабируется CSS-трансформом
  // от точки под курсором (плитки не трогаются), а когда жест кончился - один раз ставится
  // настоящий масштаб без анимации.
  function smoothWheel(map, el) {
    var pane = map.getPane("mapPane"), target = map.getZoom(), anchor = null, base = "", gesture = false, settle = null;
    function commit() {
      settle = null;
      if (!gesture) return;
      gesture = false;
      pane.style.transform = base; pane.style.transformOrigin = "";
      if (anchor && Math.abs(target - map.getZoom()) > 0.001) map.setZoomAround(anchor, target, { animate: false });
      el.classList.remove("map--moving");
    }
    el.addEventListener("wheel", function (e) {
      e.preventDefault();
      var unit = e.deltaMode === 1 ? 18 : e.deltaMode === 2 ? 380 : 1;
      var gain = e.ctrlKey ? 0.028 : 0.0075;  // щипок тачпада приходит с ctrl и мелкой дельтой
      if (!gesture) {
        gesture = true; target = map.getZoom();
        anchor = map.mouseEventToContainerPoint(e);
        base = pane.style.transform;
        var lp = map.containerPointToLayerPoint(anchor);
        pane.style.transformOrigin = lp.x + "px " + lp.y + "px";
        el.classList.add("map--moving");
      }
      target = Math.max(map.getMinZoom(), Math.min(map.getMaxZoom(), target - e.deltaY * unit * gain));
      pane.style.transform = base + " scale(" + Math.pow(2, target - map.getZoom()) + ")";
      if (settle) clearTimeout(settle);
      settle = setTimeout(commit, 140);
    }, { passive: false });
    // Тени и подсветки пересчитываются каждый кадр - на время движения гасим их.
    var calm = null;
    map.on("movestart zoomstart", function () { if (calm) clearTimeout(calm); el.classList.add("map--moving"); });
    map.on("moveend zoomend", function () { if (calm) clearTimeout(calm); calm = setTimeout(function () { el.classList.remove("map--moving"); }, 140); });
  }

  function makeMap(el, center, zoom) {
    if (typeof el === "string") el = document.getElementById(el);
    var map = L.map(el, MAP_OPTIONS).setView(center, zoom);
    map._bases = baseLayers();
    map._bases[BASEMAPS[0].name].addTo(map);
    smoothWheel(map, el);
    mapControls(map, el);
    return map;
  }

  // Свои элементы карты: масштаб, слои, подпись источников.
  var PLUS = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3v10M3 8h10"/></svg>';
  var MINUS = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8h10"/></svg>';
  function mapControls(map, el) {
    var box = document.createElement("div");
    box.className = "mc";
    box.innerHTML =
      '<div class="mc__zoom" role="group" aria-label="Масштаб">' +
      '<button type="button" class="mc__z" data-z="1" aria-label="Приблизить">' + PLUS + "</button>" +
      '<span class="mc__sep"></span>' +
      '<button type="button" class="mc__z" data-z="-1" aria-label="Отдалить">' + MINUS + "</button></div>" +
      '<div class="mc__layers"><button type="button" class="mc__lbtn" data-pop="layers" aria-expanded="false"><i class="ad-icon ad-icon--map-trifold ad-icon--sm"></i><span>Слои</span></button>' +
      '<div class="mc__panel" data-panel="layers" hidden><div class="mc__h">Подложка</div><div class="mc__seg" data-bases></div><div data-overlays></div></div></div>' +
      '<div class="mc__layers" data-legend-wrap hidden><button type="button" class="mc__lbtn" data-pop="legend" aria-expanded="false"><i class="ad-icon ad-icon--info ad-icon--sm"></i><span>Легенда</span></button>' +
      '<div class="mc__panel mc__panel--legend" data-panel="legend" hidden></div></div>' +
      '<button type="button" class="mc__lbtn mc__full" data-full aria-pressed="false" title="Карта на весь экран"><i class="mc__fic"></i><span>На весь экран</span></button>';
    el.appendChild(box);
    var credit = document.createElement("div");
    credit.className = "mc__credit";
    credit.textContent = "© Esri · © участники OpenStreetMap";
    el.appendChild(credit);
    L.DomEvent.disableClickPropagation(box); L.DomEvent.disableScrollPropagation(box);
    box.querySelectorAll("[data-z]").forEach(function (b) {
      b.addEventListener("click", function () { map.setZoom(Math.round(map.getZoom()) + +b.getAttribute("data-z"), { animate: !REDUCE }); });
    });
    // Всплывающие панели «Слои» и «Легенда»: открыта одна, клик по карте закрывает
    function setPanel(name, on) {
      box.querySelectorAll("[data-panel]").forEach(function (p) {
        var me = p.getAttribute("data-panel") === name && on, b = box.querySelector('[data-pop="' + p.getAttribute("data-panel") + '"]');
        if (me) window.Motion.show(p, "side"); else if (!p.hidden) window.Motion.hide(p, "side");
        b.setAttribute("aria-expanded", String(me));
      });
    }
    box.querySelectorAll("[data-pop]").forEach(function (b) {
      b.addEventListener("click", function () { var n = b.getAttribute("data-pop"); setPanel(n, box.querySelector('[data-panel="' + n + '"]').hidden); });
    });
    map.on("click", function () { setPanel(null, false); });
    // Карта на весь экран: прячем всё вокруг (класс у ближайшего экрана), Escape - обратно
    var full = box.querySelector("[data-full]"), host = el.closest(".layout") || el.parentElement;
    function setFull(on) {
      host.classList.toggle("map-full", on);
      full.setAttribute("aria-pressed", String(on));
      full.title = on ? "Свернуть карту" : "Карта на весь экран";
      full.querySelector("span").textContent = on ? "Свернуть" : "На весь экран";
      var t0 = Date.now();
      (function tick() { map.invalidateSize({ pan: false }); if (Date.now() - t0 < 420) requestAnimationFrame(tick); })();
      setTimeout(function () { map.invalidateSize(); map.fire("moveend"); }, 460);
    }
    full.addEventListener("click", function () { setFull(!host.classList.contains("map-full")); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && host.classList.contains("map-full") && !document.querySelector(".modal")) setFull(false); });
    map._legend = function (html) { var w = box.querySelector("[data-legend-wrap]"); w.hidden = !html; box.querySelector('[data-panel="legend"]').innerHTML = html || ""; };
    var seg = box.querySelector("[data-bases]");
    Object.keys(map._bases).forEach(function (name, i) {
      var b = document.createElement("button");
      b.type = "button"; b.textContent = name; b.className = i === 0 ? "is-on" : "";
      b.addEventListener("click", function () {
        Object.keys(map._bases).forEach(function (n) { map.removeLayer(map._bases[n]); });
        map._bases[name].addTo(map);
        seg.querySelectorAll("button").forEach(function (x) { x.classList.toggle("is-on", x === b); });
      });
      seg.appendChild(b);
    });
    // Слои поверх подложки добавляет тот, кто строит карту: map._overlay("Маршруты", слой)
    var ov = box.querySelector("[data-overlays]");
    map._overlay = function (name, layer) {
      if (!ov.children.length) ov.insertAdjacentHTML("beforeend", '<div class="mc__h">На карте</div>');
      var l = document.createElement("label");
      l.className = "mc__chk";
      l.innerHTML = '<input type="checkbox" checked><span>' + esc(name) + "</span>";
      l.querySelector("input").addEventListener("change", function (e) { layer._wanted = e.target.checked; if (e.target.checked) layer.addTo(map); else map.removeLayer(layer); map.fire("zoomend"); });
      ov.appendChild(l);
    };
  }
  // Вписать область: обычной анимацией масштаба, без перелёта по дуге.
  function flyBounds(map, b, opt) { map.fitBounds(b, Object.assign({ animate: !REDUCE }, opt || {})); }
  function flyTo(map, p, zoom) {
    if (zoom == null) map.panTo(p, { animate: !REDUCE });
    else map.setView(p, zoom, { animate: !REDUCE });
  }

  // Карта парков. opts: {onPark(id)}. Отдаёт {select(id), redraw(m)}.
  function parkMap(el, m, opts) {
    opts = opts || {};
    if (!window.L) { el.innerHTML = '<p class="muted map__off">Карта не загрузилась: нет доступа к интернету для подложки.</p>'; return { select: function () {}, redraw: function () {}, selectRoute: function () {}, resize: function () {}, refit: function () {} }; }
    var map = makeMap(el, [59.92, 30.36], 10);
    el._map = map;  // для отладки из консоли
    var canvas = L.canvas({ padding: 0.5, tolerance: 7 });  // tolerance: линию легче задеть кликом
    var routeLayer = L.layerGroup().addTo(map), stopLayer = L.layerGroup().addTo(map);
    var layer = L.layerGroup().addTo(map), markers = {}, current = m, selected = null, selRoute = null;
    // Подписи номеров маршрутов: маленькие, на линии; видны при приближении, чтобы не засорять обзор города
    var labelLayer = L.layerGroup();
    function syncLabels() { var on = map.getZoom() >= 11.5; if (on && !map.hasLayer(labelLayer) && labelLayer._wanted !== false) labelLayer.addTo(map); if (!on && map.hasLayer(labelLayer)) map.removeLayer(labelLayer); }
    map.on("zoomend", syncLabels);
    // места подписей зависят от масштаба и видимой области - пересчитываем после каждого сдвига
    map.on("moveend", function () { placeLabels(); });
    map._overlay("Маршруты", routeLayer);
    map._overlay("Номера маршрутов", labelLayer);
    map._overlay("Остановки выбранного маршрута", stopLayer);
    map._overlay("Парки и переброски", layer);
    // Оттенок направления B: цвет маршрута, чуть светлее - чтобы различать «туда» и «обратно»
    function shade(hex, k) {
      var n = parseInt(hex.slice(1), 16), r = n >> 16, g = (n >> 8) & 255, b = n & 255;
      function f(c) { return Math.round(c + (255 - c) * k); }
      return "#" + ((1 << 24) + (f(r) << 16) + (f(g) << 8) + f(b)).toString(16).slice(1);
    }
    // Стрелки направления движения вдоль линии: через каждые ~650 м, повёрнуты по ходу
    function arrows(line, color, offset) {
      var step = 650, acc = -offset, prev = L.latLng(line[0]);
      for (var i = 1; i < line.length; i++) {
        var cur = L.latLng(line[i]), seg = prev.distanceTo(cur);
        while (acc + seg >= step) {
          var t = (step - acc) / seg, at = L.latLng(prev.lat + (cur.lat - prev.lat) * t, prev.lng + (cur.lng - prev.lng) * t);
          var p1 = map.project(prev), p2 = map.project(cur), ang = Math.atan2(p2.y - p1.y, p2.x - p1.x) * 180 / Math.PI;
          L.marker(at, { interactive: false, keyboard: false, icon: L.divIcon({ className: "dir-arrow", iconSize: [0, 0],
            html: '<b style="transform:translate(-50%,-50%) rotate(' + ang.toFixed(1) + 'deg)"><i style="--c:' + color + '"></i></b>' }) }).addTo(stopLayer);
          seg -= (step - acc); prev = at; acc = 0;
        }
        acc += seg; prev = cur;
      }
    }
    function drawRoutes() {
      routeLayer.clearLayers(); stopLayer.clearLayers();
      var list = (current.routeList || []).filter(function (r) { return r.geo; });
      // сначала спокойные, сверху - с проблемами, самый верх - выбранный
      list.sort(function (a, b) { return rank(a) - rank(b); });
      list.forEach(function (r) {
        var on = selRoute === r.id, dim = selRoute && !on, bad = current.state && r.unfilled;
        r.geo.directions.forEach(function (d, i) {
          if (d.line.length < 2) return;
          // у выбранного маршрута направление A - цветом маршрута, B - чуть светлее; у остальных оба одним цветом
          var col = on && i > 0 ? shade(r.geo.color, 0.42) : r.geo.color;
          var base = on ? 5 : bad ? 4 : 3;
          // Проблема (наряды без автобуса) - ярко-красная обводка под линией маршрута.
          if (bad) L.polyline(d.line, { renderer: canvas, color: "#e0001b", weight: on ? 12 : 9, opacity: dim ? 0.2 : 0.95, interactive: false }).addTo(routeLayer);
          else if (on) L.polyline(d.line, { renderer: canvas, color: "#ffffff", weight: base + 4, opacity: 0.95, interactive: false }).addTo(routeLayer);
          var line = L.polyline(d.line, { renderer: canvas, color: col, weight: base, opacity: dim ? 0.15 : on || bad ? 1 : 0.7 });
          line.bindTooltip("<b>Маршрут " + esc(r.number) + "</b>" + (d.name ? "<br>" + esc(d.id) + ": " + esc(d.name) : "") +
            (current.state ? "<br>На линии " + r.filled + " из " + r.total + (r.unfilled ? ", без автобуса " + r.unfilled : "") + (r.noDriver ? ", без водителя смен " + r.noDriver : "") : ""), { sticky: true });
          line.on("click", function (e) { L.DomEvent.stopPropagation(e); opts.onRoute && opts.onRoute(r.id, e.latlng, d); });
          // наведение: линия чуть толще и ярче
          line.on("mouseover", function () { line.setStyle({ weight: base + 2.5, opacity: dim ? 0.5 : 1 }); line.bringToFront(); map.getContainer().style.cursor = "pointer"; });
          line.on("mouseout", function () { line.setStyle({ weight: base, opacity: dim ? 0.15 : on || bad ? 1 : 0.7 }); map.getContainer().style.cursor = ""; });
          line.addTo(routeLayer);
          if (on) arrows(d.line, col, i ? 325 : 0);  // стрелки направлений - вразбежку, чтобы на общих улицах не наложились
        });
        if (on) {
          // остановки каждого направления - его цветом; общая для обоих остановка рисуется один раз
          var seen = {};
          r.geo.directions.forEach(function (d, i) {
            var col = i > 0 ? shade(r.geo.color, 0.42) : r.geo.color;
            d.stops.forEach(function (s, k) {
              var key = s.name + "|" + s.lat.toFixed(4) + s.lon.toFixed(4); if (seen[key]) return; seen[key] = 1;
              var end = k === 0 || k === d.stops.length - 1;
              L.circleMarker([s.lat, s.lon], { renderer: canvas, radius: end ? 7 : 4.5, color: col, weight: end ? 3 : 2, fillColor: "#ffffff", fillOpacity: 1 })
                .bindTooltip(esc(s.name) + " · " + esc(d.id) + (end ? (k === 0 ? " · начало" : " · конечная") : ""), { direction: "top" }).addTo(stopLayer);
            });
          });
        }
      });
      placeLabels();
      function rank(r) { return (selRoute === r.id ? 100 : 0) + (r.unfilled ? 10 : 0) + (r.noDriver ? 5 : 0); }
    }
    // Подписи номеров без наложений: для каждого маршрута перебираем точки вдоль линии и берём
    // первую, где табличка не задевает уже поставленные. Не нашлось места - подписи нет (лучше, чем каша).
    function placeLabels() {
      labelLayer.clearLayers();
      if (map.getZoom() < 11.5) return;
      var list = (current.routeList || []).filter(function (r) { return r.geo; });
      list.sort(function (a, b) { return (selRoute === b.id) - (selRoute === a.id) || (b.unfilled ? 1 : 0) - (a.unfilled ? 1 : 0); });
      var placed = [], size = map.getSize(), fr = [0.4, 0.25, 0.55, 0.7, 0.15, 0.85, 0.32, 0.62, 0.48];
      list.forEach(function (r) {
        var w = 12 + 7 * String(r.number).length, h = 18, pad = 4;
        var dirs = r.geo.directions.filter(function (d) { return d.line.length > 1; });
        for (var di = 0; di < dirs.length; di++) {
          for (var fi = 0; fi < fr.length; fi++) {
            var ll = dirs[di].line[Math.floor(dirs[di].line.length * fr[fi])], pt = map.latLngToContainerPoint(ll);
            if (pt.x < w || pt.y < h || pt.x > size.x - w || pt.y > size.y - h) continue;
            var box = { x1: pt.x - w / 2 - pad, x2: pt.x + w / 2 + pad, y1: pt.y - h / 2 - pad, y2: pt.y + h / 2 + pad };
            if (placed.some(function (q) { return box.x1 < q.x2 && box.x2 > q.x1 && box.y1 < q.y2 && box.y2 > q.y1; })) continue;
            placed.push(box);
            var on = selRoute === r.id, dim = selRoute && !on;
            L.marker(ll, { interactive: false, keyboard: false, zIndexOffset: on ? 400 : 0,
              icon: L.divIcon({ className: "rl-wrap", iconSize: [0, 0], html: '<span class="rl' + (dim ? " is-dim" : "") + (on ? " is-on" : "") + '" style="--c:' + r.geo.color + '">' + esc(r.number) + "</span>" }) }).addTo(labelLayer);
            return;
          }
        }
      });
    }
    function routeBounds(ids) {
      var b = null;
      (current.routeList || []).forEach(function (r) {
        if (!r.geo || (ids && ids.indexOf(r.id) < 0)) return;
        r.geo.directions.forEach(function (d) { d.line.forEach(function (p) { b = b ? b.extend(p) : L.latLngBounds(p, p); }); });
      });
      return b;
    }
    // Легенда - ровно то, что сейчас может быть на карте
    function legend() {
      var multi = (current.parkList || []).length > 1, st = !!current.state;
      var it = [
        ['<i class="lg-line lg-line--multi"></i>', "Маршрут - у каждого свой цвет"],
        st ? ['<i class="lg-line lg-line--bad"></i>', "На маршруте есть наряды без автобуса"] : null,
        ['<i class="lg-line lg-line--a"></i><i class="lg-line lg-line--b"></i>', "Выбранный маршрут: направление A и обратное B"],
        ['<i class="lg-arrow"></i>', "Направление движения"],
        ['<i class="lg-stop"></i><i class="lg-stop lg-stop--end"></i>', "Остановка, начальная и конечная"],
        ['<span class="rl lg-rl" style="--c:#0067a5">26</span>', "Номер маршрута (видно при приближении)"],
        st ? ['<span class="lg-pk lg-pk--ok"></span><span class="lg-pk lg-pk--bad"></span>', "Парк: все наряды на линии / есть нехватка"] : ['<span class="lg-pk"></span>', "Парк"],
        multi ? ['<i class="lg-line lg-line--transfer"></i>', "Переброска автобусов между парками"] : null
      ].filter(Boolean);
      map._legend('<div class="mc__h">Обозначения</div>' + it.map(function (x) { return '<div class="lg-it"><span class="lg-ic">' + x[0] + "</span><span>" + x[1] + "</span></div>"; }).join(""));
    }
    function draw() {
      legend();
      drawRoutes();
      layer.clearLayers(); markers = {};
      var pts = [];
      // переброски: линия от парка-донора к парку-получателю, толщина по числу автобусов
      var flows = {};
      current.transfers.forEach(function (t) {
        var v = current.vehicles[t.vehicle_id]; if (!v) return;
        var k = v.park_id + ">" + t.to_park; flows[k] = (flows[k] || 0) + 1;
      });
      Object.keys(flows).forEach(function (k) {
        var p = k.split(">"), a = current.parks[p[0]], b = current.parks[p[1]];
        if (!a || !b || !a.coords || !b.coords) return;
        var mid = [(a.coords[0] + b.coords[0]) / 2 + (b.coords[1] - a.coords[1]) * 0.12, (a.coords[1] + b.coords[1]) / 2 - (b.coords[0] - a.coords[0]) * 0.12];
        var line = L.polyline(curve(a.coords, mid, b.coords), { color: "#227b81", weight: 2 + Math.min(8, flows[k] / 2), opacity: 0.55 }).addTo(layer);
        line.bindTooltip(flows[k] + " " + plural(flows[k], "автобус", "автобуса", "автобусов") + ": " + parkShort(current, a.id) + " → " + parkShort(current, b.id), { sticky: true });
        var end = curve(a.coords, mid, b.coords).slice(-2);
        var ang = Math.atan2(end[1][0] - end[0][0], end[1][1] - end[0][1]) * 180 / Math.PI;
        L.marker(end[1], { icon: L.divIcon({ className: "map-arrow", html: '<span style="transform:rotate(' + (-ang) + 'deg)">➤</span>', iconSize: [16, 16] }), interactive: false }).addTo(layer);
      });
      current.parkList.forEach(function (p) {
        if (!p.coords) return;
        pts.push(p.coords);
        // Метка парка: квадрат в точке парка, справа - название и под ним «на линии N из M».
        var miss = p.lineTotal - p.lineFilled;
        var tone = !current.state ? "idle" : p.state === "down" ? "down" : miss ? "bad" : "ok";
        var sub = !current.state ? p.lineTotal + " " + plural(p.lineTotal, "наряд", "наряда", "нарядов")
          : '<b class="pk__n--' + (miss ? "bad" : "ok") + '">' + p.lineFilled + "</b> из <b>" + p.lineTotal + "</b>";
        var html = '<div class="pk pk--' + tone + (selected === p.id ? " is-selected" : "") + '"><span class="pk__ic"><i class="ad-icon ad-icon--bus"></i></span><span class="pk__tx"><b>' +
          esc(p.name.replace("Автобусный парк", "Парк")) + "</b><small>" + sub + "</small></span></div>";
        var mk = L.marker(p.coords, { icon: L.divIcon({ className: "pk-wrap", html: html, iconSize: [0, 0], iconAnchor: [0, 0] }), zIndexOffset: 500 }).addTo(layer);
        var noDrv = p.unfilled.filter(function (u) { return u.kind === "shift" && u.type === "line"; }).length;
        // подсказка при наведении: краткая сводка по парку (её собирает экран, если умеет)
        mk.bindTooltip(opts.parkTip ? opts.parkTip(p) : esc(p.address || "") + (p.approx ? " (на карте примерно)" : ""),
          { direction: "right", offset: [100, 0], className: "ptip-wrap", opacity: 1 });
        mk.on("click", function () { opts.onPark && opts.onPark(p.id); });
        markers[p.id] = mk;
      });
      var rb = routeBounds(null);
      if (rb && !draw.fitted) { flyBounds(map, rb, { padding: [40, 40] }); draw.fitted = true; }
      else if (pts.length > 1 && !draw.fitted) { flyBounds(map, L.latLngBounds(pts), { padding: [60, 60] }); draw.fitted = true; }
      else if (pts.length === 1 && !draw.fitted) { flyTo(map, pts[0], 12); draw.fitted = true; }
    }
    function curve(a, c, b) {
      var out = [];
      for (var i = 0; i <= 16; i++) {
        var t = i / 16, u = 1 - t;
        out.push([u * u * a[0] + 2 * u * t * c[0] + t * t * b[0], u * u * a[1] + 2 * u * t * c[1] + t * t * b[1]]);
      }
      return out;
    }
    draw();
    syncLabels();
    setTimeout(function () { map.invalidateSize(); }, 50);
    return {
      map: map,
      select: function (id) { selected = id; draw(); var p = current.parks[id]; if (p && p.coords && !selRoute) flyTo(map, p.coords); },
      selectRoute: function (id, pad, noFit) {
        selRoute = id || null; drawRoutes();
        var b = id && !noFit && routeBounds([id]);
        if (b) flyBounds(map, b, { paddingTopLeft: [30, 30], paddingBottomRight: [30, pad || 30], maxZoom: 14 });
      },
      redraw: function (mm, handlers) { if (handlers) opts = handlers; current = mm; draw(); syncLabels(); },
      popup: function (latlng, html, onClose, cls) {
        var wide = cls === "ppop" ? { maxWidth: 300, minWidth: 300 } : { maxWidth: 600, minWidth: 540 };
        var p = L.popup({ className: cls || "rpop", maxWidth: wide.maxWidth, minWidth: wide.minWidth, autoPanPaddingTopLeft: [20, 60], autoPanPaddingBottomRight: [20, 20] })
          .setLatLng(latlng).setContent(html).openOn(map);
        if (onClose) p.on("remove", onClose);
        return p.getElement();
      },
      closePopup: function () { map.closePopup(); },
      refit: function () { draw.fitted = false; },
      resize: function () { map.invalidateSize(); }
    };
  }

  window.Disp = {
    $: $, esc: esc, toMin: toMin, hm: hm, clock: clock, num: num, pct: pct, plural: plural,
    REASONS: REASONS, PRIORITY: PRIORITY, CLASSES: CLASSES, KINDS: KINDS,
    api: api, loadGeo: loadGeo, geoFor: geoFor, geoAll: function () { return GEO; }, loadParks: loadParks, loadRegistry: loadRegistry, loadMarks: loadMarks, saveMarks: saveMarks, submitDay: submitDay, calculate: calculate, explain: explain,
    eventOptions: eventOptions, eventApply: eventApply,
    build: build, intervalText: intervalText, coverage: coverage, lineDuties: lineDuties, running: running,
    vehicleName: vehicleName, driverName: driverName, dutyLabel: dutyLabel, parkShort: parkShort,
    explainHtml: explainHtml, gantt: gantt, makeMap: makeMap, flyBounds: flyBounds, flyTo: flyTo, coverageChart: coverageChart, parkMap: parkMap
  };
})();
