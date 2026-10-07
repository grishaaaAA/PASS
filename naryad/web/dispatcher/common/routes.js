/*
 * Редактор справочника маршрутов: список, карточка, трасса и остановки на карте.
 * Данные - /x/geo (naryad/geo/store.py). Сохраняется только по кнопке «Сохранить».
 */
(function () {
  "use strict";
  var D = window.Disp, $ = D.$, esc = D.esc, toast = D.events.toast;
  var G = { routes: [], parks: {} }, park = "P07";
  var cur = null, isNew = false, dirIdx = 0, mode = null, undo = [], dirty = false, hlStop = -1;
  // та же палитра, что у импорта (naryad/geo/osm_import.py): цвета маршрутов парка не повторяются
  var PALETTE = ["#be0032", "#0067a5", "#f38400", "#008856", "#875692", "#e25822", "#1f2a7a", "#8db600", "#b3446c",
    "#0f8c99", "#f6a600", "#604e97", "#882d17", "#e68fac", "#2b3d26", "#c51b7d", "#654522", "#f99379", "#5a8f29"];

  // ---------- карта ----------
  var map = D.makeMap("map", [59.85, 30.32], 11);
  var canvas = L.canvas({ padding: 0.3 });
  var ctxLayer = L.layerGroup().addTo(map), editLayer = L.layerGroup().addTo(map), parkLayer = L.layerGroup().addTo(map);

  function drawContext() {
    ctxLayer.clearLayers();
    G.routes.filter(function (r) { return r.park_id === park && (!cur || r.id !== cur.id); }).forEach(function (r) {
      r.directions.forEach(function (d) {
        if (d.line.length < 2) return;
        L.polyline(d.line, { renderer: canvas, color: r.color, weight: cur ? 2 : 3.5, opacity: cur ? 0.25 : 0.75 })
          .bindTooltip("Маршрут " + esc(r.number) + (d.name ? "<br>" + esc(d.name) : ""), { sticky: true })
          .on("click", function () { if (!mode) tryOpen(r.id); })
          .addTo(ctxLayer);
      });
    });
    parkLayer.clearLayers();
    var p = G.parks[park];
    var n = G.routes.filter(function (r) { return r.park_id === park; }).length;
    if (p) L.marker([p.lat, p.lon], { zIndexOffset: 500, icon: L.divIcon({ className: "pk-wrap", iconSize: [0, 0], iconAnchor: [0, 0],
      html: '<div class="pk pk--idle"><span class="pk__ic"><i class="ad-icon ad-icon--bus"></i></span><span class="pk__tx"><b>Парк ' +
        park.replace("P0", "№").replace("P", "№") + "</b><small>" + n + " " + D.plural(n, "маршрут", "маршрута", "маршрутов") + "</small></span></div>" }) })
      .bindTooltip("Автобусный парк " + park.replace("P0", "№")).addTo(parkLayer);
  }

  function dir() { return cur && cur.directions[dirIdx]; }

  function drawEdit() {
    editLayer.clearLayers();
    if (!cur) return;
    var d = dir();
    cur.directions.forEach(function (x, i) {
      if (i === dirIdx || x.line.length < 2) return;
      L.polyline(x.line, { renderer: canvas, color: cur.color, weight: 3, opacity: 0.45, dashArray: "6 6", interactive: false }).addTo(editLayer);
    });
    if (!d) return;
    if (d.line.length >= 2) {
      L.polyline(d.line, { color: "#fff", weight: 10, opacity: 0.9, interactive: false }).addTo(editLayer);
      var pl = L.polyline(d.line, { color: cur.color, weight: 5, opacity: 1 }).addTo(editLayer);
      pl.on("click", function (e) {
        if (mode !== "line") return;
        L.DomEvent.stopPropagation(e);
        change(); var k = nearestSeg(d.line, e.latlng); d.line.splice(k + 1, 0, ll(e.latlng)); refresh();
      });
      // стрелки направления
      for (var i = 6; i < d.line.length; i += 10) arrow(d.line[i - 1], d.line[i]);
    }
    if (mode === "line") {
      d.line.forEach(function (p, i) {
        var end = i === 0 || i === d.line.length - 1;
        var mk = L.marker(p, { draggable: true, icon: L.divIcon({ className: "", html: '<div class="vx' + (end ? " vx--end" : "") + '"></div>', iconSize: [0, 0] }), title: "Точка " + (i + 1) + ": тащите; правый клик - удалить" }).addTo(editLayer);
        mk.on("dragstart", change);
        mk.on("dragend", function () { d.line[i] = ll(mk.getLatLng()); refresh(); });
        mk.on("contextmenu", function () { change(); d.line.splice(i, 1); refresh(); });
      });
    }
    d.stops.forEach(function (s, i) {
      var mk = L.marker([s.lat, s.lon], { draggable: true, zIndexOffset: 1000,
        icon: L.divIcon({ className: "", html: '<div class="sp' + (hlStop === i ? " is-hl" : "") + '" style="border-color:' + cur.color + '">' + (i + 1) + "</div>", iconSize: [0, 0] }) }).addTo(editLayer);
      mk.bindTooltip((i + 1) + ". " + esc(s.name), { direction: "top", offset: [0, -8] });
      mk.on("dragstart", change);
      mk.on("dragend", function () { var p = mk.getLatLng(); s.lat = r6(p.lat); s.lon = r6(p.lng); refresh(); });
      mk.on("click", function () { highlight(i, true); });
    });
  }
  function arrow(a, b) {
    var ang = Math.atan2(b[0] - a[0], (b[1] - a[1]) * Math.cos(a[0] * Math.PI / 180)) * 180 / Math.PI;
    L.marker(b, { interactive: false, icon: L.divIcon({ className: "map-arrow", html: '<span style="color:' + cur.color + ";transform:rotate(" + (-ang) + 'deg)">➤</span>', iconSize: [14, 14] }) }).addTo(editLayer);
  }
  function r6(x) { return Math.round(x * 1e6) / 1e6; }
  function ll(p) { return [r6(p.lat), r6(p.lng)]; }
  function nearestSeg(line, p) {
    var best = 0, bd = Infinity, k = Math.cos(p.lat * Math.PI / 180);
    for (var i = 0; i < line.length - 1; i++) {
      var d = segDist([p.lat, p.lng], line[i], line[i + 1], k).d;
      if (d < bd) { bd = d; best = i; }
    }
    return best;
  }
  function segDist(p, a, b, k) {
    var ax = a[1] * k, ay = a[0], bx = b[1] * k, by = b[0], px = p[1] * k, py = p[0], dx = bx - ax, dy = by - ay;
    var t = dx === 0 && dy === 0 ? 0 : Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)));
    return { d: Math.hypot(ax + t * dx - px, ay + t * dy - py), t: t };
  }
  // Положение точки вдоль трассы: номер отрезка + доля - чтобы вставить остановку по порядку.
  function along(line, p) {
    var k = Math.cos(p[0] * Math.PI / 180), best = 0, bd = Infinity;
    for (var i = 0; i < line.length - 1; i++) {
      var r = segDist(p, line[i], line[i + 1], k);
      if (r.d < bd) { bd = r.d; best = i + r.t; }
    }
    return best;
  }

  map.on("click", function (e) {
    var d = dir(); if (!d || !mode) return;
    change();
    if (mode === "line") d.line.push(ll(e.latlng));
    else if (mode === "stop") {
      var s = { id: "m-" + Date.now(), name: "Новая остановка", lat: r6(e.latlng.lat), lon: r6(e.latlng.lng) };
      var pos = d.line.length > 1 ? along(d.line, [s.lat, s.lon]) : Infinity, at = d.stops.length;
      if (isFinite(pos)) for (var i = 0; i < d.stops.length; i++) { if (along(d.line, [d.stops[i].lat, d.stops[i].lon]) > pos) { at = i; break; } }
      d.stops.splice(at, 0, s);
      refresh();
      highlight(at, true, true);
      return;
    }
    refresh();
  });

  // ---------- список ----------
  function load() {
    return D.loadGeo().then(function (g) {
      G = g;
      var parks = {}; parks.P07 = 1; G.routes.forEach(function (r) { parks[r.park_id] = 1; });
      $("#park").innerHTML = Object.keys(parks).sort().map(function (p) { return '<option value="' + p + '">Парк ' + p.replace("P0", "№").replace("P", "№") + "</option>"; }).join("");
      $("#park").value = park;
      renderList(); drawContext();
    });
  }
  $("#park").addEventListener("change", function (e) { if (!confirmLeave()) { e.target.value = park; return; } park = e.target.value; $("#toPark").href = "/#prep/" + park; close(); renderList(); drawContext(); fitAll(); });
  $("#q").addEventListener("input", renderList);

  function renderList() {
    var q = $("#q").value.trim().toLowerCase();
    var list = G.routes.filter(function (r) { return r.park_id === park && (!q || (r.number + " " + r.name).toLowerCase().indexOf(q) >= 0); });
    var all = G.routes.filter(function (r) { return r.park_id === park; });
    $("#stat").textContent = all.length + " " + D.plural(all.length, "маршрут", "маршрута", "маршрутов") + ", остановок " +
      all.reduce(function (n, r) { return n + r.directions.reduce(function (m, d) { return m + d.stops.length; }, 0); }, 0);
    $("#list").innerHTML = list.map(function (r) {
      var empty = !r.directions.some(function (d) { return d.line.length > 1; });
      var st = r.directions.reduce(function (n, d) { return n + d.stops.length; }, 0);
      return '<button class="ri" data-id="' + esc(r.id) + '"><span class="ri__n" style="background:' + r.color + '">' + esc(r.number) + '</span><span><div class="ri__t">' + esc(r.name || "без названия") +
        '</div><div class="ri__s' + (empty ? " ri__warn" : "") + '">' + (empty ? "нет трассы - нарисуйте" : r.directions.length + " " + D.plural(r.directions.length, "направление", "направления", "направлений") + ", остановок " + st) +
        "</div></span><span class=\"ri__s\">" + (r.source.indexOf("osm") === 0 ? "OSM" : "вручную") + "</span></button>";
    }).join("") || '<p class="muted caption">Ничего не найдено</p>';
    if (!renderList.done) { renderList.done = true; window.Motion.stagger($("#list").children); }
    $("#list").querySelectorAll("[data-id]").forEach(function (b) { b.addEventListener("click", function () { tryOpen(b.getAttribute("data-id")); }); });
  }

  function fitAll() {
    var b = null;
    G.routes.forEach(function (r) { if (r.park_id === park) r.directions.forEach(function (d) { d.line.forEach(function (p) { b = b ? b.extend(p) : L.latLngBounds(p, p); }); }); });
    if (b) D.flyBounds(map, b, { padding: [30, 30] });
  }

  // ---------- карточка ----------
  function confirmLeave() { return !dirty || window.confirm("Изменения маршрута не сохранены. Уйти без сохранения?"); }
  function tryOpen(id) { if (cur && cur.id === id) return; if (!confirmLeave()) return; open(JSON.parse(JSON.stringify(G.routes.filter(function (r) { return r.id === id; })[0])), false); }

  function open(r, fresh) {
    cur = r; isNew = fresh; dirIdx = 0; mode = null; undo = []; setDirty(fresh); hlStop = -1;
    $("#paneList").hidden = true; $("#err").hidden = true; window.Motion.show($("#paneEdit"), "side", { force: true });
    $("#btnDel").hidden = fresh;
    renderEdit(); drawContext(); drawEdit();
    var b = null; cur.directions.forEach(function (d) { d.line.concat(d.stops.map(function (s) { return [s.lat, s.lon]; })).forEach(function (p) { b = b ? b.extend(p) : L.latLngBounds(p, p); }); });
    if (b) D.flyBounds(map, b, { padding: [40, 40], maxZoom: 15 });
  }
  function close() {
    cur = null; mode = null; setDirty(false); setMode(null);
    $("#paneEdit").hidden = true; window.Motion.show($("#paneList"), "side", { force: true });
    editLayer.clearLayers(); drawContext(); renderList();
  }
  $("#btnBack").addEventListener("click", function () { if (confirmLeave()) close(); });
  $("#btnAdd").addEventListener("click", function () {
    if (!confirmLeave()) return;
    var used = G.routes.filter(function (r) { return r.park_id === park; }).map(function (r) { return r.color.toLowerCase(); });
    open({ id: null, park_id: park, number: "", name: "", color: PALETTE.filter(function (c) { return used.indexOf(c) < 0; })[0] || PALETTE[0], note: "", source: "вручную",
      directions: [{ id: "A", name: "", line: [], stops: [] }] }, true);
    setMode("line");
    $("#fNumber").focus();
  });

  function renderEdit() {
    $("#fNumber").value = cur.number; $("#fName").value = cur.name; $("#fColor").value = cur.color; $("#fNote").value = cur.note || "";
    $("#fSource").textContent = "Источник: " + (cur.source.indexOf("osm:") === 0 ? "OpenStreetMap, отношения " + cur.source.slice(4) : cur.source) + (cur.updated_at ? " · изменён " + cur.updated_at.replace("T", " ").slice(0, 16) + " UTC" : "");
    renderDirs();
  }
  function renderDirs() {
    $("#dirChips").innerHTML = cur.directions.map(function (d, i) {
      return '<button class="chip' + (i === dirIdx ? " is-on" : "") + '" data-i="' + i + '" title="' + esc(d.name) + '">' + esc(d.id) + (d.name ? ": " + esc(d.name) : "") + "</button>";
    }).join("") || '<span class="caption muted">Нет направлений - добавьте</span>';
    $("#dirChips").querySelectorAll("[data-i]").forEach(function (b) { b.addEventListener("click", function () { dirIdx = +b.getAttribute("data-i"); hlStop = -1; renderDirs(); drawEdit(); }); });
    var d = dir();
    $("#dirBox").hidden = !d;
    if (!d) return;
    $("#dName").value = d.name;
    $("#dInfo").textContent = "Трасса: " + d.line.length + " " + D.plural(d.line.length, "точка", "точки", "точек") + ", " + (lengthKm(d.line)).toFixed(1).replace(".", ",") + " км";
    $("#sCount").textContent = d.stops.length ? d.stops.length + "" : "";
    $("#stops").innerHTML = d.stops.map(function (s, i) {
      return '<li class="st' + (i === hlStop ? " is-hl" : "") + '" data-i="' + i + '"><input value="' + esc(s.name) + '" aria-label="Название остановки ' + (i + 1) + '"><span class="st__b">' +
        '<button data-a="up" title="Выше">↑</button><button data-a="down" title="Ниже">↓</button><button data-a="show" title="Показать на карте">◎</button><button data-a="del" title="Удалить">✕</button></span></li>';
    }).join("") || '<li class="caption muted">Остановок нет. Нажмите «Поставить остановку» и кликните по карте.</li>';
    $("#stops").querySelectorAll(".st").forEach(function (li) {
      var i = +li.getAttribute("data-i");
      var inp = li.querySelector("input");
      inp.addEventListener("focus", function () { change.pending = true; });
      inp.addEventListener("input", function () { if (change.pending) { change(); change.pending = false; } d.stops[i].name = inp.value; drawEdit(); });
      li.querySelectorAll("button").forEach(function (b) {
        b.addEventListener("click", function () {
          var a = b.getAttribute("data-a");
          if (a === "show") { highlight(i, false); D.flyTo(map, [d.stops[i].lat, d.stops[i].lon], Math.max(map.getZoom(), 16)); return; }
          change();
          if (a === "del") d.stops.splice(i, 1);
          if (a === "up" && i > 0) d.stops.splice(i - 1, 0, d.stops.splice(i, 1)[0]);
          if (a === "down" && i < d.stops.length - 1) d.stops.splice(i + 1, 0, d.stops.splice(i, 1)[0]);
          refresh();
        });
      });
    });
  }
  function highlight(i, scroll, focus) {
    hlStop = i; renderDirs(); drawEdit();
    var li = $("#stops").querySelector('[data-i="' + i + '"]');
    if (li && scroll) li.scrollIntoView({ block: "nearest" });
    if (li && focus) { var inp = li.querySelector("input"); inp.focus(); inp.select(); }
  }
  function lengthKm(l) {
    var s = 0; for (var i = 1; i < l.length; i++) { var k = Math.cos(l[i][0] * Math.PI / 180); s += Math.hypot((l[i][0] - l[i - 1][0]) * 111.32, (l[i][1] - l[i - 1][1]) * 111.32 * k); }
    return s;
  }

  // поля карточки
  [["#fNumber", "number"], ["#fName", "name"], ["#fNote", "note"]].forEach(function (f) {
    $(f[0]).addEventListener("focus", function () { change.pending = true; });
    $(f[0]).addEventListener("input", function (e) { if (change.pending) { change(); change.pending = false; } cur[f[1]] = e.target.value; });
  });
  $("#fColor").addEventListener("input", function (e) { change(); cur.color = e.target.value; drawEdit(); });
  $("#dName").addEventListener("focus", function () { change.pending = true; });
  $("#dName").addEventListener("input", function (e) { if (change.pending) { change(); change.pending = false; } dir().name = e.target.value; renderChipsOnly(); });
  function renderChipsOnly() { var b = $("#dirChips").querySelector('[data-i="' + dirIdx + '"]'); if (b) b.textContent = dir().id + (dir().name ? ": " + dir().name : ""); }

  // направления
  function nextDirId() { var used = cur.directions.map(function (d) { return d.id; }); return "ABCDEFGH".split("").filter(function (c) { return used.indexOf(c) < 0; })[0]; }
  $("#btnDirAdd").addEventListener("click", function () {
    var id = nextDirId(); if (!id) return toast("Не больше 8 направлений", true);
    change(); cur.directions.push({ id: id, name: "", line: [], stops: [] }); dirIdx = cur.directions.length - 1; refresh(); setMode("line");
  });
  $("#tReverse").addEventListener("click", function () {
    var d = dir(), id = nextDirId(); if (!id) return toast("Не больше 8 направлений", true);
    change();
    var parts = d.name.split(" → ");
    cur.directions.push({ id: id, name: parts.length === 2 ? parts[1] + " → " + parts[0] : "", line: d.line.slice().reverse(),
      stops: d.stops.slice().reverse().map(function (s) { return { id: "m-" + Date.now() + "-" + Math.random().toString(36).slice(2, 6), name: s.name, lat: s.lat, lon: s.lon }; }) });
    dirIdx = cur.directions.length - 1; refresh();
    toast("Обратное направление создано. Проверьте трассу: на односторонних улицах она отличается");
  });
  $("#tFlip").addEventListener("click", function () { var d = dir(); change(); d.line.reverse(); d.stops.reverse(); refresh(); });
  $("#tClear").addEventListener("click", function () { if (!window.confirm("Стереть трассу этого направления? Остановки останутся.")) return; change(); dir().line = []; refresh(); setMode("line"); });
  $("#tDirDel").addEventListener("click", function () {
    if (!window.confirm("Удалить направление «" + (dir().name || dir().id) + "» вместе с трассой и остановками?")) return;
    change(); cur.directions.splice(dirIdx, 1); dirIdx = Math.max(0, dirIdx - 1); refresh();
  });

  // режимы карты
  $("#tLine").addEventListener("click", function () { setMode(mode === "line" ? null : "line"); });
  $("#tStop").addEventListener("click", function () { setMode(mode === "stop" ? null : "stop"); });
  function setMode(m) {
    mode = m;
    $("#tLine").setAttribute("aria-pressed", m === "line"); $("#tStop").setAttribute("aria-pressed", m === "stop");
    $("#map").classList.toggle("is-tool", !!m);
    if (m) window.Motion.show($("#hint"), "down"); else window.Motion.hide($("#hint"), "fade");
    $("#hint").textContent = m === "line" ? "Клик по карте - продолжить трассу. Клик по линии - вставить точку. Точки можно тащить, правый клик - удалить точку."
      : m === "stop" ? "Клик по карте - новая остановка, она встанет по порядку вдоль трассы. Остановки можно тащить." : "";
    drawEdit();
  }

  // ---------- изменения, отмена, сохранение ----------
  function change() { undo.push(JSON.stringify(cur)); if (undo.length > 200) undo.shift(); setDirty(true); }
  function setDirty(v) { dirty = v; $("#dirty").hidden = !v; }
  function refresh() { renderDirs(); drawEdit(); }
  $("#btnUndo").addEventListener("click", doUndo);
  function doUndo() {
    if (!undo.length) return toast("Отменять нечего");
    cur = JSON.parse(undo.pop()); dirIdx = Math.min(dirIdx, Math.max(0, cur.directions.length - 1)); renderEdit(); drawEdit();
    if (!undo.length && !isNew) setDirty(false);
  }
  document.addEventListener("keydown", function (e) {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z" && cur && !/INPUT|TEXTAREA/.test(document.activeElement.tagName)) { e.preventDefault(); doUndo(); }
    if (e.key === "Escape" && mode) setMode(null);
  });
  window.addEventListener("beforeunload", function (e) { if (dirty) { e.preventDefault(); e.returnValue = ""; } });

  $("#btnSave").addEventListener("click", function () {
    $("#err").hidden = true;
    if (!cur.number.trim()) return showErr("Нужен номер маршрута");
    var bad = cur.directions.filter(function (d) { return d.line.length === 1; })[0];
    if (bad) return showErr("Направление " + bad.id + ": в трассе одна точка - добавьте ещё или очистите");
    var req = isNew ? D.api("POST", "/x/geo/routes", cur) : D.api("PUT", "/x/geo/routes/" + encodeURIComponent(cur.id), cur);
    $("#btnSave").disabled = true;
    req.then(function (saved) {
      $("#btnSave").disabled = false;
      var i = G.routes.map(function (r) { return r.id; }).indexOf(saved.id);
      if (i >= 0) G.routes[i] = saved; else G.routes.push(saved);
      cur = JSON.parse(JSON.stringify(saved)); isNew = false; undo = []; setDirty(false);
      $("#btnDel").hidden = false; renderEdit(); drawEdit(); renderList();
      toast("Маршрут " + saved.number + " сохранён");
    }, function (e) { $("#btnSave").disabled = false; showErr(e.message); });
  });
  $("#btnDel").addEventListener("click", function () {
    if (!window.confirm("Удалить маршрут " + cur.number + " из справочника? Трасса и остановки пропадут с карты. Прошлая версия файла останется в routes.json.bak.")) return;
    D.api("DELETE", "/x/geo/routes/" + encodeURIComponent(cur.id)).then(function () {
      G.routes = G.routes.filter(function (r) { return r.id !== cur.id; });
      var n = cur.number; setDirty(false); close(); toast("Маршрут " + n + " удалён");
    }, function (e) { showErr(e.message); });
  });
  function showErr(t) { $("#err").textContent = t; $("#err").hidden = false; }

  // Адрес: /routes#park=P07 - справочник парка, /routes#P07-R01 - сразу карточка маршрута.
  load().then(function () {
    var h = decodeURIComponent(location.hash.slice(1));
    var r = h && G.routes.filter(function (x) { return x.id === h; })[0];
    if (h.indexOf("park=") === 0) park = h.slice(5) || park;
    else if (r) park = r.park_id;
    if ($("#park").querySelector('option[value="' + park + '"]')) $("#park").value = park;
    $("#toPark").href = "/#prep/" + park;
    renderList(); drawContext();
    if (r) tryOpen(h); else fitAll();
  });
})();
