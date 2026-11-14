from .validation import require_tables, unique, finite, references


SCHEMA = {'households': {'household': ['str'], 'zone': ['str'], 'income_low': ['number'], 'income_high': ['null', 'number'], 'weight': ['number'], 'stratum': ['str'], 'psu': ['str'], 'membership_status': ['str']}, 'members': {'member': ['str'], 'household': ['str'], 'role': ['str'], 'needs_care': ['boolean'], 'subsidy_eligible': ['boolean']}, 'bills': {'bill': ['str'], 'member': ['str'], 'period': ['str'], 'service_hours': ['number'], 'transport': ['null', 'number'], 'meal_fee': ['number'], 'purchased': ['boolean']}, 'tariffs': {'period': ['str'], 'hour_price': ['number'], 'eligible_share': ['number'], 'household_cap': ['number'], 'disposable_deduction': ['number']}}
OPTIONS = {'period': ['2026-09', '2026-10'], 'zone': ['all', 'urban', 'rural']}


def validate_structure(data, schema):
    require_tables(data, list(schema))
    for table, fields in schema.items():
        for row in data[table]:
            if not isinstance(row, dict) or any(field not in row for field in fields):
                raise ValueError("表字段缺失：" + table)
            for field, kinds in fields.items():
                value = row[field]
                kind = "null" if value is None else ("boolean" if isinstance(value,bool) else ("number" if isinstance(value,(int,float)) else type(value).__name__))
                if kind not in kinds:
                    raise ValueError("字段类型错误：" + table + "." + field)
                if kind == "number":
                    finite(value)


def validate(data):
    validate_structure(data, SCHEMA)
    unique(data['households'], ['household'])
    unique(data['members'], ['member'])
    unique(data['bills'], ['bill'])
    unique(data['tariffs'], ['period'])
    references(data['members'], 'household', data['households'], 'household')
    references(data['bills'], 'member', data['members'], 'member')
    for row in data["households"]:
        finite(row["income_low"], minimum=0)
        finite(row["weight"], minimum=0.000001)
        if row["income_high"] is not None and row["income_high"] < row["income_low"]:
            raise ValueError("收入上界低于下界")


def view(rows, measure, note):
    return {"summary":{"明细条数":len(rows),"有数值记录":sum(isinstance(row.get(measure),(int,float)) for row in rows)},
            "rows":rows,"series":[{"label":str(position+1),"y":row.get(measure)} for position,row in enumerate(rows)],
            "notes":[note]}


def explore(data, selection):
    for key, choices in OPTIONS.items():
        if selection.get(key) not in choices:
            raise ValueError("选择范围无效：" + key)
    families = {row["household"]:row for row in data["households"] if selection["zone"] == "all" or row["zone"] == selection["zone"]}
    members = {row["member"]:row for row in data["members"] if row["household"] in families}
    tariff = next(row for row in data["tariffs"] if row["period"] == selection["period"])
    rows = [{**row,"household":members[row["member"]]["household"],"listed_service_cost":row["service_hours"]*tariff["hour_price"]} for row in data["bills"] if row["period"] == selection["period"] and row["member"] in members]
    return view(rows, "listed_service_cost", "这里只展示挂牌服务成本，未计算补助、共同资源或家庭可负担率。")
