(() => {
  const panel = document.getElementById('calibration-panel');
  if (!panel) return;
  const form = panel.closest('form');
  const target = document.getElementById('calibration-target');
  const selector = document.getElementById('calibration-item');
  const output = document.getElementById('calibration-result');
  const confirm = document.getElementById('calibration-confirm');
  const signature = document.getElementById('calibration-signature');
  const preview = document.getElementById('calibration-preview');
  let snapshot = '', latest = null, generation = 0;
  const money = n => Number(n).toLocaleString('zh-TW', {maximumFractionDigits: 10});
  const escape = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const active = () => form.querySelector('[name="tax_mode"]:checked')?.value === 'calibrated';
  function item(row) {
    const get = cls => row.querySelector(cls)?.value.trim() || '';
    return {product_name:get('.js-product'), material:get('.js-material'), size:get('.js-size'),
      quantity:get('.js-qty'), variant_count:get('.js-variant-count'), unit:get('.js-unit'),
      unit_price:get('.js-price'), note:get('.js-note')};
  }
  function payload() {
    const mode = form.querySelector('[name="mode"]:checked')?.value || 'normal';
    const data = {mode, target_total:target.value, adjustment_index:selector.value};
    if (mode === 'project') {
      data.work_units = [...form.querySelectorAll('.work-unit')].map(unit => ({
        name:unit.querySelector('.js-work-unit-name').value.trim(),
        items:[...unit.querySelectorAll('.js-item-row')].map(item)
      }));
    } else {
      data.items = [...form.querySelectorAll('.js-item-row')].filter(r => !r.closest('.work-unit')).map(item);
    }
    return data;
  }
  function rows(data) {
    return (data.mode === 'project' ? data.work_units.flatMap(u => u.items) : data.items).filter(i => i.product_name);
  }
  function invalidate() {
    generation++;
    snapshot = ''; latest = null; signature.value = '';
    confirm.checked = false; confirm.disabled = true;
    output.textContent = '請計算校準明細並確認。';
  }
  function totals(result) {
    for (const [id, value, label] of [
      ['items-subtotal', result.subtotal, '校準後未稅合計'],
      ['tax-amount', result.tax_amount, '稅額（5%）']
    ]) {
      const el = document.getElementById(id);
      el.textContent = 'NT$ ' + money(value);
      el.previousElementSibling.textContent = label;
    }
    const total = document.getElementById('quote-total') || document.getElementById('order-total');
    total.textContent = 'NT$ ' + money(result.total);
  }
  function sync() {
    panel.hidden = !active();
    target.disabled = !active();
    selector.disabled = !active();
    if (!active()) return;
    const data = payload(), items = rows(data);
    const previous = selector.value || selector.dataset.initial;
    const names = items.map((r,i) => [String(i), (i + 1) + '. ' + r.product_name]);
    if (JSON.stringify(names) !== selector.dataset.options) {
      selector.replaceChildren(...names.map(([value, name]) => new Option(name, value)));
      selector.value = names.some(([v]) => v === previous) ? previous : String(items.length - 1);
      selector.dataset.options = JSON.stringify(names);
      selector.dataset.initial = '';
    }
    if (snapshot && snapshot !== JSON.stringify(payload())) invalidate();
    if (latest) totals(latest);
    else {
      for (const id of ['items-subtotal', 'tax-amount', 'quote-total', 'order-total']) {
        const el = document.getElementById(id);
        if (el) el.textContent = '待校準';
      }
    }
  }
  form.addEventListener('input', e => {
    if (e.target === confirm) return;
    invalidate(); sync();
  });
  form.addEventListener('change', e => {
    if (e.target === confirm) {
      signature.value = confirm.checked && latest ? latest.signature : '';
      return;
    }
    invalidate(); sync();
  });
  form.addEventListener('click', () => setTimeout(sync, 0));
  // Capture submission before the existing item serializers.
  form.addEventListener('submit', e => {
    if (!active()) return;
    sync();
    if (!confirm.checked || !latest || snapshot !== JSON.stringify(payload())) {
      e.preventDefault();
      output.textContent = '內容尚未校準或已變動，請重新計算並勾選確認後儲存。';
      panel.scrollIntoView({block:'center'});
    }
  }, true);
  preview.onclick = async () => {
    sync(); invalidate();
    const data = payload(), requestSnapshot = JSON.stringify(data), requestGeneration = generation;
    preview.disabled = true;
    output.textContent = '計算中…';
    try {
      const response = await fetch('/api/pricing/calibrate', {
        method:'POST', headers:{'Content-Type':'application/json'}, body:requestSnapshot
      });
      const result = await response.json();
      if (generation !== requestGeneration || JSON.stringify(payload()) !== requestSnapshot) return;
      if (!response.ok || !result.ok) throw new Error(result.error || '計算失敗');
      snapshot = requestSnapshot; latest = result;
      output.innerHTML = '<div style="overflow:auto"><table><thead><tr><th>品項</th><th>約定含稅單價</th><th>最終未稅單價</th><th>原未稅金額</th><th>調整</th><th>最終未稅金額</th></tr></thead><tbody>' +
        result.items.map(r => '<tr><td>' + escape(r.product_name) + '</td><td>' + money(r.source_price) +
          '</td><td>' + escape(r.unit_price) + '</td><td>' + money(r.before) + '</td><td>' +
          (r.adjustment > 0 ? '+' : '') + money(r.adjustment) + '</td><td>' + money(r.subtotal) + '</td></tr>').join('') +
        '</tbody></table></div><p>未稅合計 ' + money(result.subtotal) + ' ＋ 稅額 ' + money(result.tax_amount) +
        ' ＝ 含稅總計 ' + money(result.total) + '</p>';
      confirm.disabled = false;
      totals(result);
    } catch (error) {
      if (generation === requestGeneration) output.textContent = error.message;
    } finally { preview.disabled = false; }
  };
  invalidate(); sync();
})();
