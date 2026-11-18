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

const INSTRUMENT_LABEL: Record<string, string> = {
  INDEX: 'INDEX',
  SINGLE_STOCK: 'SINGLE STOCK',
  FX: 'FX',
  BASKET: 'BASKET',
};

function pickDefaultInstrument(kinds: string[]): string {
  if (kinds.includes('INDEX')) return 'INDEX';
  if (kinds.includes('SINGLE_STOCK')) return 'SINGLE_STOCK';
  return kinds[0] || '';
}

const AssetUniverseSelector: React.FC<Props> = ({
  value,
  onChange,
  requiredCapability,
  label,
  className = '',
}) => {
  const [uni, setUni] = useState<CorrUniverseResponse | null>(null);
  const [assetClass, setAssetClass] = useState<string>('EQUITY');
  const [market, setMarket] = useState<string>('US');
  const [instrument, setInstrument] = useState<string>('INDEX');
  const [q, setQ] = useState('');

  useEffect(() => {
    const ac = new AbortController();
    fetchCorrUniverse({ signal: ac.signal })
      .then((d) => {
        setUni(d);
        const families = d.asset_classes?.length ? d.asset_classes : Object.keys(d.tree);
        const fam = families.includes('EQUITY') ? 'EQUITY' : families[0];
        if (fam) setAssetClass(fam);
        const markets = Object.keys(d.tree[fam] || {});
        const m0 = markets.includes('US') ? 'US' : markets[0];
        if (m0) setMarket(m0);
        const kinds = Object.keys(d.tree[fam]?.[m0] || {});
        const k0 = pickDefaultInstrument(kinds);
        if (k0) setInstrument(k0);
      })
      .catch(() => setUni(null));
    return () => ac.abort();
  }, []);

  const assetClasses = useMemo(() => {
    if (!uni) return [];
    return uni.asset_classes?.length ? uni.asset_classes : Object.keys(uni.tree);
  }, [uni]);

  const markets = useMemo(
    () => (uni && assetClass ? Object.keys(uni.tree[assetClass] || {}) : []),
    [uni, assetClass]
  );

  const instruments = useMemo(
    () => (uni && assetClass && market ? Object.keys(uni.tree[assetClass]?.[market] || {}) : []),
    [uni, assetClass, market]
  );

  // Keep market/instrument coherent when family changes
  useEffect(() => {
    if (!markets.length) return;
    if (!markets.includes(market)) setMarket(markets[0]);
  }, [markets, market]);

  useEffect(() => {
    if (!instruments.length) return;
    if (!instruments.includes(instrument)) setInstrument(pickDefaultInstrument(instruments));
  }, [instruments, instrument]);

  const options: CorrTarget[] = useMemo(() => {
    if (!uni || !assetClass || !market || !instrument) return [];
    let list = (uni.tree[assetClass]?.[market]?.[instrument] || []) as CorrTarget[];
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
  }, [uni, assetClass, market, instrument, q, requiredCapability]);

  useEffect(() => {
    if (!options.length) return;
    if (!options.some((o) => o.symbol === value)) {
      onChange(options[0].symbol);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assetClass, market, instrument, options.length]);

  const showInstrument = assetClass === 'EQUITY' || instruments.length > 1;

  return (
    <div className={`flex flex-col gap-1 min-w-[180px] ${className}`}>
      {label && <span className="text-[10px] font-bold text-slate-500 uppercase">{label}</span>}
      <div className="flex flex-wrap gap-1">
        <select
          className={selectClass}
          value={assetClass}
          onChange={(e) => setAssetClass(e.target.value)}
          title="Asset class"
        >
          {assetClasses.map((a) => (
            <option key={a} value={a}>
              {a}
            </option>
          ))}
        </select>
        <select
          className={selectClass}
          value={market}
          onChange={(e) => setMarket(e.target.value)}
          disabled={!markets.length}
          title="Market"
        >
          {markets.length === 0 ? (
            <option value="">—</option>
          ) : (
            markets.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))
          )}
        </select>
        {showInstrument && (
          <select
            className={selectClass}
            value={instrument}
            onChange={(e) => setInstrument(e.target.value)}
            disabled={!instruments.length}
            title="Instrument"
          >
            {instruments.map((k) => (
              <option key={k} value={k}>
                {INSTRUMENT_LABEL[k] || k}
              </option>
            ))}
          </select>
        )}
      </div>
      <input
        className={`${selectClass} w-full`}
        placeholder="Search ticker / region"
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />
      <select
        className={`${selectClass} w-full`}
        value={options.some((o) => o.symbol === value) ? value : ''}
        onChange={(e) => onChange(e.target.value)}
        disabled={!options.length}
      >
        {!options.length && <option value="">No tickers</option>}
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
