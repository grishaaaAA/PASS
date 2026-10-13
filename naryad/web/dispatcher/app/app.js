/* Экран расчёта: сводка, проблемы, карта, наряды маршрута, события. День приходит с экрана подготовки (prep.js). */
(function () {
  "use strict";
  var D = window.Disp, $ = D.$, esc = D.esc, M = window.Motion;
  var S = { info: null, day: null, plan: null, m: null, map: null, tab: "routes", route: null, duty: null, park: null, probsOpen: false };
  // Фото парков для карточки (откуда взяты - app/img/CREDITS.md)
  var PARK_PHOTOS = {
    P07: { src: "img/park-P07.jpg" }
  };

  // Смена левой панели: список ↔ подробности - сдвигом сбоку
  function show(id) {
    ["paneResult", "paneDetail"].forEach(function (p) { if (p !== id) $("#" + p).hidden = true; });
    M.show($("#" + id), "side");
  }
  function busy(text) { if (text) { $("#busyText").textContent = text; M.show($("#busy"), "fade"); } else M.hide($("#busy"), "fade"); }

  // ---------- Расчёт дня, собранного на экране подготовки ----------
  // Отдаёт обещание: день проверен, план построен, экран расчёта открыт.
  function run(day, onShown) {
    return D.submitDay(day).then(function (r) {
      S.info = r.info; S.day = day; S.route = null; S.duty = null; S.park = null;
      return D.calculate(S.info.day_id);
    }).then(function (r) {
      S.plan = r.plan;
      onShown && onShown();  // экран расчёта видим - теперь карта узнает свой размер
      $("#drawer").hidden = true;
      S.run = r.plan.run || null;
      S.m = D.build(S.day, S.plan, r.state, r.intervals);
      var handlers = { onPark: openPark, onRoute: routeCard, parkTip: parkTip };
      if (!S.map) S.map = D.parkMap($("#map"), S.m, handlers);
      else { S.map.resize(); S.map.refit(); S.map.redraw(S.m, handlers); }
      S.map.selectRoute(null);
      setTab("routes");
      S.probsOpen = false;  // окно проблем по умолчанию свёрнуто - открывается по заголовку или строке состояния
      renderSummary(); renderProbs(); show("paneResult");
    });
  }
  window.App = { run: run, resize: function () { S.map && S.map.resize(); }, runNo: function () { return S.run ? S.run.id : null; } };

  // Состояние после события: интервалы пересчитывает движок, поэтому сначала спросить их
  function apply(state) {
    return D.intervals(S.info.day_id).then(function (iv) { draw(state, iv); }, function () { draw(state, null); });
  }
  function draw(state, iv) {
    S.m = D.build(S.day, S.plan, state, iv);
    S.map.redraw(S.m);
    if (S.route && S.route.indexOf("res:") !== 0) S.map.selectRoute(S.route, Math.round($("#drawer").offsetHeight + 30));
    renderSummary(); renderList(); renderProbs();
    if (S.route) openRoute(S.route, true);
    // Открытый наряд - заново по новому состоянию: иначе в панели остаётся то, что стояло до события или правки
    var duty = S.route && S.duty && S.m.duties[S.duty];
    var row = duty && $('#drGantt .gantt__row[data-duty="' + CSS.escape(duty.id) + '"]');
    if (row) { row.classList.add("is-selected"); dutyClick(duty, S.dutyT); }
  }

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
    $("#resTitle").innerHTML = "Расчёт" + (S.run ? " № " + S.run.id : "") + ' <small>' + (+d[2]) + " " + MONTHS[+d[1] - 1] + "</small>";
    $("#resParks").innerHTML = m.parkList.map(function (p) {
      return '<button type="button" class="res-park" data-park="' + p.id + '"><b>' + esc(p.name) + "</b><span>" + esc(p.address || "") + "</span></button>";
    }).join("");
    $("#resParks").querySelectorAll("[data-park]").forEach(function (b) { b.addEventListener("click", function () { openPark(b.getAttribute("data-park")); }); });
    // Всё хорошо - никакого уведомления; есть проблемы - маленький красный значок с числом
    var bad = pr.line.length + pr.shifts.length + pr.viol.length, al = $("#resAlert");
    al.hidden = !bad;
    if (bad) { $("#resAlertN").textContent = bad; al.title = "Проблемы: " + bad + " - открыть список"; al.setAttribute("aria-label", al.title); }
    // показатели - они же переключают список ниже
    var k = [
      ["routes", "Наряды на линии", t.lineFilled, t.lineTotal, pr.line.length ? "bad" : ""],
      // смены на линии по текущему состоянию дня: после событий число меняется
      ["shifts", "Смены с водителем", t.shiftFilled, t.shiftTotal, pr.shifts.length ? "warn" : ""],
      ["reserve", "Резерв выпущен", t.resFilled, t.resTotal, ""]
    ];
    if (m.parkList.length > 1) k.push(["routes", "Переброски", m.transfers.length, null, ""]);
    $("#kpis").innerHTML = k.map(function (x) {
      return '<button type="button" role="tab" class="kpi' + (x[4] ? " kpi--" + x[4] : "") + (S.tab === x[0] ? " is-on" : "") + '" data-view="' + x[0] + '"><span class="kpi__v">' + D.num(x[2]) +
        (x[3] != null ? " <small>из " + D.num(x[3]) + "</small>" : "") + '</span><span class="kpi__l">' + x[1] + "</span></button>";
    }).join("");
    $("#kpis").querySelectorAll("[data-view]").forEach(function (b) { b.addEventListener("click", function () { setTab(b.getAttribute("data-view")); }); });
    $("#nLog").textContent = m.state.log.length || "";
    window.dispatchEvent(new Event("autodisp:run"));  // крошки в шапке узнают номер расчёта
  }
  $("#resAlert").addEventListener("click", function () { S.probsOpen = true; renderProbs(true); });

  function setTab(t) {
    S.tab = t;
    document.querySelectorAll(".tab").forEach(function (x) { x.classList.toggle("is-on", x.getAttribute("data-tab") === t); });
    document.querySelectorAll("#kpis [data-view]").forEach(function (x) { x.classList.toggle("is-on", x.getAttribute("data-view") === t); });
    if (S.m) renderList();
  }
  document.querySelectorAll(".tab").forEach(function (b) { b.addEventListener("click", function () { setTab(b.getAttribute("data-tab")); }); });

  function renderList() {
    var m = S.m, el = $("#list"), html = "";
    var routes = m.routeList.slice();
    if (S.tab === "routes") {
      routes.sort(function (a, b) { return b.unfilled - a.unfilled || a.priority - b.priority || collate(a.number, b.number); });
      html = '<div class="rows-head"><span>Маршрут</span><span>Наряды</span><span>Интервал, худший</span><span></span></div>' +
        routes.map(function (r) { return routeItem(r, "<b>" + r.filled + "</b> / " + r.total, r.unfilled ? "bad" : "ok", D.worstShort(r.worst)); }).join("");
    } else if (S.tab === "shifts") {
      routes.sort(function (a, b) { return b.noDriver - a.noDriver || collate(a.number, b.number); });
      html = '<div class="rows-head"><span>Маршрут</span><span>Смены</span><span>Водители</span><span></span></div>' + routes.map(function (r) {
        var total = 0, filled = 0;
        r.duties.forEach(function (d) { d.shifts.forEach(function (s) { total++; if (s.segs.length) filled++; }); });
        return routeItem(r, "<b>" + filled + "</b> / " + total, r.noDriver ? "warn" : "ok", r.noDriver ? "без водителя " + r.noDriver : "все с водителем");
      }).join("");
    } else if (S.tab === "reserve") {
      var res = Object.keys(m.duties).map(function (k) { return m.duties[k]; }).filter(function (d) { return d.type !== "line"; }).sort(function (a, b) { return a.start - b.start; });
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
    el.innerHTML = html;
    M.stagger(el.children, "up");
    el.querySelectorAll("[data-route]").forEach(function (b) { b.addEventListener("click", function () { openRoute(b.getAttribute("data-route")); }); });
    el.querySelectorAll("[data-res]").forEach(function (b) { b.addEventListener("click", function () { openDuty(m.duties[b.getAttribute("data-res")]); }); });
  }
  var collator = new Intl.Collator("ru", { numeric: true });
  function collate(a, b) { return collator.compare(a, b); }
  // Компактная строка списка: номер в цвете маршрута, главное число, пояснение мелко, состояние точкой
  function routeItem(r, main, tone, sub) {
    var c = r.geo ? r.geo.color : "var(--accent-deep)";
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
              '<span class="probs__txt"><span class="probs__row"><span class="chip chip--route" style="--c:' + (r && r.geo ? r.geo.color : "var(--accent-deep)") + '">' + (r ? esc(r.number) : "Р") + "</span>" +
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
    if (!S.plan) return "<b>" + esc(p.name) + "</b><br>" + esc(p.address || "");
    return '<div class="ptip"><b>' + esc(p.name) + '</b><span class="ptip__addr">' + esc(p.address || "") + "</span>" + statsHtml(parkStats(p).slice(0, 6), "ptip__dl") + '<span class="ptip__hint">Нажмите - карточка парка</span></div>';
  }
  function openPark(id) {
    var m = S.m, p = m && m.parks[id]; if (!p || !p.coords) return;
    S.park = id; S.map.select(id);
    var ph = PARK_PHOTOS[id];
    var html = '<div class="pcard">' + (ph ? '<figure class="pcard__ph"><img src="' + ph.src + '" alt="' + esc(p.name) + '" width="800" height="600"></figure>' : "") +
      '<div class="pcard__body"><div class="pcard__t">' + esc(p.name) + '</div><div class="pcard__addr">' + esc(p.address || "") + "</div>" +
      (S.plan ? statsHtml(parkStats(p), "pcard__dl") : "") +
      '<button type="button" class="ad-btn ad-btn--sm btn-plain" data-act="routes"><i class="ad-icon ad-icon--path ad-icon--sm"></i>Маршруты парка</button></div></div>';
    var el = S.map.popup(p.coords, html, null, "ppop");
    var b = el && el.querySelector('[data-act="routes"]');
    b && b.addEventListener("click", function () { S.map.closePopup(); setTab("routes"); show("paneResult"); });
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
  function routeCard(id, latlng, dirClicked) {
    var m = S.m, r = m.routes[id]; if (!r) return;
    var g = r.geo, calc = !!(S.plan && m.state);
    S.map.selectRoute(id, 0, true);
    var stops = g ? g.directions.reduce(function (n, d) { return n + d.stops.length; }, 0) : 0;
    var lens = g ? g.directions.filter(function (d) { return d.line.length > 1; }).map(function (d) { return kmText(lineKm(d.line)); }) : [];
    var first = g && g.directions[0] && g.directions[0].stops;
    var ends = first && first.length ? first[0].name + " - " + first[first.length - 1].name : (g && g.name) || "";
    var status = !calc ? "" : r.unfilled ? '<span class="ad-badge ad-badge--danger">без автобуса: ' + r.unfilled + "</span>"
      : r.noDriver ? '<span class="ad-badge ad-badge--warning">без водителя: ' + r.noDriver + "</span>" : '<span class="ad-badge ad-badge--success">все наряды закрыты</span>';
    var rows = [
      ["Парк", D.parkShort(m, r.park)],
      ["Важность", D.PRIORITY[r.priority]],
      ["Нарядов на день", calc ? r.filled + " из " + r.total + " на линии" : String(r.total)],
      calc ? ["Интервал", D.worstText(r.worst)] : null,
      ["Круг", r.turn + " мин"],
      ["Длина рейса", kmText(r.length) + " по справке" + (lens.length ? ", по карте " + lens.join(" / ") : "")],
      ["Автобусы", r.classes.map(function (c) { return D.CLASSES[c]; }).join(", ")],
      ["Остановок", g ? stops + " в " + g.directions.length + " " + D.plural(g.directions.length, "направлении", "направлениях", "направлениях") : "нет на карте"]
    ].filter(Boolean);
    // направления: оба, кликнутое - выделено
    var dirs = g ? g.directions.filter(function (d) { return d.line.length > 1 || d.stops.length; }) : [];
    var dirsHtml = dirs.length ? '<div class="rc__dirs"><div class="rc__lbl">Направления</div>' + dirs.map(function (d) {
      var st = d.stops, from = st.length ? st[0].name : "", to = st.length ? st[st.length - 1].name : "";
      var on = dirClicked && dirClicked.id === d.id;
      return '<div class="rc__dir' + (on ? " is-on" : "") + '"><span class="rc__dl">' + esc(d.id) + '</span><span>' + esc(from) + ' <i class="arr"></i> ' + esc(to) +
        "<small>" + st.length + " " + D.plural(st.length, "остановка", "остановки", "остановок") + (d.line.length > 1 ? " · " + kmText(lineKm(d.line)) : "") + "</small></span></div>";
    }).join("") + "</div>" : "";
    var html = '<div class="rc"><div class="rc__head"><span class="rc__n" style="background:' + (g ? g.color : "var(--accent-deep)") + '">' + esc(r.number) + "</span>" +
      '<div class="rc__ht"><div class="rc__t">Маршрут ' + esc(r.number) + '</div><div class="rc__s">' + esc(ends) + "</div></div>" + (status ? '<div class="rc__status">' + status + "</div>" : "") + "</div>" +
      '<div class="rc__cols"><div class="rc__col">' + dirsHtml + '<div class="rc__why" id="rcWhy"></div></div>' +
      '<div class="rc__col"><dl class="rc__dl">' + rows.filter(function (x) { return x[0] !== "Остановок" && x[0] !== "Длина рейса"; }).concat([["Длина рейса", kmText(r.length) + " по справке"]])
        .map(function (x) { return "<dt>" + x[0] + "</dt><dd>" + esc(x[1]) + "</dd>"; }).join("") + "</dl></div></div>" +
      '<div class="rc__actions">' +
      (calc ? '<button class="ad-btn ad-btn--secondary ad-btn--sm" data-act="duties"><i class="ad-icon ad-icon--calendar-dots ad-icon--sm"></i>Наряды</button>' : "") +
      '<a class="ad-btn ad-btn--sm btn-plain" href="/routes#' + encodeURIComponent(g ? g.id : "") + '" target="_blank" rel="noopener"><i class="ad-icon ad-icon--pencil-simple ad-icon--sm"></i>Трасса</a></div></div>';
    var el = S.map.popup(latlng, html, function () { if ($("#drawer").hidden) { S.map.selectRoute(null); } });
    var b = el && el.querySelector('[data-act="duties"]');
    b && b.addEventListener("click", function () { S.map.closePopup(); openRoute(id); });
    var bad = calc && r.unfilled ? r.duties.filter(function (d) { return d.unfilled; })[0] : null;
    if (bad) {
      D.explain(S.info.day_id, "unfilled", bad.id).then(function (x) {
        var w = el && el.querySelector("#rcWhy"); if (w) w.innerHTML = '<div class="rc__wq">Почему не закрыт наряд ' + D.hm(bad.start) + "-" + D.hm(bad.end) + "</div>" + esc(x.answer);
      }, function () {});
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
    S.route = id;
    M.show($("#drawer"), "sheet");
    $("#drCov").parentNode.hidden = false;  // график покрытия есть только у маршрута
    S.map.select(r.park);
    if (!keepSide) S.map.selectRoute(id, Math.round($("#drawer").offsetHeight + 30));
    $("#drTitle").textContent = "Маршрут " + r.number + " · " + D.parkShort(m, r.park);
    $("#drSub").textContent = "Важность " + D.PRIORITY[r.priority] + " · на линии " + r.filled + " из " + r.total + " нарядов · круг " + r.turn + " мин · " +
      "интервал " + D.worstText(r.worst);
    var a = Math.floor(Math.min.apply(null, r.duties.map(function (d) { return d.start; })) / 60) * 60;
    var b = Math.ceil(Math.max.apply(null, r.duties.map(function (d) { return d.end; })) / 60) * 60;
    D.coverageChart($("#drCov"), D.coverage(r.duties, a, b, 10), { height: 90 });
    D.gantt($("#drGantt"), m, r.duties, { onDuty: dutyClick });
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
    S.route = "res:" + parkId;
    M.show($("#drawer"), "sheet");
    S.map.select(parkId);
    S.map.selectRoute(null);
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

  $("#drClose").addEventListener("click", function () { M.hide($("#drawer"), "sheet"); S.route = null; S.duty = null; S.map.selectRoute(null); });

  function dutyClick(duty, t) {
    var side = $("#drSide"), m = S.m, day = S.info.day_id;
    S.duty = duty.id; S.dutyT = t;
    side.innerHTML = '<div class="label">' + esc(D.dutyLabel(m, duty)) + '</div><div class="caption muted">' + D.hm(duty.start) + "-" + D.hm(duty.end) + ", класс " + (D.CLASSES[duty.cls] || duty.cls) +
      ", смен " + duty.shiftCount + '</div><div id="xpV"><span class="spinner"></span></div><div id="xpD"></div>' +
      '<div class="drawer__acts">' +
      (duty.segs.length ? '<button class="ad-btn ad-btn--outline ad-btn--sm" id="btnEv"><i class="ad-icon ad-icon--warning ad-icon--sm"></i>Сход, неявка, опоздание…</button>' : "") +
      '<button class="ad-btn ad-btn--outline ad-btn--sm" id="btnEdit"><i class="ad-icon ad-icon--pencil-simple ad-icon--sm"></i>Изменить…</button></div>';
    var vReq = duty.segs.length ? D.explain(day, "vehicle", duty.id) : D.explain(day, "unfilled", duty.id);
    M.swap(side);
    vReq.then(function (x) { $("#xpV").innerHTML = D.explainHtml(x, duty.segs.length && D.changedSinceMorning(m, "duty", duty)); M.swap($("#xpV")); }, function (e) { $("#xpV").innerHTML = '<p class="err">' + esc(e.message) + "</p>"; });
    var sh = duty.shifts.filter(function (s) { return s.start <= t && t < s.end; })[0] || duty.shifts[0];
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
