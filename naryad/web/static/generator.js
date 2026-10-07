/*
 * Генератор данных на один день - перенос naryad/data (city.py, days.py, check.py)
 * для страницы, которая работает без сервера. Логика та же, что в Python;
 * справочные данные (парки, справка парка №7, вводные кейса, допущения, имена)
 * подставляет сборка из Python-кода - см. naryad/web/build_artifact.py.
 *
 * Случайные числа свои (не как в Python), поэтому номер набора даёт
 * одинаковые данные внутри страницы, но не совпадает с выгрузкой из Python.
 * Формат данных - docs/CONTRACT.md; соответствие проверяет tests/test_artifact.py.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory;
  else root.AutoDispGenerator = factory(root.AUTODISP_DATA);
})(typeof self !== "undefined" ? self : this, function (D) {
  "use strict";
  var CLASSES = D.classes, LABELS = D.class_labels, DYN = D.dynamics, CASE = D.case, PARK7 = D.park7;
  var PLATE_LETTERS = "АВЕКМНОРСТУХ", PLATE_REGIONS = ["78", "98", "178", "198"];
  var BIG_CLASS_FILL = 0.85, SPEED_KMH = 18, LAYOVER_MIN = 20, EXTRA_PERMIT_BOOST = 1.15, DRIVERS_PER_VEHICLE = 2;
  var DAY_TYPES = ["weekday", "weekend"], DAY_CODES = { weekday: "WD", weekend: "WE" };
  var DUTY_TIMES = { 1: [[330, 540], [480, 570]], 2: [[300, 420], [900, 1020]], 3: [[290, 340], [1140, 1230]] };
  var INTERNAL = { maintenance_offset: 1, rota_offset: 1, vacation_start: 1 };
  var WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"];
  var PARK_NAMES = {};
  D.city_parks.forEach(function (p) { PARK_NAMES[p[0]] = p[1]; });

  /* --- Случайные числа с номером набора: sfc32 из хеша строки --- */
  function Rng(seedText) {
    var h1 = 1779033703, h2 = 3144134277, h3 = 1013904242, h4 = 2773480762, s = String(seedText);
    for (var i = 0; i < s.length; i++) {
      var k = s.charCodeAt(i);
      h1 = h2 ^ Math.imul(h1 ^ k, 597399067); h2 = h3 ^ Math.imul(h2 ^ k, 2869860233);
      h3 = h4 ^ Math.imul(h3 ^ k, 951274213); h4 = h1 ^ Math.imul(h4 ^ k, 2716044179);
    }
    h1 = Math.imul(h3 ^ (h1 >>> 18), 597399067); h2 = Math.imul(h4 ^ (h2 >>> 22), 2869860233);
    h3 = Math.imul(h1 ^ (h3 >>> 17), 951274213); h4 = Math.imul(h2 ^ (h4 >>> 19), 2716044179);
    var a = (h1 ^ h2 ^ h3 ^ h4) >>> 0, b = (h2 ^ h1) >>> 0, c = (h3 ^ h1) >>> 0, d = (h4 ^ h1) >>> 0;
    this.random = function () {
      a >>>= 0; b >>>= 0; c >>>= 0; d >>>= 0;
      var t = (a + b) | 0; a = b ^ (b >>> 9); b = (c + (c << 3)) | 0; c = (c << 21) | (c >>> 11);
      d = (d + 1) | 0; t = (t + d) | 0; c = (c + t) | 0;
      return (t >>> 0) / 4294967296;
    };
    for (var n = 0; n < 15; n++) this.random();
  }
  Rng.prototype.below = function (n) { return Math.floor(this.random() * n); };
  Rng.prototype.randrange = function (lo, hi, step) {
    step = step || 1;
    return lo + step * this.below(Math.ceil((hi - lo) / step));
  };
  Rng.prototype.randint = function (lo, hi) { return this.randrange(lo, hi + 1); };
  Rng.prototype.choice = function (arr) { return arr[this.below(arr.length)]; };
  Rng.prototype.uniform = function (lo, hi) { return lo + (hi - lo) * this.random(); };
  Rng.prototype.shuffle = function (arr) {
    for (var i = arr.length - 1; i > 0; i--) { var j = this.below(i + 1), t = arr[i]; arr[i] = arr[j]; arr[j] = t; }
    return arr;
  };
  Rng.prototype.sample = function (arr, k) { return this.shuffle(arr.slice()).slice(0, k); };
  Rng.prototype.lognormvariate = function (mu, sigma) {
    var u = 1 - this.random(), v = this.random();
    return Math.exp(mu + sigma * Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v));
  };

  /* --- Помощники, как в Python --- */
  function pyRound(x) {  // округление как round() в Python: половина - к чётному
    var r = Math.round(x);
    if (Math.abs(x - Math.trunc(x)) === 0.5) r = 2 * Math.round(x / 2);
    return r;
  }
  function round2(x) { return Math.round(x * 100) / 100; }
  function pad(n, w) { var s = String(n); while (s.length < w) s = "0" + s; return s; }
  function hhmm(m) { return pad(Math.floor(m / 60), 2) + ":" + pad(m % 60, 2); }
  function minutes(t) { var p = t.split(":"); return +p[0] * 60 + +p[1]; }
  function sum(arr) { return arr.reduce(function (s, x) { return s + x; }, 0); }
  function parkNumber(id) { return parseInt(id.replace(/\D/g, ""), 10); }
  function range(n) { var a = []; for (var i = 0; i < n; i++) a.push(i); return a; }

  /* Делит целое пропорционально весам, сумма точно сохраняется. keys - порядок. */
  function splitTotal(total, keys, weightOf) {
    var weights = keys.map(weightOf), wsum = sum(weights), result = {};
    if (wsum <= 0) { keys.forEach(function (k) { result[k] = 0; }); return result; }
    var exact = weights.map(function (w) { return total * w / wsum; });
    var base = exact.map(function (e) { return Math.trunc(e); });
    var rest = total - sum(base);
    var order = range(keys.length).sort(function (i, j) { return (exact[j] - base[j]) - (exact[i] - base[i]); });
    order.slice(0, rest).forEach(function (i) { base[i] += 1; });
    keys.forEach(function (k, i) { result[k] = base[i]; });
    return result;
  }
  function fleetByClass(spec) {
    var c = {};
    spec.fleet.forEach(function (f) { c[f[1]] = (c[f[1]] || 0) + f[3]; });
    return c;
  }

  /* --- Парки: вводные -> подробное описание (city.py) --- */
  function routeClasses(spec) {
    var rows = spec.routes, fleet = fleetByClass(spec);
    var present = CLASSES.slice().reverse().filter(function (c) { return fleet[c]; });
    var order = range(rows.length).sort(function (i, j) { return rows[j][1] - rows[i][1]; });
    var classOf = {};
    present.slice(0, -1).forEach(function (cls) {
      var cap = BIG_CLASS_FILL * fleet[cls] * spec.tech_readiness, used = 0;
      order.forEach(function (i) {
        var need = rows[i][1];
        if (classOf[i] !== undefined || need === 0) return;
        if (used + need <= cap) { classOf[i] = cls; used += need; }
      });
    });
    order.forEach(function (i) { if (classOf[i] === undefined) classOf[i] = present[present.length - 1]; });
    return rows.map(function (r, i) { return [r[0], r[1], r[2], r[3], classOf[i]]; });
  }
  function sheetPark() {
    var spec = JSON.parse(JSON.stringify(PARK7));
    spec.routes = routeClasses(spec);
    return spec;
  }
  function expectedReserve(spec, dayType) {
    var fleet = fleetByClass(spec), need = {}, col = dayType === "weekday" ? 1 : 2;
    spec.routes.forEach(function (r) { need[r[4]] = (need[r[4]] || 0) + r[col]; });
    var keys = Object.keys(fleet);
    var spare = {};
    keys.forEach(function (c) { spare[c] = Math.max(0, fleet[c] * spec.tech_readiness - (need[c] || 0)); });
    if (!keys.some(function (c) { return spare[c]; })) spare = fleet;
    return splitTotal(spec.reserve[dayType], keys, function (c) { return spare[c]; });
  }
  function modelsLikePark7(cls, count) {
    var sample = PARK7.fleet.filter(function (f) { return f[1] === cls; });
    if (!sample.length) return [["средний класс, модель не задана", cls, "diesel", count]];
    var split = splitTotal(count, range(sample.length), function (i) { return sample[i][3]; });
    return range(sample.length).filter(function (i) { return split[i]; })
      .map(function (i) { return [sample[i][0], cls, sample[i][2], split[i]]; });
  }

  function defaultSetup() {
    var n = CASE.park_count, release = CASE.release_per_park, mix = CASE.class_mix;
    var routes = splitTotal(CASE.routes_total, range(n), function () { return 1; });
    var prev = { medium: 0, big: 0, extra_big: 0 }, setup = [];
    D.city_parks.slice(0, n).forEach(function (p, i) {
      var total = splitTotal((i + 1) * release, CLASSES, function (c) { return mix[c] || 0; });
      var classes = {};
      CLASSES.forEach(function (c) { classes[c] = total[c] - prev[c]; });
      setup.push({ id: p[0], classes: classes, routes: routes[i], readiness: CASE.tech_readiness, sheet: false });
      prev = total;
    });
    return setup;
  }

  function parkSpecs(setup, rng) {
    var known = {}, seen = {}, factor = CASE.weekend_factor, specs = [];
    D.city_parks.forEach(function (p) { known[p[0]] = p; });
    setup.forEach(function (entry) {
      var id = entry.id;
      if (!known[id] || seen[id]) throw new Error("Парк " + id + ": такого нет или он указан дважды");
      seen[id] = true;
      var name = known[id][1], address = known[id][2];
      if (entry.sheet) {
        if (id !== PARK7.id) throw new Error(name + ": справки по этому парку нет");
        specs.push(sheetPark());
        return;
      }
      var byClass = {};
      CLASSES.forEach(function (c) { byClass[c] = parseInt(entry.classes[c] || 0, 10); });
      var release = sum(CLASSES.map(function (c) { return byClass[c]; }));
      if (release <= 0) throw new Error(name + ": выпуск должен быть больше нуля");
      var reserveTotal = pyRound(release * CASE.reserve_share);
      var reserve = splitTotal(reserveTotal, CLASSES, function (c) { return byClass[c]; });
      var line = {};
      CLASSES.forEach(function (c) { line[c] = byClass[c] - reserve[c]; });
      var present = CLASSES.filter(function (c) { return line[c] > 0; });
      var nRoutes = parseInt(entry.routes, 10);
      if (nRoutes < present.length) {
        throw new Error(name + ": маршрутов " + nRoutes + ", а классов автобусов " + present.length +
          " - на каждый класс нужен хотя бы один маршрут");
      }
      var perClass = splitTotal(nRoutes - present.length, present, function (c) { return line[c]; });
      var rows = [];
      present.forEach(function (cls) {
        var k = perClass[cls] + 1;
        if (line[cls] < k) {
          throw new Error(name + ": класс " + cls + " - нарядов на маршрутах " + line[cls] + ", а маршрутов " + k +
            "; уменьшите число маршрутов");
        }
        var weights = range(k).map(function () { return rng.lognormvariate(0, 0.6); });
        var sizes = splitTotal(line[cls] - k, range(k), function (j) { return weights[j]; });
        range(k).forEach(function (j) {
          var weekday = sizes[j] + 1;
          rows.push([null, weekday, pyRound(weekday * factor), round2(rng.uniform(8, 25)), cls]);
        });
      });
      rows.sort(function (x, y) { return y[1] - x[1]; });
      rows.forEach(function (r, j) { r[0] = "П" + parkNumber(id) + "-" + pad(j + 1, 2); });
      var reserveWeekend = pyRound(reserveTotal * factor);
      var fleet = [];
      CLASSES.forEach(function (c) {
        if (byClass[c]) fleet = fleet.concat(modelsLikePark7(c, Math.ceil(byClass[c] / CASE.release_coef)));
      });
      var releaseByDay = { weekday: release, weekend: sum(rows.map(function (r) { return r[2]; })) + reserveWeekend };
      var shiftMix = {};
      DAY_TYPES.forEach(function (d) {
        shiftMix[d] = splitTotal(releaseByDay[d], [1, 2, 3], function (k) { return CASE.shift_mix[k]; });
      });
      specs.push({
        id: id, name: name, address: address, list_count: sum(fleet.map(function (f) { return f[3]; })),
        tech_readiness: +entry.readiness, release: releaseByDay,
        reserve: { weekday: reserveTotal, weekend: reserveWeekend }, shift_mix: shiftMix, fleet: fleet, routes: rows
      });
    });
    if (!specs.length) throw new Error("Выберите хотя бы один парк");
    return specs;
  }

  /* --- Подробное описание -> записи (city.py) --- */
  function parkRecord(spec) {
    return {
      id: spec.id, name: spec.name, address: spec.address || null, lat: spec.lat || null, lon: spec.lon || null,
      state: "working", list_count: spec.list_count, tech_readiness: spec.tech_readiness,
      release_weekday: spec.release.weekday, release_weekend: spec.release.weekend,
      reserve_weekday: spec.reserve.weekday, reserve_weekend: spec.reserve.weekend
    };
  }
  function routeRecords(spec, rng) {
    var rows = spec.routes;
    var busy = range(rows.length).filter(function (i) { return rows[i][1] > 0; })
      .sort(function (i, j) { return rows[j][1] - rows[i][1]; });
    var third = busy.length / 3, priority = {};
    busy.forEach(function (i, rank) { priority[i] = rank < third ? 1 : rank < 2 * third ? 2 : 3; });
    return rows.map(function (r, i) {
      var length = r[3], source = spec.source ? "справка" : "синтетика";
      if (length === null) { length = round2(rng.uniform(10, 25)); source = "синтетика"; }
      var turnaround = 2 * length / SPEED_KMH * 60 + LAYOVER_MIN;
      return {
        id: spec.id + "-R" + pad(i + 1, 2), park_id: spec.id, number: r[0], allowed_classes: [r[4]],
        priority: priority[i] || 3, length_km: length, length_source: source,
        turnaround_min: pyRound(turnaround / 5) * 5, duties_weekday: r[1], duties_weekend: r[2]
      };
    });
  }
  function plate(rng, used) {
    for (;;) {
      var p = rng.choice(PLATE_LETTERS) + rng.choice(PLATE_LETTERS) + " " + pad(rng.randrange(1, 1000), 3) + " " +
        rng.choice(PLATE_REGIONS);
      if (!used[p]) { used[p] = true; return p; }
    }
  }
  function vehicleRecords(spec, routes, rng, used) {
    var number = parkNumber(spec.id), vehicles = [];
    spec.fleet.forEach(function (f) {
      for (var c = 0; c < f[3]; c++) {
        var n = vehicles.length + 1;
        vehicles.push({
          id: spec.id + "-V" + pad(n, 4), board_number: String(number * 1000 + n), plate: plate(rng, used),
          model: f[0], "class": f[1], fuel: f[2], park_id: spec.id, home_route_id: null,
          maintenance_offset: rng.randrange(0, 1000)
        });
      }
    });
    CLASSES.forEach(function (cls) {
      var pool = rng.shuffle(vehicles.filter(function (v) { return v["class"] === cls; }));
      routes.filter(function (r) { return r.allowed_classes[0] === cls; })
        .sort(function (x, y) { return y.duties_weekday - x.duties_weekday; })
        .forEach(function (route) {
          for (var k = 0; k < route.duties_weekday && pool.length; k++) pool.pop().home_route_id = route.id;
        });
    });
    return vehicles;
  }
  function dutyRecords(spec, routes, dayType, rng) {
    var code = DAY_CODES[dayType], plan = [];
    routes.forEach(function (route) {
      for (var k = 0; k < route["duties_" + dayType]; k++) {
        plan.push([route.id + "-" + code + pad(k + 1, 2), "line", route.id, route.allowed_classes[0]]);
      }
    });
    var reserve = expectedReserve(spec, dayType), n = 0;
    CLASSES.forEach(function (cls) {
      for (var k = 0; k < (reserve[cls] || 0); k++) {
        n += 1;
        plan.push([spec.id + "-RES-" + code + pad(n, 2), "reserve", null, cls]);
      }
    });
    var mix = spec.shift_mix[dayType], counts = [];
    [1, 2, 3].forEach(function (k) { for (var i = 0; i < mix[k]; i++) counts.push(k); });
    if (counts.length !== plan.length) {
      throw new Error(spec.name + ": нарядов по видам смен " + counts.length + ", а нарядов на маршрутах и в резерве " +
        plan.length + " (" + dayType + ")");
    }
    rng.shuffle(counts);
    var duties = [], shifts = [];
    plan.forEach(function (p, idx) {
      var k = counts[idx], t = DUTY_TIMES[k];
      var start = rng.randrange(t[0][0], t[0][1] + 1, 5), end = start + rng.randrange(t[1][0], t[1][1] + 1, 5);
      duties.push({ id: p[0], park_id: spec.id, type: p[1], route_id: p[2], vehicle_class: p[3], day_type: dayType,
        start: hhmm(start), end: hhmm(end), shift_count: k });
      var part = Math.floor(Math.floor((end - start) / k) / 5) * 5;
      for (var i = 0; i < k; i++) {
        shifts.push({ id: p[0] + "-S" + (i + 1), duty_id: p[0], order: i + 1, start: hhmm(start + i * part),
          end: hhmm(i === k - 1 ? end : start + (i + 1) * part),
          start_place: i === 0 || p[1] === "reserve" ? "park" : "line" });
      }
    });
    return { duties: duties, shifts: shifts };
  }
  function driversPerShift() {
    var cycle = DYN.work_days + DYN.rest_days;
    var absent = DYN.vacation_days / 365 + DYN.sick_start * (DYN.sick_days[0] + DYN.sick_days[1]) / 2;
    return DYN.working_spare / (DYN.work_days / cycle * (1 - absent));
  }
  function fullName(rng) {
    var surname = rng.choice(D.names.surnames), pat = rng.choice(D.names.patronymics);
    if (rng.random() < D.names.female_share) return surname + "а " + rng.choice(D.names.female) + " " + pat[1];
    return surname + " " + rng.choice(D.names.male) + " " + pat[0];
  }
  function driverRecords(spec, vehicles, rng) {
    var weekdayShifts = sum([1, 2, 3].map(function (k) { return k * spec.shift_mix.weekday[k]; }));
    var total = pyRound(weekdayShifts * driversPerShift());
    var fleet = fleetByClass(spec), fleetTotal = sum(Object.keys(fleet).map(function (c) { return fleet[c]; }));
    var present = CLASSES.slice().reverse().filter(function (c) { return fleet[c]; }), tops = [], left = total;
    present.slice(0, -1).forEach(function (cls) {
      var k = Math.min(left, pyRound(total * fleet[cls] / fleetTotal * EXTRA_PERMIT_BOOST));
      for (var i = 0; i < k; i++) tops.push(cls);
      left -= k;
    });
    for (var i = 0; i < left; i++) tops.push(present[present.length - 1]);
    rng.shuffle(tops);
    var number = parkNumber(spec.id), cycle = DYN.work_days + DYN.rest_days;
    var offsets = rng.shuffle(range(total).map(function (n) { return n % cycle; }));
    var drivers = range(total).map(function (n) {
      return {
        id: spec.id + "-D" + pad(n + 1, 4), tab_number: pad(number, 2) + pad(n + 1, 4), full_name: fullName(rng),
        park_id: spec.id, classes: CLASSES.slice(0, CLASSES.indexOf(tops[n]) + 1), home_vehicle_id: null,
        rota_offset: offsets[n], vacation_start: rng.randrange(0, 365)
      };
    });
    var pools = {};
    drivers.forEach(function (d) { var c = d.classes[d.classes.length - 1]; (pools[c] = pools[c] || []).push(d); });
    rng.shuffle(vehicles.filter(function (v) { return v.home_route_id; })).forEach(function (v) {
      for (var t = 0; t < DRIVERS_PER_VEHICLE; t++) {
        var from = CLASSES.slice(CLASSES.indexOf(v["class"]));
        for (var j = 0; j < from.length; j++) {
          if (pools[from[j]] && pools[from[j]].length) { pools[from[j]].pop().home_vehicle_id = v.id; break; }
        }
      }
    });
    return drivers;
  }
  function buildCity(setup, seed) {
    var rng = new Rng("city-" + seed), used = {};
    var city = { parks: [], routes: [], duties: [], shifts: [], vehicles: [], drivers: [] };
    parkSpecs(setup, rng).forEach(function (spec) {
      var routes = routeRecords(spec, rng), vehicles = vehicleRecords(spec, routes, rng, used);
      city.parks.push(parkRecord(spec));
      city.routes = city.routes.concat(routes);
      city.vehicles = city.vehicles.concat(vehicles);
      DAY_TYPES.forEach(function (d) {
        var r = dutyRecords(spec, routes, d, rng);
        city.duties = city.duties.concat(r.duties);
        city.shifts = city.shifts.concat(r.shifts);
      });
      city.drivers = city.drivers.concat(driverRecords(spec, vehicles, rng));
    });
    return city;
  }

  /* --- Состояние дня (days.py): утро, первый день --- */
  function ordinal(y, m, d) { return Date.UTC(y, m - 1, d) / 86400000 + 719163; }
  function dayInfo(iso) {
    var p = iso.split("-"), y = +p[0], m = +p[1], d = +p[2];
    var weekday = (new Date(Date.UTC(y, m - 1, d)).getUTCDay() + 6) % 7;  // 0 - понедельник
    return { iso: iso, ord: ordinal(y, m, d), yday: ordinal(y, m, d) - ordinal(y, 1, 1), weekday: weekday,
      type: weekday >= 5 ? "weekend" : "weekday" };
  }
  function initialState(city, seed) {
    var rng = new Rng("start-" + seed), vehicles = {}, drivers = {};
    city.vehicles.forEach(function (v) { vehicles[v.id] = ["ok", 0]; });
    city.parks.forEach(function (park) {
      var own = city.vehicles.filter(function (v) { return v.park_id === park.id; });
      var byClass = {};
      CLASSES.forEach(function (c) { byClass[c] = own.filter(function (v) { return v["class"] === c; }); });
      var broken = splitTotal(pyRound(own.length * (1 - park.tech_readiness)), CLASSES,
        function (c) { return byClass[c].length; });
      CLASSES.forEach(function (c) {
        var chosen = rng.sample(byClass[c], broken[c]), repair = pyRound(chosen.length * DYN.repair_share);
        chosen.forEach(function (v, k) {
          vehicles[v.id] = k < repair ? ["repair", rng.randint(1, DYN.repair_days[1])] : ["maintenance", 1];
        });
      });
    });
    var share = DYN.sick_start * (DYN.sick_days[0] + DYN.sick_days[1]) / 2;
    city.drivers.forEach(function (d) { drivers[d.id] = rng.random() < share ? rng.randint(1, DYN.sick_days[1]) : 0; });
    return { vehicles: vehicles, drivers: drivers };
  }
  function strip(item) {
    var out = {};
    Object.keys(item).forEach(function (k) { if (!INTERNAL[k]) out[k] = item[k]; });
    return out;
  }
  function driverStatus(d, day, sickLeft) {
    var cycle = DYN.work_days + DYN.rest_days;
    if (((day.yday - d.vacation_start) % 365 + 365) % 365 < DYN.vacation_days) return "vacation";
    if (sickLeft > 0) return "sick";
    if ((day.ord + d.rota_offset) % cycle >= DYN.work_days) return "day_off";
    return "work";
  }
  function snapshot(city, state, day, seed) {
    var duties = city.duties.filter(function (d) { return d.day_type === day.type; });
    var ids = {};
    duties.forEach(function (d) { ids[d.id] = true; });
    var rng = new Rng("medical-" + seed + "-" + day.iso);
    return {
      parks: city.parks, routes: city.routes, duties: duties,
      shifts: city.shifts.filter(function (s) { return ids[s.duty_id]; }),
      vehicles: city.vehicles.map(function (v) {
        var st = state.vehicles[v.id], item = strip(v);
        item.condition = st[0];
        item.repair_days_left = st[0] !== "ok" ? st[1] : null;
        return item;
      }),
      drivers: city.drivers.map(function (d) {
        var status = driverStatus(d, day, state.drivers[d.id]), roll = rng.random(), item = strip(d);
        item.schedule = status;
        item.medical = status !== "work" ? null : roll < DYN.medical_passed ? "passed" :
          roll < DYN.medical_passed + DYN.medical_failed ? "failed" : "pending";
        return item;
      })
    };
  }

  function generate(setup, iso, seed) {
    var day = dayInfo(iso), city = buildCity(setup, seed);
    var data = { meta: {
      format_version: D.format_version, generator: "AUTODISP, страница генератора", preset: "case",
      inputs: { parks_setup: setup }, seed: seed, date: iso, day_type: day.type, day_index: 0, moment: "morning",
      source: CASE.source, assumptions: D.assumptions.concat(D.case_assumptions)
    } };
    var snap = snapshot(city, initialState(city, seed), day, seed);
    Object.keys(snap).forEach(function (k) { data[k] = snap[k]; });
    return data;
  }

  /* --- Проверка (check.py): то, что видит диспетчер, и целостность --- */
  function check(data) {
    var errors = [], warnings = [], idx = {};
    ["parks", "routes", "duties", "shifts", "vehicles", "drivers"].forEach(function (e) {
      idx[e] = {};
      data[e].forEach(function (x) {
        if (idx[e][x.id]) errors.push(e + ": номер " + x.id + " повторяется");
        idx[e][x.id] = x;
      });
    });
    function ref(e, item, f, target) {
      if (item[f] !== null && !target[item[f]]) errors.push(e + " " + item.id + ": " + f + " = " + item[f] + ", такого нет");
    }
    data.routes.forEach(function (r) { ref("routes", r, "park_id", idx.parks); });
    data.vehicles.forEach(function (v) {
      ref("vehicles", v, "park_id", idx.parks);
      ref("vehicles", v, "home_route_id", idx.routes);
      if ((v.condition === "ok") !== (v.repair_days_left === null)) {
        errors.push("vehicles " + v.id + ": срок ремонта указывается только у неисправного автобуса");
      }
    });
    data.drivers.forEach(function (d) {
      ref("drivers", d, "park_id", idx.parks);
      ref("drivers", d, "home_vehicle_id", idx.vehicles);
      var v = idx.vehicles[d.home_vehicle_id];
      if (v && d.classes.indexOf(v["class"]) < 0) errors.push("drivers " + d.id + ": нет допуска к классу " + v["class"]);
      if (d.schedule !== "work" && d.medical !== null) errors.push("drivers " + d.id + ": не работает сегодня, но указан медосмотр");
    });
    var shiftsOf = {};
    data.shifts.forEach(function (s) { ref("shifts", s, "duty_id", idx.duties); (shiftsOf[s.duty_id] = shiftsOf[s.duty_id] || []).push(s); });
    data.duties.forEach(function (d) {
      ref("duties", d, "park_id", idx.parks);
      if (d.type === "line") {
        if (d.route_id === null) errors.push("duties " + d.id + ": линейный наряд без маршрута");
        else {
          ref("duties", d, "route_id", idx.routes);
          var r = idx.routes[d.route_id];
          if (r && r.allowed_classes.indexOf(d.vehicle_class) < 0) errors.push("duties " + d.id + ": класс не допущен на маршрут " + r.number);
        }
      } else if (d.route_id !== null) errors.push("duties " + d.id + ": у резервного наряда не должно быть маршрута");
      var own = (shiftsOf[d.id] || []).slice().sort(function (a, b) { return a.order - b.order; });
      if (own.length !== d.shift_count) { errors.push("duties " + d.id + ": смен " + own.length + ", а указано " + d.shift_count); return; }
      var point = minutes(d.start);
      for (var i = 0; i < own.length; i++) {
        if (minutes(own[i].start) !== point || minutes(own[i].end) <= point) { errors.push("duties " + d.id + ": смены идут не встык"); return; }
        point = minutes(own[i].end);
      }
      if (point !== minutes(d.end)) errors.push("duties " + d.id + ": смены не доходят до конца наряда");
    });
    var dayType = data.meta.day_type;
    data.parks.forEach(function (park) {
      var pid = park.id;
      var vs = data.vehicles.filter(function (v) { return v.park_id === pid; });
      if (vs.length !== park.list_count) errors.push("парк " + pid + ": по документам " + park.list_count + " автобусов, в списке " + vs.length);
      var ds = data.duties.filter(function (d) { return d.park_id === pid; });
      var line = ds.filter(function (d) { return d.type === "line"; }).length, reserve = ds.length - line;
      if (reserve !== park["reserve_" + dayType]) warnings.push("парк " + pid + ": резерв " + reserve + ", по плану " + park["reserve_" + dayType]);
      if (ds.length !== park["release_" + dayType]) warnings.push("парк " + pid + ": нарядов " + ds.length + ", выпуск по плану " + park["release_" + dayType]);
      CLASSES.forEach(function (c) {
        var have = vs.filter(function (v) { return v["class"] === c && v.condition === "ok"; }).length;
        var need = ds.filter(function (d) { return d.vehicle_class === c; }).length;
        if (need > have) warnings.push("парк " + pid + ": класс " + c + " - исправных " + have + ", нужно " + need + ", не хватает " + (need - have));
      });
      var ids = {};
      ds.forEach(function (d) { ids[d.id] = true; });
      var needShifts = data.shifts.filter(function (s) { return ids[s.duty_id]; }).length;
      var working = data.drivers.filter(function (d) { return d.park_id === pid && d.schedule === "work" && d.medical !== "failed"; }).length;
      if (working < needShifts) warnings.push("парк " + pid + ": смен " + needShifts + ", водителей готово " + working + ", не хватает " + (needShifts - working));
    });
    return { errors: errors, warnings: warnings };
  }

  function dayNumbers(data, pid) {
    function own(arr) { return arr.filter(function (x) { return !pid || x.park_id === pid; }); }
    var vs = own(data.vehicles), ds = own(data.duties), drs = own(data.drivers), ids = {};
    ds.forEach(function (d) { ids[d.id] = true; });
    var ok = vs.filter(function (v) { return v.condition === "ok"; });
    var line = ds.filter(function (d) { return d.type === "line"; }).length;
    var n = {
      vehicles: vs.length, ok: ok.length,
      repair: vs.filter(function (v) { return v.condition === "repair"; }).length,
      maintenance: vs.filter(function (v) { return v.condition === "maintenance"; }).length,
      line: line, reserve: ds.length - line, idle: ok.length - ds.length, drivers: drs.length,
      shifts: data.shifts.filter(function (s) { return ids[s.duty_id]; }).length,
      medical_failed: drs.filter(function (d) { return d.medical === "failed"; }).length
    };
    ["work", "day_off", "sick", "vacation"].forEach(function (st) {
      n[st] = drs.filter(function (d) { return d.schedule === st; }).length;
    });
    n.ready = n.work - n.medical_failed;
    return n;
  }

  /* --- То, что раньше делал сервер (web/server.py) --- */
  function humanize(text) {
    return text.replace(/(?:парк )?(P\d\d)\b/g, function (m, id) { return PARK_NAMES[id] || m; })
      .replace(/класс (medium|big|extra_big)/g, function (m, c) { return "класс «" + LABELS[c] + "»"; });
  }
  function sheetValues() {
    var spec = sheetPark(), classes = { medium: 0, big: 0, extra_big: 0 };
    spec.routes.forEach(function (r) { classes[r[4]] += r[1]; });
    var res = expectedReserve(spec, "weekday");
    Object.keys(res).forEach(function (c) { classes[c] += res[c]; });
    return { classes: classes, routes: spec.routes.length, readiness: Math.round(spec.tech_readiness * 1000) / 10 };
  }
  function defaults() {
    var setup = {}, sheet = sheetValues();
    defaultSetup().forEach(function (e) { setup[e.id] = e; });
    return { version: D.page_version, parks: D.city_parks.map(function (p) {
      var e = setup[p[0]];
      return { id: p[0], name: p[1], address: p[2], classes: e.classes, routes: e.routes,
        readiness: Math.round(e.readiness * 1000) / 10, sheet: p[0] === PARK7.id ? sheet : null };
    }) };
  }
  function number(value, low, high, label, isFloat) {
    var v = isFloat ? parseFloat(value) : parseInt(value, 10);
    if (value === "" || value === null || value === undefined || isNaN(v) || (!isFloat && String(value).indexOf(".") >= 0)) {
      throw new Error(label + ": нужно число");
    }
    if (v < low || v > high) throw new Error(label + ": от " + low + " до " + high);
    return v;
  }
  function parseRequest(raw) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(raw.date || "")) throw new Error("Дата: укажите день");
    var seed = number(raw.seed === "" ? 1 : raw.seed, 0, 1e9, "Номер набора"), setup = [];
    (raw.parks || []).forEach(function (item) {
      var name = PARK_NAMES[item.id];
      if (!name) throw new Error("Нет парка " + item.id);
      if (!item.enabled) return;
      if (item.sheet) { setup.push({ id: item.id, sheet: true }); return; }
      var classes = {};
      CLASSES.forEach(function (c) { classes[c] = number(item.classes[c], 0, 2000, name + ", класс «" + LABELS[c] + "»"); });
      if (!sum(CLASSES.map(function (c) { return classes[c]; }))) throw new Error(name + ": укажите выпуск хотя бы по одному классу");
      setup.push({ id: item.id, sheet: false, classes: classes, routes: number(item.routes, 1, 300, name + ", маршрутов"),
        readiness: number(item.readiness, 50, 100, name + ", исправных %", true) / 100 });
    });
    if (!setup.length) throw new Error("Отметьте хотя бы один парк");
    return { day: raw.date, seed: seed, setup: setup };
  }
  function summarize(req) {
    var data = generate(req.setup, req.day, req.seed), report = check(data), issues = {};
    report.warnings.concat(report.errors).forEach(function (t) {
      var m = t.match(/\b(P\d\d)\b/), key = m ? m[1] : "";
      (issues[key] = issues[key] || []).push(humanize(t));
    });
    var totals = dayNumbers(data);
    totals.routes = data.routes.length;
    return {
      date: req.day, weekday: WEEKDAYS[dayInfo(req.day).weekday], day_type: data.meta.day_type, seed: req.seed,
      totals: totals,
      parks: data.parks.map(function (p) {
        var n = dayNumbers(data, p.id);
        n.id = p.id;
        n.routes = data.routes.filter(function (r) { return r.park_id === p.id; }).length;
        n.issues = issues[p.id] || [];
        return n;
      }),
      other_issues: issues[""] || [], error_count: report.errors.length, assumptions: data.meta.assumptions
    };
  }

  /* --- Выгрузка: JSON и CSV в ZIP (csvio.py) --- */
  function csvCell(v, kind) {
    if (v === null || v === undefined) return "";
    var s = kind === "list" ? v.join("|") : String(v);
    return /[;"\r\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  }
  function csvFiles(data) {
    var files = {};
    Object.keys(D.schema).forEach(function (entity) {
      var fields = D.schema[entity];
      var lines = [fields.map(function (f) { return f[0]; }).join(";")];
      data[entity].forEach(function (item) {
        lines.push(fields.map(function (f) { return csvCell(item[f[0]], f[1]); }).join(";"));
      });
      files[entity + ".csv"] = "﻿" + lines.join("\r\n") + "\r\n";
    });
    files["meta.json"] = JSON.stringify(data.meta, null, 1);
    return files;
  }
  var CRC = (function () {
    var t = [];
    for (var n = 0; n < 256; n++) { var c = n; for (var k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; }
    return t;
  })();
  function crc32(bytes) {
    var c = 0xffffffff;
    for (var i = 0; i < bytes.length; i++) c = CRC[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
    return (c ^ 0xffffffff) >>> 0;
  }
  function zip(files) {  // архив без сжатия: просто и без библиотек
    var enc = new TextEncoder(), parts = [], central = [], offset = 0;
    function u16(v) { return [v & 255, (v >>> 8) & 255]; }
    function u32(v) { return [v & 255, (v >>> 8) & 255, (v >>> 16) & 255, (v >>> 24) & 255]; }
    Object.keys(files).forEach(function (name) {
      var data = enc.encode(files[name]), nameBytes = enc.encode(name), crc = crc32(data);
      var head = [].concat(u32(0x04034b50), u16(20), u16(0x0800), u16(0), u16(0), u16(0x21), u32(crc),
        u32(data.length), u32(data.length), u16(nameBytes.length), u16(0));
      parts.push(new Uint8Array(head), nameBytes, data);
      central.push(new Uint8Array([].concat(u32(0x02014b50), u16(20), u16(20), u16(0x0800), u16(0), u16(0), u16(0x21),
        u32(crc), u32(data.length), u32(data.length), u16(nameBytes.length), u16(0), u16(0), u16(0), u16(0), u32(0),
        u32(offset))), nameBytes);
      offset += head.length + nameBytes.length + data.length;
    });
    var size = central.reduce(function (s, p) { return s + p.length; }, 0);
    var end = new Uint8Array([].concat(u32(0x06054b50), u16(0), u16(0), u16(Object.keys(files).length),
      u16(Object.keys(files).length), u32(size), u32(offset), u16(0)));
    return new Blob(parts.concat(central, [end]), { type: "application/zip" });
  }
  function exportFile(req, format) {
    var data = generate(req.setup, req.day, req.seed), stem = "autodisp_" + req.day + "_seed" + req.seed;
    if (format === "json") return { name: stem + ".json", data: JSON.stringify(data, null, 1) };
    if (format === "csv") return { name: stem + "_csv.zip", data: zip(csvFiles(data)) };
    throw new Error("Формат выгрузки: json или csv");
  }

  return { defaults: defaults, parseRequest: parseRequest, summarize: summarize, exportFile: exportFile,
    generate: generate, check: check, csvFiles: csvFiles, Rng: Rng };
});
