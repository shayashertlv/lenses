// DTO amounts are nonnegative decimal strings. Round only their displayed cents;
// parsing the amount as a Number would round decimal ties through binary floats.
function decimalParts(value) {
  const decimal = /^(\d+)(?:\.(\d*))?$/.exec(String(value ?? ''));
  return decimal ? {whole: decimal[1], fraction: decimal[2] || ''} : null;
}

export function money(value) {
  const decimal = decimalParts(value);
  if (!decimal) return '—';
  const {whole, fraction} = decimal;
  let cents = BigInt(whole) * 100n + BigInt(fraction.padEnd(2, '0').slice(0, 2));
  if (fraction.length > 2 && fraction[2] >= '5') cents += 1n;
  const digits = cents.toString().padStart(3, '0');
  return `$${digits.slice(0, -2)}.${digits.slice(-2)}`;
}

// Subtract exact amounts before display rounding, without changing either DTO.
export function cutoffBalance(cutoff, settled) {
  const budget = decimalParts(cutoff), spent = decimalParts(settled);
  if (!budget || !spent) return null;
  const scale = Math.max(budget.fraction.length, spent.fraction.length);
  const units = amount => BigInt(amount.whole + amount.fraction.padEnd(scale, '0'));
  const difference = units(budget) - units(spent);
  const decimal = value => {
    const digits = value.toString().padStart(scale + 1, '0');
    return scale ? `${digits.slice(0, -scale)}.${digits.slice(-scale)}` : digits;
  };
  return {left: difference > 0n ? decimal(difference) : '0',
    overrun: difference < 0n ? decimal(-difference) : '0', exceeded: difference < 0n};
}
