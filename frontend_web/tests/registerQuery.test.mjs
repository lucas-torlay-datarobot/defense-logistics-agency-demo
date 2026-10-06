import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';
const source = readFileSync(new URL('../src/registerQuery.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { registerQuery, defaults } = await import('data:text/javascript;base64,' + Buffer.from(js).toString('base64'));
test('all orders include history and join current risk at NIIN/destination grain', () => {
  const sql = registerQuery('orders', defaults, '2025-12-31', 0);
  assert.doesNotMatch(sql, /WHERE/);
  assert.match(sql, /o.destination = l.location/);
  assert.match(sql, /actual_receipt_date/);
  assert.match(sql, /LIMIT 51 OFFSET 0/);
});
test('future supply uses snapshot date, with literal name search and quantity ordering', () => {
  const sql = registerQuery('orders', { ...defaults, due: 'future', search: "O'Brien_100%", sort: 'quantity', direction: 'asc', minQuantity: '20' }, '2025-12-31', 2);
  assert.match(sql, /expected_receipt_date > CAST\('2025-12-31' AS DATE\)/);
  assert.match(sql, /O''Brien_100%/);
  assert.match(sql, /quantity >= 20/);
  assert.match(sql, /ORDER BY quantity ASC NULLS LAST, order_id/);
  assert.match(sql, /OFFSET 100/);
});
test('risk percentages are fractions and invalid numeric filters fail before querying', () => {
  assert.match(registerQuery('inventory', { ...defaults, minRisk: '65' }, '2025-12-31', 0), /shortage_probability_14d >= 0.65/);
  for (const value of ['NaN', '-1', '101', '1; DROP TABLE items']) {
    assert.throws(() => registerQuery('overview', { ...defaults, minRisk: value }, '2025-12-31', 0));
  }
  assert.throws(() => registerQuery('orders', { ...defaults, minQuantity: '5', maxQuantity: '1' }, '2025-12-31', 0));
});
test('sort expressions are allowlisted and paging has a stable tie break', () => {
  const sql = registerQuery('inventory', { ...defaults, sort: 'niin; DROP TABLE items' }, '2025-12-31', 1);
  assert.doesNotMatch(sql, /DROP/);
  assert.match(sql, /NULLS LAST, niin, location/);
});
