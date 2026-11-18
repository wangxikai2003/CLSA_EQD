import React, { useEffect, useMemo, useState } from 'react';
import { CorrTarget, CorrUniverseResponse, fetchCorrUniverse } from '../../services/api_v1';

type Props = {
  value: string;
  onChange: (symbol: string) => void;
  requiredCapability?: string;
  label?: string;
  className?: string;
};

const selectClass =
  'px-2 py-1.5 text-xs font-bold border border-slate-200 rounded-md bg-slate-50 text-[#000066] focus:outline-none focus:ring-2 focus:ring-[#000066]';

const AssetUniverseSelector: React.FC<Props> = ({
  value,
  onChange,
  requiredCapability,
  label,
  className = '',
}) => {
  const [uni, setUni] = useState<CorrUniverseResponse | null>(null);
  const [market, setMarket] = useState<string>('US');
  const [assetClass, setAssetClass] = useState<string>('INDEX');
  const [q, setQ] = useState('');

  useEffect(() => {
    const ac = new AbortController();
    fetchCorrUniverse({ signal: ac.signal })
      .then((d) => {
        setUni(d);
        const markets = Object.keys(d.tree);
        const m0 = markets.includes('US') ? 'US' : markets[0];
        if (m0) setMarket(m0);
        const acs = Object.keys(d.tree[m0] || {});
        const a0 = acs.includes('INDEX') ? 'INDEX' : acs[0];
        if (a0) setAssetClass(a0);
      })
      .catch(() => setUni(null));
    return () => ac.abort();
  }, []);

  const markets = useMemo(() => (uni ? Object.keys(uni.tree) : []), [uni]);
  const assetClasses = useMemo(
    () => (uni && market ? Object.keys(uni.tree[market] || {}) : []),
    [uni, market]
  );

  const options: CorrTarget[] = useMemo(() => {
    if (!uni) return [];
    let list = (uni.tree[market]?.[assetClass] || []) as CorrTarget[];
    if (requiredCapability) {
      list = list.filter((t) => (t.capabilities || []).includes(requiredCapability));
    }
    const qq = q.trim().toLowerCase();
    if (qq) {
      list = list.filter(
        (t) =>
          t.symbol.toLowerCase().includes(qq) ||
          (t.subregion || '').toLowerCase().includes(qq) ||
          (t.name || '').toLowerCase().includes(qq)
      );
    }
    return list;
  }, [uni, market, assetClass, q, requiredCapability]);

  // Keep selection coherent when cascading
  useEffect(() => {
    if (!options.length) return;
    if (!options.some((o) => o.symbol === value)) {
      onChange(options[0].symbol);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [market, assetClass, options.length]);

  return (
    <div className={`flex flex-col gap-1 min-w-[160px] ${className}`}>
      {label && <span className="text-[10px] font-bold text-slate-500 uppercase">{label}</span>}
      <div className="flex flex-wrap gap-1">
        <select className={selectClass} value={market} onChange={(e) => setMarket(e.target.value)}>
          {markets.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
        <select
          className={selectClass}
          value={assetClass}
          onChange={(e) => setAssetClass(e.target.value)}
        >
          {assetClasses.map((a) => (
            <option key={a} value={a}>
              {a}
            </option>
          ))}
        </select>
      </div>
      <input
        className={`${selectClass} w-full`}
        placeholder="Search ticker / region"
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />
      <select className={`${selectClass} w-full`} value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map((o) => (
          <option key={o.symbol} value={o.symbol}>
            {o.symbol}
          </option>
        ))}
      </select>
    </div>
  );
};

export default AssetUniverseSelector;
