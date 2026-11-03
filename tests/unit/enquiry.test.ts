import { test } from 'node:test';
import assert from 'node:assert/strict';
import { validateEnquiry, composeFirmEmail, SERVICE_IDS } from '../../supabase/functions/_shared/enquiry.ts';

const FIRM = '123e4567-e89b-12d3-a456-426614174000';
const good = { firmId: FIRM, service: 'purchase', nation: 'England', price: 325000, total: 1850.5,
  name: ' Sam Client ', email: 'sam@client.example', phone: '07700 900123', message: 'Leasehold flat' };

test('accepts a valid enquiry and trims fields', () => {
  const v = validateEnquiry(good);
  assert.equal(v.ok, true);
  if (v.ok) { assert.equal(v.value.name, 'Sam Client'); assert.equal(v.value.price, 325000); }
});

test('rejects bad firm ids, unknown services, missing names and bad emails', () => {
  assert.equal(validateEnquiry({ ...good, firmId: 'nope' }).ok, false);
  assert.equal(validateEnquiry({ ...good, service: 'divorce; drop table' }).ok, false);
  assert.equal(validateEnquiry({ ...good, name: '   ' }).ok, false);
  for (const email of ['', 'sam', 'sam@client', 'a b@c.com', 'x@y.com\nBcc: evil@x.com', '<x@y.com>']) {
    assert.equal(validateEnquiry({ ...good, email }).ok, false, `email ${JSON.stringify(email)}`);
  }
  assert.equal(validateEnquiry(null).ok, false);
  assert.equal(validateEnquiry('x').ok, false);
});

test('every service the app offers is accepted', () => {
  for (const id of SERVICE_IDS) assert.equal(validateEnquiry({ ...good, service: id }).ok, true, id);
});

test('numbers are sanitised and long text is capped', () => {
  const v = validateEnquiry({ ...good, price: -5, total: 'lots', message: 'x'.repeat(5000), nation: 'Atlantis' });
  assert.equal(v.ok, true);
  if (v.ok) {
    assert.equal(v.value.price, null);
    assert.equal(v.value.total, null);
    assert.equal(v.value.nation, null);
    assert.equal(v.value.message.length, 2000);
  }
});

test('firm email is plain text and carries the details a firm needs', () => {
  const v = validateEnquiry({ ...good, message: '<script>alert(1)</script>' });
  assert.equal(v.ok, true);
  if (!v.ok) return;
  const { subject, text } = composeFirmEmail('Strangford Solicitors', v.value);
  assert.match(subject, /Buying a home/);
  assert.match(text, /Strangford Solicitors, you have a new quote request/);
  assert.match(text, /Property value: £325,000/);
  assert.match(text, /Total the client saw.*£1,850\.5/);
  assert.match(text, /Email: sam@client\.example/);
  assert.match(text, /Phone: 07700 900123/);
  assert.match(text, /<script>alert\(1\)<\/script>/); // present verbatim, but only ever sent as text/plain
  assert.equal(subject.includes('Sam'), false, 'client-supplied text stays out of the subject line');
});
