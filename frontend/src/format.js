/** Shared display formatting. Currency-aware for the Singapore and India markets. */

const SYMBOLS = { SGD: 'S$', INR: '\u20b9' };

export function currencySymbol(currency) {
  return SYMBOLS[currency] || (currency ? currency + ' ' : '');
}

function formatter(currency, options) {
  return new Intl.NumberFormat(currency === 'INR' ? 'en-IN' : 'en-SG', options);
}

export function money(value, currency) {
  if (value === null || value === undefined || Number.isNaN(value)) return 'n/a';
  return formatter(currency, {
    style: 'currency',
    currency: currency || 'SGD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

export function compactMoney(value, currency) {
  if (value === null || value === undefined || Number.isNaN(value)) return 'n/a';
  const sign = value < 0 ? '-' : '';
  const body = formatter(currency, { notation: 'compact', maximumFractionDigits: 1 }).format(
    Math.abs(value),
  );
  return sign + currencySymbol(currency) + body;
}

export function num(value, digits) {
  if (value === null || value === undefined || Number.isNaN(value)) return 'n/a';
  return new Intl.NumberFormat('en-SG', {
    minimumFractionDigits: digits === undefined ? 0 : digits,
    maximumFractionDigits: digits === undefined ? 2 : digits,
  }).format(value);
}

export function pct(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return 'n/a';
  const sign = value > 0 ? '+' : '';
  return sign + value.toFixed(2) + '%';
}

export const BASIS_LABEL = {
  measured: 'Measured',
  derived: 'Derived',
  assumed: 'Assumed',
};

export const BASIS_HELP = {
  measured: 'Taken directly from the uploaded BoQ or a published observation.',
  derived: 'Calculated from measured inputs using the documented formula.',
  assumed: 'Apportioned, overridden or modelled. No measured evidence. Read the justification.',
};
