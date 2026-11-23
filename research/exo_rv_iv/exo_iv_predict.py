"""
Implied Vol Predict From Benchmark
----------------------------------
用标的 RV 与基准指数 RV 的回归关系，把基准市场上的 ATM IV 映射到个股，
得到个股 IV Mid / Bid / Ask，再与系统 shift_kv 的 vol charge 对比发邮件。
"""
import argparse
import html
import math
import multiprocessing
import tempfile
import traceback

import numpy as np
import pandas as pd
import pdblp
import statsmodels.api as sm
from dateutil.relativedelta import relativedelta
from eqdquant.ql.logging import log
from eqdquant.ql.utils import cmdline
from eqdquant.ql.utils.profiler import timer

from util import messaging, script_util

# Bloomberg IV 期限名 -> 交易日窗口
tenor_dict = {
    '30DAY': 21,
    '60DAY': 42,
    '3MTH': 63,
    '6MTH': 126
}
tenor_dict_inverse = {v: k for k, v in tenor_dict.items()}


def get_benchmark_bid_to_mid(u):
    """不同市场基准期权的半价差（vol points），用于从 mid 推 bid/ask。"""
    if u in ['SPX Index', 'NDX Index']:
        return 0.3
    elif u in ['SHSN300 Index', 'CSI1000 Index', '510500 CH']:
        return 1.
    elif u in ['HSI Index', 'HSCEI Index']:
        return 0.5
    elif u.endswith(' HK'):
        return 2.
    else:
        raise NotImplementedError


# 用作对标的主流指数（会拉成分股，便于给个股匹配基准）
benchmark_load = {
    'SPX Index',
    'NDX Index',
    'SHSN300 Index',
    'CSI1000 Index',
    'SH000905 Index',
    'HSCEI Index',
    'HSI Index',
}


def get_rv_info_in_batch(spot_data_raw, tl_one):
    """并行池用：对一批 ticker 逐个调用 get_rv_info。"""
    return [get_rv_info(spot_data_raw[[t]]) for t in tl_one]


def get_rv_info(spot_data):
    """
    单只标的/基准的 RV 时间序列：
    从现价算多期限年化已实现波动率，供后续回归使用。
    """
    rv_data = spot_data.dropna().copy()

    rv_info = {
        'underlying': spot_data.columns[0][0].replace(' Equity', ''),
        'BBGCode': spot_data.columns[0][0],
        'FirstDate': rv_data.index[0].date(),
        'EndDate': rv_data.index[-1].date(),
        'Length': rv_data.shape[0]
    }

    rv_data.columns = ['Spot']
    rv_data['Return'] = rv_data['Spot'].rolling(2).apply(lambda x: math.log(x[-1] / x[0]))
    rv_data['ReturnSquared'] = [math.pow(r, 2) for r in rv_data['Return']]

    # 年化 RV（%）：100 * sqrt(252 * mean(r^2))
    for rw in [21, 42, 63, 126, 252]:
        rv_data[f'RV_{rw}'] = rv_data['ReturnSquared'].rolling(rw).apply(
            lambda x: max(0.01, 100 * math.sqrt(252 * x.mean()))
        )
    rv_info['Data'] = rv_data
    return rv_info


def predict_rv(base_data, benchmark_data, benchmark_implied):
    """
    核心预测：把个股 RV 对基准 RV 做滚动回归，再用基准 IV 映射出个股 IV。

    base_data       : 个股 RV 信息（含 Data）
    benchmark_data  : 基准 RV 信息（含 Data）
    benchmark_implied : 基准 ATM IV 时间序列（列名如 IV_21_BM）
    """
    # 对齐：个股 RV + 基准 RV + 基准 IV
    rv_data = base_data['Data'].join(benchmark_data['Data'], rsuffix='_BM').join(benchmark_implied)
    base_info = {
        **{k: v for k, v in base_data.items() if k != 'Data'},
        **{f'Benchmark_{k}': v for k, v in benchmark_data.items() if k != 'Data'}
    }
    rv_stat = {}
    benchmark_bid_to_mid = get_benchmark_bid_to_mid(base_info['Benchmark_underlying'])

    # 对每个期限做滚动回归与 IV 映射
    for rw in [21, 42, 63, 126]:
        regression_result = []
        regression_length = rw  # 回归窗口 = 该期限长度
        rv_data_clean = rv_data[[f'RV_{rw}', f'RV_{rw}_BM']].dropna()

        # 逐日滚动：用最近 rw 天的 (个股RV, 基准RV) 做无截距 OLS
        # 模型：基准RV ≈ β * 个股RV  =>  个股相对基准的 beta = 1/β
        for i in range(regression_length, rv_data_clean.shape[0] + 1):
            regress_data = rv_data_clean.iloc[i - regression_length:i]
            model_x = regress_data[f'RV_{rw}']       # 个股 RV
            model_y = regress_data[f'RV_{rw}_BM']    # 基准 RV
            model = sm.OLS(model_y, model_x)
            model_fit = model.fit()
            model_error = model_y - model_fit.predict()
            model_beta = model_fit.params[0]

            regression_result_one = {
                'val_date': regress_data.index[-1],
                f'RVBeta_{rw}': 1 / model_beta,
                f'RegressionError_{rw}_Std': model_error.std() / model_beta
            }

            # β>0 时才映射 IV：个股 Mid ≈ 基准IV / β
            if model_beta > 0:
                iv_bm = rv_data[f'IV_{rw}_BM'].loc[regress_data.index[-1]]
                rv = regress_data.iloc[-1][f'RV_{rw}']
                mid = iv_bm / model_beta
                regression_result_one[f'IV_{rw}_Mid'] = mid

                # 按目标 Sharpe=1，用回归残差 + 基准 bid/ask 推个股 Bid/Ask
                sharpe_target = 1
                iv_bm_bid = iv_bm - benchmark_bid_to_mid
                d1 = iv_bm_bid * iv_bm_bid - sharpe_target * model_error.std() * 2 * model_beta * rv / math.sqrt(252 / rw)
                if d1 > 0:
                    regression_result_one[f'IV_{rw}_BidSpread'] = (math.sqrt(d1) - iv_bm) / model_beta
                    regression_result_one[f'IV_{rw}_Bid'] = mid + regression_result_one[f'IV_{rw}_BidSpread']

                iv_bm_ask = iv_bm + benchmark_bid_to_mid
                d2 = iv_bm_ask * iv_bm_ask + sharpe_target * model_error.std() * 2 * model_beta * rv / math.sqrt(252 / rw)
                regression_result_one[f'IV_{rw}_AskSpread'] = (math.sqrt(d2) - iv_bm) / model_beta
                regression_result_one[f'IV_{rw}_Ask'] = mid + regression_result_one[f'IV_{rw}_AskSpread']

            regression_result.append(regression_result_one)

        if len(regression_result) > 0:
            regression_result = pd.DataFrame(regression_result).set_index('val_date')
            rv_data = rv_data.join(regression_result)

            # ---- 回测 P&L：按 beta 对冲后，买个股 vol / 卖基准 vol ----
            # 个股腿：用未来真实 RV^2 - Bid^2；基准腿：相反方向
            rv_data[f'Pnl_{rw}_Bid'] = rv_data[f'RVBeta_{rw}'] ** 2 * (
                rv_data[f'RV_{rw}'].shift(-rw - 1) ** 2 - rv_data[f'IV_{rw}_Bid'] ** 2)
            rv_data[f'Pnl_{rw}_Bid_BM'] = -(
                rv_data[f'RV_{rw}_BM'].shift(-rw - 1) ** 2 - (rv_data[f'IV_{rw}_BM'] - benchmark_bid_to_mid) ** 2)
            rv_data[f'Pnl_{rw}_Bid_Total'] = rv_data[f'Pnl_{rw}_Bid'] + rv_data[f'Pnl_{rw}_Bid_BM']

            # ---- 最新一日的报价输出 ----
            for k in ['Mid', 'Bid', 'Ask', 'BidSpread', 'AskSpread']:
                rv_stat[f'IV_{rw}_{k}'] = rv_data[f'IV_{rw}_{k}'].iloc[-1]

            rv_stat[f'RVBeta_{rw}'] = rv_data[f'RVBeta_{rw}'].iloc[-1]
            rv_stat[f'RegressionError_{rw}_Std'] = rv_data[f'RegressionError_{rw}_Std'].iloc[-1]

            # ---- 历史序列统计：均值/标准差/最大最小，P&L 再算 Sharpe ----
            for cl in [f'RVBeta_{rw}', f'IV_{rw}_BidSpread', f'IV_{rw}_AskSpread',
                       f'Pnl_{rw}_Bid', f'Pnl_{rw}_Bid_BM', f'Pnl_{rw}_Bid_Total']:
                rv_stat_col = rv_data[cl].dropna()
                if rv_stat_col.shape[0] > 0:
                    rv_stat[f'{cl}_StartDate'] = rv_stat_col.index[0]
                    rv_stat[f'{cl}_EndDate'] = rv_stat_col.index[-1]
                    rv_stat[f'{cl}_Mean'] = rv_stat_col.mean()
                    rv_stat[f'{cl}_Std'] = rv_stat_col.std()
                    rv_stat[f'{cl}_Max'] = rv_stat_col.max()
                    rv_stat[f'{cl}_Min'] = rv_stat_col.min()
                    if 'Pnl' in cl:
                        rv_stat[f'{cl}_Sharpe'] = rv_stat[f'{cl}_Mean'] * math.sqrt(252 / rw) / rv_stat[f'{cl}_Std']

    rv_info = {**rv_stat, **base_info}
    return rv_info


def parse_vol_shift(s):
    """把 shift_kv 里的 vol 字符串转成百分比点数（例如 0.25 -> 25）。"""
    try:
        return float(s) * 100
    except Exception as ex:
        return np.nan


def get_benchmark(t, benchmark_dict):
    """
    给个股选基准指数：
    - 本身是 Index：直接用自己
    - A股：优先成分里的沪深300/中证1000，否则默认中证1000
    - 美股：优先 NDX/SPX，否则默认 SPX
    - 港股：优先恒生/国企，否则默认国企
    """
    if t.endswith(' Index'):
        return t
    elif t.endswith(' CH Equity'):
        for bi in ['SHSN300 Index', 'CSI1000 Index']:
            if t in benchmark_dict[bi]:
                return bi
        return 'CSI1000 Index'
    elif t.endswith(' US Equity'):
        for bi in ['NDX Index', 'SPX Index']:
            if t in benchmark_dict[bi]:
                return bi
        return 'SPX Index'
    elif t.endswith(' HK Equity'):
        for bi in ['HSI Index', 'HSCEI Index']:
            if t in benchmark_dict[bi]:
                return bi
        return 'HSCEI Index'
    else:
        raise NotImplementedError


if __name__ == '__main__':
    # ==================== 1. 命令行参数 ====================
    parser = argparse.ArgumentParser()
    parser.add_argument('-date', default='T-1')
    parser.add_argument('-email')
    parser.add_argument('-parallel', default=False)
    parser.add_argument('-ticker', default='underlying_config')
    args = parser.parse_args()
    log.info(args)
    run_date = cmdline.get_date(args.date)
    receiver = cmdline.get_default_receiver(
        args.email, ['EQDExoticTrading@citicclsa.com'])

    try:
        # ==================== 2. 读取系统当前 vol charge（shift_kv）====================
        shift_kv = script_util.read_ui_config('shift_kv')
        all_charge = shift_kv[shift_kv.apply(lambda row: '1' in row['shift_tier'].split(',') \
                                                         and not row['underlying'].startswith('DUMMY') \
                                                         and row['cell'].startswith('vol=') \
                                                         and ' ' in row['underlying'] \
                                                         and 'all_exo' in row['payoff_type'], axis=1)][['underlying', 'cell']] \
            .rename(columns={'cell': 'charge'})
        all_charge['charge'] = all_charge.apply(
            lambda row: {p.split('=')[0]: p.split('=')[-1] for p in row['charge'].split(',')}, axis=1)
        all_charge['vol'] = [parse_vol_shift(c['vol']) for c in all_charge['charge']]
        all_charge['vol_bump_type'] = [c['vol_bump_type'] for c in all_charge['charge']]
        all_charge = all_charge[all_charge['vol_bump_type'] == 'FLAT']

        # ==================== 3. 确定要跑的 ticker 列表 ====================
        if args.ticker is None:
            raise ValueError('Please input -ticker')
        elif args.ticker == 'shift_kv':
            ticker_list = sorted({u.replace(' CH', ' CH Equity').replace(' C1', ' CH Equity').replace(' C2', ' CH Equity') for u in all_charge['underlying']})
        elif args.ticker == 'underlying_config':
            # 默认：group 含 exo_IV_predict 的标的
            underlying_config = script_util.read_ui_config('underlying_config')
            underlying_config = underlying_config[['exo_IV_predict' in g.split(',') for g in underlying_config['group']]]
            ticker_list = sorted({u.replace(' CH', ' CH Equity').replace(' C1', ' CH Equity').replace(' C2', ' CH Equity') for u in underlying_config['underlying']})
        else:
            ticker_list = [c.replace('_', ' ') for c in args.ticker.split(',')]

        hist_start_date = run_date - relativedelta(years=7)

        with pdblp.bopen(timeout=5000) as bbg_con:
            # ==================== 4a. 基准成分股 + AH 股映射 ====================
            # 拉各指数成分，用于给个股匹配「属于哪个指数」
            benchmark_index_members = bbg_con.bulkref(tickers=list(benchmark_load), flds=['INDEX_MEMBERS_SHARES'])
            benchmark_index_members = benchmark_index_members[benchmark_index_members['name'] == 'Index Member'] \
                .groupby('ticker')['value'].apply(lambda x: [f"{u.replace(' CS', ' CH')} Equity" for u in x]).to_dict()

            # A/H 转换：若有对应 H 股且有 IV，则用 H 股所在市场当基准线索
            h_share = bbg_con.ref(tickers=ticker_list, flds='A/H_SHARE_CONVERSION').set_index('ticker')['value'].dropna().to_dict()
            if len(h_share) > 0:
                h_share_vol = bbg_con.bdh(
                    tickers=list(set(h_share.values())),
                    flds='3MTH_IMPVOL_100.0%MNY_DF',
                    start_date=f'{hist_start_date:%Y%m%d}',
                    end_date=f'{run_date:%Y%m%d}')
                h_share = {k: v for k, v in h_share.items() if (v, '3MTH_IMPVOL_100.0%MNY_DF') in h_share_vol.columns}

            # 每只个股最终选用的基准
            benchmark_index_dict = {t: h_share.get(t, get_benchmark(t, benchmark_index_members)) for t in ticker_list}

            # ==================== 4b. 拉基准 ATM IV + 个股/基准现价 ====================
            ticker_list_load = list(set(ticker_list + list(benchmark_index_dict.values())))
            ticker_list_slice_num = math.ceil(len(ticker_list_load) / 500)
            ticker_list_group = np.array_split(ticker_list_load, ticker_list_slice_num)

            bbg_vol = bbg_con.bdh(
                tickers=list(set(benchmark_index_dict.values())),
                flds=[f'{k}_IMPVOL_100.0%MNY_DF' for k, v in tenor_dict.items()],
                start_date=f'{hist_start_date:%Y%m%d}',
                end_date=f'{run_date:%Y%m%d}')

            bbg_spot_result = pd.DataFrame()
            for i, tl in enumerate(ticker_list_group):
                log.info(f'Loading BBG Quotes {i + 1} / {ticker_list_slice_num} from {tl[0]} to {tl[-1]}')
                bbg_spot_result_one = bbg_con.bdh(
                    tickers=list([str(t) for t in tl]),
                    flds=['PX_LAST'],
                    start_date=f'{hist_start_date:%Y%m%d}',
                    end_date=f'{run_date:%Y%m%d}',
                    elms=[('adjustmentNormal', True),
                          ('adjustmentAbnormal', True),
                          ('adjustmentSplit', True)])
                bbg_spot_result = pd.concat([bbg_spot_result, bbg_spot_result_one], axis=1)

        # ==================== 5. 先算所有标的（含基准）的 RV 序列 ====================
        ticker_list_result = ticker_list
        total_number_of_tickers = len(ticker_list_result)
        cpu_count = multiprocessing.cpu_count()

        log.info('Calculating prediction')
        if cmdline.get_bool(args.parallel) and len(ticker_list_result) > 10:
            number_of_pools = 2
            with timer(f'Processing in parallel {len(ticker_list_result)=} {cpu_count=} {number_of_pools=}'):
                with multiprocessing.Pool(number_of_pools) as pool:
                    tickers_partition = np.array_split(bbg_spot_result.columns, number_of_pools)
                    async_results = [pool.apply_async(get_rv_info_in_batch, (bbg_spot_result, tl)) for tl in tickers_partition]
                    rv_result_raw = [r for r_list in async_results for r in r_list.get()]
        else:
            with timer(f'Processing in sequence {len(bbg_spot_result.columns)=}'):
                rv_result_raw = [get_rv_info(bbg_spot_result[[t]]) for t in bbg_spot_result.columns]
        rv_result_raw = {row['BBGCode']: row for row in rv_result_raw}

        # ==================== 6. 个股 vs 基准：回归 + IV 映射 ====================
        rv_result = [
            predict_rv(
                rv_result_raw[t],
                rv_result_raw[benchmark_index_dict[t]],
                bbg_vol[benchmark_index_dict[t]].rename(
                    columns={f'{k}_IMPVOL_100.0%MNY_DF': f'IV_{v}_BM' for k, v in tenor_dict.items()})
            )
            for t in ticker_list_result
        ]
        rv_result_df = pd.DataFrame(rv_result)

        # ==================== 7. 与系统 charge 对齐，算差异 ====================
        result_df = all_charge.merge(rv_result_df, on='underlying', how='outer')
        # 重点看 3M Bid：系统 vol vs 模型 IV_63_Bid
        result_df['shift_diff'] = result_df['vol'] - result_df['IV_63_Bid']
        result_df['shift_abs_diff'] = abs(result_df['shift_diff']).fillna(99999)

        if args.ticker != 'shift_kv':
            result_df = result_df[result_df['underlying'].isin([t.replace(' Equity', '') for t in ticker_list])]

        result_df['shift_kv_string'] = result_df.apply(
            lambda row: f"vol={row['IV_63_Bid'] / 100:.3f},vol_bump_type={row['vol_bump_type']}", axis=1)

        # ==================== 8. 邮件：差异 Top20 + 全量 CSV ====================
        email_df = result_df.sort_values('shift_abs_diff', ascending=False).iloc[:20][
            ['underlying', 'vol', 'IV_63_Bid', 'shift_diff', 'Benchmark_underlying',
             'RVBeta_63', 'RegressionError_63_Std', 'IV_63_Mid', 'IV_63_Ask',
             'IV_63_BidSpread', 'IV_63_AskSpread']]

        subject = f'Implied Vol Predict From Benchmark {run_date}'
        with tempfile.TemporaryDirectory() as temp:
            attach_path = f'{temp}/{subject.replace(" ", "_")}.csv'
            result_df.drop([c for c in result_df.columns if '_normalized' in c or c == 'charge'], axis=1)\
                .sort_values('underlying').to_csv(attach_path, index=False)
            messaging.send_email(
                to=receiver, subject=subject,
                body=email_df.to_html(index=False, float_format=lambda v: f'{v:,.1f}'),
                attachments=attach_path)

    except Exception as ex:
        error_msg = html.escape(f"{type(ex)}\n{str(ex)}\n{traceback.format_exc()}").replace('\n', "<br />")
        subject = '[FAIL] IV predict'
        messaging.send_email(to=receiver, subject=subject, body=error_msg)
        raise ex
