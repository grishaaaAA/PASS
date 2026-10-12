/*
 * Правка плана диспетчером: поставить или снять автобус и водителя вручную.
 * Кого можно поставить, отдаёт движок (GET edits/options): все они ставятся без нарушений норм.
 * Нужен не из списка - диспетчер вводит номер сам. Если правка нарушает нормы, движок отвечает 409
 * со списком нарушений, и решение «применить всё равно» остаётся за диспетчером: оно уходит с force
 * и причиной и остаётся в журнале.
 */
(function () {
  "use strict";
  var D = window.Disp, esc = D.esc;
  var TIME = /^\d{1,2}:\d{2}$/;

  function names(segs, fn) {
    var out = [];
    segs.forEach(function (s) { var n = fn(s.id); if (out.indexOf(n) < 0) out.push(n); });
    return out.join(", ");
  }

  // Что можно менять на наряде: автобус на всём наряде или водитель одной смены.
  function targets(m, duty) {
    var list = [{ id: duty.id, bus: true, from: duty.start, to: duty.end, has: duty.segs.length > 0,
      label: "Автобус: " + (duty.segs.length ? names(duty.segs, function (id) { return D.vehicleName(m, id); }) : "нет") }];
    duty.shifts.forEach(function (s) {
      list.push({ id: s.id, bus: false, from: s.start, to: s.end, has: s.segs.length > 0, order: s.order,
        label: "Смена " + s.order + ", " + D.hm(s.start) + "-" + D.hm(s.end) + ": " +
          (s.segs.length ? names(s.segs, function (id) { return D.driverName(m, id); }) : "нет водителя") });
    });
    return list;
  }

  // Что показать диспетчеру после правки, без служебных номеров наряда и смены.
  function doneText(m, tg, edit, forced) {
    var text;
    if (edit.type === "set_vehicle") text = "Поставлен автобус " + D.vehicleName(m, edit.vehicle_id);
    else if (edit.type === "set_driver") text = "Поставлен водитель " + D.driverName(m, edit.driver_id) + ", смена " + tg.order;
    else if (edit.type === "clear_vehicle") text = "Автобус снят с наряда";
    else text = "Водитель снят со смены " + tg.order;
    return text + (forced ? ". Нарушений: " + forced : "");
  }

  // Окно «Изменить»: duty - наряд из модели экрана, t - момент, по которому щёлкнули на диаграмме.
  function open(m, dayId, duty, t, onApplied) {
    var list = targets(m, duty);
    var under = duty.shifts.filter(function (s) { return s.start <= t && t < s.end; })[0];
    // Что предложить сначала: у наряда нет автобуса - автобус; в смене под щелчком нет водителя - её водитель
    var pick = 0;
    if (duty.segs.length && under && !under.segs.length) {
      list.forEach(function (x, i) { if (!x.bus && x.id === under.id) pick = i; });
    }
    var box = document.createElement("div");
    box.className = "modal";
    box.innerHTML =
      '<div class="modal__box" role="dialog" aria-modal="true" aria-label="Изменить наряд">' +
      '<div class="modal__head"><div><div class="caption muted">' + esc(D.dutyLabel(m, duty)) + ", " + D.hm(duty.start) + "-" + D.hm(duty.end) + "</div>" +
      '<h3 class="ev__h">Изменить</h3></div><button type="button" class="btn-close" data-close aria-label="Закрыть"></button></div>' +
      '<div class="ev__form ed__form">' +
      '<label class="ad-field"><span class="ad-field__label">Что изменить</span><select class="ad-input" name="what">' +
      list.map(function (x, i) { return '<option value="' + i + '"' + (i === pick ? " selected" : "") + ">" + esc(x.label) + "</option>"; }).join("") +
      "</select></label>" +
      '<label class="ad-field"><span class="ad-field__label">С</span><input class="ad-input" name="from" inputmode="numeric" autocomplete="off"></label>' +
      '<label class="ad-field"><span class="ad-field__label">До</span><input class="ad-input" name="to" inputmode="numeric" autocomplete="off"></label></div>' +
      '<div class="ev__opts" data-cands></div>' +
      '<div class="ed__more">' +
      '<label class="ad-field"><span class="ad-field__label" data-other-label></span><input class="ad-input" name="other" autocomplete="off"></label>' +
      '<label class="ad-field"><span class="ad-field__label">Причина</span><input class="ad-input" name="reason" maxlength="200" placeholder="Необязательно" autocomplete="off"></label></div>' +
      '<p class="ev__err" data-err hidden></p>' +
      '<div class="cf__actions"><button type="button" class="ad-btn ad-btn--sm btn-plain" data-clear></button>' +
      '<button type="button" class="ad-btn ad-btn--primary" data-set disabled>Поставить</button></div></div>';
    document.body.appendChild(box);
    window.Motion.enter(box, "fade"); window.Motion.enter(box.firstChild, "scale");

    var $ = function (sel) { return box.querySelector(sel); };
    var what = $("[name=what]"), fromIn = $("[name=from]"), toIn = $("[name=to]"), other = $("[name=other]"), reason = $("[name=reason]");
    var cands = $("[data-cands]"), err = $("[data-err]"), setBtn = $("[data-set]"), clearBtn = $("[data-clear]");
    var chosen = null, token = 0, asking = false, working = false;
    var cur = function () { return list[+what.value]; };

    var close = function () { window.Motion.leave(box.firstChild, "scale"); window.Motion.leave(box, "fade"); document.removeEventListener("keydown", onKey); };
    var onKey = function (e) { if (e.key === "Escape" && !asking) close(); };
    document.addEventListener("keydown", onKey);
    box.addEventListener("click", function (e) { if (e.target === box || e.target.closest("[data-close]")) close(); });

    function showErr(text) { err.textContent = text || ""; err.hidden = !text; }
    function sync() {
      var tg = cur();
      $("[data-other-label]").textContent = tg.bus ? "Другой автобус" : "Другой водитель";
      other.placeholder = tg.bus ? "Гаражный номер" : "Табельный номер";
      clearBtn.textContent = tg.bus ? "Снять автобус" : "Снять водителя";
      clearBtn.hidden = !tg.has;
      setBtn.disabled = working || !(chosen || other.value.trim());
      clearBtn.disabled = working;
    }

    // Кандидаты на выбранное окно времени. Ответ, пришедший после нового запроса, отбрасывается.
    function load() {
      var tg = cur(), seq = ++token;
      chosen = null; other.value = ""; showErr(""); sync();
      var from = fromIn.value.trim(), to = toIn.value.trim();
      if (!TIME.test(from) || !TIME.test(to)) { cands.innerHTML = '<p class="muted">Укажите время в виде 10:30</p>'; return; }
      cands.innerHTML = '<p class="muted"><span class="spinner"></span> Ищу, кого можно поставить…</p>';
      var q = { from: from, to: to };
      q[tg.bus ? "duty_id" : "shift_id"] = tg.id;
      D.editOptions(dayId, q).then(function (res) {
        if (seq !== token) return;
        var items = tg.bus ? res.vehicles : res.drivers;
        // Класс пишем, только если в списке они разные: список уже отобран под класс наряда
        var mixed = tg.bus && items.some(function (x) { return x.class !== items[0].class; });
        if (!items.length) {
          cands.innerHTML = '<p class="muted">' + (tg.bus ? "Нет автобуса, которого можно поставить без нарушений норм." : "Нет водителя, которого можно поставить без нарушений норм.") + "</p>";
        } else {
          cands.innerHTML = '<div class="caption muted">Можно поставить без нарушений: ' + (res.total > res.shown ? res.shown + " из " + res.total : res.total) + '</div><div class="cand-list">' +
            items.map(function (x) {
              return tg.bus
                ? '<button type="button" class="cand" data-id="' + esc(x.vehicle_id) + '" aria-pressed="false"><b>' + esc(x.board_number) + "</b>" + (mixed ? '<span class="muted">' + esc(D.CLASSES[x.class] || x.class) + "</span>" : "") + "</button>"
                : '<button type="button" class="cand" data-id="' + esc(x.driver_id) + '" aria-pressed="false"><b>' + esc(D.driverName(m, x.driver_id)) + '</b><span class="muted">' + esc(x.tab_number) + "</span>" +
                  (x.own_vehicle ? '<span class="ad-badge ad-badge--neutral">на своём автобусе</span>' : "") + "</button>";
            }).join("") + "</div>";
          window.Motion.stagger(cands.querySelectorAll(".cand"), "up");
        }
      }, function (e) { if (seq === token) cands.innerHTML = '<p class="ev__err">' + esc(e.message) + "</p>"; });
    }

    function resetWindow() {
      var tg = cur();
      fromIn.value = D.hm(tg.from); toIn.value = D.hm(tg.to);
      load();
    }

    cands.addEventListener("click", function (e) {
      var b = e.target.closest(".cand"); if (!b) return;
      chosen = b.getAttribute("data-id"); other.value = ""; showErr("");
      cands.querySelectorAll(".cand").forEach(function (x) {
        var on = x === b;
        x.classList.toggle("is-selected", on); x.setAttribute("aria-pressed", String(on));
      });
      sync();
    });
    other.addEventListener("input", function () {
      if (other.value.trim()) {
        chosen = null;
        cands.querySelectorAll(".cand.is-selected").forEach(function (x) { x.classList.remove("is-selected"); x.setAttribute("aria-pressed", "false"); });
      }
      showErr(""); sync();
    });
    what.addEventListener("change", resetWindow);
    var timer = null;
    [fromIn, toIn].forEach(function (el) {
      el.addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(load, 350); });
    });

    // Кого ставим: выбранный из списка или введённый номер. Введённого ищем среди автобусов и водителей дня.
    function who() {
      if (chosen) return chosen;
      var text = other.value.trim(), tg = cur();
      var pool = tg.bus ? m.vehicles : m.drivers, key = tg.bus ? "board_number" : "tab_number";
      var id = Object.keys(pool).filter(function (k) { return String(pool[k][key]) === text; })[0];
      if (!id) showErr((tg.bus ? "Автобуса с номером " : "Водителя с табельным номером ") + text + " нет в этом дне");
      return id || null;
    }

    function finish(state, edit, tg) {
      close();
      var n = state.new_violations ? state.new_violations.length : 0;
      D.events.toast(doneText(m, tg, edit, n), n > 0);
      onApplied(state);
    }

    function run(edit, tg) {
      working = true; sync();
      D.editApply(dayId, edit).then(function (state) { finish(state, edit, tg); }, function (e) {
        working = false; sync();
        if (e.status === 409 && !edit.force && e.body && e.body.violations && e.body.violations.length) {
          asking = true;
          ask(m, e.body.violations, edit.reason || "").then(function (why) {
            asking = false;
            if (why === null) return;
            var forced = Object.assign({}, edit, { force: true });
            if (why) forced.reason = why; else delete forced.reason;
            run(forced, tg);
          });
          return;
        }
        showErr(e.message);
      });
    }

    function submit(clear) {
      var tg = cur(), edit = { type: (clear ? "clear_" : "set_") + (tg.bus ? "vehicle" : "driver"), from: fromIn.value.trim(), to: toIn.value.trim() };
      if (!TIME.test(edit.from) || !TIME.test(edit.to)) { showErr("Укажите время в виде 10:30"); return; }
      edit[tg.bus ? "duty_id" : "shift_id"] = tg.id;
      if (!clear) {
        var id = who(); if (!id) return;
        edit[tg.bus ? "vehicle_id" : "driver_id"] = id;
      }
      if (reason.value.trim()) edit.reason = reason.value.trim();
      showErr("");
      run(edit, tg);
    }
    setBtn.addEventListener("click", function () { submit(false); });
    clearBtn.addEventListener("click", function () { submit(true); });

    resetWindow();
  }

  // Вопрос «применить всё равно?» со списком нарушений. Отдаёт обещание: текст причины (возможно пустой)
  // при согласии, null при отказе (Escape, клик мимо, «Назад»).
  function ask(m, violations, reason) {
    return new Promise(function (resolve) {
      var box = document.createElement("div");
      box.className = "modal";
      box.innerHTML = '<div class="modal__box modal__box--sm" role="alertdialog" aria-modal="true" aria-labelledby="edH">' +
        '<h3 class="ev__h" id="edH">Правка нарушает нормы. Применить всё равно?</h3>' +
        '<ul class="ed__viol">' + violations.map(function (v) { return "<li>" + esc(D.plain(m, v.text)) + "</li>"; }).join("") + "</ul>" +
        '<label class="ad-field"><span class="ad-field__label">Причина, она останется в журнале</span><input class="ad-input" name="why" maxlength="200" autocomplete="off" value="' + esc(reason) + '"></label>' +
        '<div class="cf__actions"><button type="button" class="ad-btn ad-btn--sm btn-plain" data-no>Назад</button>' +
        '<button type="button" class="ad-btn ad-btn--sm ad-btn--danger" data-yes>Применить всё равно</button></div></div>';
      document.body.appendChild(box);
      window.Motion.enter(box, "fade"); window.Motion.enter(box.firstChild, "scale");
      var done = function (v) {
        document.removeEventListener("keydown", onKey);
        window.Motion.leave(box.firstChild, "scale"); window.Motion.leave(box, "fade");
        resolve(v);
      };
      var onKey = function (e) { if (e.key === "Escape") done(null); };
      document.addEventListener("keydown", onKey);
      box.addEventListener("click", function (e) {
        if (e.target === box || e.target.closest("[data-no]")) done(null);
        else if (e.target.closest("[data-yes]")) done(box.querySelector("[name=why]").value.trim());
      });
      box.querySelector("[data-no]").focus();
    });
  }

  D.edit = { open: open };
})();
