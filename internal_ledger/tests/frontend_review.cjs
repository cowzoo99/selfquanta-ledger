const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createRequire} = require('node:module');
const benchRequire = createRequire(path.resolve(__dirname, '../../../frappe/package.json'));
const Vue = benchRequire('vue');
global.document = {activeElement: null};
const {parse, compileScript, compileTemplate} = benchRequire('@vue/compiler-sfc');
const src = path.resolve(__dirname, '../public/frontend/src');
let calls = [];
global.frappe = {session: {user: 'Administrator'}, user_roles: [], boot: {lang: 'zh'},
  datetime: {get_today: () => '2026-10-01'}, call: async args => {calls.push(args); return {message: []};}};
const labels = new Function(fs.readFileSync(path.join(src, 'labels.js'), 'utf8').replaceAll('export const ', 'const ') +
  '; return {directions,sources,transactionTypes,draftStatuses,processingStatuses,classificationStatuses};')();
function node(type, text = '') {
  return {type, text, children: [], props: {}, parent: null, options: [], addEventListener() {}, removeEventListener() {}};
}
const renderer = Vue.createRenderer({
  createElement: node, createText: text => node('#text', text), createComment: text => node('#comment', text),
  setText: (el, text) => {el.text = text;}, setElementText: (el, text) => {el.text = text; el.children = [];},
  patchProp: (el, key, previous, next) => {el.props[key] = next;},
  insert: (el, parent, anchor) => {if (el.parent) el.parent.children.splice(el.parent.children.indexOf(el), 1);
    const index = anchor ? parent.children.indexOf(anchor) : -1;
    parent.children.splice(index < 0 ? parent.children.length : index, 0, el); el.parent = parent;},
  remove: el => {if (el.parent) el.parent.children.splice(el.parent.children.indexOf(el), 1);},
  parentNode: el => el.parent, nextSibling: el => el.parent?.children[el.parent.children.indexOf(el) + 1],
});
function compile(file, realChildren = false) {
  const {descriptor} = parse(fs.readFileSync(path.join(src, file), 'utf8'), {filename: file});
  const script = compileScript(descriptor, {id: file});
  const compiled = script.content.replace(/import\s+([^;]+?)\s+from\s+['"]([^'"]+)['"];?/g, (_, binding, name) => {
    if (binding.startsWith('{')) return `const ${binding.replace(/\bas\b/g, ':')} = load(${JSON.stringify(name)});`;
    return `const ${binding} = load(${JSON.stringify(name)});`;
  }).replace('export default ', 'return ');
  const component = new Function('load', compiled)(name => {
    if (name === 'vue') return Vue;
    if (name.endsWith('labels')) return labels;
    if (name.endsWith('api')) return {call: async (method, args = {}) =>
      (await frappe.call({method: 'internal_ledger.api.review_api.' + method, args})).message, errorMessage: e => e.message};
    if (realChildren && /\/(ReviewCenter|DuplicateReview)\.vue$/.test(name)) {
      return compile(path.posix.normalize(path.posix.join(path.posix.dirname(file), name)), true);
    }
    return {name, render() {return Vue.h('test-child', {'data-component': name});}};
  });
  const template = compileTemplate({source: descriptor.template.content, filename: file, id: file,
    compilerOptions: {mode: 'function', bindingMetadata: script.bindings}});
  assert.deepEqual(template.errors, []);
  component.render = new Function('Vue', template.code)(Vue);
  return component;
}
function mount(file, values, realChildren = false) {
  const component = compile(file, realChildren);
  const props = Vue.reactive(values), root = node('root');
  const host = {setup: () => () => Vue.h(component, props)};
  const app = renderer.createApp(host); app.mount(root);
  return {state: root._vnode.component.subTree.component.setupState, props, root, app};
}
function child(vnode, name) {
  if (vnode.component?.type.__name === name) return vnode.component;
  for (const next of [vnode.component?.subTree, ...(Array.isArray(vnode.children) ? vnode.children : [])]) {
    if (next && typeof next === 'object') {const found = child(next, name); if (found) return found;}
  }
}
const settle = async () => {await new Promise(resolve => setImmediate(resolve)); await Vue.nextTick();};
function descendants(root) {return [root, ...root.children.flatMap(descendants)];}
function text(root) {return root.text + root.children.map(text).join('');}
function item(id, batch = 'new', values = {}) {
  return {transaction: {name: 'tx-' + id, import_batch: batch, status: 'Imported', direction: 'expense',
      amount: 10, occurred_at: '2026-10-01', counterparty: id, duplicate_review_status: '', ...values},
    draft: {name: id, status: 'Draft', lines: [{account: '费用', debit: 10}, {account: '资金', credit: 10}]},
    error: null, can_approve: true};
}
async function run(name) {
  if (name === 'batch_scope') {
    const current = item('current'), old = item('older', 'old');
    const page = mount('pages/ReviewCenter.vue', {items: [old, current], canWrite: true, loading: false,
      initialFilter: '待审核', initialBatch: 'new'});
    assert.deepEqual(page.state.visibleItems.map(row => row.transaction.name), ['tx-current']);
    assert.deepEqual([...page.state.selected], ['current']);
    page.state.confirmPosting(); await Vue.nextTick();
    assert.equal(page.state.postingSummary.count, 1); assert.equal(page.state.postingSummary.debit, 10);
    assert.match(text(page.root), /批次.*new/);
    assert.equal(calls.length, 0, 'preview never writes');
    page.props.initialBatch = 'old'; await Vue.nextTick();
    assert.deepEqual([...page.state.selected], ['older']); assert.equal(page.state.confirmation, '');
    page.props.initialBatch = ''; await Vue.nextTick();
    assert.equal(page.state.visibleItems.length, 2, 'generic queue remains unscoped');
    page.app.unmount();
  } else if (name === 'refresh_selection') {
    const page = mount('pages/ReviewCenter.vue', {items: [item('one'), item('two')], canWrite: true,
      loading: false, initialFilter: '待审核'});
    page.state.selected = ['one']; page.state.confirmPosting(); assert.ok(page.state.confirmation);
    page.props.loading = true; await Vue.nextTick();
    page.props.items = [item('one'), item('two'), item('new-on-refresh')]; page.props.loading = false;
    await Vue.nextTick();
    assert.deepEqual([...page.state.selected], ['one'], 'refresh must preserve explicit exclusions');
    assert.equal(page.state.confirmation, '', 'refresh invalidates the old confirmation');
    await page.state.postBatch(); assert.equal(calls.length, 0);
    page.props.items = [item('two')]; await Vue.nextTick();
    assert.deepEqual([...page.state.selected], [], 'remove invalid selection');
    page.state.filter = '待分类'; await Vue.nextTick();
    page.state.filter = '待审核'; await Vue.nextTick();
    assert.deepEqual([...page.state.selected], ['two'], 'new scope initializes once');
    page.app.unmount();
    const loading = mount('pages/ReviewCenter.vue', {items: [], canWrite: true, loading: true, initialFilter: '待审核'});
    loading.props.items = [item('loaded')]; loading.props.loading = false; await Vue.nextTick();
    assert.deepEqual([...loading.state.selected], ['loaded']); loading.app.unmount();
  } else if (name === 'validation_form') {
    const row = {...item('invalid'), draft: null, classification: null, error: '资金账户验证失败', can_classify: true};
    const page = mount('pages/ReviewCenter.vue', {items: [row], canWrite: true, loading: false, initialFilter: '待分类'});
    page.state.open(row); await Vue.nextTick();
    const manual = () => descendants(page.root).filter(el => el.props['data-component']?.includes('ManualClassificationForm'));
    assert.equal(manual().length, 1, 'validation failures need the existing manual repair form');
    page.props.canWrite = false; await Vue.nextTick(); assert.equal(manual().length, 0);
    page.props.canWrite = true; await Vue.nextTick();
    page.state.open({...row, can_classify: false, transaction: {...row.transaction, status: 'Duplicate', duplicate_review_status: 'Pending'}});
    await Vue.nextTick(); assert.equal(manual().length, 0); page.app.unmount();
  } else if (name === 'pending_duplicates') {
    const pending = {...item('pending', 'new', {status: 'Duplicate', duplicate_review_status: 'Pending'}), draft: null};
    const confirmed = {...item('confirmed', 'new', {status: 'Duplicate', duplicate_review_status: 'Confirmed'}), draft: null};
    const page = mount('pages/ReviewCenter.vue', {items: [pending, confirmed], canWrite: true, loading: false, initialFilter: '疑似重复'});
    assert.deepEqual(page.state.visibleItems.map(row => row.transaction.name), ['tx-pending']);
    const home = mount('pages/Dashboard.vue', {items: [pending, confirmed], month: '2026-10', counts: {}, loading: false});
    assert.equal(home.state.cards[0][2], page.state.visibleItems.length);
    page.app.unmount(); home.app.unmount();
  } else if (['import_scope_total', 'import_total'].includes(name)) {
    const page = mount('pages/ImportPreview.vue', {canWrite: true});
    page.state.result = {batch_name: 'batch/一', summary: {total: 3, created: 1, duplicate: 1, manual: 1, conflict: 0, failed: 0}};
    await Vue.nextTick();
    const link = descendants(page.root).find(el => el.type === 'a' && text(el).includes('查看自动生成'));
    if (name === 'import_total') assert.match(text(page.root), /共\s*3\s*笔/);
    else {
      assert.equal(link.props.href, '/app/ledger-workbench/review/待审核/batch%2F%E4%B8%80');
      assert.equal(page.state.remainingRoute, '/app/ledger-workbench/review/疑似重复/batch%2F%E4%B8%80');
      page.state.result.summary.duplicate = 0; await Vue.nextTick();
      assert.equal(page.state.remainingRoute, '/app/ledger-workbench/review/待分类/batch%2F%E4%B8%80');
    }
    page.app.unmount();
  } else if (name === 'route_scope') {
    const mounts = [], wrapper = {ledger_ready: true, ledger_root: {}};
    let route = ['ledger-workbench', 'review', '待审核', 'batch/一'];
    const context = {frappe: {pages: {'ledger-workbench': {}}, get_route: () => route},
      internal_ledger: {mount_workbench: (root, options) => {mounts.push(options); return {unmount() {}};}}};
    vm.runInNewContext(fs.readFileSync(path.join(src, '../../../internal_ledger/page/ledger_workbench/ledger_workbench.js'), 'utf8'), context);
    context.frappe.pages['ledger-workbench'].on_page_show(wrapper);
    assert.equal(mounts[0].batch, 'batch/一');
    route = ['ledger-workbench', 'review', '待审核', 'other']; context.frappe.pages['ledger-workbench'].on_page_show(wrapper);
    assert.equal(mounts.length, 2); assert.equal(mounts[1].batch, 'other');
    const page = mount('App.vue', {view: 'review', filter: '待审核', batch: 'batch/一'});
    const review = descendants(page.root).find(el => el.props['data-component']?.includes('ReviewCenter'));
    assert.equal(review.props['initial-batch'], 'batch/一'); page.app.unmount();
  } else if (['async_initial_selection', 'pending_buttons', 'pending_handlers'].includes(name)) {
    let finish;
    let pending = new Promise(resolve => {finish = resolve;});
    frappe.call = async args => {calls.push(args);
      return args.method.endsWith('list_review_items') ? pending : {message: []};};
    const page = mount('App.vue', {view: 'review', filter: '待审核', batch: 'new'}, true);
    const review = () => child(page.root._vnode, 'ReviewCenter').setupState;
    assert.equal(page.state.loading, true);
    finish({message: [item('old', 'old'), item('one')]}); await settle();
    if (name === 'async_initial_selection') {
      assert.deepEqual([...review().selected], ['one'], 'actual App onMounted response initializes the child current scope');
    } else {
      review().selected = ['one']; review().confirmPosting();
      const oldConfirmation = review().confirmation; assert.ok(oldConfirmation);
      pending = new Promise(resolve => {finish = resolve;});
      const refresh = page.state.refresh(); await Vue.nextTick();
      assert.equal(review().confirmation, '', 'pending refresh invalidates the old confirmation');
      if (name === 'pending_buttons') {
        const preview = descendants(page.root).find(el => el.type === 'button' && text(el).includes('审核并过账选中项'));
        assert.equal(preview.props.disabled, true, 'preview is disabled during pending refresh');
        review().confirmation = oldConfirmation; await Vue.nextTick();
        const confirm = descendants(page.root).find(el => el.type === 'button' && text(el) === '确认审核并过账');
        assert.equal(confirm.props.disabled, true, 'formal confirmation is disabled during pending refresh');
      } else {
        review().confirmPosting(); const reopened = review().confirmation;
        review().confirmation = oldConfirmation;
        await review().postBatch();
        assert.equal(calls.filter(row => row.method.endsWith('approve_and_post')).length, 0,
          'handler guard must prevent RPC even when called directly with an old valid confirmation');
        assert.equal(reopened, '', 'preview handler cannot reopen confirmation while pending');
      }
      finish({message: [item('one')]}); await refresh; await settle();
    }
    page.app.unmount();
  } else if (name === 'duplicate_scope_link') {
    const row = {...item('new'), draft: null}; row.transaction.status = 'Duplicate'; row.transaction.duplicate_review_status = 'Pending';
    const page = mount('pages/ReviewCenter.vue', {items: [row], canWrite: true, loading: false,
      initialFilter: '疑似重复', initialBatch: 'new'});
    page.state.open(row); await Vue.nextTick();
    const link = descendants(page.root).find(el => el.type === 'a' && text(el) === '前往重复流水复核');
    assert.equal(link.props.href, '/app/ledger-workbench/duplicates/疑似重复/new/tx-new');
    page.app.unmount();
  } else if (name === 'duplicate_scoped_component') {
    const pair = (id, batch, status = 'Pending') => ({candidate: {...item(id, batch).transaction,
      status: 'Duplicate', duplicate_review_status: status}, original: item('original-' + id, batch).transaction});
    const older = pair('old', 'old', 'Confirmed'), current = pair('new', 'new');
    let released = false;
    frappe.call = async args => {calls.push(args);
      if (args.method.endsWith('resolve_duplicate')) {released = true; return {message: {processing: {status: 'manual'}}};}
      return {message: [older, {...current, candidate: {...current.candidate, status: released ? 'Imported' : 'Duplicate', duplicate_review_status: released ? 'Released' : 'Pending'}}]};};
    const page = mount('pages/DuplicateReview.vue', {canWrite: true, batch: 'new', transaction: 'tx-new'}); await settle();
    assert.deepEqual(page.state.items.map(row => row.candidate.name), ['tx-new']);
    assert.deepEqual(calls[0].args, {batch: 'new', transaction: 'tx-new'});
    page.state.notes = {'tx-new': '本批独立交易', 'tx-old': '越界尝试'};
    await page.state.resolve({...older, candidate: {...older.candidate, duplicate_review_status: 'Pending'}}, 'release');
    assert.equal(calls.filter(row => row.method.endsWith('resolve_duplicate')).length, 0);
    await page.state.resolve(current, 'release'); await settle();
    const write = calls.find(row => row.method.endsWith('resolve_duplicate'));
    assert.deepEqual(write.args, {name: 'tx-new', decision: 'release', note: '本批独立交易', batch: 'new', transaction: 'tx-new'});
    const hrefs = descendants(page.root).filter(el => el.type === 'a').map(el => el.props.href);
    assert.ok(hrefs.includes('/app/ledger-workbench/review/疑似重复/new/tx-new'));
    assert.ok(hrefs.includes('/app/ledger-workbench/review/待分类/new/tx-new'));
    page.app.unmount();
    const legacy = mount('pages/DuplicateReview.vue', {canWrite: true}); await settle();
    assert.deepEqual(legacy.state.items.map(row => row.candidate.name), ['tx-old', 'tx-new']);
    assert.match(text(legacy.root), /已确认重复/); legacy.app.unmount();
    const review = mount('pages/ReviewCenter.vue', {items: [item('one'), item('two')], canWrite: true, loading: false,
      initialFilter: '待审核', initialBatch: 'new', initialTransaction: 'tx-one'});
    assert.deepEqual(review.state.visibleItems.map(row => row.transaction.name), ['tx-one']); review.app.unmount();
  } else if (name === 'duplicate_route_scope') {
    const mounts = [], wrapper = {ledger_ready: true, ledger_root: {}};
    let route = ['ledger-workbench', 'duplicates', '疑似重复', 'new', 'tx-new'];
    const context = {frappe: {pages: {'ledger-workbench': {}}, get_route: () => route},
      internal_ledger: {mount_workbench: (root, options) => {mounts.push(options); return {unmount() {}};}}};
    vm.runInNewContext(fs.readFileSync(path.join(src, '../../../internal_ledger/page/ledger_workbench/ledger_workbench.js'), 'utf8'), context);
    context.frappe.pages['ledger-workbench'].on_page_show(wrapper);
    assert.equal(mounts[0].transaction, 'tx-new');
    route = ['ledger-workbench', 'duplicates', '疑似重复', 'new', 'tx-other'];
    context.frappe.pages['ledger-workbench'].on_page_show(wrapper); assert.equal(mounts.length, 2);
    frappe.call = async args => {calls.push(args); return {message: []};};
    const page = mount('App.vue', {view: 'duplicates', batch: 'new', transaction: 'tx-new'}, true); await settle();
    const duplicate = child(page.root._vnode, 'DuplicateReview');
    assert.equal(duplicate.props.batch, 'new'); assert.equal(duplicate.props.transaction, 'tx-new'); page.app.unmount();
  } else throw new Error('unknown case ' + name);
  console.log('PASS ' + name);
}
run(process.argv[2]).catch(error => {console.error(error); process.exitCode = 1;});
