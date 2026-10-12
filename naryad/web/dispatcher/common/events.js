/*
 * События внутри дня: сход, ДТП, неявка, опоздание. Окно с вариантами замены от движка
 * (POST events/options) и применение выбранного (POST events/apply).
 */
(function () {
  "use strict";
  var D = window.Disp, esc = D.esc;

  function toast(text, bad) {
    var t = document.createElement("div");
    t.className = "toast" + (bad ? " toast--bad" : "");
    t.textContent = text;
    document.body.appendChild(t);
    window.Motion.enter(t, "up");
    setTimeout(function () { window.Motion.leave(t, "up"); }, bad ? 6000 : 3500);
  }

  function eventTitle(m, ev) {
    if (ev.type === "no_show") return "Неявка водителя " + D.driverName(m, ev.driver_id) + " в " + ev.at;
    if (ev.type === "late") return "Опоздание водителя " + D.driverName(m, ev.driver_id) + " на " + ev.delay_min + " мин, смена с " + ev.at;
    if (!ev.vehicle_id) return ev.title || "Событие в " + ev.at;  // вид, которого этот экран ещё не знает
    var v = "автобуса " + D.vehicleName(m, ev.vehicle_id);
    if (ev.type === "accident") return "ДТП " + v + " в " + ev.at;
    return "Сход " + v + " в " + ev.at + (ev.duration_min ? " на " + ev.duration_min + " мин" : " до конца дня");
  }

  // Окно: выбор события для наряда в момент t, затем варианты.
  function open(m, dayId, duty, t, onApplied) {
    var seg = duty.segs.filter(function (s) { return s.from <= t && t < s.to; })[0] || duty.segs[duty.segs.length - 1];
    var shift = duty.shifts.filter(function (s) { return s.segs.length && s.start <= t && t < s.end; })[0];
    var drv = shift && (shift.segs.filter(function (g) { return g.from <= t && t < g.to; })[0] || shift.segs[0]);
    // Опоздать можно на смену, которая ещё не началась: водитель её смены и время начала.
    // Так в парках и делают: опоздавшего ставят попозже, на его место едет тот, кто уже пришёл.
    var lates = duty.shifts.filter(function (s) { return s.segs.length; }).map(function (s) {
      var g = s.segs.slice().sort(function (a, b) { return a.from - b.from; })[0];
      return { shift: s, id: g.id, at: g.from };
    });
    var box = document.createElement("div");
    box.className = "modal";
    box.innerHTML =
      '<div class="modal__box" role="dialog" aria-modal="true" aria-label="Событие на наряде">' +
      '<div class="modal__head"><div><div class="caption muted">' + esc(D.dutyLabel(m, duty)) + ", " + D.hm(duty.start) + "-" + D.hm(duty.end) + "</div>" +
      '<h3 class="ev__h">Что случилось?</h3></div><button type="button" class="btn-close" data-close aria-label="Закрыть"></button></div>' +
      '<div class="ev__form">' +
      '<label class="ad-field"><span class="ad-field__label">Событие</span><select class="ad-input" name="type">' +
      (seg ? '<option value="breakdown">Сход автобуса ' + esc(D.vehicleName(m, seg.id)) + "</option>" +
        '<option value="accident">ДТП автобуса ' + esc(D.vehicleName(m, seg.id)) + "</option>" : "") +
      (drv ? '<option value="no_show">Неявка водителя ' + esc(D.driverName(m, drv.id)) + "</option>" : "") +
      lates.map(function (x, i) {
        return '<option value="late:' + i + '">Опоздание: ' + esc(D.driverName(m, x.id)) + ", смена с " + D.hm(x.at) + "</option>";
      }).join("") +
      '</select></label>' +
      '<label class="ad-field"><span class="ad-field__label">Время</span><input class="ad-input" name="at" value="' + D.hm(Math.max(duty.start + 10, Math.min(t, duty.end - 30))) + '" pattern="\\d{1,2}:\\d{2}"></label>' +
      '<label class="ad-field" data-dur><span class="ad-field__label">Сход на, мин</span><input class="ad-input" name="dur" type="number" min="0" step="5" placeholder="до конца дня"></label>' +
      '<label class="ad-field" data-late hidden><span class="ad-field__label">Опоздание, мин</span><input class="ad-input" name="late" type="number" min="5" step="5" value="30"></label>' +
      '<button class="ad-btn ad-btn--primary" data-find><i class="ad-icon ad-icon--lightning ad-icon--sm"></i>Найти замену</button></div>' +
      '<div class="ev__opts"></div></div>';
    document.body.appendChild(box);
    window.Motion.enter(box, "fade"); window.Motion.enter(box.firstChild, "scale");
    var close = function () { window.Motion.leave(box.firstChild, "scale"); window.Motion.leave(box, "fade"); document.removeEventListener("keydown", onKey); };
    var onKey = function (e) { if (e.key === "Escape") close(); };
    document.addEventListener("keydown", onKey);
    box.addEventListener("click", function (e) { if (e.target === box || e.target.closest("[data-close]")) close(); });
    if (!seg && !drv && !lates.length) { box.querySelector(".ev__opts").innerHTML = '<p class="muted">На этом наряде нет ни автобуса, ни водителя - событию не к чему относиться.</p>'; box.querySelector("[data-find]").disabled = true; }
    var typeSel = box.querySelector("[name=type]"), atIn = box.querySelector("[name=at]"), atDefault = atIn.value;
    var lateOf = function () { return typeSel.value.indexOf("late:") === 0 ? lates[+typeSel.value.slice(5)] : null; };
    var syncDur = function () {
      var late = lateOf();
      box.querySelector("[data-dur]").hidden = typeSel.value !== "breakdown";
      box.querySelector("[data-late]").hidden = !late;
      atIn.value = late ? D.hm(late.at) : atDefault;  // опоздание - к началу смены
    };
    typeSel.addEventListener("change", syncDur); syncDur();
    box.querySelector("[data-find]").addEventListener("click", function () {
      var late = lateOf(), out = box.querySelector(".ev__opts");
      var ev = { type: late ? "late" : typeSel.value, at: atIn.value.trim() };
      if (late) {
        ev.driver_id = late.id; ev.delay_min = Math.round(+box.querySelector("[name=late]").value);
        if (!(ev.delay_min > 0)) { out.innerHTML = '<p class="ev__err">Укажите, на сколько минут опаздывает водитель</p>'; return; }
      } else if (ev.type === "no_show") ev.driver_id = drv.id; else ev.vehicle_id = seg.id;
      var dur = box.querySelector("[name=dur]").value;
      if (ev.type === "breakdown" && dur) ev.duration_min = +dur;
      out.innerHTML = '<p class="muted"><span class="spinner"></span> Поиск вариантов замены…</p>';
      D.eventOptions(dayId, ev).then(function (res) {
        out.innerHTML = '<div class="caption muted">' + esc(eventTitle(m, ev)) + ". Цена - часы простоя с учётом важности маршрута, меньше - лучше.</div>" +
          res.options.map(function (o, i) {
            return '<div class="opt' + (i === 0 && o.kind !== "none" ? " is-best" : "") + '"><div class="opt__head">' +
              (i === 0 && o.kind !== "none" ? '<span class="ad-badge ad-badge--success">Лучший</span>' : "") +
              '<span class="ad-badge ad-badge--neutral">' + esc(o.kind === "none" && ev.type === "late" ? "Ждать" : D.KINDS[o.kind] || o.kind) + '</span><span class="opt__title">' + esc(o.title) + "</span>" +
              '<button class="ad-btn ad-btn--sm ' + (i === 0 ? "ad-btn--primary" : "ad-btn--outline") + '" data-apply="' + o.index + '">Применить</button></div>' +
              '<div class="opt__nums"><span>Цена <b>' + o.cost + "</b></span><span>Простой <b>" + o.lost_minutes + " мин</b></span>" + (o.note ? "<span>" + esc(o.note) + "</span>" : "") + "</div>" +
              (o.explanation ? '<details><summary class="caption">Почему</summary>' + D.explainHtml(o.explanation) + "</details>" : "") + "</div>";
          }).join("");
        out.querySelectorAll("[data-apply]").forEach(function (b) {
          b.addEventListener("click", function () {
            b.disabled = true;
            D.eventApply(dayId, ev, +b.getAttribute("data-apply")).then(function (state) {
              close();
              toast("Применено: " + eventTitle(m, ev) + (state.violations && state.violations.length ? ". Нарушений: " + state.violations.length : ""), state.violations && state.violations.length);
              onApplied(state, ev);
            }, function (e) { b.disabled = false; toast(e.message, true); });
          });
        });
      }, function (e) { out.innerHTML = '<p class="ev__err">' + esc(e.message) + "</p>"; });
    });
  }

  // Подтверждение: окно с двумя действиями, кнопки названы действием («Уйти без сохранения» / «Остаться»).
  // Отдаёт обещание: true - подтвердил, false - отказался (Escape, клик мимо, вторая кнопка).
  function confirm(o) {
    return new Promise(function (resolve) {
      var box = document.createElement("div");
      box.className = "modal";
      box.innerHTML = '<div class="modal__box modal__box--sm" role="alertdialog" aria-modal="true" aria-labelledby="cfH"><h3 class="ev__h" id="cfH">' + esc(o.title) + "</h3>" +
        (o.text ? '<p class="cf__text">' + esc(o.text) + "</p>" : "") +
        '<div class="cf__actions"><button type="button" class="ad-btn ad-btn--sm btn-plain" data-no>' + esc(o.cancel || "Отмена") + "</button>" +
        '<button type="button" class="ad-btn ad-btn--sm ' + (o.danger ? "ad-btn--danger" : "ad-btn--primary") + '" data-yes>' + esc(o.ok) + "</button></div></div>";
      document.body.appendChild(box);
      window.Motion.enter(box, "fade"); window.Motion.enter(box.firstChild, "scale");
      var done = function (v) { document.removeEventListener("keydown", onKey); window.Motion.leave(box.firstChild, "scale"); window.Motion.leave(box, "fade"); resolve(v); };
      var onKey = function (e) { if (e.key === "Escape") done(false); };
      document.addEventListener("keydown", onKey);
      box.addEventListener("click", function (e) { if (e.target === box || e.target.closest("[data-no]")) done(false); else if (e.target.closest("[data-yes]")) done(true); });
      box.querySelector("[data-no]").focus();
    });
  }

  D.events = { open: open, toast: toast, title: eventTitle, confirm: confirm };
})();
