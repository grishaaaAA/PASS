"""История назначений из сохранённых расчётов и оперативных изменений."""

from naryad.ops.replan import OpsState


def _clock(minutes):
    return f"{minutes // 60:02}:{minutes % 60:02}"


def _members(day, state, duty_ids):
    ids = set(duty_ids)
    for key in duty_ids:
        duty = day.duties[key]
        if duty.route_id:
            ids.add(duty.route_id)
        ids.update(seg.who for seg in state.vehicles.get(key, ()))
        for shift in day.shifts_by_duty.get(key, ()):
            ids.update(seg.who for seg in state.drivers.get(shift.id, ()))
    return ids


def affected(previous, after):
    """Сохраняем связи для замен, даже если назначение исчезнет из итогового состояния."""
    day = after.day
    duties = {key for key in previous.vehicles.keys() | after.vehicles.keys()
              if previous.vehicles.get(key) != after.vehicles.get(key)}
    duties.update(day.shifts[key].duty_id
                  for key in previous.drivers.keys() | after.drivers.keys()
                  if previous.drivers.get(key) != after.drivers.get(key))
    return sorted(_members(day, previous, duties) | _members(day, after, duties))


def _assignments(day, state, duties, kind, entity_id):
    for duty in duties:
        bus_segments = state.vehicles.get(duty.id, ())
        driver_segments = [seg for shift in day.shifts_by_duty.get(duty.id, ())
                           for seg in state.drivers.get(shift.id, ())]
        if kind == "veh":
            bus_segments = [seg for seg in bus_segments if seg.who == entity_id]
            if not bus_segments:
                continue
        elif kind == "drv":
            driver_segments = [seg for seg in driver_segments if seg.who == entity_id]
            if not driver_segments:
                continue
        route = day.routes.get(duty.route_id)
        points = sorted({t for seg in [*bus_segments, *driver_segments] for t in (seg.start, seg.end)})
        rows = []
        for start, end in zip(points, points[1:]):
            bus = next((seg.who for seg in bus_segments if seg.start <= start < seg.end), None)
            drv = next((seg.who for seg in driver_segments if seg.start <= start < seg.end), None)
            if not (bus or drv) or (kind == "veh" and not bus) or (kind == "drv" and not drv):
                continue
            if rows and rows[-1][3:] == (start, bus, drv):
                rows[-1] = (duty, route, rows[-1][2], end, bus, drv)
            else:
                rows.append((duty, route, start, end, bus, drv))
        yield from rows


def history(records, kind, entity_id, park_id):
    """Соединяем автобус и водителя только на пересечении их отрезков одного наряда."""
    results = []
    for record in records:
        day, state, plan = record.day, record.state, record.plan
        item = {"route": day.routes, "veh": day.vehicles, "drv": day.drivers}[kind].get(entity_id)
        if item is None or item.park_id != park_id or not state or not plan:
            continue
        duties = [d for d in day.duties.values() if kind != "route" or d.route_id == entity_id]
        morning = OpsState.from_plan(day, plan) if record.log else state
        item_keys = {d.id for d in duties} if kind == "route" else set()
        rows = set(_assignments(day, state, duties, kind, entity_id))
        old_rows = set(_assignments(day, morning, duties, kind, entity_id))
        item_keys.update(row[0].id for row in rows | old_rows)
        # Старые записи до сохранения affected_ids: идентификатор самого ресурса или наряда.
        log = [e for e in record.log if entity_id in e.get("affected_ids", ())
               or entity_id in (e.get("vehicle_id"), e.get("driver_id"))
               or e.get("duty_id") in item_keys
               or (e.get("shift_id") in day.shifts and day.shifts[e["shift_id"]].duty_id in item_keys)]
        if not rows and not old_rows and not log:
            continue
        names = {d["id"]: d["full_name"] for d in record.raw["drivers"]}

        def serialize(entries):
            return [{"from": _clock(start), "to": _clock(end), "duty_id": duty.id,
                     "route": ({"id": route.id, "park": route.park_id, "number": route.number} if route else None),
                     "bus": ({"id": bus, "park": day.vehicles[bus].park_id, "name": day.vehicles[bus].board_number} if bus else None),
                     "driver": ({"id": drv, "park": day.drivers[drv].park_id, "name": names[drv]} if drv else None)}
                    for duty, route, start, end, bus, drv in sorted(entries, key=lambda row: (row[2], row[0].id, row[4] or "", row[5] or ""))]

        results.append({"date": day.meta["date"], "day_id": record.id, "assignments": serialize(rows),
                        "previous": serialize(old_rows - rows), "log": list(reversed(log))})
    return sorted(results, key=lambda row: (row["date"], row["day_id"]), reverse=True)
