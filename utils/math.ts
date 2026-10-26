// Standard Normal Cumulative Distribution Function
export const cnd = (x: number): number => {
  const a1 = 0.31938153;
  const a2 = -0.356563782;
  const a3 = 1.781477937;
  const a4 = -1.821255978;
  const a5 = 1.330274429;
  const p = 0.2316419;
  const c = 0.39894228;

  if (x >= 0) {
    const t = 1.0 / (1.0 + p * x);
    return (
      1.0 -
      c *
        Math.exp((-x * x) / 2.0) *
        t *
        (a1 + t * (a2 + t * (a3 + t * (a4 + t * a5))))
    );
  } else {
    const t = 1.0 / (1.0 + p * -x);
    return (
      c *
      Math.exp((-x * x) / 2.0) *
      t *
      (a1 + t * (a2 + t * (a3 + t * (a4 + t * a5))))
    );
  }
};

// Standard Normal Probability Density Function
export const ndf = (x: number): number => {
  return (1.0 / Math.sqrt(2 * Math.PI)) * Math.exp(-0.5 * x * x);
};
