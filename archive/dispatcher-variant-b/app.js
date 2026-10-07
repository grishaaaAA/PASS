/* Вариант Б «Пульт»: шаги загрузка → проверка → нарядка, рабочий стол с вкладками, карта и «почему» справа. */
(function () {
  "use strict";
  var D = window.Disp, $ = D.$, esc = D.esc;
  var S = { info: null, day: null, plan: null, m: null, map: null, tab: "summary", route: null };

  function step(n) {
    document.querySelectorAll("#steps li").forEach(function (li) {
      var k = +li.getAttribute("data-step");
      li.classList.toggle("is-on", k === n); li.classList.toggle("is-done", k < n);
      li.querySelector("span").textContent = k < n ? "✓" : k;
    });
    $("#start").hidden = n === 3; $("#desk").hidden = n !== 3;
    $(".hero").hidden = n !== 1; $("#check").hidden = n !== 2;
    $("#btnNew").hidden = n === 1; $("#dayChip").hidden = n === 1;
  }
  function busy(text) { $("#busy").hidden = !text; if (text) $("#busyText").textContent = text; }

  // ---------- 1. Загрузка ----------
  var drop = $("#drop");
  $("#file").addEventListener("change", function (e) { if (e.target.files.length) load(D.loadFiles(e.target.files)); e.target.value = ""; });
  ["dragenter", "dragover"].forEach(function (n) { drop.addEventListener(n, function (e) { e.preventDefault(); drop.classList.add("is-over"); }); });
  ["dragleave", "drop"].forEach(function (n) { drop.addEventListener(n, function () { drop.classList.remove("is-over"); }); });
  drop.addEventListener("drop", function (e) { e.preventDefault(); if (e.dataTransfer.files.length) load(D.loadFiles(e.dataTransfer.files)); });
  document.querySelectorAll("[data-demo]").forEach(function (b) { b.addEventListener("click", function () { load(D.loadDemo(b.getAttribute("data-demo"))); }); });
  $("#btnNew").addEventListener("click", function () {
    if (S.map) { S.map.map.remove(); S.map = null; }
    S.route = null; step(1);
  });

  function load(p) {
    $("#loadErr").hidden = true; busy("Читаю и проверяю данные…");
    p.then(function (r) {
      busy(null); S.info = r.info; S.day = r.day; S.m = D.build(r.day, null, null); checkScreen();
    }, function (e) {
      busy(null);
      $("#loadErr").textContent = e.message + (e.details && e.details.length ? ":\n• " + e.details.slice(0, 8).join("\n• ") : "");
      $("#loadErr").hidden = false;
    });
  }

  // ---------- 2. Проверка ----------
  function checkScreen() {
    var i = S.info, c = i.counts, m = S.m;
    $("#dayChip").textContent = dateText(i.date) + " · " + (i.day_type === "weekday" ? "будний" : "выходной");
    $("#checkTitle").textContent = (c.parks === 1 ? S.day.parks[0].name : c.parks + " " + D.plural(c.parks, "парк", "парка", "парков")) + " на " + dateText(i.date);
    var items = [["Маршруты", c.routes], ["Наряды", c.duties], ["Смены", c.shifts], ["Автобусы", c.vehicles], ["Исправны", m.totals.vehOk], ["Водители", c.drivers]];
    $("#counts").innerHTML = items.map(function (x) { return '<div class="count"><b>' + D.num(x[1]) + "</b><span>" + x[0] + "</span></div>"; }).join("");
    $("#checkRows").innerHTML = m.parkList.map(function (p) {
      var need = p.lineTotal + p.resTotal, lack = need - p.vehOk;
      var shiftsNeed = 0; Object.keys(m.shifts).forEach(function (k) { var s = m.shifts[k]; if (m.duties[s.duty].park === p.id) shiftsNeed++; });
      var badge = lack > 0 ? '<span class="ad-badge ad-badge--warning">не хватает ' + lack + " " + D.plural(lack, "автобуса", "автобусов", "автобусов") + "</span>"
        : '<span class="ad-badge ad-badge--success">автобусов хватает</span>';
      return "<tr><td>" + esc(p.name) + '</td><td class="ad-num">' + p.routes.length + '</td><td class="ad-num">' + p.lineTotal + '</td><td class="ad-num">' + p.resTotal +
        '</td><td class="ad-num">' + p.vehOk + " из " + p.vehAll + '</td><td class="ad-num">' + p.drvWork + "</td><td>" + badge + "</td></tr>";
    }).join("");
    $("#warns").innerHTML = i.warnings.length ? '<details class="warnbox"><summary>Что заметила проверка данных: ' + i.warnings.length + "</summary><ul>" +
      i.warnings.map(function (w) { return "<li>" + esc(w) + "</li>"; }).join("") + "</ul>Это не ошибки: такие нехватки движок и решает.</details>" : "";
    step(2);
  }
  $("#btnCalc").addEventListener("click", function () {
    busy("Движок расставляет автобусы, переброски и водителей…");
    D.calculate(S.info.day_id).then(function (r) {
      busy(null); S.plan = r.plan; step(3); apply(r.state); setTab("summary");
    }, function (e) { busy(null); D.events.toast(e.message, true); });
  });

  function apply(state) {
    S.m = D.build(S.day, S.plan, state);
    if (!S.map) S.map = D.parkMap($("#map"), S.m, { onPark: function (id) { $("#fPark").value = id; $("#fBad").checked = false; setTab("routes"); parkWhy(id); },
      onRoute: function (id) { S.route = id; S.map.selectRoute(id); routeWhy(id); if (S.tab === "duties") showRoute(id); else if (S.tab === "routes") routes(); } });
    else S.map.redraw(S.m);
    if (S.route) S.map.selectRoute(S.route);
    kpis(); summary(); routesFilterInit(); routes(); dutiesInit(); log();
    if (S.route) showRoute(S.route);
  }

  // ---------- 3. Рабочий стол ----------
  function kpis() {
    var m = S.m, t = m.totals, sm = S.plan.summary, n = sm.numbers;
    var noVeh = t.lineTotal - t.lineFilled, noDrv = n.shifts_total - n.shifts_filled;
    var viol = S.plan.violations.length + S.plan.rest_violations.length + ((m.state.violations || []).length);
    $("#kpis").innerHTML =
      '<div class="kpi kpi--main"><span class="kpi__l">Итог расчёта · ' + S.plan.seconds + ' с</span><span class="kpi__v">' + esc(sm.answer) + '</span><span class="kpi__s">' +
      (viol ? "Нарушений норм: " + viol : "Нормы Приказа № 160 соблюдены") + (m.state.log.length ? " · событий: " + m.state.log.length : "") + "</span></div>" +
      kpi("Наряды на линии", t.lineFilled, t.lineTotal, noVeh ? "bad" : "", noVeh ? noVeh + " без автобуса" : "все закрыты") +
      kpi("Смены с водителем", n.shifts_filled, n.shifts_total, noDrv ? "warn" : "", noDrv ? noDrv + " без водителя" : "все закрыты") +
      kpi("Резерв выпущен", t.resFilled, t.resTotal, "", "резерв уходит на линию первым") +
      '<div class="kpi"><span class="kpi__l">Переброски</span><span class="kpi__v">' + m.transfers.length + '</span><span class="kpi__s">' +
      D.plural(m.transfers.length, "автобус работает", "автобуса работают", "автобусов работают") + " в чужом парке</span></div>";
    $("#nLog").textContent = m.state.log.length || "";
  }
  function kpi(label, a, b, tone, sub) {
    return '<div class="kpi' + (tone ? " kpi--" + tone : "") + '"><span class="kpi__l">' + label + '</span><span class="kpi__v">' + D.num(a) + " <small>из " + D.num(b) +
      '</small></span><div class="meter"><i style="width:' + D.pct(a, b) + '%"></i></div><span class="kpi__s">' + sub + "</span></div>";
  }

  document.querySelectorAll(".seg__b").forEach(function (b) { b.addEventListener("click", function () { setTab(b.getAttribute("data-tab")); }); });
  function setTab(t) {
    S.tab = t;
    document.querySelectorAll(".seg__b").forEach(function (b) { b.classList.toggle("is-on", b.getAttribute("data-tab") === t); });
    $("#tabSummary").hidden = t !== "summary"; $("#tabRoutes").hidden = t !== "routes"; $("#tabDuties").hidden = t !== "duties"; $("#tabLog").hidden = t !== "log";
    if (t === "routes") routes();
  }

  // Сводка: что важно, город по часам, парки.
  function summary() {
    var m = S.m, sm = S.plan.summary;
    var risks = [];
    m.routeList.forEach(function (r) {
      if (r.unfilled) risks.push({ tone: "", w: 100 - r.priority * 10 + r.unfilled, r: r, t: "Маршрут " + r.number + ": " + r.unfilled + " " + D.plural(r.unfilled, "наряд", "наряда", "нарядов") + " без автобуса",
        s: D.parkShort(m, r.park) + " · важность " + D.PRIORITY[r.priority] + (r.worst ? " · интервал до " + D.intervalText(r.worst) : "") });
      else if (r.noDriver) risks.push({ tone: "warn", w: 50 - r.priority * 10 + r.noDriver, r: r, t: "Маршрут " + r.number + ": " + r.noDriver + " " + D.plural(r.noDriver, "смена", "смены", "смен") + " без водителя",
        s: D.parkShort(m, r.park) + " · важность " + D.PRIORITY[r.priority] + " · автобус есть, водителя на часть дня нет" });
    });
    risks.sort(function (a, b) { return b.w - a.w; });
    var rd = m.unfilled.filter(function (u) { return u.kind === "shift" && u.type === "reserve"; }).length;
    var html = '<div class="card"><h3 class="card__h">Главное</h3><div class="xp"><div class="xp__a">' + esc(sm.answer) + '</div><ul class="xp__r">' +
      sm.reasons.map(function (r) { return "<li>" + esc(r) + "</li>"; }).join("") + "</ul></div></div>";
    html += '<div class="card"><h3 class="card__h">Требует внимания <span class="caption muted">' + (risks.length ? risks.length + " " + D.plural(risks.length, "маршрут", "маршрута", "маршрутов") + ", сначала важные" : "") + "</span></h3>" +
      (risks.length ? '<div class="risk">' + risks.slice(0, 12).map(function (x) {
        return '<button class="rc' + (x.tone ? " rc--" + x.tone : "") + '" data-route="' + x.r.id + '"><span class="rc__t">' + esc(x.t) + '</span><span class="rc__s">' + esc(x.s) + "</span></button>";
      }).join("") + "</div>" + (risks.length > 12 ? '<span class="caption muted">Остальные - во вкладке «Маршруты»</span>' : "") : '<p class="muted">Все маршруты закрыты полностью.</p>') +
      (rd ? '<span class="caption muted">Ещё ' + rd + " " + D.plural(rd, "смена", "смены", "смен") + " резерва без водителя - на линию это не влияет.</span>" : "") + "</div>";
    html += '<div class="card"><h3 class="card__h">Город по часам <span class="caption muted">автобусов на линии по всем маршрутам</span></h3><div id="cityCov"></div></div>';
    html += '<div class="card"><h3 class="card__h">Парки</h3><div class="ad-table-wrap"><table class="ad-table pt"><thead><tr><th>Парк</th><th>Наряды на линии</th><th class="ad-num">Без автобуса</th><th class="ad-num">Смен без водителя</th><th class="ad-num">Получил</th><th class="ad-num">Отдал</th></tr></thead><tbody>' +
      m.parkList.map(function (p) {
        var miss = p.lineTotal - p.lineFilled, sh = p.unfilled.filter(function (u) { return u.kind === "shift"; }).length;
        return '<tr data-park="' + p.id + '"><td>' + esc(p.name) + '</td><td><div class="pbar" title="' + p.lineFilled + " из " + p.lineTotal + '"><i style="width:' + D.pct(p.lineFilled, p.lineTotal) + '%"></i><b style="width:' + D.pct(miss, p.lineTotal) + '%"></b></div><span class="caption muted">' + p.lineFilled + " из " + p.lineTotal + '</span></td><td class="ad-num">' + (miss || "-") + '</td><td class="ad-num">' + (sh || "-") +
          '</td><td class="ad-num">' + (p.transIn || "-") + '</td><td class="ad-num">' + (p.transOut || "-") + "</td></tr>";
      }).join("") + "</tbody></table></div></div>";
    $("#tabSummary").innerHTML = html;
    var ld = D.lineDuties(m);
    D.coverageChart($("#cityCov"), D.coverage(ld, m.span[0], m.span[1], 10), { height: 130 });
    $("#tabSummary").querySelectorAll("[data-route]").forEach(function (b) { b.addEventListener("click", function () { openRoute(b.getAttribute("data-route")); }); });
    $("#tabSummary").querySelectorAll("[data-park]").forEach(function (b) { b.addEventListener("click", function () { parkWhy(b.getAttribute("data-park")); S.map.select(b.getAttribute("data-park")); }); });
  }

  // Маршруты: таблица с фильтрами.
  function routesFilterInit() {
    var sel = $("#fPark"), v = sel.value;
    sel.innerHTML = '<option value="">Все парки</option>' + S.m.parkList.map(function (p) { return '<option value="' + p.id + '">' + esc(p.name) + "</option>"; }).join("");
    sel.value = v;
  }
  ["#q", "#fPark", "#fBad"].forEach(function (id) { $(id).addEventListener(id === "#q" ? "input" : "change", routes); });
  function routes() {
    if (!S.m || !S.m.state) return;
    var m = S.m, q = $("#q").value.trim().toLowerCase(), park = $("#fPark").value, bad = $("#fBad").checked;
    var list = m.routeList.filter(function (r) {
      if (park && r.park !== park) return false;
      if (bad && !r.unfilled && !r.noDriver) return false;
      return !q || r.number.toLowerCase().indexOf(q) >= 0 || D.parkShort(m, r.park).toLowerCase().indexOf(q) >= 0;
    }).sort(function (a, b) { return b.unfilled - a.unfilled || b.noDriver - a.noDriver || a.priority - b.priority || (a.number < b.number ? -1 : 1); });
    $("#rCount").textContent = list.length + " из " + m.routeList.length;
    $("#rRows").innerHTML = list.map(function (r) {
      var st = r.unfilled ? '<span class="ad-badge ad-badge--danger">нет автобуса</span>' : r.noDriver ? '<span class="ad-badge ad-badge--warning">нет водителя</span>' : '<span class="ad-badge ad-badge--success">закрыт</span>';
      var w = r.worst ? D.intervalText(r.worst) + " в " + D.clock(r.worst.t) : '<span class="muted">как по плану</span>';
      return '<tr tabindex="0" data-route="' + r.id + '"' + (S.route === r.id ? ' aria-selected="true"' : "") + '><td class="num-b">' + esc(r.number) + "</td><td>" + esc(D.parkShort(m, r.park)) + "</td><td>" + D.PRIORITY[r.priority] +
        '</td><td class="ad-num">' + r.filled + " из " + r.total + '</td><td class="ad-num">' + (r.noDriver || "-") + "</td><td>" + w + "</td><td>" + st + "</td></tr>";
    }).join("") || '<tr><td colspan="7" class="muted">Ничего не найдено' + (bad ? " - снимите «Только с проблемами»" : "") + "</td></tr>";
    $("#rRows").querySelectorAll("[data-route]").forEach(function (tr) {
      tr.addEventListener("click", function () { S.route = tr.getAttribute("data-route"); routeWhy(S.route); S.map.selectRoute(S.route); });
      tr.addEventListener("dblclick", function () { openRoute(tr.getAttribute("data-route")); });
    });
  }

  // Наряды маршрута.
  function dutiesInit() {
    var m = S.m, sel = $("#dRoute");
    sel.innerHTML = m.parkList.map(function (p) {
      return '<optgroup label="' + esc(p.name) + '">' + p.routes.map(function (r) {
        return '<option value="' + r.id + '">Маршрут ' + esc(r.number) + (r.unfilled ? " · без автобуса " + r.unfilled : r.noDriver ? " · без водителя " + r.noDriver : "") + "</option>";
      }).join("") + "</optgroup>";
    }).join("");
    if (!S.route) { var first = m.routeList.filter(function (r) { return r.unfilled; })[0] || m.routeList[0]; S.route = first && first.id; }
    sel.value = S.route;
  }
  $("#dRoute").addEventListener("change", function (e) { showRoute(e.target.value); routeWhy(e.target.value); S.map.selectRoute(e.target.value); });
  function openRoute(id) { S.route = id; setTab("duties"); showRoute(id); routeWhy(id); S.map.selectRoute(id); }
  function showRoute(id) {
    var m = S.m, r = m.routes[id]; if (!r) return;
    S.route = id; $("#dRoute").value = id;
    $("#dSub").textContent = D.parkShort(m, r.park) + " · важность " + D.PRIORITY[r.priority] + " · " + r.filled + " из " + r.total + " нарядов · круг " + r.turn + " мин · классы: " + r.classes.map(function (c) { return D.CLASSES[c]; }).join(", ");
    var a = Math.floor(Math.min.apply(null, r.duties.map(function (d) { return d.start; })) / 60) * 60, b = Math.ceil(Math.max.apply(null, r.duties.map(function (d) { return d.end; })) / 60) * 60;
    D.coverageChart($("#dCov"), D.coverage(r.duties, a, b, 10), { height: 100 });
    D.gantt($("#dGantt"), m, r.duties, { onDuty: dutyWhy });
  }

  // ---------- «Почему так» ----------
  function why(title, sub, body) {
    $("#why").innerHTML = '<div class="why__h"><i class="ad-icon ad-icon--chat-circle-dots ad-icon--sm"></i>Почему так</div><div class="why__sub">' + esc(title) + "</div>" +
      (sub ? '<div class="caption muted">' + esc(sub) + "</div>" : "") + '<div id="whyBody">' + (body || '<span class="spinner"></span>') + "</div>";
  }
  function fail(e) { var b = $("#whyBody"); if (b) b.innerHTML = '<p class="err">' + esc(e.message) + "</p>"; }

  function routeWhy(id) {
    var m = S.m, r = m.routes[id], day = S.info.day_id;
    why("Маршрут " + r.number, D.parkShort(m, r.park) + " · " + r.filled + " из " + r.total + " нарядов");
    var bad = r.duties.filter(function (d) { return d.unfilled; })[0];
    var reqs = [D.explain(day, "interval", id, D.hm(r.worst ? r.worst.t : r.duties[0].start + 180))];
    if (bad) reqs.unshift(D.explain(day, "unfilled", bad.id));
    Promise.all(reqs).then(function (xs) {
      $("#whyBody").innerHTML = xs.map(D.explainHtml).join('<hr class="hr">') +
        (S.tab !== "duties" ? '<button class="ad-btn ad-btn--secondary ad-btn--sm" id="toDuties"><i class="ad-icon ad-icon--calendar-dots ad-icon--sm"></i>Наряды маршрута</button>' : "");
      var b = $("#toDuties"); b && b.addEventListener("click", function () { openRoute(id); });
    }, fail);
  }

  function parkWhy(id) {
    var m = S.m, p = m.parks[id];
    var sh = p.unfilled.filter(function (u) { return u.kind === "shift"; }).length;
    why(p.name, (p.address || "") + (p.approx ? " · на карте примерно" : ""),
      '<ul class="xp__r"><li>На линии ' + p.lineFilled + " из " + p.lineTotal + " нарядов, резерв " + p.resFilled + " из " + p.resTotal + "</li><li>Исправных автобусов " + p.vehOk + " из " + p.vehAll +
      ", водителей в работе " + p.drvWork + "</li>" + (sh ? "<li>Смен без водителя: " + sh + "</li>" : "") +
      (p.transIn ? "<li>Получил из других парков: " + p.transIn + " " + D.plural(p.transIn, "автобус", "автобуса", "автобусов") + "</li>" : "") +
      (p.transOut ? "<li>Отдал в другие парки: " + p.transOut + " " + D.plural(p.transOut, "автобус", "автобуса", "автобусов") + " сверх своего выпуска</li>" : "") + "</ul>");
  }

  function dutyWhy(duty, t) {
    var m = S.m, day = S.info.day_id;
    why(D.dutyLabel(m, duty), D.hm(duty.start) + "-" + D.hm(duty.end) + " · класс " + (D.CLASSES[duty.cls] || duty.cls) + " · смен " + duty.shiftCount);
    var sh = duty.shifts.filter(function (s) { return s.start <= t && t < s.end; })[0] || duty.shifts[0];
    var reqs = [duty.segs.length ? D.explain(day, "vehicle", duty.id) : D.explain(day, "unfilled", duty.id)];
    if (sh && duty.segs.length) reqs.push(D.explain(day, sh.segs.length ? "driver" : "unfilled", sh.id).catch(function () { return null; }));
    Promise.all(reqs).then(function (xs) {
      $("#whyBody").innerHTML = xs.filter(Boolean).map(D.explainHtml).join('<hr class="hr">') +
        (duty.segs.length ? '<button class="ad-btn ad-btn--outline ad-btn--sm" id="btnEv"><i class="ad-icon ad-icon--warning ad-icon--sm"></i>Сход, ДТП, неявка…</button>' : "");
      var b = $("#btnEv");
      b && b.addEventListener("click", function () { D.events.open(m, day, duty, t, function (state) { apply(state); }); });
    }, fail);
  }

  // ---------- События ----------
  function log() {
    var m = S.m, l = m.state.log;
    $("#tabLog").innerHTML = '<div class="card"><h3 class="card__h">Журнал дня</h3>' + (l.length ? l.map(function (x) {
      return '<div class="logi"><b>' + esc(x.at) + "</b><div><div>" + esc(D.events.title(m, x)) + '</div><div class="caption muted">Решение: ' + esc(x.title) + "</div></div></div>";
    }).join("") : '<p class="muted">Событий пока нет. Во вкладке «Наряды» выберите наряд и нажмите «Сход, ДТП, неявка» - движок предложит до трёх вариантов замены с ценой.</p>') + "</div>";
  }

  function dateText(s) {
    var p = s.split("-"), mo = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
    return +p[2] + " " + mo[+p[1] - 1] + " " + p[0];
  }
  window.addEventListener("resize", function () { S.map && S.map.resize(); });
  step(1);
})();
