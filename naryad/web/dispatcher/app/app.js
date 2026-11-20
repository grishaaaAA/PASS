/* Экран расчёта: сводка, проблемы, карта, наряды маршрута, события. День приходит с экрана подготовки (prep.js). */
(function () {
  "use strict";
  var D = window.Disp, $ = D.$, esc = D.esc, M = window.Motion, Ops = window.Operations;
  var S = { info: null, day: null, plan: null, m: null, map: null, tab: "routes", route: null, routeDirection: null, duty: null, park: null, probsOpen: false };
  var selectedParks = new Set(), openColumns = new Set(), parkCatalog = [];
  var parkPicker = $("#parkPicker"), parkOptions = $("#parkOptions"), parkList = $("#resParks");
  var parkToggle = $("#parkPickerToggle"), parkPanel = $("#parkPickerPanel");
  var resultLayout = $("#scrResult"), side = $("#side"), sideResize = $("#sideResize");
  var dragOffset = 0;
  function maxSideWidth() {
    var width = resultLayout.clientWidth;
    return Math.floor(Math.min(560, resultLayout.classList.contains("is-split") ? width / 2 - 120 : width * 0.6));
  }
  function setSideWidth(width) {
    width = Math.min(maxSideWidth(), Math.max(320, width));
    resultLayout.style.setProperty("--side-width", Math.round(width) + "px");
  }
  sideResize.addEventListener("pointerdown", function (e) {
    if (!e.isPrimary || e.button !== 0) return;
    dragOffset = side.getBoundingClientRect().right - e.clientX;
    sideResize.setPointerCapture(e.pointerId);
    resultLayout.classList.add("is-resizing");
  });
  sideResize.addEventListener("pointermove", function (e) {
    if (sideResize.hasPointerCapture(e.pointerId)) {
      setSideWidth(e.clientX + dragOffset - resultLayout.getBoundingClientRect().left);
    }
  });
  sideResize.addEventListener("lostpointercapture", function () { resultLayout.classList.remove("is-resizing"); });
  sideResize.addEventListener("keydown", function (e) {
    var width = side.offsetWidth;
    var next = { ArrowLeft: width - 20, ArrowRight: width + 20, Home: 320, End: maxSideWidth() }[e.key];
    if (next === undefined) return;
    e.preventDefault();
    setSideWidth(next);
  });
  if (window.ResizeObserver) {
    new ResizeObserver(function () {
      if (!sideResize.offsetWidth) return;
      sideResize.setAttribute("aria-valuemin", "320");
      sideResize.setAttribute("aria-valuemax", String(maxSideWidth()));
      sideResize.setAttribute("aria-valuenow", String(side.offsetWidth));
      sideResize.setAttribute("aria-valuetext", side.offsetWidth + " пикселей");
    }).observe(side);
    new ResizeObserver(function () {
      if ($("#map").clientWidth) window.App.resize();
    }).observe($("#map"));
  }
  // Фото парков для карточки (откуда взяты - app/img/CREDITS.md)
  var PARK_PHOTOS = {
    P07: { src: "img/park-P07.jpg" }
  };

  // Смена левой панели: список ↔ подробности - сдвигом сбоку
  function show(id) {
    ["paneResult", "paneDetail", "paneRoute", "paneRouteBuses"].forEach(function (p) { if (p !== id) $("#" + p).hidden = true; });
    M.show($("#" + id), "side");
  }
  function busy(text) { if (text) { $("#busyText").textContent = text; M.show($("#busy"), "fade"); } else M.hide($("#busy"), "fade"); }

  // ---------- Расчёт дня, собранного на экране подготовки ----------
  // Отдаёт обещание: день проверен, план построен, экран расчёта открыт.
  function run(day, onShown) {
    return D.submitDay(day).then(function (r) {
      return D.calculate(r.info.day_id).then(function (result) { showResult(day, r.info, result, false, onShown); });
    });
  }
  function openSaved(id, onShown) {
    var base = "/api/days/" + encodeURIComponent(id);
    return Promise.all([D.api("GET", base), D.api("GET", base + "/plan"), D.api("GET", base + "/state"), D.intervals(id), D.loadGeo()])
      .then(function (r) {
        if (onShown() === false) return;
        showResult(r[0], { day_id: id }, { plan: r[1], state: r[2], intervals: r[3] }, true);
      });
  }
  function showResult(day, info, r, editing, onShown) {
    S.info = info; S.day = day; S.route = null; S.routeDirection = null; S.duty = null; S.park = null; S.editing = editing;
    S.plan = r.plan;
    S.run = r.plan.run || null;
    onShown && onShown();  // экран расчёта видим - теперь карта узнает свой размер
    $("#drawer").hidden = true; $("#scrResult").classList.remove("is-split");
    S.m = D.build(S.day, S.plan, r.state, r.intervals);
    searchQuery.value = "";
    searchClear.hidden = true;
    searchCategory = null;
    closeSearch();
    selectedParks = new Set(S.m.parkList.map(function (park) { return park.id; }));
    openColumns.clear();
    var handlers = { onPark: openPark, onRoute: routeCard, parkTip: parkTip };
    if (window.L) {
      if (!S.map) S.map = D.parkMap($("#map"), S.m, handlers);
      else { S.map.resize(); S.map.closePopup(); S.map.redraw(S.m, handlers); S.map.setParks(selectedParks, true); }
      var mapTools = $("#map .mc__tools");
      if (!mapTools.contains(parkPicker)) mapTools.prepend(parkPicker);
    }
    renderParkPicker(true);
    toggleParkPanel(false);
    $("#mapFallback").hidden = !!S.map;
    setTab("routes");
    S.probsOpen = false;  // окно проблем по умолчанию свёрнуто - открывается по заголовку или строке состояния
    renderSummary(); renderProbs(); show("paneResult");
    $("#resTitle").focus({ preventScroll: true });
  }
  window.App = { run: run, openSaved: openSaved, resize: function () { S.map && S.map.resize(); },
    setParkCatalog: function (parks) { parkCatalog = parks; if (S.m) renderParkPicker(true); } };

  // Состояние после события: интервалы пересчитывает движок, поэтому сначала спросить их
  function apply(state) {
    return D.intervals(S.info.day_id).then(function (iv) { draw(state, iv); }, function () { draw(state, null); });
  }
  function draw(state, iv) {
    S.m = D.build(S.day, S.plan, state, iv);
    if (S.map) {
      S.map.redraw(S.m);
      if (S.route && S.route.indexOf("res:") !== 0) S.map.selectRoute(S.route, 0, true, S.routeDirection);
    }
    renderSummary(); renderList(); renderProbs();
    if (S.route) openRoute(S.route, true);
    // Открытый наряд - заново по новому состоянию: иначе в панели остаётся то, что стояло до события или правки
    var duty = S.route && S.duty && S.m.duties[S.duty];
    var row = duty && $('#drGantt .gantt__row[data-duty="' + CSS.escape(duty.id) + '"]');
    if (row) { row.classList.add("is-selected"); dutyClick(duty, S.dutyT); }
  }

  function toggleParkPanel(open) {
    if ((parkToggle.getAttribute("aria-expanded") === "true") === open) return;
    parkToggle.setAttribute("aria-expanded", String(open));
    parkPanel.inert = !open;
    if (open) M.show(parkPanel, "down", { duration: 210 });
    else M.hide(parkPanel, "scale");
  }
  function renderParkPicker(rebuildCatalog) {
    var parks = S.m.parkList.slice().sort(function (a, b) { return collate(a.name, b.name); });
    parkList.innerHTML = parks.filter(function (park) { return selectedParks.has(park.id); }).map(function (park) {
      var id = esc(park.id), name = esc(park.name);
      var label = '<b>' + name + '</b>';
      var locate = S.map && park.coords ? '<button type="button" class="park-picker__park" data-locate="' + id +
        '" aria-label="Показать на карте: ' + name + '" title="Показать на карте"><i class="ad-icon ad-icon--map-pin" aria-hidden="true"></i><span>' + label + '</span></button>'
        : '<div class="park-picker__park"><span>' + label + '</span></div>';
      return '<li>' + locate + '</li>';
    }).join("");
    if (rebuildCatalog) {
      var inCalculation = new Set(parks.map(function (park) { return park.id; }));
      var options = new Map(parkCatalog.map(function (park) { return [park.id, park]; }));
      parks.forEach(function (park) { options.set(park.id, Object.assign({}, options.get(park.id), park)); });
      var catalog = Array.from(options.values());
      parkOptions.innerHTML = '<h3>Все парки</h3>' + Ops.parkKinds.map(function (kind) {
        var group = catalog.filter(function (park) { return (park.kind || "bus") === kind.id; });
        var count = group.length ? group.length + " " + D.plural(group.length, "парк", "парка", "парков") : "";
        var cards = group.map(function (park) {
          return Ops.parkCard(park, { compact: true, picked: selectedParks.has(park.id),
            disabledReason: inCalculation.has(park.id) ? "" : park.available ? "Вне расчёта" : "Реестр не подключён" });
        }).join("");
        return '<details class="kind park-picker__group"><summary class="kind__head"><span class="kind__ic">' + kind.icon + '</span><span class="kind__t">' + kind.name +
          '</span><span class="kind__n">' + count + '</span><i class="ad-icon ad-icon--caret-down ad-icon--sm kind__car" aria-hidden="true"></i></summary>' +
          (cards ? '<div class="park-picker__cards">' + cards + '</div>' : '<p class="caption muted">Парки не подключены</p>') + '</details>';
      }).join("");
    } else {
      parkOptions.querySelectorAll("[data-pick-park]").forEach(function (card) {
        var picked = selectedParks.has(card.dataset.pickPark);
        card.classList.toggle("is-picked", picked);
        card.setAttribute("aria-checked", String(picked));
      });
    }
    var hasRoutes = S.m.routeList.some(function (r) {
      return selectedParks.has(r.park) && r.geo && r.geo.directions.some(function (d) { return d.line.length > 1; });
    });
    var status = $("#parkPickerStatus");
    status.textContent = !selectedParks.size ? "Парки не выбраны" : S.map && !hasRoutes ? "Нет схем маршрутов" : "";
    status.hidden = !status.textContent;
  }
  function setSelectedParks(ids, fit) {
    selectedParks = new Set(ids);
    if (S.map) {
      S.map.closePopup();
      S.map.setParks(selectedParks, fit);
    }
    renderParkPicker();
    renderList();
  }
  function revealPark(id) {
    if (!selectedParks.has(id)) setSelectedParks([...selectedParks, id], false);
  }
  parkToggle.addEventListener("click", function () {
    var open = parkToggle.getAttribute("aria-expanded") !== "true";
    if (S.map && open) S.map.map.fire("closecontrolpanels");
    toggleParkPanel(open);
  });
  parkPicker.addEventListener("click", function (e) {
    var locate = e.target.closest("[data-locate]");
    if (locate) {
      var id = locate.dataset.locate;
      toggleParkPanel(false);
      parkToggle.focus({ preventScroll: true });
      if ($("#drawer").hidden) openPark(id);
      else closePanel(function () { openPark(id); });
      return;
    }
    var card = e.target.closest("[data-pick-park]");
    if (!card) return;
    var parkId = card.dataset.pickPark;
    var next = new Set(selectedParks);
    if (next.has(parkId)) next.delete(parkId);
    else next.add(parkId);
    setSelectedParks(next, $("#drawer").hidden);
    M.enter(parkList, "fade", 0, 170);
    if (!$("#drawer").hidden) closePanel();
  });
  parkPicker.addEventListener("keydown", function (e) {
    if (e.key !== "Escape" || parkPanel.hidden) return;
    e.preventDefault();
    e.stopPropagation();
    toggleParkPanel(false);
    parkToggle.focus();
  });
  document.addEventListener("click", function (e) {
    if (!e.composedPath().includes(parkPicker)) toggleParkPanel(false);
  });

  // Поиск по записям текущего расчёта.
  var searchForm = $("#resultSearch"), searchQuery = $("#resultQuery"), searchMatches = $("#resultMatches");
  var searchClear = $("#resultClear"), searchEntries = $("#resultEntries");
  var searchCategory = null;
  var searchCategories = Array.from(searchMatches.querySelectorAll("[data-category]"));
  function searchText(value) {
    return String(value || "").toLocaleLowerCase("ru").replace(/ё/g, "е").replace(/[№#]/g, " ").trim();
  }
  function closeSearch() {
    searchMatches.hidden = true;
    searchForm.classList.remove("is-open");
  }
  function renderSearch() {
    if (!S.m) return;
    searchClear.hidden = !searchQuery.value;
    var query = searchText(searchQuery.value);
    searchCategories.forEach(function (button) {
      button.setAttribute("aria-pressed", String(button.dataset.category === searchCategory));
    });
    searchForm.classList.add("is-open");
    searchMatches.hidden = false;
    if (!query && !searchCategory) { searchEntries.innerHTML = ""; return; }
    var terms = query ? query.split(/\s+/) : [];
    function matching(items, title, keywords) {
      return items.filter(function (item) {
        var text = searchText(keywords(item));
        return terms.every(function (term) { return text.includes(term); });
      }).sort(function (a, b) {
        var exactA = searchText(title(a)) === query, exactB = searchText(title(b)) === query;
        return Number(exactB) - Number(exactA) || collate(title(a), title(b));
      }).slice(0, 6);
    }
    var m = S.m;
    var routes = !searchCategory || searchCategory === "route" ? matching(m.routeList, function (r) { return r.number; },
      function (r) { return "маршрут " + r.number + " " + (r.geo ? r.geo.name : ""); }) : [];
    var drivers = !searchCategory || searchCategory === "driver" ? matching(Object.values(m.drivers), function (d) { return d.full_name; },
      function (d) { return d.full_name + " " + d.tab_number; }) : [];
    var buses = !searchCategory || searchCategory === "veh" ? matching(Object.values(m.vehicles), function (v) { return v.board_number; },
      function (v) { return "автобус борт " + v.board_number; }) : [];
    function result(kind, id, title, detail) {
      return '<button type="button" class="result-search__match" data-kind="' + kind + '" data-id="' + esc(id) + '">' +
        '<span>' + esc(title) + '</span><small>' + esc(detail) + '</small></button>';
    }
    var routeResults = routes.length ? '<h2>Маршруты</h2>' + routes.map(function (r) {
      return result("route", r.id, "Маршрут " + r.number, D.parkShort(m, r.park) + (r.geo && r.geo.name ? " · " + r.geo.name : ""));
    }).join("") : "";
    var driverResults = drivers.length ? '<h2>Водители</h2>' + drivers.map(function (d) {
      return result("driver", d.id, d.full_name, D.parkShort(m, d.park_id) + (d.tab_number ? " · Таб. № " + d.tab_number : ""));
    }).join("") : "";
    var busResults = buses.length ? '<h2>Автобусы</h2>' + buses.map(function (v) {
      return result("veh", v.id, "Борт " + v.board_number, D.parkShort(m, v.park_id));
    }).join("") : "";
    searchEntries.innerHTML = (routeResults + driverResults + busResults) || '<p role="status">Ничего не найдено</p>';
  }
  function chooseSearch(button) {
    if (!button) return;
    var id = button.dataset.id;
    var m = S.m, kind = button.dataset.kind;
    var item = kind === "route" ? m.routes[id] : kind === "veh" ? m.vehicles[id] : m.drivers[id];
    if (!item) return;
    searchQuery.focus({ preventScroll: true });
    closeSearch();
    Ops.openRecord({ kind: kind === "driver" ? "drv" : kind, id: id, park: item.park || item.park_id,
      name: item.number || item.board_number || item.full_name }, searchQuery);
  }
  searchClear.addEventListener("click", function () {
    searchQuery.value = "";
    searchCategory = null;
    renderSearch();
    searchQuery.focus({ preventScroll: true });
  });
  searchQuery.addEventListener("input", renderSearch);
  searchQuery.addEventListener("focus", function () { if (searchMatches.hidden) renderSearch(); });
  searchForm.addEventListener("submit", function (e) {
    e.preventDefault();
    if (searchMatches.hidden) { searchQuery.focus(); if (searchMatches.hidden) renderSearch(); }
    else chooseSearch(searchEntries.querySelector("button"));
  });
  searchMatches.addEventListener("click", function (e) {
    var category = e.target.closest("[data-category]");
    if (category) {
      searchCategory = searchCategory === category.dataset.category ? null : category.dataset.category;
      renderSearch();
    } else chooseSearch(e.target.closest("[data-kind]"));
  });
  searchForm.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !searchMatches.hidden) {
      e.preventDefault();
      e.stopPropagation();
      searchQuery.focus({ preventScroll: true });
      closeSearch();
    } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (searchMatches.hidden) renderSearch();
      var buttons = Array.from(searchMatches.querySelectorAll("button"));
      if (searchMatches.hidden || !buttons.length) return;
      e.preventDefault();
      var index = buttons.indexOf(document.activeElement);
      var next = index < 0 ? (e.key === "ArrowDown" ? 0 : buttons.length - 1) : (index + (e.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length;
      buttons[next].focus();
    }
  });
  searchQuery.addEventListener("click", function () { if (searchMatches.hidden) renderSearch(); });
  document.addEventListener("click", function (e) { if (!searchForm.contains(e.target)) closeSearch(); });

  // ---------- Левое меню: парк и состояние, показатели, списки ----------
  function problems() {
    var m = S.m;
    return {
      line: m.unfilled.filter(function (u) { return u.kind === "duty" && u.type === "line"; }),
      shifts: m.unfilled.filter(function (u) { return u.kind === "shift" && u.type === "line"; }),
      resShifts: m.unfilled.filter(function (u) { return u.kind === "shift" && u.type !== "line"; }),
      viol: (S.plan.violations || []).concat(S.plan.rest_violations || [], m.state.violations || [])
    };
  }
  var MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
  function renderSummary() {
    var m = S.m, t = m.totals, pr = problems();
    // Заголовок - номер расчёта из журнала расчётов; ниже - парки расчёта с адресами
    var d = (S.run && S.run.day || m.meta.date).split("-");
    var code = D.runCode(S.run);
    $("#resTitle").innerHTML = (S.editing ? "Редактирование расчёта" : "Расчёт") + (code ? " " + esc(code) : "") +
      ' <small>' + (+d[2]) + " " + MONTHS[+d[1] - 1] + "</small>";
    // Всё хорошо - никакого уведомления; есть проблемы - маленький красный значок с числом
    var bad = pr.line.length + pr.shifts.length + pr.viol.length, al = $("#resAlert");
    al.hidden = !bad;
    if (bad) { $("#resAlertN").textContent = bad; al.title = "Проблемы: " + bad + " - открыть список"; al.setAttribute("aria-label", al.title); }
    var k = [
      ["Наряды на линии", t.lineFilled, t.lineTotal, t.lineTotal ? (t.lineFilled < t.lineTotal ? "bad" : "ok") : ""],
      // смены на линии по текущему состоянию дня: после событий число меняется
      ["Смены с водителем", t.shiftFilled, t.shiftTotal, t.shiftTotal ? (t.shiftFilled < t.shiftTotal ? "warn" : "ok") : ""],
      ["Резерв выпущен", t.resFilled, t.resTotal, t.resTotal ? (t.resFilled >= t.resTotal ? "bad" : t.resFilled ? "warn" : "ok") : ""]
    ];
    if (m.parkList.length > 1) k.push(["Переброски", m.transfers.length, null, ""]);
    var statusText = { ok: "В норме", warn: "Требует внимания", bad: "Проблема" };
    $("#kpis").innerHTML = k.map(function ([label, value, total, tone]) {
      return '<div class="kpi' + (tone ? " kpi--" + tone : "") + '"><div class="kpi__top"><span class="kpi__v">' + D.num(value) +
        (total != null ? " <small>из " + D.num(total) + "</small>" : "") + '</span>' +
        (tone === "bad" ? '<i class="kpi__warning ad-icon ad-icon--warning-circle" aria-hidden="true"></i>' : '') +
        '</div><span class="kpi__l">' + label + '</span>' +
        (tone ? '<span class="kpi__status">' + statusText[tone] + '</span>' : '') + '</div>';
    }).join("");
    $("#nLog").textContent = m.state.log.length || "";
  }
  $("#resAlert").addEventListener("click", function () { S.probsOpen = true; renderProbs(true); });

  function setTab(t) {
    var changed = S.tab !== t;
    S.tab = t;
    resultTabs.forEach(function (x) {
      var selected = x.getAttribute("data-tab") === t;
      x.classList.toggle("is-on", selected);
      x.setAttribute("aria-selected", String(selected));
      x.tabIndex = selected ? 0 : -1;
      if (selected) $("#list").setAttribute("aria-labelledby", x.id);
    });
    if (S.m) renderList();
    if (changed) M.swap($("#list"));
  }
  var resultTabs = Array.from(document.querySelectorAll(".tab[data-tab]"));
  resultTabs.forEach(function (b, index) {
    b.id = "result-tab-" + b.getAttribute("data-tab");
    b.setAttribute("aria-controls", "list");
    b.addEventListener("click", function () { setTab(b.getAttribute("data-tab")); });
    b.addEventListener("keydown", function (e) {
      var next = { ArrowLeft: index - 1, ArrowRight: index + 1, Home: 0, End: resultTabs.length - 1 }[e.key];
      if (next === undefined) return;
      e.preventDefault();
      var tab = resultTabs[(next + resultTabs.length) % resultTabs.length];
      tab.focus();
      setTab(tab.getAttribute("data-tab"));
    });
  });

  function renderList() {
    var m = S.m, el = $("#list"), html = "";
    var routes = m.routeList.filter(function (r) { return selectedParks.has(r.park); });
    if (S.tab === "routes") {
      routes.sort(function (a, b) { return b.unfilled - a.unfilled || a.priority - b.priority || collate(a.number, b.number); });
      var groups = Ops.groupRoutes(routes);
      html = (groups.some(function (g) { return g.demo; }) ? '<p class="result-column-note muted">Демонстрационные колонны парка № 7</p>' : '') +
        '<div class="column-groups">' + groups.map(function (g) {
          var filled = g.routes.reduce(function (n, r) { return n + r.filled; }, 0);
          var total = g.routes.reduce(function (n, r) { return n + r.total; }, 0);
          return '<details class="column-group" data-result-column="' + esc(g.id) + '"' +
            (openColumns.has(g.id) ? ' open' : '') + '><summary>' +
            Ops.columnHeading(g, selectedParks.size > 1 ? D.parkShort(m, g.park) : "") +
            '<span class="column-group__meta">' + D.num(total) + ' ' + D.plural(total, "наряд", "наряда", "нарядов") +
            ' · ' + D.num(g.routes.length) + ' ' + D.plural(g.routes.length, "маршрут", "маршрута", "маршрутов") +
            (filled < total ? ' · на линии ' + D.num(filled) : '') + '</span></summary>' +
            '<div class="rows-head"><span>Маршрут</span><span>Наряды</span><span>Интервал, худший</span><span></span></div>' +
            g.routes.map(function (r) { return routeItem(r, "<b>" + r.filled + "</b> / " + r.total, r.unfilled ? "bad" : "ok", D.worstShort(r.worst)); }).join("") +
            '</details>';
        }).join("") + '</div>';
    } else if (S.tab === "shifts") {
      routes.sort(function (a, b) { return b.noDriver - a.noDriver || collate(a.number, b.number); });
      html = '<div class="rows-head"><span>Маршрут</span><span>Смены</span><span>Водители</span><span></span></div>' + routes.map(function (r) {
        var total = 0, filled = 0;
        r.duties.forEach(function (d) { d.shifts.forEach(function (s) { total++; if (s.segs.length) filled++; }); });
        return routeItem(r, "<b>" + filled + "</b> / " + total, r.noDriver ? "warn" : "ok", r.noDriver ? "без водителя " + r.noDriver : "");
      }).join("");
    } else if (S.tab === "reserve") {
      var res = Object.keys(m.duties).map(function (k) { return m.duties[k]; }).filter(function (d) {
        return d.type !== "line" && selectedParks.has(d.park);
      }).sort(function (a, b) { return a.start - b.start; });
      // строка резерва нажимается: открывает резерв парка в той же панели, что и маршрут
      html = res.length ? '<div class="rows-head"><span></span><span>Автобус</span><span>Время, класс</span><span></span></div>' + res.map(function (d) {
        var on = d.segs.length > 0;
        return '<button class="row' + (S.duty === d.id ? " is-on" : "") + '" data-res="' + esc(d.id) + '"><span class="chip chip--res">Р</span><span class="row__main">' + (on ? "<b>" + esc(D.vehicleName(m, d.segs[0].id)) + "</b>" : '<span class="muted">не выпущен</span>') +
          '</span><span class="row__sub">' + D.hm(d.start) + "–" + D.hm(d.end) + " · " + (D.CLASSES[d.cls] || d.cls) + '</span><span class="item__dot item__dot--' + (on ? "ok" : "off") + '"></span></button>';
      }).join("") : '<div class="empty">Резервных нарядов нет</div>';
    } else {
      var log = m.state.log;
      html = log.length ? log.map(function (l) {
        if (l.kind === "edit") {  // ручная правка диспетчера: окно времени вместо момента события
          var why = l.forced ? "Под ответственность диспетчера" + (l.reason ? ": " + l.reason : "") : (l.reason || "Правка диспетчера");
          return '<div class="item item--static"><span class="chip">' + esc(l.from) + '</span><span><div class="item__t">' + esc(D.plain(m, l.title)) +
            '</div><div class="item__s">' + esc(why) + "</div></span><span></span></div>";
        }
        return '<div class="item item--static"><span class="chip">' + esc(l.at) + '</span><span><div class="item__t">' + esc(D.events.title(m, l)) + '</div><div class="item__s">Решение: ' + esc(D.plain(m, l.title)) + "</div></span><span></span></div>";
      }).join("") : '<div class="empty">Событий пока нет. Откройте маршрут, нажмите на наряд и отметьте сход, ДТП, неявку или опоздание.</div>';
    }
    if ((S.tab === "routes" || S.tab === "shifts") && !routes.length) html = '<div class="empty">Нет маршрутов в расчёте</div>';
    el.innerHTML = html;
    el.querySelectorAll("[data-result-column]").forEach(function (group) {
      group.addEventListener("toggle", function () {
        if (!group.isConnected) return;
        if (group.open) openColumns.add(group.dataset.resultColumn);
        else openColumns.delete(group.dataset.resultColumn);
      });
    });
    M.stagger(el.children, "up");
    el.querySelectorAll("[data-route]").forEach(function (b) { b.addEventListener("click", function () { openRoute(b.getAttribute("data-route")); }); });
    el.querySelectorAll("[data-res]").forEach(function (b) { b.addEventListener("click", function () { openDuty(m.duties[b.getAttribute("data-res")]); }); });
  }
  var collator = new Intl.Collator("ru", { numeric: true });
  function collate(a, b) { return collator.compare(a, b); }
  // Компактная строка списка: номер в цвете маршрута, главное число, пояснение мелко, состояние точкой
  function routeItem(r, main, tone, sub) {
    var c = D.routeBadgeColor(r.geo && r.geo.color);
    return '<button class="row' + (S.route === r.id ? " is-on" : "") + '" data-route="' + r.id + '"><span class="chip chip--route" style="--c:' + c + '">' + esc(r.number) + "</span>" +
      '<span class="row__main">' + main + '</span><span class="row__sub">' + esc(sub) + "</span>" +
      '<span class="item__dot item__dot--' + tone + '" aria-hidden="true"></span></button>';
  }

  // ---------- Проблемы: окошко поверх карты ----------
  // Виды проблем: у каждого свой значок и цвет. Порядок - от самого важного для линии.
  var PROB_KINDS = [
    { id: "bus", title: "Нет автобуса", icon: "bus", tone: "bad" },
    { id: "driver", title: "Нет водителя", icon: "steering-wheel", tone: "warn" },
    { id: "norms", title: "Нарушение норм", icon: "timer", tone: "bad" }
  ];
  function renderProbs(focus) {
    var pr = problems(), box = $("#probs"), m = S.m;
    var lists = { bus: pr.line, driver: pr.shifts, norms: pr.viol };
    var n = pr.line.length + pr.shifts.length + pr.viol.length;
    if (!n) { M.hide(box, "fade"); return; }
    $("#probsN").textContent = n;
    $("#probsBody").innerHTML = PROB_KINDS.filter(function (k) { return lists[k.id].length; }).map(function (k) {
      var list = lists[k.id];
      var head = '<div class="probs__grp"><span class="pk-ic pk-ic--' + k.tone + '"><i class="ad-icon ad-icon--' + k.icon + ' ad-icon--sm"></i></span><span>' + k.title + "</span><b>" + list.length + "</b></div>";
      var items = k.id === "norms"
        ? list.slice(0, 20).map(function (v) {
            return '<div class="probs__it probs__it--text"><span class="pk-ic pk-ic--sm pk-ic--' + k.tone + '"><i class="ad-icon ad-icon--' + k.icon + '"></i></span><span class="probs__txt"><b>' + esc(D.VIOLATIONS[v.code] || "Нарушение") + "</b><small>" + esc(D.plain(m, v.text)) + "</small></span></div>";
          }).join("")
        : list.slice(0, 60).map(function (u) {
            var r = u.route, what = u.kind === "shift" ? "смена " + m.shifts[u.id].order : "наряд " + D.dutyNo(m, u.duty);
            return '<button type="button" class="probs__it" data-problem="' + esc(u.id) + '"><span class="pk-ic pk-ic--sm pk-ic--' + k.tone + '"><i class="ad-icon ad-icon--' + k.icon + '"></i></span>' +
              '<span class="probs__txt"><span class="probs__row"><span class="chip chip--route" style="--c:' + D.routeBadgeColor(r && r.geo && r.geo.color) + '">' + (r ? esc(r.number) : "Р") + "</span>" +
              "<b>" + esc(what) + '</b><span class="probs__time">' + D.hm(u.start) + "–" + D.hm(u.end) + "</span></span>" +
              "<small>" + esc(D.REASONS[u.reason] || u.reason) + "</small></span></button>";
          }).join("") + (list.length > 60 ? '<div class="probs__more">и ещё ' + (list.length - 60) + "</div>" : "");
      return '<section class="probs__sec">' + head + items + "</section>";
    }).join("");
    $("#probsBody").querySelectorAll("[data-problem]").forEach(function (b) { b.addEventListener("click", function () { openProblem(b.getAttribute("data-problem")); }); });
    // в заголовке свёрнутого окна - значки видов с числами, чтобы суть была видна без раскрытия
    $("#probsKinds").innerHTML = PROB_KINDS.filter(function (k) { return lists[k.id].length; }).map(function (k) {
      return '<span class="probs__kind" title="' + k.title + '"><i class="ad-icon ad-icon--' + k.icon + ' ad-icon--sm"></i>' + lists[k.id].length + "</span>";
    }).join("");
    box.classList.toggle("is-collapsed", !S.probsOpen);
    $("#probsHead").setAttribute("aria-expanded", String(S.probsOpen));
    M.show(box, "down", { force: !!focus });
  }
  $("#probsHead").addEventListener("click", function () {
    S.probsOpen = !S.probsOpen;
    var box = $("#probs"), body = $("#probsBody");
    $("#probsHead").setAttribute("aria-expanded", String(S.probsOpen));
    if (S.probsOpen) { box.classList.remove("is-collapsed"); M.enter(body, "down"); }
    else box.classList.add("is-collapsed");
  });

  // ---------- Парк: подсказка при наведении и карточка с фото по клику ----------
  function parkStats(p) {
    var m = S.m, noDrv = p.unfilled.filter(function (u) { return u.kind === "shift" && u.type === "line"; }).length;
    var vehLine = 0;
    Object.keys(m.duties).forEach(function (k) { var d = m.duties[k]; if (d.park === p.id && d.type === "line" && d.segs.length) vehLine++; });
    var drvOn = 0;
    Object.keys(m.shifts).forEach(function (k) { var sh = m.shifts[k]; if (m.duties[sh.duty].park === p.id && sh.segs.length) drvOn++; });
    return [
      ["маршрутов", p.routes.length], ["автобусов на линии", vehLine + " из " + p.lineTotal], ["в резерве", p.resFilled + " из " + p.resTotal],
      ["исправных", p.vehOk + " из " + p.vehAll], ["водителей на сменах", drvOn], ["без автобуса", p.lineTotal - p.lineFilled, p.lineTotal - p.lineFilled ? "bad" : ""],
      ["без водителя", noDrv, noDrv ? "warn" : ""]
    ].concat(p.transIn || p.transOut ? [["получил / отдал", p.transIn + " / " + p.transOut]] : []);
  }
  // Показатель: главное число крупно и жирно, «из N» мельче, подпись под ним - ещё мельче
  function statsHtml(list, cls) {
    return '<dl class="' + cls + '">' + list.map(function (x) {
      var v = String(x[1]), m = v.match(/^(\S+)(\s+(?:из|\/)\s+.+)$/);
      var dd = m ? "<b>" + esc(m[1]) + '</b><span class="sec">' + esc(m[2]) + "</span>" : "<b>" + esc(v) + "</b>";
      return '<div' + (x[2] ? ' class="is-' + x[2] + '"' : "") + "><dt>" + esc(x[0]) + "</dt><dd>" + dd + "</dd></div>";
    }).join("") + "</dl>";
  }
  function parkTip(p) {
    var ph = PARK_PHOTOS[p.id];
    return '<div class="pcard pcard--hover">' + (ph ? '<figure class="pcard__ph"><img src="' + ph.src + '" alt="' + esc(p.name) + '" width="800" height="600"></figure>' : "") +
      '<div class="pcard__body"><div class="pcard__t">' + esc(p.name) + '</div><div class="pcard__addr">' + esc(p.address || "") + "</div>" +
      (S.plan ? statsHtml(parkStats(p), "pcard__dl") : "") + '</div></div>';
  }
  function openPark(id) {
    var m = S.m, p = m && m.parks[id]; if (!p || !p.coords) return;
    revealPark(id);
    S.park = id; S.map.select(id); S.map.showParkCard(id);
  }

  // Подробно о незакрытом: объяснение + переход к маршруту.
  function openProblem(id) {
    var m = S.m, u = m.unfilled.filter(function (x) { return x.id === id; })[0];
    show("paneDetail");
    $("#detail").innerHTML = '<div class="detail__card"><span class="spinner"></span></div>';
    D.explain(S.info.day_id, "unfilled", id).then(function (x) {
      var r = u && u.route;
      $("#detail").innerHTML = '<h2 class="detail__h">' + (r ? "Маршрут " + esc(r.number) : "Резерв") + "</h2>" +
        '<div class="caption muted">' + esc(D.parkShort(m, u.park)) + " · " + (u.kind === "shift" ? "смена " : "наряд ") + D.hm(u.start) + "-" + D.hm(u.end) + "</div>" +
        '<div class="detail__card">' + D.explainHtml(x) + "</div>";
      M.swap($("#detail"));
      if (r) openRoute(r.id);
    }, function (e) { $("#detail").innerHTML = '<p class="err">' + esc(e.message) + "</p>"; });
  }
  $("#btnBack").addEventListener("click", function () { show("paneResult"); });

  // ---------- Карточка маршрута по клику на карте ----------
  function busesOnRoute(m, r) {
    return Array.from(new Set(r.duties.flatMap(function (d) { return d.segs.map(function (seg) { return seg.id; }); })))
      .map(function (id) { return m.vehicles[id]; }).filter(Boolean).sort(function (a, b) { return String(a.board_number).localeCompare(String(b.board_number), "ru", { numeric: true }); });
  }
  function renderRouteViewer(id) {
    var m = S.m, r = m && m.routes[id], g = r && r.geo, host = $("#routeViewer");
    if (!r || !host) return;
    var dirs = g ? g.directions.filter(function (d) { return d.line.length > 1 || d.stops.length; }) : [];
    var active = dirs.filter(function (d) { return d.id === S.routeDirection; })[0] || dirs[0];
    S.routeDirection = active ? active.id : null;
    var stops = active ? active.stops : [];
    var color = D.routeBadgeColor(g && g.color);
    var buses = busesOnRoute(m, r);
    var calc = !!(S.plan && m.state);
    var status = !calc ? "" : r.unfilled ? '<span class="ad-badge ad-badge--danger">без автобуса: ' + r.unfilled + '</span>'
      : r.noDriver ? '<span class="ad-badge ad-badge--warning">без водителя: ' + r.noDriver + '</span>'
      : '<span class="ad-badge ad-badge--success">Все наряды закрыты</span>';
    var dutyCount = calc ? r.filled + " из " + r.total + " на линии" : String(r.total);
    var directionHtml = dirs.length > 1 ? '<div class="route-directions" role="tablist" aria-label="Направления маршрута">' + dirs.map(function (d) {
      var st = d.stops, first = st.length ? st[0].name : "", last = st.length ? st[st.length - 1].name : "";
      var on = active && active.id === d.id;
      return '<button type="button" class="route-direction' + (on ? " is-on" : "") + '" data-route-direction="' + esc(d.id) + '" role="tab" aria-selected="' + String(on) + '">' +
        '<span class="route-direction__main"><span class="route-direction__code">' + esc(d.id) + '</span><span>' + esc(first) + '</span></span>' +
      '<span class="route-direction__to">' + esc(last) + '</span><span class="route-direction__count">' + st.length + " " + D.plural(st.length, "остановка", "остановки", "остановок") + (d.line.length > 1 ? " · " + kmText(lineKm(d.line)) : "") + '</span></button>';
    }).join("") + "</div>" : "";
    var stopsHtml = stops.length ? '<ol class="route-stops" aria-label="Остановки направления ' + esc(active.id) + '">' + stops.map(function (s, i) {
      var terminal = i === 0 ? "начальная" : i === stops.length - 1 ? "конечная" : "";
      return '<li class="route-stop-row"><button type="button" class="route-stop-row__button" data-stop-index="' + i + '" aria-label="Подсветить остановку ' + esc(s.name) + ' на карте"><span class="route-stop-row__dot" aria-hidden="true"></span><span class="route-stop-row__content"><span class="route-stop-row__main"><span class="route-stop-row__name">' + esc(s.name) + '</span>' + (terminal ? '<span class="route-stop-row__terminal">' + terminal + '</span>' : '') + '</span></span></button></li>';
    }).join("") + "</ol>" : '<p class="route-viewer__empty">Для маршрута нет данных об остановках.</p>';
    host.innerHTML = '<div class="route-viewer__head"><div class="route-viewer__title"><span class="route-viewer__number" style="background:' + color + '">' + esc(r.number) + '</span><h1 class="route-viewer__name" id="routeViewerTitle" tabindex="-1">Маршрут ' + esc(r.number) + '</h1>' +
      (g ? '<a class="route-viewer__edit" href="/routes#' + encodeURIComponent(g.id) + '" target="_blank" rel="noopener" aria-label="Редактировать маршрут ' + esc(r.number) + '" title="Редактировать маршрут"><i class="ad-icon ad-icon--pencil-simple ad-icon--sm" aria-hidden="true"></i></a>' : '') + '<button type="button" class="btn-close route-viewer__close" id="btnRouteBack" aria-label="Закрыть маршрут" title="Закрыть маршрут"></button></div>' +
      (status ? '<div class="route-viewer__status">' + status + '</div>' : '') +
      '<div class="route-viewer__overview"><button class="route-viewer__cell route-viewer__park" type="button" data-route-park="' + esc(r.park) + '"><i class="ad-icon ad-icon--bus ad-icon--sm" aria-hidden="true"></i><span class="route-viewer__cell-text"><small>Парк маршрута</small><strong>' + esc(D.parkShort(m, r.park)) + '</strong></span></button>' +
      '<button type="button" class="route-viewer__cell route-viewer__duties-link" data-route-duties="' + esc(r.id) + '"><i class="ad-icon ad-icon--calendar-dots ad-icon--sm" aria-hidden="true"></i><span class="route-viewer__cell-text"><small>Наряды на день</small><strong>' + esc(dutyCount) + '</strong></span></button>' +
      '<button type="button" class="route-viewer__cell route-viewer__buses" data-route-buses="' + esc(r.id) + '"><i class="ad-icon ad-icon--bus ad-icon--sm" aria-hidden="true"></i><span class="route-viewer__cell-text"><small>Автобусы на маршруте сегодня</small><strong>' + buses.length + '</strong></span><span class="route-viewer__cell-next" aria-hidden="true"></span></button></div></div>' + directionHtml + stopsHtml;
    host.querySelectorAll("[data-route-direction]").forEach(function (b) {
      b.addEventListener("click", function () { S.routeDirection = b.getAttribute("data-route-direction"); renderRouteViewer(id); if (S.map) S.map.selectRoute(id, 0, false, S.routeDirection); });
    });
    host.querySelector('[data-route-park]').addEventListener('click', function () { openPark(r.park); });
    host.querySelectorAll('[data-stop-index]').forEach(function (b) {
      var index = +b.getAttribute('data-stop-index');
      b.addEventListener('pointerenter', function () { if (S.map) S.map.highlightStop(index); });
      b.addEventListener('pointerleave', function () { if (S.map) S.map.highlightStop(null); });
      b.addEventListener('focus', function () { if (S.map) S.map.highlightStop(index); });
      b.addEventListener('blur', function () { if (S.map) S.map.highlightStop(null); });
    });
    host.querySelector('[data-route-buses]').addEventListener('click', function () { openRouteBuses(id); });
    var duties = host.querySelector("[data-route-duties]");
    duties && duties.addEventListener("click", function () { openRouteDuties(id, true); });
  }

  var routeBusesReturnScroll = 0;
  function openRouteBuses(id) {
    var m = S.m, r = m && m.routes[id], host = $("#routeBuses");
    if (!r || !host) return;
    var buses = busesOnRoute(m, r), color = D.routeBadgeColor(r.geo && r.geo.color);
    host.innerHTML = '<div class="route-buses__head"><button type="button" class="route-buses__back" id="btnBusesBack" aria-label="Назад к маршруту"><i class="arr arr--left" aria-hidden="true"></i>Маршрут ' + esc(r.number) + '</button>' +
      '<h1 id="routeBusesTitle" tabindex="-1">Автобусы на маршруте сегодня</h1><p>' + buses.length + ' ' + D.plural(buses.length, "автобус", "автобуса", "автобусов") + '</p></div>' +
      (buses.length ? '<ul class="route-buses__list">' + buses.map(function (v) { return '<li><button type="button" class="route-buses__item" data-route-bus="' + esc(v.id) + '"><span class="route-buses__badge" style="background:' + color + '" aria-hidden="true"><i class="ad-icon ad-icon--bus ad-icon--sm"></i></span><span class="route-buses__info"><strong>Борт ' + esc(v.board_number) + '</strong>' + (v.model ? '<small>' + esc(v.model) + '</small>' : '') + '</span><span class="route-viewer__cell-next" aria-hidden="true"></span></button></li>'; }).join('') + '</ul>' : '<p class="route-viewer__empty">Пока нет автобусов на линии.</p>');
    host.querySelector('#btnBusesBack').addEventListener('click', function () { show('paneRoute'); $('.side').scrollTop = routeBusesReturnScroll; $('#routeViewerTitle').focus({ preventScroll: true }); });
    host.querySelectorAll('[data-route-bus]').forEach(function (b) { b.addEventListener('click', function () { var v = m.vehicles[b.getAttribute('data-route-bus')]; if (v) Ops.openRecord({ kind: 'veh', id: v.id, park: v.park_id, name: v.board_number }, b); }); });
    routeBusesReturnScroll = $('.side').scrollTop;
    show('paneRouteBuses');
    $('.side').scrollTop = 0;
    host.querySelector('#routeBusesTitle').focus({ preventScroll: true });
  }

  function routeCard(id, latlng, dirClicked) {
    if (!S.m.routes[id]) return;
    if (S.route !== id) S.routeDirection = null;
    openRoute(id);
    if (dirClicked && dirClicked.id && dirClicked.id !== S.routeDirection) {
      S.routeDirection = dirClicked.id;
      renderRouteViewer(id);
      if (S.map) S.map.selectRoute(id, 0, false, S.routeDirection);
    }
  }
  function lineKm(l) {
    var s = 0; for (var i = 1; i < l.length; i++) { var k = Math.cos(l[i][0] * Math.PI / 180); s += Math.hypot((l[i][0] - l[i - 1][0]) * 111.32, (l[i][1] - l[i - 1][1]) * 111.32 * k); }
    return s;
  }
  function kmText(x) { return (Math.round(x * 10) / 10).toLocaleString("ru-RU") + " км"; }

  // ---------- Наряды маршрута ----------
  function openRoute(id, keepSide) {
    if (id.indexOf("res:") === 0) return openReserve(id.slice(4), keepSide);
    var m = S.m, r = m.routes[id]; if (!r) return;
    revealPark(r.park);
    if (S.route !== id) S.routeDirection = null;
    S.route = id;
    S.routeDirection = S.routeDirection || (r.geo && r.geo.directions[0] && r.geo.directions[0].id);
    $("#paneRoute").style.setProperty("--route-blue", r.geo && r.geo.color || "var(--accent-deep)");
    show("paneRoute");
    renderRouteViewer(id);
    if (S.map) {
      S.map.closePopup();
      S.map.select(r.park);
      S.map.selectRoute(id, 0, !!keepSide, S.routeDirection);
    }
    $("#routeViewerTitle").focus({ preventScroll: true });
  }

  function openRouteDuties(id, keepSide) {
    if (id.indexOf("res:") === 0) return openReserve(id.slice(4), keepSide);
    var m = S.m, r = m.routes[id]; if (!r) return;
    revealPark(r.park);
    S.route = id;
    show("paneResult");
    openPanel();
    if (S.map) {
      S.map.select(r.park);
      if (!keepSide) S.map.selectRoute(id);
    }
    $("#drTitle").textContent = "Маршрут " + r.number + " · " + D.parkShort(m, r.park);
    $("#drSub").textContent = "На линии " + r.filled + " из " + r.total + " нарядов · круг " + r.turn + " мин" +
      (r.worst ? " · интервал " + D.worstText(r.worst) : "");
    $("#drSub").insertAdjacentHTML("beforeend", ' · ' + Ops.recordButton("route", r.id, r.park, r.number, "Карточка маршрута"));
    $("#drCov").parentNode.hidden = !r.duties.length;
    if (!r.duties.length) $("#drGantt").innerHTML = '<p class="caption muted">Для маршрута в этом расчёте нет нарядов.</p>';
    else {
      var a = Math.floor(Math.min.apply(null, r.duties.map(function (d) { return d.start; })) / 60) * 60;
      var b = Math.ceil(Math.max.apply(null, r.duties.map(function (d) { return d.end; })) / 60) * 60;
      D.coverageChart($("#drCov"), D.coverage(r.duties, a, b, 10), { height: 90 });
      D.gantt($("#drGantt"), m, r.duties, { onDuty: dutyClick });
    }
    if (!keepSide) $("#drSide").innerHTML = '<p class="caption muted">Нажмите на наряд, чтобы узнать, почему на нём этот автобус и водитель, или отметить сход.</p>';
    document.querySelectorAll("[data-route]").forEach(function (x) { x.classList.toggle("is-on", x.getAttribute("data-route") === id); });
  }
  // Резерв парка - в той же панели, что и маршрут: диаграмма нарядов, по щелчку объяснение,
  // «Изменить…» и события. Трассы и интервала у резерва нет: без графика покрытия и без линии на карте.
  function reserveOf(m, parkId) {
    return Object.keys(m.duties).map(function (k) { return m.duties[k]; })
      .filter(function (d) { return d.type !== "line" && d.park === parkId; })
      .sort(function (a, b) { return a.start - b.start || (a.id < b.id ? -1 : 1); });
  }
  function openReserve(parkId, keepSide) {
    var m = S.m, list = reserveOf(m, parkId); if (!list.length) return;
    revealPark(parkId);
    S.route = "res:" + parkId;
    openPanel();
    if (S.map) { S.map.select(parkId); S.map.selectRoute(null); }
    var on = list.filter(function (d) { return d.segs.length; }).length;
    $("#drTitle").textContent = "Резерв · " + D.parkShort(m, parkId);
    $("#drSub").textContent = "Выпущено " + on + " из " + list.length + " " + D.plural(list.length, "наряда", "нарядов", "нарядов");
    $("#drCov").parentNode.hidden = true;
    D.gantt($("#drGantt"), m, list, { onDuty: dutyClick });
    if (!keepSide) $("#drSide").innerHTML = '<p class="caption muted">Нажмите на наряд, чтобы узнать, почему на нём этот автобус и водитель, или изменить его.</p>';
    document.querySelectorAll("[data-route]").forEach(function (x) { x.classList.remove("is-on"); });
  }
  // Строка вкладки «Резерв»: сразу резерв парка и боковая панель этого наряда
  function openDuty(duty) {
    if (!duty) return;
    openReserve(duty.park);
    var row = $('#drGantt .gantt__row[data-duty="' + CSS.escape(duty.id) + '"]');
    if (row) row.classList.add("is-selected");
    document.querySelectorAll("[data-res]").forEach(function (x) { x.classList.toggle("is-on", x.getAttribute("data-res") === duty.id); });
    dutyClick(duty, duty.start);
  }

  // Панель маршрута - правая половина экрана: при открытии карта сжимается до своей половины, при закрытии - обратно
  var drawerTrigger;
  function openPanel() {
    var d = $("#drawer");
    if (d._out) { d._out.cancel(); d._out = null; }  // панель ещё уезжала - остаётся открытой
    if (!d.hidden) return;
    drawerTrigger = document.activeElement;
    $("#scrResult").classList.add("is-split");
    M.show($("#drawer"), "right");
    $("#drClose").focus({ preventScroll: true });
    window.App.resize();
  }
  function closePanel(after) {
    S.route = null; S.duty = null;
    document.querySelectorAll("[data-route].is-on").forEach(function (x) { x.classList.remove("is-on"); });
    if ($("#drawer").contains(document.activeElement)) {
      var target = drawerTrigger && drawerTrigger.isConnected && drawerTrigger !== document.body ? drawerTrigger : $("#resTitle");
      target.focus({ preventScroll: true });
    }
    M.hide($("#drawer"), "right", function () {
      $("#scrResult").classList.remove("is-split");
      if (S.map) { S.map.resize(); S.map.setParks(selectedParks, true); }
      if (typeof after === "function") after();
    });
  }
  $("#drClose").addEventListener("click", closePanel);
  function closeRoute() {
    S.route = null; S.routeDirection = null;
    show("paneResult");
    if (S.map) { S.map.selectRoute(null); S.map.closePopup(); }
    document.querySelectorAll("[data-route].is-on").forEach(function (x) { x.classList.remove("is-on"); });
  }
  document.addEventListener("click", function (e) { if (e.target.closest("#btnRouteBack")) closeRoute(); });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !$("#drawer").hidden && !document.querySelector(".modal, dialog[open]")) closePanel();
  });

  function dutyClick(duty, t) {
    var side = $("#drSide"), m = S.m, day = S.info.day_id;
    S.duty = duty.id; S.dutyT = t;
    side.innerHTML = '<div class="label">' + esc(D.dutyLabel(m, duty)) + '</div><div class="caption muted">' + D.hm(duty.start) + "-" + D.hm(duty.end) + ", класс " + (D.CLASSES[duty.cls] || duty.cls) +
      ", смен " + duty.shiftCount + '</div><div id="xpV"><span class="spinner"></span></div><div id="xpD"></div>' +
      '<div id="dutyRecords" class="drawer__acts"></div><div class="drawer__acts">' +
      (duty.segs.length ? '<button class="ad-btn ad-btn--outline ad-btn--sm" id="btnEv"><i class="ad-icon ad-icon--warning ad-icon--sm"></i>Сход, неявка, опоздание…</button>' : "") +
      '<button class="ad-btn ad-btn--outline ad-btn--sm" id="btnEdit"><i class="ad-icon ad-icon--pencil-simple ad-icon--sm"></i>Изменить…</button></div>';
    var vReq = duty.segs.length ? D.explain(day, "vehicle", duty.id) : D.explain(day, "unfilled", duty.id);
    M.swap(side);
    vReq.then(function (x) { $("#xpV").innerHTML = D.explainHtml(x, duty.segs.length && D.changedSinceMorning(m, "duty", duty)); M.swap($("#xpV")); }, function (e) { $("#xpV").innerHTML = '<p class="err">' + esc(e.message) + "</p>"; });
    var sh = duty.shifts.filter(function (s) { return s.start <= t && t < s.end; })[0] || duty.shifts[0];
    var buses = Array.from(new Set(duty.segs.map(function (seg) { return seg.id; })));
    var drivers = Array.from(new Set(duty.shifts.flatMap(function (shift) {
      return shift.segs.map(function (seg) { return seg.id; });
    })));
    $("#dutyRecords").innerHTML = buses.map(function (id) {
      var v = m.vehicles[id];
      return v ? Ops.recordButton("veh", id, v.park_id, v.board_number, "Борт " + v.board_number) : "";
    }).concat(drivers.map(function (id) {
      var d = m.drivers[id];
      return d ? Ops.recordButton("drv", id, d.park_id, d.full_name, D.driverName(m, id)) : "";
    })).join("");
    if (sh && duty.segs.length) {
      D.explain(day, sh.segs.length ? "driver" : "unfilled", sh.id).then(function (x) { $("#xpD").innerHTML = D.explainHtml(x, sh.segs.length && D.changedSinceMorning(m, "shift", sh)); M.swap($("#xpD")); }, function () {});
    }
    var be = $("#btnEv");
    be && be.addEventListener("click", function () {
      D.events.open(m, day, duty, t, function (state) { apply(state); });
    });
    $("#btnEdit").addEventListener("click", function () {
      D.edit.open(m, day, duty, t, function (state) { apply(state); });
    });
  }

  function dateText(s) {
    var p = s.split("-"); var months = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
    return +p[2] + " " + months[+p[1] - 1] + " " + p[0];
  }

  window.addEventListener("resize", function () { S.map && S.map.resize(); });
})();
