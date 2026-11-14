const state = {context: null, current: null, mode: 'explore', trial: null, comparison: null};
const element = identifier => document.getElementById(identifier);

async function request(path, body) {
  const response = await fetch(path, body === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
  });
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || '请求失败');
  return value;
}

function message(value, error = false) {
  element('status').textContent = value;
  element('status').className = error ? 'error' : '';
}

async function guarded(action) {
  try { await action(); } catch (error) { message(error.message, true); }
}

function selectedRevision() {
  return {revision: element('revision').value};
}

function scopeSelection() {
  const selection = {};
  for (const control of state.context.product.options) {
    selection[control[0]] = element('select-' + control[0]).value;
  }
  return selection;
}

function numberOrNull(value) {
  if (value === '' || value === null || value === undefined) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function renderControls() {
  const revision = element('revision').value || state.context.default_revision;
  element('revision').replaceChildren();
  for (const dataset of state.context.datasets) {
    const option = document.createElement('option');
    option.value = dataset.revision;
    option.textContent = dataset.label + ' · ' + dataset.revision.slice(0, 9);
    element('revision').append(option);
  }
  element('revision').value = revision;
  if (!element('revision').value) element('revision').value = state.context.default_revision;
  if (element('controls').children.length) return;
  for (const [key, labelText, choices] of state.context.product.options) {
    const label = document.createElement('label');
    label.textContent = labelText;
    const select = document.createElement('select');
    select.id = 'select-' + key;
    for (const choice of choices) {
      const option = document.createElement('option');
      option.value = choice;
      option.textContent = choice;
      select.append(option);
    }
    select.value = state.context.product.selection[key];
    label.append(select);
    element('controls').append(label);
  }
  const altPeriod = element('alt-period');
  for (const choice of state.context.product.options[0][2]) {
    const option = document.createElement('option');
    option.value = choice; option.textContent = choice; altPeriod.append(option);
  }
  altPeriod.value = state.context.product.options[0][2].find(
    value => value !== state.context.product.selection.period);
  const defaults = state.context.product.trial_defaults || {};
  element('trial-mode').value = defaults.mode || 'observed';
  element('trial-line').value = String(defaults.burden_line ?? 0.30);
  element('trial-missing').value = defaults.missing_transport || 'impute_median';
}

function switchMode(mode) {
  state.mode = mode;
  element('tab-explore').classList.toggle('active', mode === 'explore');
  element('tab-trial').classList.toggle('active', mode === 'trial');
  element('panel-explore').classList.toggle('hidden', mode !== 'explore');
  element('panel-trial').classList.toggle('hidden', mode !== 'trial');
  element('view-explore').classList.toggle('hidden', mode !== 'explore');
  element('view-trial').classList.toggle('hidden', mode !== 'trial');
}

function displayValue(value) {
  if (value === null || value === undefined) return '未观测/开区间';
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(4);
  return typeof value === 'object' ? JSON.stringify(value) : String(value);
}

function drawChart(series) {
  const canvas = element('chart');
  const context = canvas.getContext('2d');
  context.clearRect(0, 0, canvas.width, canvas.height);
  const values = series.filter(point => typeof point.y === 'number' && Number.isFinite(point.y));
  context.font = '12px sans-serif';
  if (!values.length) { context.fillText('该范围没有可定量的描述值', 30, 50); return; }
  const low = Math.min(0, ...values.map(point => point.y));
  const high = Math.max(1, ...values.map(point => point.y));
  const scale = value => 250 - (value - low) / (high - low || 1) * 210;
  context.strokeStyle = '#90a5ad';
  context.beginPath(); context.moveTo(45, 20); context.lineTo(45, 250); context.lineTo(940, 250); context.stroke();
  context.fillStyle = '#0a7168';
  const scatter = values.every(point => typeof point.x === 'number');
  const minimumX = scatter ? Math.min(...values.map(point => point.x)) : 0;
  const maximumX = scatter ? Math.max(...values.map(point => point.x)) : 1;
  values.forEach((point, index) => {
    const position = scatter ? 60 + (point.x - minimumX) / (maximumX - minimumX || 1) * 850 : 55 + index * 875 / values.length;
    if (scatter) {
      context.beginPath(); context.arc(position, scale(point.y), 3.5, 0, Math.PI * 2); context.fill();
    } else {
      context.fillRect(position, Math.min(scale(point.y), scale(0)), Math.max(3, 800 / values.length), Math.max(1, Math.abs(scale(point.y) - scale(0))));
    }
    if (values.length < 12) context.fillText(point.label, position, 275);
  });
  context.fillStyle = '#29434e';
  context.fillText(high.toFixed(2), 0, 45); context.fillText(low.toFixed(2), 0, 250);
  context.fillText(scatter ? '所选资料的数值坐标（仅为原始描述）' : '按表中顺序展示描述值；缺测不绘制为零', 50, 20);
}

function renderExplore(result, heading) {
  state.current = result;
  element('summary').replaceChildren();
  for (const [label, value] of Object.entries(result.view.summary)) {
    const card = document.createElement('article');
    const title = document.createElement('span'); title.textContent = label;
    const amount = document.createElement('strong'); amount.textContent = displayValue(value);
    card.append(title, amount); element('summary').append(card);
  }
  element('notes').replaceChildren();
  for (const note of result.view.notes) {
    const paragraph = document.createElement('p'); paragraph.textContent = note; element('notes').append(paragraph);
  }
  const rows = result.view.rows;
  const fields = rows.length ? Object.keys(rows[0]) : [];
  const header = document.createElement('tr');
  for (const field of fields) { const cell = document.createElement('th'); cell.textContent = field; header.append(cell); }
  element('columns').replaceChildren(header); element('rows').replaceChildren();
  for (const row of rows) {
    const line = document.createElement('tr'); line.tabIndex = 0;
    for (const field of fields) { const cell = document.createElement('td'); cell.textContent = displayValue(row[field]); line.append(cell); }
    line.onclick = () => { element('details').textContent = JSON.stringify(row, null, 2); };
    line.onkeydown = event => { if (event.key === 'Enter') line.click(); };
    element('rows').append(line);
  }
  drawChart(result.view.series);
  element('details').textContent = JSON.stringify({revision: result.revision, selection: result.selection,
    history: result.view.history || null}, null, 2);
  message((heading || '已更新描述') + ' · 数据 ' + result.revision.slice(0, 12));
}

const STATUS_TEXT = {over_burden: '超过负担线', under_burden: '低于负担线', indeterminate: '不可判断'};
const CI_TEXT = value => value === null || value === undefined ? '区间不可用' :
  '[' + (value[0] * 100).toFixed(2) + '%, ' + (value[1] * 100).toFixed(2) + '%]';

function renderTrial(view, heading, response = null) {
  state.current = response;
  state.trial = view;
  state.comparison = null;
  element('compare-box').textContent = '尚未运行情景对比。';
  const notes = element('trial-notes');
  notes.replaceChildren();
  for (const note of view.notes) {
    const paragraph = document.createElement('p'); paragraph.textContent = '· ' + note; notes.append(paragraph);
  }
  const summary = element('trial-summary');
  summary.replaceChildren();
  const cards = [
    ['超过负担线家庭比例', displayValue(view.summary['超过负担线家庭比例_pct']) + '%',
      '自助95%区间 ' + CI_TEXT(view.summary['超过负担线比例95pct自助区间'])],
    ['低于负担线家庭比例', displayValue(view.summary['低于负担线家庭比例_pct']) + '%', ''],
    ['不可判断家庭比例', displayValue(view.summary['不可判断家庭比例_pct']) + '%',
      '自助95%区间 ' + CI_TEXT(view.summary['不可判断比例95pct自助区间'])],
    ['户均净支出（点估计）', displayValue(view.summary['户均净支出_点估计']) + ' 元',
      '自助95%区间 ' + (view.summary['户均净支出95pct自助区间'] === null ? '不可用' :
        '[' + view.summary['户均净支出95pct自助区间'][0].toFixed(1) + ', ' +
        view.summary['户均净支出95pct自助区间'][1].toFixed(1) + '] 元')],
    ['户均净支出可成立区间', view.summary['户均净支出可成立区间'].map(v => v === null ? '开区间' : v.toFixed(1)).join(' ~ ') + ' 元', ''],
    ['抽样权重合计（家庭数 ' + view.summary['家庭数'] + '）', displayValue(view.summary['抽样权重合计']), ''],
    ['触及补助封顶家庭数', displayValue(view.summary['触及补助封顶家庭数']), '整户补助只扣一次'],
    ['存在未满足照护需要家庭数', displayValue(view.summary['存在未满足照护需要家庭数']),
      '未购买不等于没有需要'],
    ['成员关系待确认家庭数', displayValue(view.summary['成员关系待确认家庭数']), '一律不可判断'],
    ['交通费漏报家庭数', displayValue(view.summary['交通费漏报家庭数']), ''],
  ];
  for (const [labelText, value, hint] of cards) {
    const card = document.createElement('article');
    const title = document.createElement('span'); title.textContent = labelText;
    const amount = document.createElement('strong'); amount.textContent = value;
    card.append(title, amount);
    if (hint) { const small = document.createElement('span'); small.textContent = hint; card.append(small); }
    summary.append(card);
  }
  drawCdf(view);
  const componentBody = element('component-rows');
  componentBody.replaceChildren();
  for (const component of view.components) {
    const line = document.createElement('tr');
    for (const text of [component.label, displayValue(component.weighted_mean), displayValue(component.weighted_total_estimate)]) {
      const cell = document.createElement('td'); cell.textContent = text; line.append(cell);
    }
    componentBody.append(line);
  }
  const body = element('household-rows');
  body.replaceChildren();
  for (const row of view.rows) {
    const line = document.createElement('tr');
    line.tabIndex = 0;
    const ratioText = [row.ratio_low, row.ratio_high].map(value =>
      value === null ? '开' : value.toFixed(3)).join(' ~ ');
    const values = [
      row.household, row.zone, row.membership_status === 'disputed' ? '待确认' : '已确认',
      [row.income_low, row.income_high].map(value => value === null ? '开' : value).join(' ~ '),
      [row.net_spend_low, row.net_spend_high].map(value => value.toFixed(1)).join(' ~ '),
      ratioText, STATUS_TEXT[row.status], row.missing_transport_items, row.unmet_need_bills, row.weight];
    for (const text of values) {
      const cell = document.createElement('td'); cell.textContent = text;
      if (text === STATUS_TEXT[row.status]) cell.className = 'badge badge-' + row.status;
      line.append(cell);
    }
    line.onclick = () => showHouseholdTrace(row);
    line.onkeydown = event => { if (event.key === 'Enter') line.click(); };
    body.append(line);
  }
  element('details').textContent = JSON.stringify({
    revision: response ? response.revision : null,
    scenario: view.effective_scenario,
    effective_tariff: view.effective_tariff, uncertainty: view.uncertainty}, null, 2);
  const revisionText = response ? ' · 数据 ' + response.revision.slice(0, 12) : '';
  message((heading || '试算完成') + revisionText + ' · 家庭 ' + view.summary['家庭数'] + ' 户');
}

function showHouseholdTrace(row) {
  const trace = {
    家庭: row.household,
    分类: STATUS_TEXT[row.status],
    不能确定或需提示的原因: row.reasons,
    逐账单过程: row.bill_steps,
    家庭层面汇总过程: row.steps,
    结果区间: {
      净支出: [row.net_spend_low, row.net_spend_point, row.net_spend_high],
      负担率: [row.ratio_low, row.ratio_high]},
    未满足需要: {账单数: row.unmet_need_bills, 服务费缺口估计: row.unmet_need_gap},
  };
  element('details').textContent = JSON.stringify(trace, null, 2);
}

function drawCdf(view) {
  const canvas = element('cdf-chart');
  const context = canvas.getContext('2d');
  context.clearRect(0, 0, canvas.width, canvas.height);
  const points = view.cdf;
  context.font = '12px sans-serif';
  if (!points.length) { context.fillText('没有可绘制的有限负担率区间', 30, 50); return; }
  const maxX = Math.max(0.5, ...points.map(point => point.x));
  const mapX = x => 55 + Math.min(x, maxX) / maxX * 870;
  const mapY = y => 250 - y * 210;
  context.strokeStyle = '#90a5ad';
  context.beginPath(); context.moveTo(45, 20); context.lineTo(45, 250); context.lineTo(940, 250); context.stroke();
  const line = view.effective_scenario.burden_line;
  if (line <= maxX) {
    context.strokeStyle = '#9c302d'; context.setLineDash([6, 4]);
    context.beginPath(); context.moveTo(mapX(line), 20); context.lineTo(mapX(line), 250); context.stroke();
    context.setLineDash([]);
    context.fillStyle = '#9c302d';
    context.fillText('负担线 ' + line, mapX(line) + 4, 32);
  }
  const path = (key, color, fill) => {
    context.strokeStyle = color; context.fillStyle = color + '22';
    context.beginPath();
    context.moveTo(mapX(points[0].x), mapY(points[0][key]));
    for (const point of points) context.lineTo(mapX(point.x), mapY(point[key]));
    context.stroke();
    if (fill) {
      context.lineTo(mapX(points[points.length - 1].x), mapY(0));
      context.lineTo(mapX(points[0].x), mapY(0)); context.closePath(); context.fill();
    }
  };
  path('lower', '#15634b', false);
  path('upper', '#2e7da8', false);
  context.fillStyle = '#29434e';
  context.fillText('绿线：负担率确定不超过 x 的加权累计；蓝线：可能不超过 x 的加权累计（区间带）', 50, 18);
  context.fillText('横轴截断于 ' + maxX.toFixed(2) + '；纵轴为加权累计比例', 50, 275);
}

function buildScenario() {
  const scenario = {
    ...scopeSelection(),
    mode: element('trial-mode').value,
    burden_line: Number(element('trial-line').value),
    missing_transport: element('trial-missing').value,
  };
  const hours = numberOrNull(element('trial-hours').value);
  if (hours !== null) scenario.uniform_hours = hours;
  const override = {};
  const mapping = [['tariff-price', 'hour_price'], ['tariff-share', 'eligible_share'],
    ['tariff-cap', 'household_cap'], ['tariff-deduction', 'disposable_deduction']];
  for (const [identifier, field] of mapping) {
    const value = numberOrNull(element(identifier).value);
    if (value !== null) override[field] = value;
  }
  if (Object.keys(override).length) scenario.tariff_override = override;
  return scenario;
}

function renderComparison(result) {
  state.comparison = result;
  const view = result.view;
  const box = element('compare-box');
  box.replaceChildren();
  const intro = document.createElement('p');
  intro.textContent = view.common_base.note + ' 共同家庭 ' + view.common_base.household_count +
    ' 户，权重合计 ' + view.common_base.weight_sum + '。';
  box.append(intro);
  const over = view.over_burden_share;
  const ciText = over.delta_95pct_ci === null ? '区间不可用' :
    '[' + over.delta_95pct_ci[0].toFixed(2) + ', ' + over.delta_95pct_ci[1].toFixed(2) + '] 个百分点';
  const line = document.createElement('p');
  line.textContent = '超过负担线比例：基准 ' + over.base_pct.toFixed(2) + '% → 对比 ' +
    over.alternative_pct.toFixed(2) + '%，变化 ' + over.delta_pct.toFixed(2) +
    ' 个百分点，配对自助95%区间 ' + ciText + '。';
  line.className = over.delta_pct > 0 ? 'delta-up' : (over.delta_pct < 0 ? 'delta-down' : 'delta-flat');
  box.append(line);
  const list = document.createElement('ul');
  for (const delta of view.component_delta) {
    const item = document.createElement('li');
    item.textContent = delta.component + '：加权户均变化 ' +
      (delta.delta_weighted_mean >= 0 ? '+' : '') + delta.delta_weighted_mean.toFixed(2) + ' 元';
    list.append(item);
  }
  box.append(list);
  for (const note of view.notes) {
    const paragraph = document.createElement('p'); paragraph.textContent = '· ' + note; box.append(paragraph);
  }
}

async function refresh() {
  state.context = await request('/api/context');
  renderControls();
  element('archive').replaceChildren();
  for (const notebook of state.context.notebooks) {
    const button = document.createElement('button');
    const kindText = notebook.kind === 'trial' ? '试算' : (notebook.kind === 'compare' ? '对比' : '浏览');
    button.textContent = '[' + kindText + '] ' + notebook.title + ' #' + notebook.id;
    button.onclick = () => guarded(async () => {
      const saved = await request('/api/notebooks/' + notebook.id);
      element('revision').value = saved.revision;
      if (saved.kind === 'explore') {
        switchMode('explore');
        for (const [key, value] of Object.entries(saved.selection)) {
          const control = element('select-' + key); if (control) control.value = value;
        }
        renderExplore(saved.result, '已读取保存的描述，不随新数据变化');
        element('details').textContent = JSON.stringify(
          {revision: saved.revision, selection: saved.selection, history: null}, null, 2);
      } else if (saved.kind === 'trial') {
        switchMode('trial');
        hydrateTrialControls(saved.selection);
        renderTrial(saved.result.view, '已读取保存的试算，不随新数据变化', saved.result);
      } else {
        switchMode('trial');
        hydrateTrialControls(saved.selection.base);
        renderTrial(saved.result.view.base_view, '已读取保存的对比：基准情景');
        state.comparison = saved.result;
        renderComparison(saved.result);
      }    });
    element('archive').append(button);
  }
}

function hydrateTrialControls(scenario) {
  for (const [key, value] of Object.entries(scenario)) {
    const control = element('select-' + key); if (control) control.value = value;
  }
  element('trial-mode').value = scenario.mode || 'observed';
  element('trial-line').value = String(scenario.burden_line ?? 0.30);
  element('trial-missing').value = scenario.missing_transport || 'impute_median';
  element('trial-hours').value = scenario.uniform_hours ?? '';
  const override = scenario.tariff_override || {};
  element('tariff-price').value = override.hour_price ?? '';
  element('tariff-share').value = override.eligible_share ?? '';
  element('tariff-cap').value = override.household_cap ?? '';
  element('tariff-deduction').value = override.disposable_deduction ?? '';
}

element('tab-explore').onclick = () => switchMode('explore');
element('tab-trial').onclick = () => switchMode('trial');
element('inspect').onclick = () => guarded(async () =>
  renderExplore(await request('/api/explore', {...selectedRevision(), selection: scopeSelection()})));
element('run-trial').onclick = () => guarded(async () => {
  const response = await request('/api/trial', {...selectedRevision(), scenario: buildScenario()});
  renderTrial(response.view, undefined, response);
});
element('run-compare').onclick = () => guarded(async () => {
  const base = buildScenario();
  const alternative = {...base, period: element('alt-period').value, mode: element('alt-mode').value};
  const response = await request('/api/compare', {...selectedRevision(), base, alternative});
  renderTrial(response.view.base_view, '对比基准情景已试算', response);
  renderComparison(response);
});
element('save').onclick = () => guarded(async () => {
  const title = element('title').value;
  let saved;
  if (state.mode === 'explore') {
    saved = await request('/api/notebooks',
      {...selectedRevision(), title, kind: 'explore', selection: scopeSelection()});
  } else if (!state.comparison) {
    saved = await request('/api/notebooks',
      {...selectedRevision(), title, kind: 'trial', scenario: buildScenario()});
  } else {
    const base = buildScenario();
    const alternative = {...base, period: element('alt-period').value, mode: element('alt-mode').value};
    saved = await request('/api/notebooks',
      {...selectedRevision(), title, kind: 'compare', base, alternative});
  }
  await refresh();
  message('已保存记录 #' + saved.id);
  if (state.mode === 'explore') {
    element('details').textContent = JSON.stringify(
      {revision: saved.revision, selection: saved.selection, history: null}, null, 2);
  }
});
element('raw').onclick = () => guarded(async () => {
  const revision = state.current ? state.current.revision : element('revision').value;
  element('details').textContent = JSON.stringify(
    await request('/api/data?revision=' + encodeURIComponent(revision)), null, 2);
});
element('upload').onchange = () => guarded(async () => {
  const file = element('upload').files[0]; if (!file) return;
  const data = JSON.parse(await file.text());
  const uploaded = await request('/api/datasets', {label: file.name, data});
  await refresh(); element('revision').value = uploaded.revision;
  renderExplore(await request('/api/explore',
    {...selectedRevision(), selection: scopeSelection()}), '已导入独立数据版本');
});
element('export').onclick = () => guarded(async () => {
  if (!state.current) throw new Error('请先查看一个范围或运行一次试算');
  const data = await request('/api/data?revision=' + encodeURIComponent(state.current.revision));
  const blob = new Blob([JSON.stringify({description: state.current, data}, null, 2)], {type: 'application/json'});
  const url = URL.createObjectURL(blob); const link = document.createElement('a');
  link.href = url; link.download = 'analysis-' + state.current.revision.slice(0, 12) + '.json'; link.click();
  URL.revokeObjectURL(url);
});

guarded(async () => {
  await refresh();
  renderExplore(await request('/api/explore',
    {...selectedRevision(), selection: scopeSelection()}));
});
