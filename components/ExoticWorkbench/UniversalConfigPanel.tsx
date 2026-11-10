import React from 'react';
import type { UniversalExoticProductDTO } from '../../services/api_v1';

const PRESETS = [
  { id: 'Snowball Classic', label: 'Snowball' },
  { id: 'Stepdown Snowball', label: 'Stepdown Snowball' },
  { id: 'Multi-Asset FCN', label: 'FCN' },
  { id: 'Phoenix Memory Autocall', label: 'Phoenix Memory' },
  { id: 'Custom Exotic', label: 'Custom Exotic' },
];

interface Props {
  product: UniversalExoticProductDTO;
  onChange: (p: UniversalExoticProductDTO) => void;
  onLoadPreset: (name: string) => void;
  mode: 'price' | 'solve';
  onModeChange: (m: 'price' | 'solve') => void;
  solveFor: 'cpn_rate' | 'ko_rebate' | 'ki_level' | 'put_strike';
  onSolveForChange: (f: 'cpn_rate' | 'ko_rebate' | 'ki_level' | 'put_strike') => void;
  targetPrice: number;
  onTargetPriceChange: (v: number) => void;
}

function patch<T extends object>(obj: T, key: keyof T, val: unknown): T {
  return { ...obj, [key]: val };
}

const UniversalConfigPanel: React.FC<Props> = ({
  product,
  onChange,
  onLoadPreset,
  mode,
  onModeChange,
  solveFor,
  onSolveForChange,
  targetPrice,
  onTargetPriceChange,
}) => {
  const n = product.asset.underlying.length;
  const corr = product.asset.correlation_matrix || [
    [1, 0.5],
    [0.5, 1],
  ];

  const setAssetCount = (count: number) => {
    const c = Math.max(1, Math.min(4, count));
    const under = [...product.asset.underlying];
    while (under.length < c) under.push(`U${under.length + 1}`);
    under.length = c;
    const mat = Array.from({ length: c }, (_, i) =>
      Array.from({ length: c }, (_, j) => (i === j ? 1 : 0.5)),
    );
    onChange({
      ...product,
      asset: { ...product.asset, underlying: under, correlation_matrix: mat },
    });
  };

  return (
    <div className="space-y-3 text-[11px]">
      <div className="flex flex-wrap gap-1">
        {PRESETS.map((p) => (
          <button
            key={p.id}
            type="button"
            onClick={() => onLoadPreset(p.id)}
            className={`px-2 py-1 rounded border font-bold ${
              product.name === p.id
                ? 'bg-[#000066] text-white border-[#000066]'
                : 'bg-white text-slate-600 border-slate-200 hover:border-[#000066]'
            }`}
          >
            {p.label}
          </button>
        ))}
      </div>

      <div className="border border-slate-200 rounded p-2 space-y-2">
        <div className="font-bold text-[#000066] uppercase text-[10px]">Asset Basket</div>
        <div className="flex items-center gap-2">
          <span className="text-slate-500"># Underlyings</span>
          <input
            type="number"
            min={1}
            max={4}
            value={n}
            onChange={(e) => setAssetCount(Number(e.target.value))}
            className="w-14 border rounded px-1 py-0.5"
          />
          <select
            value={product.asset.basket_type}
            onChange={(e) =>
              onChange({
                ...product,
                asset: { ...product.asset, basket_type: e.target.value as any },
              })
            }
            className="border rounded px-1 py-0.5"
          >
            <option value="WORSTOF">Worst-of</option>
            <option value="BESTOF">Best-of</option>
            <option value="WEIGHTED">Weighted</option>
          </select>
        </div>
        <div className="flex flex-wrap gap-1">
          {product.asset.underlying.map((u, i) => (
            <input
              key={i}
              value={u}
              onChange={(e) => {
                const under = [...product.asset.underlying];
                under[i] = e.target.value;
                onChange({ ...product, asset: { ...product.asset, underlying: under } });
              }}
              className="w-16 border rounded px-1 py-0.5 font-mono"
            />
          ))}
        </div>
        {n >= 2 && (
          <div>
            <div className="text-slate-500 mb-1">ρ₁₂ = {(corr[0]?.[1] ?? 0.5).toFixed(2)}</div>
            <input
              type="range"
              min={-0.5}
              max={0.99}
              step={0.01}
              value={corr[0]?.[1] ?? 0.5}
              onChange={(e) => {
                const r = Number(e.target.value);
                const mat = corr.map((row) => [...row]);
                for (let i = 0; i < n; i++) {
                  for (let j = 0; j < n; j++) {
                    mat[i][j] = i === j ? 1 : r;
                  }
                }
                onChange({
                  ...product,
                  asset: { ...product.asset, correlation_matrix: mat },
                });
              }}
              className="w-full accent-[#000066]"
            />
          </div>
        )}
      </div>

      <div className="border border-slate-200 rounded p-2 space-y-2">
        <div className="font-bold text-[#000066] uppercase text-[10px]">Autocall KO</div>
        <div className="grid grid-cols-2 gap-2">
          <label className="flex flex-col gap-0.5">
            <span className="text-slate-500">Freq</span>
            <select
              value={product.ko.ko_freq}
              onChange={(e) =>
                onChange({ ...product, ko: { ...product.ko, ko_freq: e.target.value } })
              }
              className="border rounded px-1 py-0.5"
            >
              <option value="1M">1M</option>
              <option value="1D">1D</option>
              <option value="1Y">1Y</option>
            </select>
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="text-slate-500">Monitoring</span>
            <select
              value={product.ko.ko_monitoring_type}
              onChange={(e) =>
                onChange({
                  ...product,
                  ko: { ...product.ko, ko_monitoring_type: e.target.value as any },
                })
              }
              className="border rounded px-1 py-0.5"
            >
              <option value="EUROPEAN">European</option>
              <option value="AMERICAN">American</option>
            </select>
          </label>
        </div>
        <div className="max-h-28 overflow-auto border border-slate-100 rounded">
          <table className="w-full text-[10px]">
            <thead className="bg-slate-50 sticky top-0">
              <tr>
                <th className="p-1 text-left">#</th>
                <th className="p-1 text-left">KO</th>
                <th className="p-1 text-left">Shift</th>
              </tr>
            </thead>
            <tbody>
              {product.ko.ko_schedule.slice(0, 12).map((lv, i) => (
                <tr key={i} className="border-t border-slate-50">
                  <td className="p-1">{i + 1}</td>
                  <td className="p-1">
                    <input
                      type="number"
                      step={0.005}
                      value={lv}
                      onChange={(e) => {
                        const sch = [...product.ko.ko_schedule];
                        sch[i] = Number(e.target.value);
                        onChange({ ...product, ko: { ...product.ko, ko_schedule: sch } });
                      }}
                      className="w-16 border rounded px-1"
                    />
                  </td>
                  <td className="p-1">
                    <input
                      type="number"
                      step={0.001}
                      value={product.ko.ko_shift?.[i] ?? 0}
                      onChange={(e) => {
                        const sh = [...(product.ko.ko_shift || product.ko.ko_schedule.map(() => 0))];
                        sh[i] = Number(e.target.value);
                        onChange({ ...product, ko: { ...product.ko, ko_shift: sh } });
                      }}
                      className="w-16 border rounded px-1"
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="border border-slate-200 rounded p-2 space-y-2">
        <div className="font-bold text-[#000066] uppercase text-[10px]">KI & Put</div>
        <div className="grid grid-cols-2 gap-2">
          <label className="flex flex-col gap-0.5">
            <span className="text-slate-500">KI Level</span>
            <input
              type="number"
              step={0.01}
              value={product.ki.ki_level}
              onChange={(e) =>
                onChange({
                  ...product,
                  ki: { ...product.ki, ki_level: Number(e.target.value) },
                })
              }
              className="border rounded px-1 py-0.5"
            />
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="text-slate-500">Monitoring</span>
            <select
              value={product.ki.ki_monitoring_type}
              onChange={(e) =>
                onChange({
                  ...product,
                  ki: { ...product.ki, ki_monitoring_type: e.target.value as any },
                })
              }
              className="border rounded px-1 py-0.5"
            >
              <option value="DAILY">Daily AKI</option>
              <option value="EUROPEAN">European AXKI</option>
            </select>
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="text-slate-500">Put Strike</span>
            <input
              type="number"
              step={0.01}
              value={product.ki.put_strike}
              onChange={(e) =>
                onChange({
                  ...product,
                  ki: { ...product.ki, put_strike: Number(e.target.value) },
                })
              }
              className="border rounded px-1 py-0.5"
            />
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="text-slate-500">KI Shift</span>
            <input
              type="number"
              step={0.01}
              value={product.ki.ki_shift ?? 0}
              onChange={(e) =>
                onChange({
                  ...product,
                  ki: { ...product.ki, ki_shift: Number(e.target.value) },
                })
              }
              className="border rounded px-1 py-0.5"
            />
          </label>
        </div>
      </div>

      <div className="border border-slate-200 rounded p-2 space-y-2">
        <div className="font-bold text-[#000066] uppercase text-[10px]">Coupon Engine</div>
        <div className="flex flex-wrap gap-2">
          {(
            [
              ['FIXED', 'Fixed'],
              ['SNOWBALL_ACCRUED', 'Accrued'],
              ['PHOENIX_CONDITIONAL', 'Conditional'],
            ] as const
          ).map(([v, lab]) => (
            <label key={v} className="flex items-center gap-1">
              <input
                type="radio"
                checked={product.coupon.cpn_type === v}
                onChange={() =>
                  onChange({ ...product, coupon: { ...product.coupon, cpn_type: v } })
                }
              />
              {lab}
            </label>
          ))}
        </div>
        <div className="grid grid-cols-2 gap-2">
          <label className="flex flex-col gap-0.5">
            <span className="text-slate-500">Cpn Rate p.a.</span>
            <input
              type="number"
              step={0.01}
              value={product.coupon.cpn_rate}
              onChange={(e) =>
                onChange({
                  ...product,
                  coupon: {
                    ...product.coupon,
                    cpn_rate: Number(e.target.value),
                    ko_rebate: Number(e.target.value),
                  },
                })
              }
              className="border rounded px-1 py-0.5 font-bold text-green-700"
            />
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="text-slate-500">Coupon Barrier</span>
            <input
              type="number"
              step={0.01}
              value={product.coupon.coupon_barrier ?? 0.8}
              onChange={(e) =>
                onChange({
                  ...product,
                  coupon: { ...product.coupon, coupon_barrier: Number(e.target.value) },
                })
              }
              className="border rounded px-1 py-0.5"
              disabled={product.coupon.cpn_type !== 'PHOENIX_CONDITIONAL'}
            />
          </label>
        </div>
        <label className="flex items-center gap-1 text-slate-600">
          <input
            type="checkbox"
            checked={!!product.coupon.memory_cpn}
            onChange={(e) =>
              onChange({
                ...product,
                coupon: { ...product.coupon, memory_cpn: e.target.checked },
              })
            }
          />
          Memory Coupon
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-slate-500">Tenor (Y)</span>
          <input
            type="number"
            step={0.1}
            value={product.tenor}
            onChange={(e) => onChange(patch(product, 'tenor', Number(e.target.value)))}
            className="border rounded px-1 py-0.5"
          />
        </label>
      </div>

      <div className="border border-slate-200 rounded p-2 space-y-2 bg-slate-50">
        <div className="font-bold text-[#000066] uppercase text-[10px]">Valuation Mode</div>
        <div className="flex gap-3">
          <label className="flex items-center gap-1">
            <input type="radio" checked={mode === 'price'} onChange={() => onModeChange('price')} />
            Forward Price
          </label>
          <label className="flex items-center gap-1">
            <input type="radio" checked={mode === 'solve'} onChange={() => onModeChange('solve')} />
            Solve-For
          </label>
        </div>
        {mode === 'solve' && (
          <div className="grid grid-cols-2 gap-2">
            <select
              value={solveFor}
              onChange={(e) => onSolveForChange(e.target.value as any)}
              className="border rounded px-1 py-0.5"
            >
              <option value="cpn_rate">cpn_rate</option>
              <option value="ko_rebate">ko_rebate</option>
              <option value="ki_level">ki_level</option>
              <option value="put_strike">put_strike</option>
            </select>
            <label className="flex flex-col gap-0.5">
              <span className="text-slate-500">Target Price</span>
              <input
                type="number"
                step={0.01}
                value={targetPrice}
                onChange={(e) => onTargetPriceChange(Number(e.target.value))}
                className="border rounded px-1 py-0.5"
              />
            </label>
          </div>
        )}
      </div>
    </div>
  );
};

export default UniversalConfigPanel;
