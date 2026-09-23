const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
for (const kind of ['quotes', 'orders']) {
  const source = fs.readFileSync('app/templates/' + kind + '/form.html', 'utf8');
  const recalc = source.slice(source.indexOf('  function recalc()'), source.indexOf('  function escapeHtml'));
  for (const mode of ['normal', 'project']) {
    for (const taxMode of ['none', 'tax', 'inclusive']) {
      const fields = Object.fromEntries(['.js-qty', '.js-price', '.js-variant-count', '.js-single-subtotal', '.js-subtotal'].map(k => [k, {}]));
      fields['.js-qty'].value = 56;
      fields['.js-price'].value = 350;
      fields['.js-variant-count'].value = 1;
      const row = {querySelector: s => fields[s]};
      const root = {querySelectorAll: () => [row], contains: () => true};
      const label = {};
      const output = () => ({previousElementSibling: {}});
      const ctx = {
        normalSection: root, projectSection: root, money: n => n,
        itemsSubtotalEl: output(), taxAmountEl: output(), totalEl: output(),
        document: {
          querySelector: s => ({value: s.includes('tax_mode') ? taxMode : mode}),
          querySelectorAll: s => s === '.js-price-label' ? [label] : [row]
        }
      };
      vm.createContext(ctx);
      vm.runInContext(recalc + '\nrecalc();', ctx);
      assert.equal(ctx.totalEl.textContent, taxMode === 'tax' ? 20580 : 19600);
      assert.equal(ctx.taxAmountEl.textContent, taxMode === 'inclusive' ? 933 : (taxMode === 'tax' ? 980 : 0));
      if (taxMode === 'inclusive') {
        assert.equal(ctx.itemsSubtotalEl.textContent, 18667);
        assert.equal(label.textContent, '含稅單價');
      }
    }
  }
}
console.log('Both forms: normal/project × three tax modes passed.');
