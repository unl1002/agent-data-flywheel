'use strict';

// Run with: node --test agent-data-flywheel/tests/test_dashboard.cjs
// Execute the real, DOM-independent functions from the shipped HTML.
// A regression that accepts a numeric zero as proof of review must fail here.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const html = fs.readFileSync(path.join(__dirname, '..', 'assets', 'dashboard.html'), 'utf8');
const begin = html.indexOf('function isObject(');
const end = html.indexOf('function date(');
assert.ok(begin >= 0 && end > begin, 'real dashboard functions must be available for execution');
const actualFunctions = html.slice(begin, end);

function runtime() {
  const state = { bundle: null, reviewMap: new Map() };
  const context = vm.createContext({ state });
  vm.runInContext(actualFunctions, context, { filename: 'dashboard-functions.js' });
  return { state, api: context };
}

function analysis() {
  return {
    schema_version: '1.0',
    provenance: { mode: 'document_snapshot', source_files: ['document.md'], notes: [] },
    sources: { dialogue: { status: 'document_snapshot', records: 468 } },
    metrics: { dialogue_records: { label: '对话记录', value: 468, unit: 'count', basis: 'document_snapshot' } },
    distributions: {},
    records: [{ id: 'dialogue:example', source: 'dialogue', question: '查询设备数量', answer: '', evidence: [], signals: [] }],
    quality_issues: []
  };
}

function item() {
  return {
    record_id: 'dialogue:example', validity: 'valid', intent: ['data_query'],
    support: 'uncertain', badcase: 'uncertain', correctness: 'uncertain',
    root_cause: 'unknown', reason: '未提供能力范围及评测依据。',
    evidence_refs: [], capability_refs: [], evaluation_refs: [], confidence: 0.7
  };
}

function reviewed(status = 'completed', items = [item()]) {
  return {
    analysis: analysis(),
    review: {
      status, scope: 'document_examples', items,
      metrics: {
        reviewed_records: { label: '已评审记录', value: items.length, unit: 'count' },
        valid_questions: { label: '有效问题', value: items.length, unit: 'count' },
        unsupported_data_queries: { label: '已确认不支持的问数', value: 0, unit: 'count' },
        confirmed_badcases: { label: '确认 BadCase', value: 0, unit: 'count' },
        accuracy: { label: '准确率', value: null, unit: 'ratio' }
      },
      coverage: { reviewed: items.length, available: 1, ratio: items.length, by_source: [] },
      notes: []
    }
  };
}

test('not_run with numeric zero metrics remains unreviewed', () => {
  const { state, api } = runtime();
  state.bundle = api.normalizeBundle(reviewed('not_run', []));
  assert.equal(api.hasReview(), false, '0 reviewed records is not proof that AI ran');
});

test('not_run metrics render an unreviewed placeholder rather than a measured zero', () => {
  const { state, api } = runtime();
  state.bundle = api.normalizeBundle(reviewed('not_run', []));
  assert.equal(api.metric('valid_questions', true), null);
  assert.equal(api.metric('unsupported_data_queries', true), null);
  assert.equal(api.metric('confirmed_badcases', true), null);
  assert.equal(api.formattedMetric(api.metric('valid_questions', true), '未评审'), '未评审');
  assert.equal(api.metric('dialogue_records').value, 468, 'source statistics remain independent of AI state');
});

test('completed reviews with actual items preserve measured zero and unknown accuracy', () => {
  const { state, api } = runtime();
  state.bundle = api.normalizeBundle(reviewed());
  state.reviewMap = new Map(state.bundle.review.items.map(value => [value.record_id, value]));
  assert.equal(api.hasReview(), true);
  assert.equal(api.metric('valid_questions', true).value, 1);
  assert.equal(api.formattedMetric(api.metric('confirmed_badcases', true), '未评审'), '0');
  assert.equal(api.formattedMetric(api.metric('accuracy', true), '待评测'), '待评测');
  assert.equal(api.reviewFor(state.bundle.analysis.records[0]).support, 'uncertain');
});

test('completed status without items does not claim completed evaluation', () => {
  const { state, api } = runtime();
  state.bundle = api.normalizeBundle(reviewed('completed', []));
  assert.equal(api.hasReview(), false);
  assert.equal(api.metric('confirmed_badcases', true), null);
});

test('non-completed status does not expose tentative item labels', () => {
  const { state, api } = runtime();
  state.bundle = api.normalizeBundle(reviewed('in_progress'));
  state.reviewMap = new Map(state.bundle.review.items.map(value => [value.record_id, value]));
  assert.equal(api.hasReview(), false);
  assert.equal(api.metric('valid_questions', true), null);
  assert.equal(api.reviewFor(state.bundle.analysis.records[0]), null);
});

test('loading plain analysis removes previous review metrics and labels', () => {
  const { state, api } = runtime();
  state.bundle = api.normalizeBundle(reviewed());
  state.reviewMap = new Map(state.bundle.review.items.map(value => [value.record_id, value]));
  assert.equal(api.hasReview(), true);
  state.bundle = api.normalizeBundle(analysis());
  assert.equal(state.bundle.review.items.length, 0);
  assert.equal(Object.keys(state.bundle.review.metrics).length, 0);
  assert.equal(api.hasReview(), false);
  assert.equal(api.metric('valid_questions', true), null);
  assert.equal(api.reviewFor(state.bundle.analysis.records[0]), null);
});

test('raw log arrays cannot be mistaken for analysis packages', () => {
  const { api } = runtime();
  assert.throws(() => api.normalizeBundle([{ chatId: 'raw-chat', feedback: -1 }]), /分析包/);
});

test('duplicate normalized record IDs are rejected instead of merging evidence', () => {
  const { api } = runtime();
  const duplicate = analysis();
  duplicate.records.push({ ...duplicate.records[0], question: '另一条问题' });
  assert.throws(() => api.normalizeBundle(duplicate), /不唯一/);
});

test('orphaned AI labels are rejected instead of attaching to other records', () => {
  const { api } = runtime();
  const mismatch = reviewed();
  mismatch.review.items[0].record_id = 'feedback:missing';
  assert.throws(() => api.normalizeBundle(mismatch), /无法匹配/);
});

test('duplicate AI reviews for one record are rejected', () => {
  const { api } = runtime();
  const duplicate = reviewed();
  duplicate.review.items.push(item());
  assert.throws(() => api.normalizeBundle(duplicate), /评审记录重复/);
});

test('malformed imports fail before replacing the active analysis package', () => {
  const { state, api } = runtime();
  state.bundle = api.normalizeBundle(reviewed());
  const previous = state.bundle;
  assert.throws(() => { state.bundle = api.normalizeBundle({ metrics: {}, sources: {}, provenance: {} }); }, /records/);
  assert.equal(state.bundle, previous);
  assert.equal(api.hasReview(), true);
});
