// DTO amounts are nonnegative decimal strings. Round only their displayed cents;
// parsing the amount as a Number would round decimal ties through binary floats.
export function money(value) {
  if (value === null || value === undefined || value === '') return 'not yet known';
  const decimal = /^(\d+)(?:\.(\d*))?$/.exec(String(value));
  if (!decimal) return 'not yet known';
  const fraction = decimal[2] || '';
  let cents = BigInt(decimal[1]) * 100n + BigInt(fraction.padEnd(2, '0').slice(0, 2));
  if (fraction.length > 2 && fraction[2] >= '5') cents += 1n;
  const digits = cents.toString().padStart(3, '0');
  return `$${digits.slice(0, -2)}.${digits.slice(-2)}`;
}
