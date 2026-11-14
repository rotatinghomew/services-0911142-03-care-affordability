# 资料与统计口径

`households` 以家庭为抽样与共同资源单位，收入是每月税后可支配收入区间；上界 null 为开区间，不可用中点替代。`membership_status=disputed` 表示成员归属尚待确认。成员与费用通过 `member` 关联；`service_hours` 为实际需求记录，`purchased=false` 表示未购买，不能据此改成零需要。交通费 null 为漏报。`tariffs` 按月份生效，`hour_price` 单位为元每小时，`eligible_share` 对具资格成员服务费补助，`household_cap` 为每户当月合计上限，扣除的生活保留资源由 `disposable_deduction` 约定。研究人员可选择其他合理尺度，但须标记为情景假设。`stratum/psu/weight` 是分层整群抽样字段，不把每个成员当独立家庭。当前仅展示费用分项。

## 原始表

- `households`：150条示例，主键为 `household`。字段：`household`, `zone`, `income_low`, `income_high`, `weight`, `stratum`, `psu`, `membership_status`。
- `members`：450条示例，主键为 `member`。字段：`member`, `household`, `role`, `needs_care`, `subsidy_eligible`。
- `bills`：900条示例，主键为 `bill`。字段：`bill`, `member`, `period`, `service_hours`, `transport`, `meal_fee`, `purchased`。
- `tariffs`：2条示例，主键为 `period`。字段：`period`, `hour_price`, `eligible_share`, `household_cap`, `disposable_deduction`。

## 版本与扩展

`fixtures/data.json` 是完整导入材料，`product.json` 是当前浏览器默认选择。全量导入校验后按规范化内容指纹保存，相同内容不重复建版本。原始事实不原地修改；资料修订形成新版本。`null` 表示明确未观测，不因筛选或合计自动变为0。

`care_affordability/domain.py` 是领域入口，现阶段仅做字段校验及资料描述；`service.py` 连接范围选择、数据版本与服务端计算后的存档；`storage.py` 管理不可变数据和保存材料；`web/app.js` 提供浏览和导出交互。保存的材料同时包含原数据与选择，打开旧材料不重算为新数据。新增分析不限内部组织方式，但不能丢失现有原始记录核对与历史材料读取能力。统计方法、模型选择和业务结论没有预设答案。
