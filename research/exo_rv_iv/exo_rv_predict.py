"""
Implied Vol Predict from Realized
---------------------------------
用标的自身的历史已实现波动率（RV）去预测合理 IV（Mid / Bid / Offer），
再与系统 shift_kv 中的 vol charge 对比，邮件提醒差异最大的标的。
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
from dateutil.relativedelta import relativedelta
from eqdquant.ql.logging import log
from eqdquant.ql.utils import cmdline
from eqdquant.ql.utils.profiler import timer
from matplotlib import pyplot as plt
from scipy.stats import normaltest

from util import messaging, script_util


def get_rv_info_in_batch(spot_data_raw, tl_one):
    """并行池用：对一批 ticker 逐个调用 get_rv_info。"""
    return [get_rv_info(spot_data_raw[[t]]) for t in tl_one]


def get_rv_info(spot_data):
    """
    单只标的的核心计算：
    1) 从现价序列算多期限 RV
    2) 用长期 RV(252) 作为 Mid 预测
    3) 用历史 RV 相对 Mid 的分布估 Bid / Offer
    4) 用未来真实 RV 回测预测误差
    """
    rv_data = spot_data.dropna().copy()

    # ---- 元信息：标的代码、数据起止日期、样本长度 ----
    rv_info = {
        'underlying': spot_data.columns[0][0].replace(' Equity', ''),
        'FirstDate': rv_data.index[0].date(),
        'EndDate': rv_data.index[-1].date(),
        'Length': rv_data.shape[0]
    }

    # ---- 日收益 & 收益平方（用于后续滚动 RV）----
    rv_data.columns = ['Spot']
    rv_data['Return'] = rv_data['Spot'].rolling(2).apply(lambda x: math.log(x[-1] / x[0]))
    rv_data['ReturnSquared'] = [math.pow(r, 2) for r in rv_data['Return']]

    # ---- 多窗口年化已实现波动率 RV（单位：百分比点数，下限 0.01）----
    # 公式：100 * sqrt(252 * mean(r^2))，窗口 21/42/63/126/252 交易日
    for rw in [21, 42, 63, 126, 252]:
        rv_data[f'RV_{rw}'] = rv_data['ReturnSquared'].rolling(rw).apply(
            lambda x: max(0.01, 100 * math.sqrt(252 * x.mean()))
        )

    # ---- Mid 预测：用 1 年 RV(252) 作为短/中期 RV 的中枢 ----
    rv_data['RV_63_MidPredict'] = rv_data['RV_252']
    rv_data['RV_42_MidPredict'] = rv_data['RV_252']
    rv_data['RV_21_MidPredict'] = rv_data['RV_252']

    # ---- Distance：短期 RV 相对 Mid 的对数偏离（后续用分位数定买卖价）----
    rv_data['RV_21_Distance'] = rv_data.apply(
        lambda row: math.log(row['RV_21'] / row['RV_21_MidPredict']), axis=1)
    rv_data['RV_42_Distance'] = rv_data.apply(
        lambda row: math.log(row['RV_42'] / row['RV_42_MidPredict']), axis=1)
    rv_data['RV_63_Distance'] = rv_data.apply(
        lambda row: math.log(row['RV_63'] / row['RV_63_MidPredict']), axis=1)

    # ---- 回测：用「当时 Mid」去预测「未来 63 日真实 RV」----
    rv_data['RV_63_LookForward'] = rv_data['RV_63'].shift(-64)
    rv_data['RV_63_AbsError'] = rv_data['RV_63_LookForward'] - rv_data['RV_63_MidPredict']
    rv_data['RV_63_PctError'] = rv_data.apply(
        lambda row: math.log(row['RV_63_LookForward'] / row['RV_63_MidPredict']), axis=1)

    # ---- 误差序列的统计量（均值/标准差/分位数/正态检验）----
    rv_stat = {}
    for l in ['RV_63_AbsError', 'RV_63_Distance', 'RV_63_PctError', 'RV_21_Distance', 'RV_42_Distance']:
        error = rv_data[l].dropna()
        if error.shape[0] > 8:
            rv_stat[f'{l}_abs_mean'] = abs(error).mean()
            rv_stat[f'{l}_mean'] = error.mean()
            rv_stat[f'{l}_length'] = error.shape[0]
            rv_stat[f'{l}_std'] = error.std()
            # 标准化误差：后面主流程会拿来画整体分布图
            rv_stat[f'{l}_normalized'] = list(((error - rv_stat[f'{l}_mean']) / rv_stat[f'{l}_std']).dropna().values)
            rv_stat[f'{l}_25Pct'] = np.percentile(error, 25)
            rv_stat[f'{l}_75Pct'] = np.percentile(error, 75)
            rv_stat[f'{l}_10Pct'] = np.percentile(error, 10)
            rv_stat[f'{l}_normal_test'] = normaltest(error).pvalue

    # ---- 样本足够时：用 Distance 分位数把 Mid 扩成 Bid / Offer ----
    # Bid 用偏低分位（偏保守卖方报价侧），Offer 用偏高分位
    if rv_stat['RV_63_Distance_length'] > 252:
        rv_data['RV_63_BidPredict'] = rv_data.apply(
            lambda row: math.exp(min(rv_stat['RV_63_Distance_25Pct'], 0)) * row['RV_63_MidPredict'], axis=1)
        rv_data['RV_63_BidPredict2'] = rv_data.apply(
            lambda row: math.exp(min(rv_stat['RV_63_Distance_10Pct'], 0)) * row['RV_63_MidPredict'], axis=1)
        rv_data['RV_63_OfferPredict'] = rv_data.apply(
            lambda row: math.exp(max(rv_stat['RV_63_Distance_75Pct'], 0)) * row['RV_63_MidPredict'], axis=1)
        rv_data['RV_42_OfferPredict'] = rv_data.apply(
            lambda row: math.exp(max(rv_stat['RV_42_Distance_75Pct'], 0)) * row['RV_42_MidPredict'], axis=1)
        rv_data['RV_21_OfferPredict'] = rv_data.apply(
            lambda row: math.exp(max(rv_stat['RV_21_Distance_75Pct'], 0)) * row['RV_21_MidPredict'], axis=1)

        # Bid/Offer 预测相对未来真实 RV 的绝对误差
        rv_data['RV_63_BidAbsError'] = rv_data['RV_63_LookForward'] - rv_data['RV_63_BidPredict']
        rv_data['RV_63_BidAbsError2'] = rv_data['RV_63_LookForward'] - rv_data['RV_63_BidPredict2']
        rv_data['RV_63_OfferAbsError'] = rv_data['RV_63_LookForward'] - rv_data['RV_63_OfferPredict']

        for l in ['RV_63_BidAbsError', 'RV_63_BidAbsError2', 'RV_63_OfferAbsError']:
            error = rv_data[l].dropna()
            if error.shape[0] > 0:
                rv_stat[f'{l}_abs_mean'] = abs(error).mean()
                rv_stat[f'{l}_mean'] = error.mean()
                rv_stat[f'{l}_length'] = error.shape[0]
                rv_stat[f'{l}_std'] = error.std()

        # ---- 取最新一日的预测值，作为该标的最终输出 ----
        for new_label, old_label in [
            ('Bid_3M', 'RV_63_BidPredict'),
            ('Bid2_3M', 'RV_63_BidPredict2'),
            ('Mid_3M', 'RV_63_MidPredict'),
            ('Offer_3M', 'RV_63_OfferPredict'),
            ('Offer_2M', 'RV_42_OfferPredict'),
            ('Offer_1M', 'RV_21_OfferPredict')
        ]:
            if rv_data[old_label].dropna().shape[0] > 126:
                rv_info[new_label] = rv_data[old_label].iloc[-1]

        rv_info = {**rv_stat, **rv_info}
        rv_info['Mid-Bid_3M'] = rv_info['Mid_3M'] - rv_info['Bid_3M']
        rv_info['Offer-Bid_3M'] = rv_info['Offer_3M'] - rv_info['Bid_3M']

    return rv_info


def parse_vol_shift(s):
    """把 shift_kv 里的 vol 字符串转成百分比点数（例如 0.25 -> 25）。"""
    try:
        return float(s) * 100
    except Exception as ex:
        return np.nan


if __name__ == '__main__':
    # ==================== 1. 命令行参数 ====================
    parser = argparse.ArgumentParser()
    parser.add_argument('-date', default='T-1')          # 估值日，默认上一交易日
    parser.add_argument('-email')                        # 邮件接收人（可选覆盖）
    parser.add_argument('-parallel', default=False)      # 是否多进程
    parser.add_argument('-ticker', default='underlying_config')  # 标的来源
    args = parser.parse_args()
    log.info(args)
    run_date = cmdline.get_date(args.date)
    receiver = cmdline.get_default_receiver(
        args.email, ['EQDExoticTrading@citicclsa.com'])

    try:
        # ==================== 2. 读取系统当前 vol charge（shift_kv）====================
        # 筛选：tier 含 1、非 DUMMY、cell 以 vol= 开头、有空格的真实标的、all_exo、FLAT bump
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
            # 直接用有 charge 的标的
            ticker_list = sorted({u.replace(' CH', ' CH Equity').replace(' C1', ' CH Equity').replace(' C2', ' CH Equity') for u in all_charge['underlying']})
        elif args.ticker == 'underlying_config':
            # 默认：underlying_config 里同时带 exo_normalOK 与 exo_CN_T1 的标的
            underlying_config = script_util.read_ui_config('underlying_config')
            underlying_config = underlying_config[['exo_normalOK' in g.split(',') and 'exo_CN_T1' in g.split(',') for g in underlying_config['group']]]
            ticker_list = sorted({u.replace(' CH', ' CH Equity').replace(' C1', ' CH Equity').replace(' C2', ' CH Equity') for u in underlying_config['underlying']})
        else:
            # 手动指定，逗号分隔，下划线转空格
            ticker_list = [c.replace('_', ' ') for c in args.ticker.split(',')]

        # ==================== 4. 从 Bloomberg 拉近 7 年复权收盘价 ====================
        hist_start_date = run_date - relativedelta(years=7)
        ticker_list_slice_num = math.ceil(len(ticker_list) / 500)
        ticker_list_group = np.array_split(ticker_list, ticker_list_slice_num)
        with pdblp.bopen(timeout=5000) as bbg_con:
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

        # ==================== 5. 计算每只标的的 RV 预测（可并行）====================
        ticker_list_result = bbg_spot_result.columns
        total_number_of_tickers = len(ticker_list_result)
        cpu_count = multiprocessing.cpu_count()

        log.info('Calculating prediction')
        if cmdline.get_bool(args.parallel) and len(ticker_list_result) > 10:
            number_of_pools = cpu_count - 1
            with timer(f'Processing in parallel {len(ticker_list_result)=} {cpu_count=} {number_of_pools=}'):
                with multiprocessing.Pool(number_of_pools) as pool:
                    tickers_partition = np.array_split(ticker_list_result, number_of_pools)
                    async_results = [pool.apply_async(get_rv_info_in_batch, (bbg_spot_result, tl)) for tl in tickers_partition]
                    rv_result = [r for r_list in async_results for r in r_list.get()]
        else:
            with timer(f'Processing in sequence {len(ticker_list_result)=}'):
                rv_result = [get_rv_info(bbg_spot_result[[t]]) for t in ticker_list_result]

        # ==================== 6. 与系统 charge 对齐，算差异 ====================
        rv_result_df = pd.DataFrame(rv_result)
        result_df = all_charge.merge(rv_result_df, on='underlying', how='outer')
        # shift_diff = 当前系统 vol - 模型 Bid；绝对差越大越需要关注
        result_df['shift_diff'] = result_df['vol'] - result_df['Bid_3M']
        result_df['shift_abs_diff'] = abs(result_df['shift_diff']).fillna(99999)

        if args.ticker is not None:
            result_df = result_df[result_df['underlying'].isin([t.replace(' Equity', '') for t in ticker_list])]

        # 生成可直接回写 shift_kv 的字符串
        result_df['shift_kv_string'] = result_df.apply(
            lambda row: f"vol={row['Bid_3M'] / 100:.3f},vol_bump_type={row['vol_bump_type']}", axis=1)

        # ==================== 7. 邮件：差异 Top20 + 全量 CSV 附件 ====================
        email_df = result_df.sort_values('shift_abs_diff', ascending=False).iloc[:20][
            ['underlying', 'vol', 'Bid_3M', 'shift_diff', 'Bid2_3M', 'Mid_3M',
             'Mid-Bid_3M', 'Offer_3M', 'Offer_1M', 'Offer-Bid_3M', 'shift_kv_string']]

        subject = f'Implied Vol Predict from Realized {run_date}'
        with tempfile.TemporaryDirectory() as temp:
            attach_path = f'{temp}/{subject.replace(" ", "_")}.csv'
            result_df.drop([c for c in result_df.columns if '_normalized' in c or c == 'charge'], axis=1)\
                .sort_values('underlying').to_csv(attach_path, index=False)
            messaging.send_email(
                to=receiver, subject=subject,
                body=email_df.to_html(index=False, float_format=lambda v: f'{v:,.1f}'),
                attachments=attach_path)

        # ==================== 8. 诊断图：汇总标准化预测误差分布 ====================
        normalized_error_all = pd.Series(
            [v for vl in result_df['RV_63_PctError_normalized'].dropna().values for v in vl])

        from statsmodels.graphics.gofplots import qqplot

        ax = normalized_error_all.plot(kind='hist', bins=50)
        normalized_error_all.plot(kind='kde', ax=ax, secondary_y=True)
        qqplot(normalized_error_all)
        print(f'Normal test: {normaltest(normalized_error_all).pvalue}')
        print(f'Mean: {normalized_error_all.mean()}')
        print(f'Std: {normalized_error_all.std()}')
        plt.show()

    except Exception as ex:
        # 失败时发告警邮件，再把异常抛出
        error_msg = html.escape(f"{type(ex)}\n{str(ex)}\n{traceback.format_exc()}").replace('\n', "<br />")
        subject = '[FAIL] Realized Vol Predict'
        messaging.send_email(to=receiver, subject=subject, body=error_msg)
        raise ex
