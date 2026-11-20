/* Организационные колонны и карточки истории рабочего пространства. */
(function () {
  "use strict";
  var D = window.Disp, $ = D.$, esc = D.esc;
  var parkKinds = [
    { id: "bus", name: "Автобусы", icon: '<i class="ad-icon ad-icon--bus" aria-hidden="true"></i>' },
    { id: "trolley", name: "Троллейбусы", icon: '<i class="tr-ic tr-ic--trolley" aria-hidden="true"></i>' },
    { id: "tram", name: "Трамваи", icon: '<i class="tr-ic tr-ic--tram" aria-hidden="true"></i>' }
  ];
  function parkKindIcon(kind) {
    var item = parkKinds.find(function (k) { return k.id === kind; });
    return (item || parkKinds[0]).icon;
  }
  function parkCard(park, options) {
    var compact = options.compact, picked = options.picked, reason = options.disabledReason;
    var tag = reason ? "div" : "button";
    var attrs = reason ? ' aria-disabled="true"' + (compact ? ' title="' + esc(reason) + '"' : '') : ' type="button" role="checkbox" aria-checked="' + picked + '" ' +
      (compact ? 'data-pick-park="' : 'data-park="') + esc(park.id) + '"';
    var values = [[park.routes, "маршрутов"], [park.vehicles, "автобусов"],
      [park.release_weekday, "выпуск в будни"], [park.release_weekend, "выпуск в выходные"]];
    var stats = compact ? "" : '<span class="pc__nums">' + values.map(function ([value, label]) {
      return '<span><b>' + (reason || value == null ? "?" : D.num(value)) + '</b>' + label + '</span>';
    }).join("") + '</span>';
    return '<' + tag + ' class="pc' + (compact ? ' pc--compact' : '') + (reason ? ' pc--locked' : '') + (picked ? ' is-picked' : '') + '"' + attrs + '>' +
      '<span class="pc__top"><span class="pc__ic">' + parkKindIcon(park.kind) + '</span>' +
      (reason ? (compact ? '<i class="pc__lock ad-icon ad-icon--lock-simple" role="img" aria-label="' + esc(reason) + '"></i>' : '') :
        '<span class="pc__check" aria-hidden="true"></span>') + '</span>' +
      '<span class="pc__name">' + esc(park.name) + '</span><span class="pc__addr">' + esc(park.address || "") + '</span>' + stats +
      (reason && !compact ? '<span class="pc__go pc__go--off">' + esc(reason) + '</span>' : '') + '</' + tag + '>';
  }
  // Демонстрационное закрепление по конечным из справочника парка №7.
  // Маршруты сохраняют свои идентификаторы и настройки расчёта.
  var demo = {
    P07: [
      ["airport", "Пулково и Авиагородок", ["13", "13а", "39", "39Э", "82Э"]],
      ["port", "Двинская и Кировский завод", ["22", "26", "49", "65", "263"]],
      ["south", "Юго-запад и Московский район", ["3", "11", "59", "90", "141", "203"]],
      ["east", "Невский район и Дыбенко", ["31", "56", "114"]]
    ]
  };
  function column(park, number) {
    var rows = demo[park] || [], n = String(number).trim().toLowerCase();
    var index = rows.findIndex(function (r) { return r[2].some(function (v) { return v.toLowerCase() === n; }); });
    var row = rows[index];
    return { id: park + ":" + (row ? row[0] : "unassigned"), name: row ? row[1] : "Без колонны",
      number: row ? index + 1 : null, demo: !!row, park: park };
  }
  function groupRoutes(routes) {
    var by = new Map();
    routes.forEach(function (r) {
      var park = r.park_id || r.park, c = column(park, r.number);
      if (!by.has(c.id)) by.set(c.id, Object.assign(c, { routes: [] }));
      by.get(c.id).routes.push(r);
    });
    return Array.from(by.values()).sort(function (a, b) {
      return a.park.localeCompare(b.park) || Number(b.demo) - Number(a.demo) || a.number - b.number;
    });
  }
  function columnHeading(group, park) {
    var title = group.number ? "Колонна № " + group.number : group.name;
    var caption = (group.number ? [group.name, park] : [park]).filter(Boolean).join(" · ");
    return '<span class="column-group__title"><strong>' + esc(title) + '</strong>' +
      (caption ? '<small>' + esc(caption) + '</small>' : '') + '</span>';
  }

  var dialog = $("#entityDialog"), title = $("#entityTitle"), content = $("#entityContent");
  var trail = [], serial = 0, returnTo;
  var names = { veh: "Автобус", drv: "Водитель", route: "Маршрут" };
  var marks = { veh: ["Неисправен", "Исправен"], drv: ["Не выходит", "Выходит"], route: ["Выключен", "В работе"] };
  function dateText(date) {
    return new Intl.DateTimeFormat("ru", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" }).format(new Date(date));
  }
  function markTime(value) {
    return new Intl.DateTimeFormat("ru", { hour: "2-digit", minute: "2-digit" }).format(new Date(value));
  }
  function button(kind, id, park, name, label) {
    return '<button type="button" class="record-link" data-record="' + kind + '" data-id="' + esc(id) + '" data-park="' +
      esc(park) + '" data-name="' + esc(name) + '">' + esc(label || name) + '</button>';
  }
  function entityLink(kind, value) {
    return value ? button(kind, value.id, value.park, value.name || value.number,
      kind === "veh" ? "Борт " + value.name : kind === "route" ? "Маршрут " + value.number : value.name) : "—";
  }
  function rows(items) {
    return '<ul class="record-list">' + items.map(function (a) {
      return '<li><time>' + esc(a.from) + '–' + esc(a.to) + '</time><div><div class="record-people">' +
        entityLink("veh", a.bus) + ' <span aria-hidden="true">·</span> ' + entityLink("drv", a.driver) +
        '</div><small>' + (a.route ? entityLink("route", a.route) : "Резерв") + ' · ' + esc(a.duty_id) + '</small></div></li>';
    }).join("") + '</ul>';
  }
  function logs(entries) {
    return '<ul class="record-list">' + entries.map(function (e) {
      return '<li><time>' + esc(e.at || e.from || "—") + '</time><div>' + esc(e.title) +
        (e.reason ? '<br><small>' + esc(e.reason) + '</small>' : '') + '</div></li>';
    }).join("") + '</ul>';
  }
  function paint(item) {
    var token = ++serial;
    var label = item.kind === "drv" ? item.name : names[item.kind] + " " + item.name;
    title.textContent = label;
    $("#entityKind").textContent = item.kind === "drv" ? "Водитель" : "Карточка";
    $("#entityBack").hidden = trail.length < 2;
    $("#entitySubtitle").textContent = item.detail || "";
    var geo = item.kind === "route" ? D.geoFor(item.park, item.name) : null;
    var map = $("#entityGeo");
    map.hidden = !geo;
    if (geo) map.href = "/routes#" + encodeURIComponent(geo.id);
    content.innerHTML = '<p class="muted">Загружаем историю…</p>';
    title.focus({ preventScroll: true });
    content.scrollTop = 0;
    D.api("GET", "/x/assignment-history?kind=" + item.kind + "&park=" + encodeURIComponent(item.park) + "&id=" + encodeURIComponent(item.id)).then(function (data) {
      if (!dialog.open || serial !== token) return;
      var byDate = new Map();
      function bucket(date) {
        if (!byDate.has(date)) byDate.set(date, { days: [], marks: [] });
        return byDate.get(date);
      }
      data.days.forEach(function (day) { bucket(day.date).days.push(day); });
      data.marks.forEach(function (mark) { bucket(mark.date).marks.push(mark); });
      if (!byDate.size) {
        content.innerHTML = '<p class="muted">В сохранённых расчётах и журнале готовности пока нет записей по этой карточке.</p>';
        return;
      }
      content.innerHTML = '<p class="record-note muted">Назначения из расчётов AUTODISP с учётом сохранённых замен. Подтверждения фактических рейсов здесь нет.</p>' +
        Array.from(byDate.keys()).sort().reverse().map(function (date, index) {
          var group = byDate.get(date);
          return '<details class="record-day"' + (index === 0 ? " open" : "") + '><summary>' + dateText(date) + '</summary>' +
            group.days.map(function (day) {
              return (group.days.length > 1 ? '<p class="muted">Расчёт ' + esc(day.day_id) + '</p>' : '') +
                (day.assignments.length ? rows(day.assignments) : '<p class="muted">Нет назначений в сохранённом состоянии.</p>') +
                (day.previous.length ? '<details class="record-changes"><summary>Изменённые назначения из утреннего плана</summary>' + rows(day.previous) + '</details>' : '') +
                (day.log.length ? '<h3>События и правки</h3>' + logs(day.log) : '');
            }).join("") + (group.marks.length ? '<h3>Отметки готовности</h3>' + logs(group.marks.map(function (m) {
              return { at: markTime(m.at), title: marks[item.kind][Number(m.value)] };
            })) : '') + '</details>';
        }).join("");
    }, function () {
      if (dialog.open && serial === token) content.innerHTML = '<p class="err">Не удалось загрузить историю. <button type="button" class="record-link" data-record-retry>Повторить</button></p>';
    });
  }
  function open(item, trigger) {
    if (!dialog.open) {
      trail = [];
      returnTo = trigger;
      dialog.showModal();
    }
    var active = trail[trail.length - 1];
    if (active && active.kind === item.kind && active.id === item.id && active.park === item.park) return;
    trail.push(item);
    paint(item);
  }
  $("#entityClose").addEventListener("click", function () { dialog.close(); });
  $("#entityBack").addEventListener("click", function () { trail.pop(); paint(trail[trail.length - 1]); });
  dialog.addEventListener("click", function (e) { if (e.target === dialog) dialog.close(); });
  dialog.addEventListener("close", function () { serial++; if (returnTo && returnTo.isConnected) returnTo.focus(); });
  document.addEventListener("click", function (e) {
    if (e.target.closest("[data-record-retry]")) { paint(trail[trail.length - 1]); return; }
    var b = e.target.closest("[data-record]");
    if (!b) return;
    open({ kind: b.dataset.record, id: b.dataset.id, park: b.dataset.park, name: b.dataset.name }, b);
  });
  window.Operations = { parkKinds: parkKinds, parkCard: parkCard, groupRoutes: groupRoutes, column: column,
    columnHeading: columnHeading, recordButton: button, openRecord: open };
})();
