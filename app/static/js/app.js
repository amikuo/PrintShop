(function () {
  function q(sel, root=document){ return root.querySelector(sel); }
  function qa(sel, root=document){ return Array.from(root.querySelectorAll(sel)); }

  window.__ps = {
    fmtMoney(n){
      const x = Number(n || 0);
      return new Intl.NumberFormat('zh-TW').format(isNaN(x) ? 0 : x);
    },
    recalcRow(row){
      const qty = parseFloat(q('.js-qty', row)?.value || '0') || 0;
      const price = parseFloat(q('.js-price', row)?.value || '0') || 0;
      const total = qty * price;
      const out = q('.js-subtotal', row);
      if(out) out.textContent = window.__ps.fmtMoney(total);
      return total;
    },
    updateSummary(root){
      let total = 0;
      qa('.js-item-row', root).forEach(r => total += window.__ps.recalcRow(r));
      const box = q('[data-summary-total]', root);
      if(box) box.textContent = window.__ps.fmtMoney(total);
      return total;
    },
    cloneTemplate(templateId){
      const tpl = q(templateId);
      return tpl ? tpl.content.cloneNode(true) : null;
    },
    buildTags(inputEl, boxEl, values){
      const selected = new Set((inputEl.value || '').split(',').map(s=>s.trim()).filter(Boolean));
      const render = () => {
        boxEl.innerHTML = '';
        selected.forEach(v => {
          const span = document.createElement('span');
          span.className = 'tag';
          span.innerHTML = `<span>${v}</span><button type="button" aria-label="remove">×</button>`;
          span.querySelector('button').onclick = () => { selected.delete(v); sync(); };
          boxEl.appendChild(span);
        });
      };
      const sync = () => { inputEl.value = Array.from(selected).join(','); render(); };
      render();
      return {
        toggle(v){ selected.has(v) ? selected.delete(v) : selected.add(v); sync(); },
        set(vs){ selected.clear(); vs.forEach(v=>selected.add(v)); sync(); },
        values(){ return Array.from(selected); }
      };
    },
    customerAutocomplete(input, hidden, endpoint) {
      const box = document.createElement('div');
      box.className = 'picker-menu';
      input.parentElement.classList.add('picker');
      input.parentElement.appendChild(box);
      let timer = null;
      input.addEventListener('input', () => {
        hidden.value = '';
        const q = input.value.trim();
        clearTimeout(timer);
        if(!q){ box.classList.remove('show'); box.innerHTML=''; return; }
        timer = setTimeout(async () => {
          const res = await fetch(`${endpoint}?q=${encodeURIComponent(q)}`);
          const data = await res.json();
          box.innerHTML = '';
          data.forEach(row => {
            const div = document.createElement('div');
            div.className = 'picker-item';
            div.innerHTML = `<strong>${row.name}</strong><div class="muted small">${row.phone || ''} ${row.tax_id || ''} ${row.category || ''}</div>`;
            div.onclick = () => { input.value = row.name; hidden.value = row.id; box.classList.remove('show'); };
            box.appendChild(div);
          });
          box.classList.toggle('show', data.length > 0);
        }, 180);
      });
      document.addEventListener('click', (e) => {
        if(!input.parentElement.contains(e.target)){ box.classList.remove('show'); }
      });
    },
    specAutocomplete(input, onPick, endpoint) {
      const box = document.createElement('div');
      box.className = 'picker-menu';
      input.parentElement.classList.add('picker');
      input.parentElement.appendChild(box);
      let timer = null;
      input.addEventListener('input', () => {
        const q = input.value.trim();
        clearTimeout(timer);
        if(!q){ box.classList.remove('show'); box.innerHTML=''; return; }
        timer = setTimeout(async () => {
          const res = await fetch(`${endpoint}?q=${encodeURIComponent(q)}`);
          const data = await res.json();
          box.innerHTML = '';
          data.forEach(row => {
            const div = document.createElement('div');
            div.className = 'picker-item';
            div.innerHTML = `<strong>${row.product_name}</strong><div class="muted small">${row.material || ''} ${row.size || ''} ${row.unit || ''}</div>`;
            div.onclick = () => { onPick(row); box.classList.remove('show'); input.value=''; };
            box.appendChild(div);
          });
          box.classList.toggle('show', data.length > 0);
        }, 180);
      });
      document.addEventListener('click', (e) => {
        if(!input.parentElement.contains(e.target)){ box.classList.remove('show'); }
      });
    },
    finishingPicker(inputEl, boxEl, options){
      const state = { values: new Set((inputEl.value || '').split(',').map(s=>s.trim()).filter(Boolean)) };
      const render = () => {
        boxEl.innerHTML = '';
        state.values.forEach(v => {
          const span = document.createElement('span');
          span.className = 'tag';
          span.innerHTML = `<span>${v}</span><button type="button">×</button>`;
          span.querySelector('button').onclick = () => { state.values.delete(v); sync(); };
          boxEl.appendChild(span);
        });
        if(!state.values.size){
          const m = document.createElement('div');
          m.className = 'muted';
          m.textContent = '尚未選擇後加工';
          boxEl.appendChild(m);
        }
      };
      const sync = () => { inputEl.value = Array.from(state.values).join(','); render(); };
      render();

      const menu = document.createElement('div');
      menu.className = 'picker-menu';
      menu.style.maxHeight = '180px';
      options.forEach(opt => {
        const item = document.createElement('div');
        item.className = 'picker-item';
        item.textContent = opt.name;
        item.onclick = () => {
          state.values.has(opt.name) ? state.values.delete(opt.name) : state.values.add(opt.name);
          sync();
          menu.classList.remove('show');
        };
        menu.appendChild(item);
      });
      inputEl.parentElement.classList.add('picker');
      inputEl.parentElement.appendChild(menu);
      inputEl.readOnly = true;
      inputEl.addEventListener('click', (e) => {
        e.stopPropagation();
        menu.classList.add('show');
      });
      document.addEventListener('click', (e) => {
        if(!inputEl.parentElement.contains(e.target)){ menu.classList.remove('show'); }
      });
      return { sync };
    }
  };
})();
