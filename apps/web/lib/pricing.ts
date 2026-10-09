export type RoundingPolicy = "nearest_100" | "nearest_500" | "round_up_100" | "round_up_500";

export function grossMarginPercent(salePrice: number, unitCost: number): number | null {
  if (!Number.isFinite(salePrice) || !Number.isFinite(unitCost) || salePrice <= 0) return null;
  return ((salePrice - unitCost) / salePrice) * 100;
}

export function validTargetMargin(value: number): boolean {
  return Number.isFinite(value) && value > 0 && value < 100;
}

export function mathematicalSalePrice(unitCost: number, targetMarginPercent: number): number | null {
  if (!Number.isFinite(unitCost) || unitCost < 0 || !validTargetMargin(targetMarginPercent)) return null;
  return unitCost / (1 - targetMarginPercent / 100);
}

export function commercialRound(amount: number, policy: RoundingPolicy): number {
  const step = policy.endsWith("500") ? 500 : 100;
  return policy.startsWith("round_up") ? Math.ceil(amount / step) * step : Math.round(amount / step) * step;
}

export function suggestedSalePrice(unitCost: number, targetMarginPercent: number,
                                   policy: RoundingPolicy): { calculated: number; suggested: number } | null {
  const calculated = mathematicalSalePrice(unitCost, targetMarginPercent);
  return calculated === null ? null : { calculated, suggested: commercialRound(calculated, policy) };
}
