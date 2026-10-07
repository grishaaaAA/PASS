/*
 * Выбор парков и подготовка дня. Главный экран - все парки (пока доступен только №7).
 * Подготовка: реестр парка на утро с сервера, диспетчер отмечает, что сегодня работает:
 * маршруты, состояние автобусов, график и медосмотр водителей. Отметки хранятся в браузере
 * по парку, в реестр не записываются. «Рассчитать» собирает день и отдаёт его движку.
 * К дате пока не привязываемся: реестр берётся на сегодня.
 */
(function () {
  "use strict";
  var D = window.Disp, $ = D.$, esc = D.esc, toast = D.events.toast;
  var M = window.Motion;
  var P = { parks: [], sel: [], ids: [], park: null, date: null, base: null, ov: null, open: {}, tab: null, filter: { veh: "all", drv: "all" }, st: null, saved: null,
    sort: { veh: { key: "route", dir: 1 }, drv: { key: "name", dir: 1 } }, q: { veh: "", drv: "" } };

  function today() { var d = new Date(); return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()); }
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function shortName(p) { return p.name.replace("Автобусный парк", "Парк"); }

  // ---------- экраны и адрес ----------
  var current = null;
  function go(name, silent) {
    var changed = current !== name; current = name;
    $("#scrWelcome").hidden = name !== "welcome";
    $("#scrParks").hidden = name !== "parks"; $("#scrPrep").hidden = name !== "prep"; $("#scrResult").hidden = name !== "result";
    document.body.classList.toggle("is-welcome", name === "welcome");
    // смена экрана - мягкое появление нового (приветствие анимирует себя само)
    if (changed && name !== "welcome") M.enter($(name === "parks" ? "#scrParks .scr__in" : name === "prep" ? "#scrPrep .scr__in" : "#scrResult"), "up");
    document.body.classList.toggle("is-result", name === "result");
    renderCrumbs(name);
    if (!silent) {
      var hash = name === "parks" ? "#parks" : name === "welcome" ? "#" : "#" + name + "/" + P.ids.join(",");
      if (location.hash !== hash) history.pushState(null, "", hash);
    }
    if (name === "result") window.App.resize();
    var scr = $(name === "parks" ? "#scrParks" : name === "prep" ? "#scrPrep" : "#scrResult");
    if (scr && name !== "prep") scr.scrollTop = 0;
  }
  // Крошки: шаги работы, а не названия парков (парков может быть несколько): «Выбор парков / Подготовка дня / Расчёт № 12»
  function renderCrumbs(name) {
    var no = window.App && window.App.runNo && window.App.runNo();
    var crumbs = [['<button data-go="parks">Выбор парков</button>', name === "parks"]];
    if (name !== "parks" && name !== "welcome" && P.ids.length) crumbs.push(['<button data-go="prep">Подготовка дня</button>', name === "prep"]);
    if (name === "result") crumbs.push(["<span>Расчёт" + (no ? " № " + no : "") + "</span>", true]);
    $("#crumbs").innerHTML = crumbs.map(function (c) { return '<span class="crumb' + (c[1] ? " is-on" : "") + '">' + c[0] + "</span>"; }).join('<span class="crumb__sep">/</span>');
  }
  window.addEventListener("autodisp:run", function () { if (current === "result") renderCrumbs("result"); });
  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-go]"); if (!b) return;
    var to = b.getAttribute("data-go");
    if (to === "result" && !P.calculated) return;
    if (to === current) return;
    confirmLeave().then(function (ok) { if (ok) go(to); });
  });
  window.addEventListener("popstate", function () {
    if (current === "prep" && pending().length && location.hash.indexOf("#prep/") !== 0) {
      var back = location.hash;
      history.pushState(null, "", "#prep/" + P.ids.join(","));  // остаёмся, пока не ответит
      confirmLeave().then(function (ok) { if (ok) { history.replaceState(null, "", back || location.pathname); route(); } });
      return;
    }
    route();
  });
  function route() {
    var h = location.hash.slice(1).split("/");
    if ((h[0] === "prep" || h[0] === "result") && h[1]) {
      if (P.ids.join(",") === h[1] && P.base) return go(h[0] === "result" && P.calculated ? "result" : "prep", true);
      return openParks(h[1].split(","), true);
    }
    go(h[0] === "welcome" || !h[0] ? "welcome" : "parks", true);
  }
  // Каждое открытие приложения начинается с приветствия, даже если в адресе сохранён экран
  // (браузер обычно открывает последний адрес, например #prep/P07). После «Начать» - выбор парков.
  function firstRoute() {
    history.replaceState(null, "", location.pathname);
    go("welcome", true);
  }

  // ---------- 0. Приветствие ----------
  function hello() {
    var h = new Date().getHours();
    return h >= 5 && h < 12 ? "Доброе утро" : h >= 12 && h < 17 ? "Добрый день" : h >= 17 && h < 23 ? "Добрый вечер" : "Доброй ночи";
  }
  // Справа - карта, на которой прорисовываются настоящие трассы маршрутов доступных парков.
  // Порядок: карта проявляется кругом от парка (из размытия), затем линии маршрутов рисуются по очереди.
  var welcomeMap = null;
  function drawWelcomeArt() {
    var el = $("#welcomeMap");
    var routes = (D.geoAll ? D.geoAll().routes : []).filter(function (r) { return r.directions.length && r.directions[0].line.length > 1; });
    if (!window.L || !routes.length) return;
    if (welcomeMap) { welcomeMap.remove(); welcomeMap = null; }
    var map = welcomeMap = L.map(el, { zoomControl: false, attributionControl: false, dragging: false, scrollWheelZoom: false, doubleClickZoom: false,
      boxZoom: false, keyboard: false, touchZoom: false, zoomSnap: 0, fadeAnimation: false });
    var bounds = L.latLngBounds([]);
    routes.forEach(function (r) { r.directions[0].line.forEach(function (p) { bounds.extend(p); }); });
    map.fitBounds(bounds, { padding: [24, 24], animate: false });
    var tiles = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}", { maxNativeZoom: 16 }).addTo(map);
    var svg = L.svg({ padding: 0.3 }).addTo(map), paths = [];
    routes.forEach(function (r) { paths.push(L.polyline(r.directions[0].line, { renderer: svg, color: r.color, weight: 3, opacity: 0.9, lineCap: "round", lineJoin: "round", interactive: false }).addTo(map)); });
    var parks = D.geoAll().parks || {}, park = Object.keys(parks)[0];
    var center = park ? map.latLngToContainerPoint([parks[park].lat, parks[park].lon]) : map.getSize().divideBy(2);
    if (park) L.marker([parks[park].lat, parks[park].lon], { interactive: false, icon: L.divIcon({ className: "wl-park", html: '<span class="bus-mark bus-mark--sm"><i class="ad-icon ad-icon--bus"></i></span>', iconSize: [30, 30] }) }).addTo(map);
    // линии спрятаны до своей очереди
    paths.forEach(function (pl) { var p = pl.getElement(), len = p.getTotalLength(); p.style.strokeDasharray = len; p.style.strokeDashoffset = M.reduce ? 0 : len; pl._len = len; });
    if (M.reduce) return;
    el.style.opacity = 0;
    var started = false;
    function reveal() {
      if (started) return; started = true;
      el.style.opacity = "";
      var at = Math.round(center.x) + "px " + Math.round(center.y) + "px";
      el.animate([
        { clipPath: "circle(0% at " + at + ")", filter: "blur(10px)", transform: "scale(1.06)", opacity: 0.3 },
        { clipPath: "circle(140% at " + at + ")", filter: "blur(0)", transform: "scale(1)", opacity: 1 }
      ], { duration: 1300, easing: "cubic-bezier(0.32, 0.72, 0, 1)", fill: "backwards" });
      paths.forEach(function (pl, i) {
        pl.getElement().animate([{ strokeDashoffset: pl._len }, { strokeDashoffset: 0 }],
          { duration: 1400, delay: 650 + i * 90, easing: "cubic-bezier(0.65, 0, 0.35, 1)", fill: "forwards" });
      });
    }
    // ждём подложку, чтобы проявлялась карта, а не серые квадраты; но не дольше секунды
    tiles.once("load", reveal); setTimeout(reveal, 1000);
  }
  function showWelcome() {
    $("#welcomeHello").textContent = hello();
    M.stagger($("#scrWelcome .welcome__text").children, "up");
    drawWelcomeArt();
  }
  // «Начать» всегда ведёт в выбор парков
  $("#btnStart").addEventListener("click", function () {
    go("parks");
    M.stagger($("#parks").querySelectorAll(".kind")); M.stagger($("#parks").querySelectorAll(".pc"));
  });

  // ---------- 1. Выбор парков ----------
  // Клик по карточке отмечает парк; можно выбрать несколько и рассчитать их одним днём.
  function parkById(id) { return P.parks.filter(function (p) { return p.id === id; })[0]; }
  function selTitle(short) {
    var ps = P.ids.map(parkById).filter(Boolean);
    if (ps.length === 1) return short ? shortName(ps[0]) : ps[0].name;
    return (short ? "Парки " : "Автобусные парки ") + ps.map(function (p) { return p.name.replace(/^.*№\s*/, "№"); }).join(", ");
  }
  // Виды транспорта: у каждого свой раскрывающийся список парков. Пока реестры есть только у автобусных.
  var KINDS = [
    { id: "bus", name: "Автобусы", icon: '<i class="ad-icon ad-icon--bus"></i>' },
    { id: "trolley", name: "Троллейбусы", icon: '<i class="tr-ic tr-ic--trolley"></i>' },
    { id: "tram", name: "Трамваи", icon: '<i class="tr-ic tr-ic--tram"></i>' }
  ];
  function kindOpen(id) {
    try { var v = JSON.parse(localStorage.getItem("autodisp.kinds") || "null"); if (v) return !!v[id]; } catch (e) { /* без памяти */ }
    return id === "bus";
  }
  function setKindOpen(id, on) {
    var v = {}; try { v = JSON.parse(localStorage.getItem("autodisp.kinds") || "{}") || {}; } catch (e) { v = {}; }
    v[id] = on; try { localStorage.setItem("autodisp.kinds", JSON.stringify(v)); } catch (e) { /* без памяти */ }
  }
  function num(n) { return n == null ? "?" : D.num(n); }
  function parkCard(p) {
    var on = p.available, tag = on ? "button" : "div", picked = P.sel.indexOf(p.id) >= 0;
    var vals = [[on ? p.routes : null, "маршрутов"], [on ? p.vehicles : null, "автобусов"], [on ? p.release_weekday : null, "выпуск в будни"], [on ? p.release_weekend : null, "выпуск в выходные"]];
    return "<" + tag + ' class="pc' + (on ? "" : " pc--locked") + (picked ? " is-picked" : "") + '"' +
      (on ? ' type="button" role="checkbox" aria-checked="' + picked + '" data-park="' + p.id + '"' : ' aria-disabled="true"') + ">" +
      '<span class="pc__top"><span class="pc__ic"><i class="ad-icon ad-icon--bus"></i></span>' + (on ? '<span class="pc__check" aria-hidden="true"></span>' : "") + "</span>" +
      '<span class="pc__name">' + esc(p.name) + '</span><span class="pc__addr">' + esc(p.address || "") + "</span>" +
      '<span class="pc__nums">' + vals.map(function (x) { return "<span><b>" + num(x[0]) + "</b>" + x[1] + "</span>"; }).join("") + "</span>" +
      (on ? "" : '<span class="pc__go pc__go--off">Реестр парка пока не подключён</span>') + "</" + tag + ">";
  }
  function renderParks() {
    $("#parks").innerHTML = KINDS.map(function (k) {
      var list = P.parks.filter(function (p) { return (p.kind || "bus") === k.id; });
      var avail = list.filter(function (p) { return p.available; }).length;
      var head = '<span class="kind__ic">' + k.icon + '</span><span class="kind__t">' + k.name + "</span>";
      // Вид транспорта без парков - недоступен: полупрозрачный, не раскрывается
      if (!list.length) return '<div class="kind kind--off" aria-disabled="true"><div class="kind__head">' + head + '<span class="kind__n">парки не подключены</span></div></div>';
      var count = list.length + " " + D.plural(list.length, "парк", "парка", "парков") + (avail < list.length ? ", подключено " + avail : "");
      return '<details class="kind" data-kind="' + k.id + '"' + (kindOpen(k.id) ? " open" : "") + '><summary class="kind__head">' + head +
        '<span class="kind__n">' + count + '</span><i class="ad-icon ad-icon--caret-down ad-icon--sm kind__car"></i></summary>' +
        '<div class="parks__grid">' + list.map(parkCard).join("") + "</div></details>";
    }).join("");
    $("#parks").querySelectorAll("details.kind").forEach(function (d) {
      d.addEventListener("toggle", function () { setKindOpen(d.getAttribute("data-kind"), d.open); });
    });
    $("#parks").querySelectorAll("[data-park]").forEach(function (b) {
      b.addEventListener("click", function () {
        var id = b.getAttribute("data-park"), i = P.sel.indexOf(id);
        if (i >= 0) P.sel.splice(i, 1); else P.sel.push(id);
        // карточку не перерисовываем - иначе она пересоздаётся под курсором и рамка «прыгает»
        b.classList.toggle("is-picked", i < 0); b.setAttribute("aria-checked", String(i < 0));
        saveSel(); updatePick();
      });
    });
    updatePick();
  }
  function updatePick() {
    var ps = P.sel.map(parkById).filter(Boolean), avail = P.parks.filter(function (p) { return p.available; });
    var routes = 0, veh = 0; ps.forEach(function (p) { routes += p.routes; veh += p.vehicles; });
    $("#pickText").innerHTML = ps.length
      ? '<span class="go__m">Выбрано: <b>' + ps.length + "</b> " + D.plural(ps.length, "парк", "парка", "парков") + "</span>" +
        '<span class="go__m"><i class="ad-icon ad-icon--path ad-icon--sm"></i><b>' + routes + "</b> " + D.plural(routes, "маршрут", "маршрута", "маршрутов") + "</span>" +
        '<span class="go__m"><i class="ad-icon ad-icon--bus ad-icon--sm"></i><b>' + veh + "</b> " + D.plural(veh, "автобус", "автобуса", "автобусов") + "</span>"
      : '<span class="go__m">Выберите один или несколько парков</span>';
    $("#btnPickAll").hidden = ps.length === avail.length;
    $("#btnWork").disabled = !ps.length;
  }
  function saveSel() { try { localStorage.setItem("autodisp.parks", JSON.stringify(P.sel)); } catch (e) { /* без памяти */ } }
  function loadSel() {
    try { var v = JSON.parse(localStorage.getItem("autodisp.parks") || "[]"); return v.filter(function (id) { var p = parkById(id); return p && p.available; }); } catch (e) { return []; }
  }
  $("#btnPickAll").addEventListener("click", function () { P.sel = P.parks.filter(function (p) { return p.available; }).map(function (p) { return p.id; }); saveSel(); renderParks(); });

  $("#btnWork").addEventListener("click", function () {
    if (!P.sel.length) return;
    // порядок - как на экране
    openParks(P.parks.map(function (p) { return p.id; }).filter(function (id) { return P.sel.indexOf(id) >= 0; }));
  });

  // ---------- 2. Подготовка ----------
  // Реестры выбранных парков складываются в один день: движок сам решит переброски между ними.
  function openParks(ids, silent) {
    ids = ids.filter(function (id) { var p = parkById(id); return p && p.available; });
    if (!ids.length) return go("parks", silent);
    P.ids = ids; P.date = today(); P.calculated = false; P.base = null; P.tab = null;
    $("#prepTitle").textContent = selTitle(false);
    $("#btnGeo").href = "/routes#park=" + ids[0];
    go("prep", silent);
    $("#ready").innerHTML = ""; $("#sections").innerHTML = '<div class="lp__load"><span class="spinner"></span> Загружаю ' + (ids.length > 1 ? "реестры парков" : "реестр парка") + "…</div>";
    Promise.all(ids.map(function (id) { return D.loadRegistry(id, P.date); }).concat([D.loadGeo(), D.loadMarks(ids, P.date)])).then(function (r) {
      P.base = merge(r.slice(0, ids.length));
      P.st = initState();
      // отметки, сделанные сегодня раньше, - поверх реестра
      var saved = r[ids.length + 1];
      ["route", "veh", "drv"].forEach(function (k) { Object.keys(saved[k] || {}).forEach(function (id) { if (id in P.st[k]) P.st[k][id] = saved[k][id]; }); });
      P.saved = clone(P.st);
      updateSave();
      $("#sections").innerHTML = "";
      refresh(); renderPanel();
    }, function (e) { $("#sections").innerHTML = '<p class="err">' + esc(e.message) + "</p>"; });
  }
  function merge(days) {
    var out = JSON.parse(JSON.stringify(days[0]));
    days.slice(1).forEach(function (d) {
      ["parks", "routes", "duties", "shifts", "vehicles", "drivers"].forEach(function (k) { out[k] = out[k].concat(d[k]); });
    });
    return out;
  }

  // Состояние дня: что работает, какие автобусы исправны, какие водители выходят.
  // Начальные значения - из реестра, поверх - отметки диспетчера за этот день из базы (naryad/web/marks.py).
  function initState() {
    var st = { route: {}, veh: {}, drv: {} };
    P.base.routes.forEach(function (r) { st.route[r.id] = true; });
    P.base.vehicles.forEach(function (v) { st.veh[v.id] = v.condition === "ok"; });
    // выходит = работает по графику и прошёл утренний медосмотр (правило движка may_depart)
    P.base.drivers.forEach(function (d) { st.drv[d.id] = d.schedule === "work" && d.medical === "passed"; });
    return st;
  }
  // Переключения копятся, пока не нажата «Сохранить» (или «Рассчитать» - она сохраняет сама).
  // Несохранённое = отличия текущего состояния от последнего сохранённого.
  function clone(o) { return JSON.parse(JSON.stringify(o)); }
  function mark(kind, ids, value) { ids.forEach(function (id) { P.st[kind][id] = value; }); updateSave(); }
  function pending() {
    var out = [];
    if (!P.st || !P.saved) return out;
    ["route", "veh", "drv"].forEach(function (k) {
      Object.keys(P.st[k]).forEach(function (id) { if (P.st[k][id] !== P.saved[k][id]) out.push({ kind: k, id: id, value: P.st[k][id] }); });
    });
    return out;
  }
  function updateSave() {
    var n = pending().length, b = $("#btnSave");
    b.disabled = !n;
    b.querySelector("span").textContent = n ? "Сохранить (" + n + ")" : "Сохранено";
    b.classList.toggle("is-dirty", !!n);
  }
  function save() {
    var items = pending();
    if (!items.length) return Promise.resolve();
    $("#btnSave").disabled = true;
    return D.saveMarks(P.date, items).then(function () {
      items.forEach(function (it) { P.saved[it.kind][it.id] = it.value; });
      updateSave(); toast("Сохранено: " + items.length + " " + D.plural(items.length, "изменение", "изменения", "изменений"));
    }, function (e) { updateSave(); toast("Не сохранилось: " + e.message, true); throw e; });
  }
  $("#btnSave").addEventListener("click", function () { save().catch(function () {}); });
  // Уход с подготовки с несохранённым - спросить; согласие сбрасывает несохранённое
  function confirmLeave() {
    var n = pending().length;
    if (!n || current !== "prep") return Promise.resolve(true);
    return D.events.confirm({ title: "Изменения не сохранены",
      text: "Несохранённых изменений: " + n + ". Если уйти, они пропадут.", ok: "Уйти без сохранения", cancel: "Остаться", danger: true })
      .then(function (ok) { if (ok) { P.st = clone(P.saved); updateSave(); } return ok; });
  }
  window.addEventListener("beforeunload", function (e) { if (current === "prep" && pending().length) { e.preventDefault(); e.returnValue = ""; } });
  function routeOn(r) { return P.st.route[r.id]; }
  function vehOk(v) { return P.st.veh[v.id]; }
  function drvGoes(d) { return P.st.drv[d.id]; }

  // Сводка готовности: что сегодня работает и хватает ли
  function counts() {
    var b = P.base, on = {}, need = {}, have = {}, shifts = 0, lineDuties = 0, res = 0;
    b.routes.forEach(function (r) { on[r.id] = routeOn(r); });
    var dutyOn = {};
    b.duties.forEach(function (d) {
      if (d.type === "line" && !on[d.route_id]) return;
      dutyOn[d.id] = 1; need[d.vehicle_class] = (need[d.vehicle_class] || 0) + 1;
      if (d.type === "line") lineDuties++; else res++;
    });
    b.shifts.forEach(function (s) { if (dutyOn[s.duty_id]) shifts++; });
    var vehOk = 0;
    b.vehicles.forEach(function (v) { if (P.st.veh[v.id]) { vehOk++; have[v.class] = (have[v.class] || 0) + 1; } });
    var drvReady = 0;
    b.drivers.forEach(function (d) { if (P.st.drv[d.id]) drvReady++; });
    var lack = Object.keys(need).map(function (c) { return [c, need[c] - (have[c] || 0)]; }).filter(function (x) { return x[1] > 0; });
    return { routes: b.routes.filter(routeOn).length, lineDuties: lineDuties, res: res, shifts: shifts, vehOk: vehOk, vehAll: b.vehicles.length,
      drvReady: drvReady, drvAll: b.drivers.length, lack: lack, drvLack: Math.max(0, shifts - drvReady) };
  }

  // Три карточки: маршруты, автобусы, водители. Карточка открывает список под собой.
  var CARDS = [["routes", "path", "Маршруты"], ["veh", "bus", "Автобусы"], ["drv", "users", "Водители"]];
  function refresh() {
    if (!P.base) return;
    var c = counts();
    // Карточка: заголовок и подписанные показатели. Красным - то, чего не хватает.
    var lackText = c.lack.map(function (x) { return x[1] + " " + D.CLASSES[x[0]]; }).join(", ");
    var stats = {
      routes: [["в работе", c.routes + " из " + P.base.routes.length], ["нарядов на линии", c.lineDuties], ["в резерве", c.res]],
      veh: [["исправны", c.vehOk + " из " + c.vehAll], ["нужно на наряды", c.lineDuties + c.res], ["не хватает", c.lack.length ? lackText : 0, c.lack.length ? "bad" : ""]],
      drv: [["выходят", c.drvReady + " из " + c.drvAll], ["нужно на смены", c.shifts], ["не хватает", c.drvLack, c.drvLack ? "bad" : ""]]
    };
    if (!$("#ready").childElementCount) {
      $("#ready").innerHTML = CARDS.map(function (k) {
        return '<button type="button" class="rc-card" role="tab" data-card="' + k[0] + '" aria-selected="false"><span class="rc-card__head"><span class="rc-card__ic"><i class="ad-icon ad-icon--' + k[1] + '"></i></span>' +
          '<span class="rc-card__l">' + k[2] + '</span><i class="ad-icon ad-icon--caret-down ad-icon--sm rc-card__car"></i></span><span class="rc-card__stats"></span></button>';
      }).join("");
      M.stagger($("#ready").children);
      $("#ready").querySelectorAll("[data-card]").forEach(function (b) {
        b.addEventListener("click", function () { var k = b.getAttribute("data-card"); P.tab = P.tab === k ? null : k; renderPanel(); });
      });
    }
    CARDS.forEach(function (k) {
      var el = $('[data-card="' + k[0] + '"]'), bad = stats[k[0]].some(function (x) { return x[2] === "bad"; });
      el.className = "rc-card" + (bad ? " rc-card--bad" : "") + (P.tab === k[0] ? " is-on" : "");
      el.setAttribute("aria-selected", String(P.tab === k[0]));
      el.querySelector(".rc-card__stats").innerHTML = stats[k[0]].map(function (x) {
        return '<span class="rc-st' + (x[2] ? " rc-st--" + x[2] : "") + '"><b>' + esc(typeof x[1] === "number" ? D.num(x[1]) : x[1]) + "</b><span>" + esc(x[0]) + "</span></span>";
      }).join("");
    });
    $("#goText").innerHTML =
      '<span class="go__m"><i class="ad-icon ad-icon--path ad-icon--sm"></i><b>' + c.routes + "</b> " + D.plural(c.routes, "маршрут", "маршрута", "маршрутов") + " · <b>" + c.lineDuties + "</b> " + D.plural(c.lineDuties, "наряд", "наряда", "нарядов") + "</span>" +
      '<span class="go__m"><i class="ad-icon ad-icon--bus ad-icon--sm"></i><b>' + c.vehOk + "</b> " + D.plural(c.vehOk, "автобус", "автобуса", "автобусов") + "</span>" +
      '<span class="go__m"><i class="ad-icon ad-icon--users ad-icon--sm"></i><b>' + c.drvReady + "</b> " + D.plural(c.drvReady, "водитель", "водителя", "водителей") + "</span>";
    document.querySelectorAll("[data-group]").forEach(updateGroupHead);
  }

  // Список выбранной карточки - одно представление на раздел.
  function renderPanel() {
    var el = $("#sections"), wasOpen = el.classList.contains("is-open");
    refresh();
    if (!P.tab) {
      // закрытие: панель уходит вверх и гаснет, потом очищается
      if (!wasOpen) { el.innerHTML = ""; el.className = "list-panel"; return; }
      var a = el.animate(M.reduce ? [{ opacity: 1 }, { opacity: 0 }] : [{ opacity: 1, transform: "none" }, { opacity: 0, transform: "translateY(-6px)" }], { duration: M.FAST, easing: M.EASE_EXIT, fill: "forwards" });
      M.after(a, M.FAST, function () { if (!P.tab) { el.innerHTML = ""; el.className = "list-panel"; } a.cancel(); });
      return;
    }
    el.className = "list-panel is-open";
    el.innerHTML = '<div class="lp__body"></div>';
    if (wasOpen) M.swap(el.firstChild); else M.enter(el, "down");
    var body = el.querySelector(".lp__body");
    if (P.tab === "routes") routesBody(body); else if (P.tab === "veh") vehBody(body); else drvBody(body);
    refresh();
  }

  function routeInfo(r) {
    var g = D.geoFor(r.park_id, r.number);
    var dutiesToday = P.base.duties.filter(function (d) { return d.route_id === r.id; });
    return { g: g, duties: dutiesToday.length, shifts: P.base.shifts.filter(function (s) { return dutiesToday.some(function (d) { return d.id === s.duty_id; }); }).length };
  }
  function chip(r, g) { return '<span class="num" style="background:' + (g ? g.color : "var(--accent-deep)") + '">' + esc(r.number) + "</span>"; }

  // Маршруты: переключатель в каждой строке (клик по строке - тоже), общий переключатель в заголовке:
  // все включены -> выключает все, иначе включает все.
  function routesBody(el) {
    el.innerHTML = '<div class="ad-table-wrap"><table class="ad-table tbl tbl--routes"><thead><tr><th><label class="sw sw--text" title="Все маршруты"><input type="checkbox" data-all-routes aria-label="Все маршруты"><span></span><em>Все</em></label></th>' +
      "<th>Маршрут</th><th>Конечные</th><th>Важность</th><th class=\"ad-num\">Нарядов сегодня</th><th class=\"ad-num\">Смен</th><th>Класс</th></tr></thead><tbody>" +
      P.base.routes.map(function (r) {
        var i = routeInfo(r), on = routeOn(r);
        return '<tr class="' + (on ? "" : "is-off") + '" data-row="' + r.id + '"><td><label class="sw"><input type="checkbox" data-route="' + r.id + '"' + (on ? " checked" : "") + ' aria-label="Маршрут ' + esc(r.number) + ' в работе"><span></span></label></td>' +
          "<td>" + chip(r, i.g) + "</td><td>" + esc(i.g ? i.g.name : "") + "</td><td>" + D.PRIORITY[r.priority] + '</td><td class="ad-num">' + i.duties + '</td><td class="ad-num">' + i.shifts + "</td><td>" +
          r.allowed_classes.map(function (c) { return D.CLASSES[c]; }).join(", ") + "</td></tr>";
      }).join("") + "</tbody></table></div>";
    var all = el.querySelector("[data-all-routes]");
    function syncAll() {
      var ids = Object.keys(P.st.route), on = ids.filter(function (k) { return P.st.route[k]; }).length;
      all.checked = on === ids.length; all.indeterminate = on > 0 && on < ids.length;
      all.parentNode.querySelector("em").textContent = on === ids.length ? "Все" : on ? on + " из " + ids.length : "Нет";
    }
    function setRow(cb) {
      var id = cb.getAttribute("data-route");
      mark("route", [id], cb.checked);
      cb.closest("tr").classList.toggle("is-off", !cb.checked);
      syncAll(); refresh();
    }
    el.querySelectorAll("[data-route]").forEach(function (cb) { cb.addEventListener("change", function () { setRow(cb); }); });
    // клик по строке = переключить маршрут; выделять строку незачем
    el.querySelectorAll("tr[data-row]").forEach(function (tr) {
      tr.addEventListener("click", function (e) {
        e.stopPropagation();  // иначе общий обработчик таблиц дизайн-системы «выделяет» строку
        if (e.target.closest("label, input")) return;
        var cb = tr.querySelector("[data-route]"); cb.checked = !cb.checked; setRow(cb);
      });
    });
    all.addEventListener("change", function () {
      var ids = Object.keys(P.st.route), allOn = ids.every(function (k) { return P.st.route[k]; });
      var target = !allOn, change = ids.filter(function (k) { return P.st.route[k] !== target; });
      if (change.length) mark("route", change, target);
      el.querySelectorAll("[data-route]").forEach(function (cb) { cb.checked = target; cb.closest("tr").classList.toggle("is-off", !target); });
      syncAll(); refresh();
    });
    syncAll();
  }

  // Группы: по маршруту закрепления; автобусы и водители без закрепления - отдельной группой.
  // Группа без закрепления - своя у каждого парка: none-P07.
  function groupsOf(items, groupOf) {
    var by = {}, order = [];
    P.ids.forEach(function (id) {
      P.base.routes.forEach(function (r) { if (r.park_id === id) order.push(r.id); });
      order.push("none-" + id);
    });
    items.forEach(function (x) { var k = groupOf(x); (by[k] = by[k] || []).push(x); });
    return order.filter(function (k) { return by[k]; }).map(function (k) { return { id: k, items: by[k] }; });
  }
  function groupTitle(id) {
    var multi = P.ids.length > 1;
    if (id.indexOf("none-") === 0) {
      var pk = id.slice(5);
      return '<span class="num num--none">Р</span><span>Резерв' + (multi ? ' <em class="pk-tag">' + esc(shortName(parkById(pk))) + "</em>" : "") + "</span>";
    }
    var r = P.base.routes.filter(function (x) { return x.id === id; })[0], g = D.geoFor(r.park_id, r.number);
    return chip(r, g) + "<span>Маршрут " + esc(r.number) + (multi ? ' <em class="pk-tag">' + esc(shortName(parkById(r.park_id))) + "</em>" : "") +
      (routeOn(r) ? "" : ' <em class="off">сегодня не работает</em>') + "</span>";
  }

  // Поиск и фильтр. Без фильтра - группы по маршрутам (свёрнуты); с фильтром или поиском -
  // один плоский список найденного, чтобы сразу было видно, что показано и сколько.
  function toolbar(kind, filters) {
    return '<div class="tools"><input class="ad-input tools__q" type="search" placeholder="' + (kind === "veh" ? "Бортовой или госномер" : "Табельный номер или фамилия") +
      '" aria-label="Поиск" data-q="' + kind + '" value="' + esc(P.q[kind]) + '">' +
      '<div class="seg" role="radiogroup">' + filters.map(function (f) {
        return '<button type="button" role="radio" aria-checked="' + (P.filter[kind] === f[0]) + '" data-f="' + f[0] + '" class="' + (P.filter[kind] === f[0] ? "is-on" : "") + '">' + f[1] + ' <span class="seg__n" data-fn="' + f[0] + '"></span></button>';
      }).join("") + '</div></div><div class="found" data-found></div><div class="groups"></div>';
  }
  function bindToolbar(el, kind, rerender) {
    var q = el.querySelector("[data-q]"), t = null;
    q.addEventListener("input", function () { clearTimeout(t); t = setTimeout(function () { P.q[kind] = q.value.trim().toLowerCase(); rerender(); }, 150); });
    el.querySelectorAll("[data-f]").forEach(function (b) {
      b.addEventListener("click", function () {
        P.filter[kind] = b.getAttribute("data-f");
        el.querySelectorAll("[data-f]").forEach(function (x) { var on = x === b; x.classList.toggle("is-on", on); x.setAttribute("aria-checked", String(on)); });
        rerender();
      });
    });
  }
  // Переключатель состояния в строке: «Исправен / Неисправен», «Выходит / Не выходит»
  function toggle(kind, id, on, yes, no) {
    return '<label class="sw sw--text"><input type="checkbox" data-' + kind + '="' + id + '"' + (on ? " checked" : "") + '><span></span><em>' + (on ? yes : no) + "</em></label>";
  }
  function bindToggles(box, kind, yes, no, set) {
    box.querySelectorAll("[data-" + kind + "]").forEach(function (cb) {
      cb.addEventListener("change", function () {
        set(cb.getAttribute("data-" + kind), cb.checked);
        cb.parentNode.querySelector("em").textContent = cb.checked ? yes : no;
        cb.closest("tr").classList.toggle("is-off", !cb.checked);
        // строку под курсором не убираем из списка, а числа у фильтров обновляем сразу
        var body = box.closest(".lp__body"); if (body) listCounts(body, kind, kind === "veh" ? VEH : DRV);
        refresh();
      });
    });
  }
  function routeCell(rid) {
    if (!rid) return '<span class="muted">-</span>';
    var r = P.base.routes.filter(function (x) { return x.id === rid; })[0];
    return r ? chip(r, D.geoFor(r.park_id, r.number)) : "";
  }

  // Общая сборка списка: группы или плоский список, счётчики фильтра
  function listBody(el, kind, cfg) {
    if (!el.querySelector(".groups")) { el.innerHTML = toolbar(kind, cfg.filters); bindToolbar(el, kind, function () { listBody(el, kind, cfg); }); }
    var all = cfg.items(), q = P.q[kind], f = P.filter[kind];
    var byText = all.filter(function (x) { return !q || cfg.text(x).indexOf(q) >= 0; });
    cfg.filters.forEach(function (fl) {
      var n = byText.filter(function (x) { return cfg.pass(x, fl[0]); }).length;
      el.querySelector('[data-fn="' + fl[0] + '"]').textContent = D.num(n);
    });
    var shown = byText.filter(function (x) { return cfg.pass(x, f); });
    var box = el.querySelector(".groups"), found = el.querySelector("[data-found]");
    if (q || f !== "all") {
      found.textContent = "Найдено: " + D.num(shown.length);
      found.hidden = false;
      box.innerHTML = shown.length ? '<div class="grp grp--flat"><div class="grp__rows"></div></div>' : '<p class="muted empty">Ничего не найдено</p>';
      if (shown.length) cfg.rows(box.querySelector(".grp__rows"), shown, true);
    } else {
      found.hidden = true;
      var groups = groupsOf(all, cfg.group);
      box.innerHTML = groups.map(function (g) {
        return '<details class="grp" data-pin data-group="' + kind + ":" + g.id + '"' + (P.open[kind + ":" + g.id] ? " open" : "") + '><summary><span class="grp__t">' + groupTitle(g.id) +
          '</span><span class="grp__n"></span>' +
          '<i class="ad-icon ad-icon--caret-down ad-icon--sm grp__car"></i></summary><div class="grp__rows"></div></details>';
      }).join("");
      box.querySelectorAll("details.grp").forEach(function (d) {
        var gid = d.getAttribute("data-group").split(":")[1], items = groups.filter(function (g) { return g.id === gid; })[0].items;
        var fill = function () { var rows = d.querySelector(".grp__rows"); if (!rows.childElementCount) cfg.rows(rows, items, false); };
        if (d.open) fill();
        d.addEventListener("details:open", fill);
        d.addEventListener("toggle", function () { P.open[kind + ":" + gid] = d.open; if (d.open) fill(); });
      });
    }
    M.swap(box);
    refresh();
  }
  function listCounts(el, kind, cfg) {
    var q = P.q[kind], byText = cfg.items().filter(function (x) { return !q || cfg.text(x).indexOf(q) >= 0; });
    cfg.filters.forEach(function (fl) { var c = el.querySelector('[data-fn="' + fl[0] + '"]'); if (c) c.textContent = D.num(byText.filter(function (x) { return cfg.pass(x, fl[0]); }).length); });
  }

  // Таблица строк с сортировкой: клик по заголовку - сортировать по колонке, повторный - в обратную сторону.
  // Сортировка одна на раздел и действует и в группах, и в плоском списке.
  var collator = new Intl.Collator("ru", { numeric: true, sensitivity: "base" });
  function cmp(a, b) { return typeof a === "number" && typeof b === "number" ? a - b : collator.compare(String(a), String(b)); }
  function routeSortKey(rid) {
    var r = rid && P.base.routes.filter(function (x) { return x.id === rid; })[0];
    return r ? r.number : "яяя";  // резерв - в конце
  }
  function parkNum(id) { return id.replace(/^P0?/, "№"); }
  function sortItems(kind, cols, items) {
    var st = P.sort[kind], col = cols.filter(function (c) { return c.key === st.key; })[0] || cols[0];
    var tie = cols.filter(function (c) { return c.key === (kind === "veh" ? "board" : "name"); })[0];
    return items.slice().sort(function (a, b) { return st.dir * cmp(col.sort(a), col.sort(b)) || cmp(tie.sort(a), tie.sort(b)); });
  }
  function renderTable(box, kind, cols, items, flat) {
    var use = cols.filter(function (c) { return flat || !c.flatOnly; });
    var sorted = sortItems(kind, cols, items), st = P.sort[kind];
    var MAX = 300, more = sorted.length - MAX;
    box.innerHTML = '<table class="rows"><thead><tr>' + use.map(function (c) {
      var on = st.key === c.key;
      return c.sort ? '<th aria-sort="' + (on ? (st.dir > 0 ? "ascending" : "descending") : "none") + '"><button type="button" class="th-sort' + (on ? " is-on" : "") + '" data-sort="' + c.key + '">' + c.title +
        '<i class="th-sort__ic" aria-hidden="true"></i></button></th>' : "<th>" + c.title + "</th>";
    }).join("") + "</tr></thead><tbody>" + sorted.slice(0, MAX).map(function (x) {
      return '<tr class="' + (cols.on(x) ? "" : "is-off") + '">' + use.map(function (c) { return "<td>" + c.cell(x) + "</td>"; }).join("") + "</tr>";
    }).join("") + "</tbody></table>" + (more > 0 ? '<p class="muted more">Показаны первые ' + MAX + " из " + D.num(sorted.length) + " - уточните поиск</p>" : "");
    box.querySelectorAll("[data-sort]").forEach(function (b) {
      b.addEventListener("click", function () {
        var k = b.getAttribute("data-sort");
        P.sort[kind] = { key: k, dir: P.sort[kind].key === k ? -P.sort[kind].dir : 1 };
        var body = box.closest(".lp__body"); if (body) listBody(body, kind, kind === "veh" ? VEH : DRV);
      });
    });
    cols.bind(box);
  }

  // --- автобусы ---
  function vehCols() {
    var c = [
      { key: "park", title: "Парк", cell: function (v) { return parkNum(v.park_id); }, sort: function (v) { return v.park_id; } },
      { key: "board", title: "Борт", cell: function (v) { return "<b>" + esc(v.board_number) + "</b>"; }, sort: function (v) { return +v.board_number || v.board_number; } },
      { key: "route", title: "Маршрут", flatOnly: true, cell: function (v) { return routeCell(v.home_route_id); }, sort: function (v) { return routeSortKey(v.home_route_id); } },
      { key: "plate", title: "Госномер", cell: function (v) { return esc(v.plate); }, sort: function (v) { return v.plate; } },
      { key: "model", title: "Модель", cell: function (v) { return '<span class="muted">' + esc(v.model) + "</span>"; }, sort: function (v) { return v.model; } },
      { key: "class", title: "Класс", cell: function (v) { return D.CLASSES[v.class]; }, sort: function (v) { return D.CLASSES[v.class]; } },
      { key: "fuel", title: "Топливо", cell: function (v) { return v.fuel === "gas" ? "газ" : v.fuel === "electric" ? "электро" : "дизель"; }, sort: function (v) { return v.fuel; } },
      { key: "state", title: "Состояние", cell: function (v) { return toggle("veh", v.id, vehOk(v), "Исправен", "Неисправен"); }, sort: function (v) { return vehOk(v) ? 0 : 1; } }
    ];
    c.on = vehOk;
    c.bind = function (box) { bindToggles(box, "veh", "Исправен", "Неисправен", function (id, on) { mark("veh", [id], on); }); };
    return c;
  }
  var VEH = {
    filters: [["all", "Все"], ["ok", "Исправные"], ["bad", "Неисправные"]],
    items: function () { return P.base.vehicles; },
    text: function (v) { return (v.board_number + " " + v.plate.replace(/\s/g, "")).toLowerCase(); },
    pass: function (v, f) { return f === "all" || (f === "ok") === vehOk(v); },
    group: function (v) { return vehGroup(v); },
    rows: function (box, items, flat) { renderTable(box, "veh", vehCols(), items, flat); }
  };
  function vehBody(el) { listBody(el, "veh", VEH); }

  // --- водители ---
  function vehGroup(v) { return v.home_route_id || "none-" + v.park_id; }
  function drvGroup(d) { return drvRoute(d) || "none-" + d.park_id; }
  function drvRoute(d) { var v = d.home_vehicle_id && P.vehById[d.home_vehicle_id]; return v ? v.home_route_id : null; }
  function drvCols() {
    var c = [
      { key: "park", title: "Парк", cell: function (d) { return parkNum(d.park_id); }, sort: function (d) { return d.park_id; } },
      { key: "tab", title: "Таб. №", cell: function (d) { return esc(d.tab_number); }, sort: function (d) { return d.tab_number; } },
      { key: "name", title: "Водитель", cell: function (d) { return "<b>" + esc(d.full_name) + "</b>"; }, sort: function (d) { return d.full_name; } },
      { key: "route", title: "Маршрут", flatOnly: true, cell: function (d) { return routeCell(drvRoute(d)); }, sort: function (d) { return routeSortKey(drvRoute(d)); } },
      { key: "class", title: "Допуск", cell: function (d) { return d.classes.filter(function (c) { return c !== "medium"; }).map(function (c) { return D.CLASSES[c]; }).join(", "); },
        sort: function (d) { return d.classes.length; } },
      { key: "bus", title: "Автобус", cell: function (d) { var v = d.home_vehicle_id && P.vehById[d.home_vehicle_id]; return v ? esc(v.board_number) : '<span class="muted">-</span>'; },
        sort: function (d) { var v = d.home_vehicle_id && P.vehById[d.home_vehicle_id]; return v ? +v.board_number : 99999; } },
      { key: "state", title: "Сегодня", cell: function (d) { return toggle("drv", d.id, drvGoes(d), "Выходит", "Не выходит"); }, sort: function (d) { return drvGoes(d) ? 0 : 1; } }
    ];
    c.on = drvGoes;
    c.bind = function (box) { bindToggles(box, "drv", "Выходит", "Не выходит", function (id, on) { mark("drv", [id], on); }); };
    return c;
  }
  var DRV = {
    filters: [["all", "Все"], ["on", "Выходят"], ["off", "Не выходят"]],
    items: function () { return P.base.drivers; },
    text: function (d) { return (d.tab_number + " " + d.full_name).toLowerCase(); },
    pass: function (d, f) { return f === "all" || (f === "on") === drvGoes(d); },
    group: function (d) { return drvGroup(d); },
    rows: function (box, items, flat) { renderTable(box, "drv", drvCols(), items, flat); }
  };
  function drvBody(el) {
    P.vehById = {}; P.base.vehicles.forEach(function (v) { P.vehById[v.id] = v; });
    listBody(el, "drv", DRV);
  }

  function updateGroupHead(det) {
    var parts = det.getAttribute("data-group").split(":"), kind = parts[0], gid = parts[1], n = det.querySelector(".grp__n");
    if (kind === "veh") {
      var vs = P.base.vehicles.filter(function (v) { return vehGroup(v) === gid; });
      var ok = vs.filter(vehOk).length;
      n.innerHTML = '<span class="' + (ok < vs.length ? "warn" : "") + '">исправны ' + ok + " из " + vs.length + "</span>";
    } else {
      if (!P.vehById) return;
      var ds = P.base.drivers.filter(function (d) { return drvGroup(d) === gid; });
      n.textContent = "выходят " + ds.filter(drvGoes).length + " из " + ds.length;
    }
  }

  // ---------- день для движка ----------
  function buildDay() {
    var d = JSON.parse(JSON.stringify(P.base)), st = P.st;
    var gone = {};
    d.duties = d.duties.filter(function (x) { if (x.type === "line" && !st.route[x.route_id]) { gone[x.id] = 1; return false; } return true; });
    d.shifts = d.shifts.filter(function (s) { return !gone[s.duty_id]; });
    // выпуск парка = наряды, которые сегодня есть; иначе проверка данных ругается на расхождение с планом
    var kind = d.meta.day_type;
    d.parks.forEach(function (p) { p["release_" + kind] = d.duties.filter(function (x) { return x.park_id === p.id; }).length; });
    d.routes.forEach(function (r) { if (!st.route[r.id]) r["duties_" + kind] = 0; });
    d.vehicles.forEach(function (v) {
      if (st.veh[v.id]) { v.condition = "ok"; v.repair_days_left = null; }
      else if (v.condition === "ok") { v.condition = "repair"; v.repair_days_left = 1; }
    });
    d.drivers.forEach(function (x) {
      if (st.drv[x.id]) { x.schedule = "work"; x.medical = "passed"; }
      else if (x.schedule === "work" && x.medical === "passed") x.medical = "failed";  // в графике, но на линию не выходит
    });
    d.meta.source = "реестр " + P.ids.join(", ") + " на " + P.date + " с отметками диспетчера";
    return d;
  }

  $("#btnCalc").addEventListener("click", function () {
    var c = counts();
    if (!c.lineDuties) return toast("Ни один маршрут не работает - считать нечего", true);
    $("#prepBusy").hidden = false; $("#prepBusyText").textContent = "Расчёт расстановки…";
    save().then(function () { return window.App.run(buildDay(), function () { P.calculated = true; go("result"); }); }).then(function () {
      $("#prepBusy").hidden = true;
    }, function (e) {
      $("#prepBusy").hidden = true;
      toast(e.message + (e.details && e.details.length ? ": " + e.details.slice(0, 2).join("; ") : ""), true);
    });
  });

  // ---------- старт ----------
  // Заставка: короткая линия загрузки, пока приходит список парков; не меньше 500 мс, чтобы не мигала.
  var started = Date.now();
  function hideSplash() {
    setTimeout(function () {
      var sp = $("#splash"); sp.classList.add("is-done"); setTimeout(function () { sp.remove(); }, 400);
      // вход: группы и карточки появляются каскадом, когда заставка уходит
      if (current === "parks") { M.stagger($("#parks").querySelectorAll(".kind")); M.stagger($("#parks").querySelectorAll(".pc")); }
      if (current === "welcome") showWelcome();
    }, Math.max(0, 500 - (Date.now() - started)));
  }
  Promise.all([D.loadParks(), D.loadGeo()]).then(function (r) { var list = r[0];
    // доступные парки - первыми
    P.parks = list.slice().sort(function (a, b) { return (b.available ? 1 : 0) - (a.available ? 1 : 0); });
    P.sel = loadSel(); renderParks();
    firstRoute(); hideSplash();
  }, function (e) { $("#parks").innerHTML = '<p class="err">' + esc(e.message) + "</p>"; hideSplash(); });
})();
