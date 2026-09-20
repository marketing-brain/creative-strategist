// Number formatting for metric displays. Currency comes from the ad account, never assumed.
export type Formatters = {
  money: (n: number) => string;
  count: (n: number) => string;
  pct: (n: number | null, digits?: number) => string;
};

export function formatters(currency = 'USD', locale = 'en-US'): Formatters {
  let moneyFmt: Intl.NumberFormat;
  try {
    moneyFmt = new Intl.NumberFormat(locale, { style: 'currency', currency, maximumFractionDigits: 0 });
  } catch {
    // An account can carry a currency code Intl does not know; fall back to a plain number.
    moneyFmt = new Intl.NumberFormat(locale, { maximumFractionDigits: 0 });
  }
  const countFmt = new Intl.NumberFormat(locale);
  return {
    money: (n) => moneyFmt.format(n),
    count: (n) => countFmt.format(n),
    pct: (n, digits = 2) =>
      n == null ? '–' : new Intl.NumberFormat(locale, { style: 'percent', minimumFractionDigits: digits, maximumFractionDigits: digits }).format(n),
  };
}
