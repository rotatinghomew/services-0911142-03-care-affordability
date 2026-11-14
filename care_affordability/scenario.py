"""家庭可负担性情景试算引擎。

口径要点：
- 家庭是共同资源与抽样单位；补助资格按成员判定，补助合计与封顶在家庭层面只扣一次。
- 收入只有档位（上界可为开区间），全程区间运算，不使用档位中点制造精确点估计。
- 交通费漏报给出可成立的费用区间；成员关系待确认的家庭不做确定性归类。
- observed 情景只计入已购买账单，未满足的照护需要单独标记，不解释为没有需要；
  uniform 情景忽略购买状态，把记录小时数作为统一服务需要。
- 总体推断保留抽样权重，不确定性用分层整群（PSU 层内重抽）自助法估计。
"""

import math
import random

from .domain import OPTIONS

MODES = ("observed", "uniform")
MISSING_TRANSPORT = ("exclude_cost", "zero", "impute_median")
REPLICATES = 200
SEED = 20261114
OVERRIDE_FIELDS = ("hour_price", "eligible_share", "household_cap", "disposable_deduction")


def _finite_number(value, name, minimum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("情景参数应为有限数字：" + name)
    if minimum is not None and value < minimum:
        raise ValueError("情景参数低于允许范围：" + name)
    return float(value)


def validate_scenario(data, scenario):
    """校验情景参数并返回归一化后的副本与生效收费表。"""
    if not isinstance(scenario, dict):
        raise ValueError("情景参数必须是对象")
    normalized = dict(scenario)
    period = normalized.get("period")
    if period not in OPTIONS["period"]:
        raise ValueError("选择范围无效：period")
    zone = normalized.get("zone")
    if zone not in OPTIONS["zone"]:
        raise ValueError("选择范围无效：zone")
    mode = normalized.get("mode")
    if mode not in MODES:
        raise ValueError("服务情景只能是 observed 或 uniform")
    missing = normalized.get("missing_transport", "impute_median")
    if missing not in MISSING_TRANSPORT:
        raise ValueError("漏报交通费处理方式无效")
    normalized["missing_transport"] = missing
    burden_line = _finite_number(normalized.get("burden_line", 0.30), "burden_line", minimum=0.000001)
    if burden_line > 2:
        raise ValueError("负担线不应超过可支配资源的2倍")
    normalized["burden_line"] = burden_line
    tariff = dict(next(row for row in data["tariffs"] if row["period"] == period))
    override = normalized.get("tariff_override")
    if override is not None:
        if not isinstance(override, dict):
            raise ValueError("收费调整必须是对象")
        for key in override:
            if key not in OVERRIDE_FIELDS:
                raise ValueError("未知收费调整字段：" + key)
        if "hour_price" in override:
            tariff["hour_price"] = _finite_number(override["hour_price"], "hour_price", minimum=0)
        if "eligible_share" in override:
            share = _finite_number(override["eligible_share"], "eligible_share", minimum=0)
            if share > 1:
                raise ValueError("补助比例不应超过1")
            tariff["eligible_share"] = share
        if "household_cap" in override:
            tariff["household_cap"] = _finite_number(override["household_cap"], "household_cap", minimum=0)
        if "disposable_deduction" in override:
            tariff["disposable_deduction"] = _finite_number(
                override["disposable_deduction"], "disposable_deduction", minimum=0)
    normalized["tariff_override"] = override
    uniform_hours = normalized.get("uniform_hours")
    if uniform_hours is not None:
        if mode != "uniform":
            raise ValueError("统一服务小时数只在 uniform 情景下可用")
        normalized["uniform_hours"] = _finite_number(uniform_hours, "uniform_hours", minimum=0)
    return normalized, tariff


def _households_in_zone(data, zone):
    if zone == "all":
        return list(data["households"])
    return [row for row in data["households"] if row["zone"] == zone]


def _transport_references(data, period, household_ids):
    member_ids = {row["member"] for row in data["members"] if row["household"] in household_ids}
    observed = [row["transport"] for row in data["bills"]
                if row["period"] == period and row["member"] in member_ids and row["transport"] is not None]
    if not observed:
        return 0.0, 0.0
    ordered = sorted(observed)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        median = float(ordered[middle])
    else:
        median = (ordered[middle - 1] + ordered[middle]) / 2
    return median, float(ordered[-1])


def _compute_household(household, members, bills_lookup, tariff, scenario, median_transport, max_transport):
    """计算单户区间结果与可核对的分步过程。"""
    period = scenario["period"]
    mode = scenario["mode"]
    missing_policy = scenario["missing_transport"]
    uniform_hours = scenario.get("uniform_hours")
    price = tariff["hour_price"]
    share = tariff["eligible_share"]
    cap = tariff["household_cap"]
    deduction = tariff["disposable_deduction"]

    bill_steps = []
    service_cost = 0.0
    meal_total = 0.0
    transport_lo = transport_point = transport_hi = 0.0
    uncapped_subsidy = 0.0
    missing_count = 0
    unmet_gap = 0.0
    unmet_bills = 0

    for member in members:
        eligible = member["subsidy_eligible"]
        for bill in bills_lookup.get(member["member"], ()):
            if bill["period"] != period:
                continue
            hours = bill["service_hours"] if uniform_hours is None else uniform_hours
            line_service = hours * price
            included = True if mode == "uniform" else bill["purchased"]
            if not included:
                if member["needs_care"]:
                    unmet_bills += 1
                    unmet_gap += line_service
                bill_steps.append({
                    "bill": bill["bill"], "member": member["member"], "role": member["role"],
                    "service_hours": hours, "listed_service_cost": round(line_service, 4),
                    "purchased": bill["purchased"], "included": False,
                    "reason": "observed 情景未购买，不计入支出；needs_care=true 时计入未满足需要"})
                continue
            service_cost += line_service
            meal_total += bill["meal_fee"]
            if bill["transport"] is None:
                missing_count += 1
                low, high = 0.0, max_transport
                point = 0.0 if missing_policy in ("exclude_cost", "zero") else median_transport
            else:
                low = point = high = float(bill["transport"])
            transport_lo += low
            transport_point += point
            transport_hi += high
            if eligible:
                uncapped_subsidy += line_service * share
            bill_steps.append({
                "bill": bill["bill"], "member": member["member"], "role": member["role"],
                "service_hours": hours, "listed_service_cost": round(line_service, 4),
                "meal_fee": bill["meal_fee"], "transport": bill["transport"],
                "transport_used": round(point, 4), "subsidy_eligible": eligible,
                "included": True})

    subsidy = min(uncapped_subsidy, cap)
    cap_clawback = uncapped_subsidy - subsidy
    net_point = service_cost + meal_total + transport_point - subsidy
    net_lo = service_cost + meal_total + transport_lo - subsidy
    net_hi = service_cost + meal_total + transport_hi - subsidy

    income_low = household["income_low"]
    income_high = household["income_high"]
    disp_low = income_low - deduction
    disp_high = None if income_high is None else income_high - deduction

    reasons = []
    if income_high is None:
        reasons.append("收入为开区间，负担率无上界，不能做确定性排名")
    if missing_count:
        reasons.append("交通费漏报 %d 项，按 %s 给定点值并保留费用区间" % (missing_count, missing_policy))
    if household["membership_status"] == "disputed":
        reasons.append("成员关系待确认，家庭构成可能变化，强制归入不可判断")
    if unmet_bills:
        reasons.append("observed 情景下 %d 项已记录需要未购买，实际支出低估需要" % unmet_bills)
    if disp_high is not None and disp_high <= 0:
        reasons.append("收入上界扣除生活保留额后仍不大于0，任何可行收入都无法负担")
    elif disp_low <= 0:
        reasons.append("收入下界不高于生活保留额，负担率上界不可判断")

    ratio_lo = ratio_hi = None
    if disp_high is not None and disp_high > 0:
        ratio_lo = net_lo / disp_high
    if disp_low > 0:
        ratio_hi = net_hi / disp_low

    burden_line = scenario["burden_line"]
    if household["membership_status"] == "disputed":
        status = "indeterminate"
    elif disp_high is not None and disp_high <= 0:
        status = "over_burden"
    elif ratio_lo is not None and ratio_lo > burden_line:
        status = "over_burden"
    elif ratio_hi is not None and ratio_hi <= burden_line:
        status = "under_burden"
    else:
        status = "indeterminate"
        if ratio_lo is None or ratio_hi is None:
            reasons.append("可支配资源或负担率为开区间，无法确认是否超过负担线")
        else:
            reasons.append("负担率可成立区间 [%.4f, %.4f] 跨越负担线 %.4f，区间内两种分类都可能成立"
                           % (ratio_lo, ratio_hi, burden_line))

    steps = {
        "effective_tariff": tariff,
        "service_cost": round(service_cost, 4),
        "meal_total": round(meal_total, 4),
        "transport_low": round(transport_lo, 4),
        "transport_point": round(transport_point, 4),
        "transport_high": round(transport_hi, 4),
        "uncapped_subsidy": round(uncapped_subsidy, 4),
        "household_cap_applied_once": cap,
        "subsidy_after_cap": round(subsidy, 4),
        "cap_clawback": round(cap_clawback, 4),
        "net_spend_low": round(net_lo, 4),
        "net_spend_point": round(net_point, 4),
        "net_spend_high": round(net_hi, 4),
        "income_interval": [income_low, income_high],
        "disposable_low": round(disp_low, 4),
        "disposable_high": None if disp_high is None else round(disp_high, 4),
        "burden_ratio_formula": "净支出区间 / 可支配资源区间，下界=净支出下界/可支配上界，上界反向",
        "bill_count_included": sum(1 for step in bill_steps if step.get("included")),
    }
    return {
        "household": household["household"],
        "zone": household["zone"],
        "stratum": household["stratum"],
        "psu": household["psu"],
        "weight": household["weight"],
        "membership_status": household["membership_status"],
        "income_low": income_low,
        "income_high": income_high,
        "net_spend_low": round(net_lo, 4),
        "net_spend_point": round(net_point, 4),
        "net_spend_high": round(net_hi, 4),
        "ratio_low": None if ratio_lo is None else round(ratio_lo, 6),
        "ratio_high": None if ratio_hi is None else round(ratio_hi, 6),
        "status": status,
        "reasons": reasons,
        "missing_transport_items": missing_count,
        "unmet_need_bills": unmet_bills,
        "unmet_need_gap": round(unmet_gap, 4),
        "components": {
            "service_cost": round(service_cost, 4),
            "meal_total": round(meal_total, 4),
            "transport_point": round(transport_point, 4),
            "subsidy_after_cap": round(subsidy, 4),
            "cap_clawback": round(cap_clawback, 4),
        },
        "steps": steps,
        "bill_steps": bill_steps,
    }


def _household_rows(data, scenario, tariff, household_subset=None):
    households = _households_in_zone(data, scenario["zone"]) if household_subset is None else household_subset
    ids = {row["household"] for row in households}
    members_by_hh = {}
    for row in data["members"]:
        if row["household"] in ids:
            members_by_hh.setdefault(row["household"], []).append(row)
    bills_lookup = {}
    for row in data["bills"]:
        bills_lookup.setdefault(row["member"], []).append(row)
    median_transport, max_transport = _transport_references(data, scenario["period"], ids)
    rows = []
    for household in households:
        rows.append(_compute_household(
            household, members_by_hh.get(household["household"], ()),
            bills_lookup, tariff, scenario, median_transport, max_transport))
    return rows


def _weighted_share(rows, predicate, weights):
    total = sum(weights)
    if total <= 0:
        return None
    return sum(weight for row, weight in zip(rows, weights) if predicate(row)) / total


def _weighted_mean(rows, field, weights):
    total = sum(weights)
    if total <= 0:
        return None
    return sum(row[field] * weight for row, weight in zip(rows, weights)) / total


def _percentile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    index = q * (len(ordered) - 1)
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - index) + ordered[upper] * (index - lower)


def _bootstrap(rows, statistic, rng, replicates=REPLICATES):
    strata = {}
    for row in rows:
        strata.setdefault(row["stratum"], set()).add(row["psu"])
    if any(len(psus) < 2 for psus in strata.values()):
        return None, "存在仅含单个PSU的层，自助区间不可用，已只返回点估计"
    psu_lists = {stratum: sorted(psus) for stratum, psus in strata.items()}
    estimates = []
    for _ in range(replicates):
        multiplicity = {}
        for stratum, psus in psu_lists.items():
            draws = rng.choices(range(len(psus)), k=len(psus))
            for draw in draws:
                key = (stratum, psus[draw])
                multiplicity[key] = multiplicity.get(key, 0) + 1
        weights = [row["weight"] * multiplicity.get((row["stratum"], row["psu"]), 0) for row in rows]
        estimates.append(statistic(rows, weights))
    return estimates, None


def _ci(estimates):
    if estimates is None:
        return None
    return [round(_percentile(estimates, 0.025), 6), round(_percentile(estimates, 0.975), 6)]


def _cdf_band(rows, weights):
    finite_rows = [(row, weight) for row, weight in zip(rows, weights)
                   if row["ratio_low"] is not None and row["ratio_high"] is not None]
    grid = sorted({value for row, _ in finite_rows for value in (row["ratio_low"], row["ratio_high"])})
    total = sum(weight for _, weight in finite_rows)
    points = []
    for x in grid:
        certainly = sum(weight for row, weight in finite_rows if row["ratio_high"] <= x)
        possibly = sum(weight for row, weight in finite_rows if row["ratio_low"] <= x)
        points.append({"x": round(x, 6),
                       "lower": round(certainly / total, 6) if total else None,
                       "upper": round(possibly / total, 6) if total else None})
    return points, sum(weight for _, weight in finite_rows), sum(weights)


def _clean(value):
    if isinstance(value, float):
        return None if not math.isfinite(value) else value
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean(item) for item in value]
    return value


def _aggregate(rows, scenario, tariff):
    weights = [row["weight"] for row in rows]
    weight_sum = sum(weights)
    rng = random.Random(SEED)
    over_estimates, note = _bootstrap(
        rows, lambda items, w: _weighted_share(items, lambda row: row["status"] == "over_burden", w), rng)
    indet_estimates, _ = _bootstrap(
        rows, lambda items, w: _weighted_share(items, lambda row: row["status"] == "indeterminate", w),
        random.Random(SEED + 1))
    net_estimates, _ = _bootstrap(
        rows, lambda items, w: _weighted_mean(items, "net_spend_point", w), random.Random(SEED + 2))

    def share(status):
        value = _weighted_share(rows, lambda row: row["status"] == status, weights)
        return None if value is None else round(value * 100, 4)

    cdf, finite_weight, all_weight = _cdf_band(rows, weights)
    component_defs = [
        ("service_cost", "服务费（按情景口径计入）"),
        ("meal_total", "餐费"),
        ("transport_point", "交通费（漏报按选定口径）"),
        ("subsidy_after_cap", "补助（家庭封顶后，整户只扣一次）"),
        ("cap_clawback", "封顶削减额（本可补助但超过户上限）"),
    ]
    components = []
    for field, label in component_defs:
        total = sum(weights)
        mean = sum(row["components"][field] * weight for row, weight in zip(rows, weights)) / total
        components.append({
            "component": field, "label": label,
            "weighted_mean": round(mean, 4),
            "weighted_total_estimate": round(sum(row["components"][field] * row["weight"] for row in rows), 4)})
    net_mean_lo = _weighted_mean(rows, "net_spend_low", weights)
    net_mean_hi = _weighted_mean(rows, "net_spend_high", weights)
    uncertainty = {
        "method": "分层整群抽样自助法：在每个层内对PSU有放回重抽，户权重随PSU抽中次数放大",
        "replicates": REPLICATES, "seed": SEED,
        "ci_available": over_estimates is not None, "note": note}
    notes = _build_notes(rows, scenario, tariff, finite_weight, all_weight, note)
    summary = {
        "家庭数": len(rows),
        "抽样权重合计": round(weight_sum, 4),
        "超过负担线家庭比例_pct": share("over_burden"),
        "超过负担线比例95pct自助区间": None if over_estimates is None else _ci(over_estimates),
        "低于负担线家庭比例_pct": share("under_burden"),
        "不可判断家庭比例_pct": share("indeterminate"),
        "不可判断比例95pct自助区间": None if indet_estimates is None else _ci(indet_estimates),
        "户均净支出_点估计": None if net_estimates is None else round(_percentile(net_estimates, 0.5), 4),
        "户均净支出95pct自助区间": _ci(net_estimates),
        "户均净支出可成立区间": [round(net_mean_lo, 4), None if net_mean_hi is None else round(net_mean_hi, 4)],
        "成员关系待确认家庭数": sum(row["membership_status"] == "disputed" for row in rows),
        "交通费漏报家庭数": sum(row["missing_transport_items"] > 0 for row in rows),
        "存在未满足照护需要家庭数": sum(row["unmet_need_bills"] > 0 for row in rows),
        "未满足需要服务费加权总量估计": round(sum(row["unmet_need_gap"] * row["weight"] for row in rows), 4),
        "触及补助封顶家庭数": sum(row["components"]["cap_clawback"] > 0 for row in rows),
        "收入开区间家庭数": sum(row["income_high"] is None for row in rows),
    }
    buckets = []
    for status, label in (("over_burden", "超过负担线"), ("under_burden", "低于负担线"),
                          ("indeterminate", "不可判断")):
        buckets.append({"status": status, "label": label,
                        "count": sum(row["status"] == status for row in rows),
                        "weighted_share_pct": share(status)})
    return _clean({
        "summary": summary, "buckets": buckets, "components": components,
        "cdf": cdf, "rows": rows, "notes": notes,
        "effective_scenario": scenario, "effective_tariff": tariff,
        "uncertainty": uncertainty})


def _build_notes(rows, scenario, tariff, finite_weight, all_weight, bootstrap_note):
    notes = []
    if scenario["mode"] == "observed":
        notes.append("observed 情景：仅计入已购买账单；未购买不代表没有需要，未满足需要在户记录中单独标记并汇总。")
    else:
        if scenario.get("uniform_hours") is None:
            notes.append("uniform 情景：忽略购买状态，按各成员记录的服务小时数作为统一服务需要，避免把未购买解释成无需要。")
        else:
            notes.append("uniform 情景：忽略购买状态，且每位成员统一按 %s 小时试算，属于情景假设而非观测消费。"
                         % scenario["uniform_hours"])
    notes.append("补助按成员资格计算服务费补助，但户合计只扣一次并受 household_cap 封顶；生活保留额每户只扣一次。")
    if scenario.get("tariff_override"):
        notes.append("收费表经过情景调整：%s；原始事实未修改，调整只存在于本次试算。"
                     % scenario["tariff_override"])
    missing_households = sum(row["missing_transport_items"] > 0 for row in rows)
    if missing_households:
        notes.append("交通费漏报 %d 户：点值口径为 %s，区间下界按0、上界按同期同范围观测最大值，不把漏报静默当0。"
                     % (missing_households, scenario["missing_transport"]))
    if finite_weight < all_weight:
        notes.append("有 %s 权重的家庭负担率为开区间，不进入CDF绘制，但保留在三分类与不可判断统计中。"
                     % round(all_weight - finite_weight, 4))
    notes.append("收入为档位数据，全部结果以区间和分类呈现，不使用档位中点生成精确排名。")
    if bootstrap_note:
        notes.append(bootstrap_note)
    return notes


def affordability(data, scenario):
    normalized, tariff = validate_scenario(data, scenario)
    rows = _household_rows(data, normalized, tariff)
    return _aggregate(rows, normalized, tariff)


def compare(data, base, alternative):
    """在相同家庭基础上配对计算两个情景。"""
    base_norm, base_tariff = validate_scenario(data, base)
    alt_norm, alt_tariff = validate_scenario(data, alternative)
    if base_norm["zone"] != alt_norm["zone"]:
        raise ValueError("情景对比必须使用相同的居住范围，以保证家庭基础一致")
    base_rows = _household_rows(data, base_norm, base_tariff)
    alt_rows = _household_rows(data, alt_norm, alt_tariff)
    base_ids = [row["household"] for row in base_rows]
    alt_by_id = {row["household"]: row for row in alt_rows}
    common_ids = [identifier for identifier in base_ids if identifier in alt_by_id]
    if len(common_ids) != len(base_rows) or len(common_ids) != len(alt_rows):
        raise ValueError("两个情景的家庭基础不一致，拒绝对比")
    base_rows = [next(row for row in base_rows if row["household"] == identifier) for identifier in common_ids]
    alt_rows = [alt_by_id[identifier] for identifier in common_ids]
    base_view = _aggregate(base_rows, base_norm, base_tariff)
    alt_view = _aggregate(alt_rows, alt_norm, alt_tariff)

    rng = random.Random(SEED + 100)
    strata = {}
    for row in base_rows:
        strata.setdefault(row["stratum"], set()).add(row["psu"])
    psu_lists = {stratum: sorted(psus) for stratum, psus in strata.items()}
    deltas_over, deltas_net = [], []
    paired_ok = all(len(psus) >= 2 for psus in psu_lists.values())
    if paired_ok:
        for _ in range(REPLICATES):
            multiplicity = {}
            for stratum, psus in psu_lists.items():
                for draw in rng.choices(range(len(psus)), k=len(psus)):
                    key = (stratum, psus[draw])
                    multiplicity[key] = multiplicity.get(key, 0) + 1
            weights = [row["weight"] * multiplicity.get((row["stratum"], row["psu"]), 0) for row in base_rows]
            base_over = _weighted_share(base_rows, lambda row: row["status"] == "over_burden", weights)
            alt_over = _weighted_share(alt_rows, lambda row: row["status"] == "over_burden", weights)
            base_net = _weighted_mean(base_rows, "net_spend_point", weights)
            alt_net = _weighted_mean(alt_rows, "net_spend_point", weights)
            deltas_over.append((alt_over - base_over) * 100)
            deltas_net.append(alt_net - base_net)

    component_fields = ["service_cost", "meal_total", "transport_point", "subsidy_after_cap", "cap_clawback"]
    component_delta = []
    base_weights = [row["weight"] for row in base_rows]
    alt_weights = [row["weight"] for row in alt_rows]
    for field in component_fields:
        base_mean = sum(row["components"][field] * weight for row, weight in zip(base_rows, base_weights)) / sum(base_weights)
        alt_mean = sum(row["components"][field] * weight for row, weight in zip(alt_rows, alt_weights)) / sum(alt_weights)
        component_delta.append({"component": field,
                                "base_weighted_mean": round(base_mean, 4),
                                "alternative_weighted_mean": round(alt_mean, 4),
                                "delta_weighted_mean": round(alt_mean - base_mean, 4)})
    base_over = _weighted_share(base_rows, lambda row: row["status"] == "over_burden",
                                [row["weight"] for row in base_rows]) * 100
    alt_over = _weighted_share(alt_rows, lambda row: row["status"] == "over_burden",
                               [row["weight"] for row in alt_rows]) * 100
    result = {
        "common_base": {"household_count": len(common_ids),
                        "weight_sum": round(sum(row["weight"] for row in base_rows), 4),
                        "zone": base_norm["zone"],
                        "note": "两个情景使用完全相同的家庭集合与同一套自助重抽，差异不来自样本构成变化。"},
        "over_burden_share": {
            "base_pct": round(base_over, 4), "alternative_pct": round(alt_over, 4),
            "delta_pct": round(alt_over - base_over, 4),
            "delta_95pct_ci": _ci(deltas_over) if paired_ok else None},
        "mean_net_spend": {
            "delta_95pct_ci": _ci(deltas_net) if paired_ok else None},
        "component_delta": component_delta,
        "base_view": base_view, "alternative_view": alt_view,
        "notes": ["情景对比的家庭基础相同（%d 户，权重合计 %s）；月份可以不同，因为每户在两个月份都有账单。"
                  % (len(common_ids), round(sum(row["weight"] for row in base_rows), 4)),
                  "配对自助法对两个情景使用同一套PSU重抽，差值区间已消除重抽间的额外噪声。"]}
    return _clean(result)
