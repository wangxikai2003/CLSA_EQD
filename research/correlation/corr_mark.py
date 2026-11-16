import argparse
import copy
import getpass
import html
import itertools
import math
import multiprocessing
import os
import sys
import tempfile
import traceback
import uuid
from datetime import date, datetime
from multiprocessing import Pool

import numpy as np
import pandas as pd
import pdblp
from dateutil.relativedelta import relativedelta
from eqdquant.ql.api.md_api import MDAPI
from eqdquant.ql.logging import log
from eqdquant.ql.math.numerical.func import round_halfup
from eqdquant.ql.mktdata._legacy.asset_str import Currency
from eqdquant.ql.pricing.pricer.risk_measure import CorrelationT1Measure
from eqdquant.ql.repository.sqlalchemy.query import RiskQueryBuilder
from eqdquant.ql.risk import RiskEod, Risk
from eqdquant.ql.utils import cmdline
from eqdquant.ql.utils.profiler import timer
from eqdquant.ql.utils.pulseeqd_utils import load_mkt_data_from_pulseeqd, get_pulse_connection
from eqdquant.ql.utils.repository.postGresConstants import tableConst, schemaConst
from market_loader.mkt_utils import load_ref_data
from sqlalchemy.orm import Session, selectinload

sys.path.append("C:\\CLSA\\trader_tool")
from script import trader_tool_path
from util import messaging, script_util

import warnings

warnings.filterwarnings("ignore")

sub_region_dict = {v: k for k, vl in script_util.sub_region_mapping.items() for v in vl}
region_dict = {v: k for k, vl in script_util.region_map.items() for v in vl}

exclude_list = [
    'GMATUSD3.INDEX',
    '113519.CH',
    '1169.HK',
    '1844972D.HK',
    '1886.HK',
    '67.HK',
    '6837.HK',
    '885.HK',
    'ATVI.US',
    'BGNE.US',
    'BITA.US',
    'CBEST2',
    'CCKVS001',
    'CCKVS002',
    'CCKVS003',
    'CCKVS004',
    'CCKVS005',
    'CCKVS006',
    'CCLC5V30',
    'CSIH1140',
    'CSIN0852',
    'CSIN0905',
    'GMAT2',
    'GMAT3',
    'GMATUSD2',
    'GMATUSD3',
    'GSCB500P',
    'GSCBC300',
    'GSCBC500',
    'JOBS.US',
    'MACH1',
    'MLFIRPV9',
    'MMA',
    'MSCICS3X',
    'MSDL',
    'MSTARESG',
    'MXGC25DH',
    'SINA.US',
    'SQ.US',
    'SWAP2',
    'SX5ED',
    'WUBA.US',
    'TWTR.US',
    'QCMAT3Z',
    'SD3E',
    '600804.CH',
    '600804.C1',
    'FNGA.US',
    'CCINZACE',
    'CCINZADS',
    'CCINZAPN',
    'CCINZARB',
    'CCINZAXS',
    'CCINZBAF',
    'CCINZBHA',
    'CCINZBHI',
    'CCINZBJA',
    'CCINZBOS',
    'CCINZBPC',
    'CCINZCIP',
    'CCINZCOA',
    'CCINZDRR',
    'CCINZEIM',
    'CCINZGAI',
    'CCINZHCL',
    'CCINZHDB',
    'CCINZHMC',
    'CCINZHND',
    'CCINZHPC',
    'CCINZHUV',
    'CCINZICI',
    'CCINZIHF',
    'CCINZIIB',
    'CCINZINF',
    'CCINZIOC',
    'CCINZITD',
    'CCINZKMB',
    'CCINZLPC',
    'CCINZLT',
    'CCINZMM',
    'CCINZMSI',
    'CCINZNTP',
    'CCINZONG',
    'CCINZPWG',
    'CCINZRIL',
    'CCINZSBI',
    'CCINZSUN',
    'CCINZTAT',
    'CCINZTCS',
    'CCINZTEC',
    'CCINZTTM',
    'CCINZUPL',
    'CCINZUTC',
    'CCINZVED',
    'CCINZWPR',
    'CCINZYES',
    'DHCZ4',
    'CCFVTRIG',
    'CCINZZEE',
    'SOCNQQUT',
    'EXUSMTM',
    'CCGMATAF',
    'GMSUSD',
    'CSCTAUSD',
    'XAUC',
    'HSCEIDPI',
    'HSIDPI',
    'SPXDIVAN',
    'F1DV',
    'NKYDIV',
    'SMIDP',
    'NKYEDP',
    'SX7EDA',
    'CACDI',
    'GMS',
    'CCGGAS',
    'SKHYV.US',
    'GMATSGD3',
    'EWY.OC',
    'GMATAUD3',
    'ROG.SW'
]

currency_map = {
    'CH': 'CNY',
    'HK': 'HKD',
    'JP': 'JPY',
    'KS': 'KRW',
    'EU': 'EUR',
    'LN': 'GBP',
    'US': 'USD',
    'AU': 'AUD',
    'SG': 'SGD',
    'SW': 'CHF',
}

fx_fix_map = {
    ('CH', 'PX_LAST'): 'T160',
    ('HK', 'PX_LAST'): 'T170',
    ('HK', 'PX_OPEN'): 'T103',
    ('JP', 'PX_LAST'): 'T150',
    ('KS', 'PX_LAST'): 'T153',
    ('EU', 'PX_LAST'): 'L163',
    ('SW', 'PX_LAST'): 'L163',
    ('LN', 'PX_LAST'): 'L163',
    ('US', 'PX_OPEN'): 'F093',
    ('US', 'PX_LAST'): 'F160',
    ('AU', 'PX_LAST'): 'F020',
    ('SG', 'PX_LAST'): 'T170',
    ('SG', 'PX_OPEN'): 'T103',
}

proxy_info = {
    'BABA.US': {('9988 HK Equity', 'USDHKD'): ['APAC', 'EMEA']},
    'BIDU.US': {('9888 HK Equity', 'USDHKD'): ['APAC', 'EMEA']},
    'BILI.US': {('9626 HK Equity', 'USDHKD'): ['APAC', 'EMEA']},
    'JD.US': {('9618 HK Equity', 'USDHKD'): ['APAC', 'EMEA']},
    'LI.US': {('2015 HK Equity', 'USDHKD'): ['APAC', 'EMEA']},
    'NIO.US': {('9866 HK Equity', 'USDHKD'): ['APAC', 'EMEA']},
    'NTES.US': {('9999 HK Equity', 'USDHKD'): ['APAC', 'EMEA']},
    'XPEV.US': {('9868 HK Equity', 'USDHKD'): ['APAC', 'EMEA']},
    '9988.HK': {('BABA US Equity', 'HKDUSD'): ['NAM']},
    '9888.HK': {('BIDU US Equity', 'HKDUSD'): ['NAM']},
    '9626.HK': {('BILI US Equity', 'HKDUSD'): ['NAM']},
    '9618.HK': {('JD US Equity', 'HKDUSD'): ['NAM']},
    '2015.HK': {('LI US Equity', 'HKDUSD'): ['NAM']},
    '9866.HK': {('NIO US Equity', 'HKDUSD'): ['NAM']},
    '9999.HK': {('NTES US Equity', 'HKDUSD'): ['NAM']},
    '9868.HK': {('XPEV US Equity', 'HKDUSD'): ['NAM']},
}
proxy_info = {k: {proxy_region: proxy
                  for proxy, proxy_region_list in vm.items()
                  for proxy_region in proxy_region_list}
              for k, vm in proxy_info.items()}

force_proxy = {
    '3750.HK': ('300750 C2 Equity', 'HKDCNH', 'HKD'),
    'CRCL.US': ('COIN US Equity', None, 'USD'),
    '688795.CH': ('688256 CH Equity', None, 'CNY'),
    'SKHY.US': ('MU US Equity', None, 'USD'),
    '3308.HK': ('300308 C2 Equity', 'HKDCNH', 'HKD')
}

override_info = {
    'IDX.IDX': {
        'CSI1000-HSTECH': 0.88,
        'HSTECH-SH000905': 0.8,

        'SPX-SX5E': 0.92,
    },
    'EQ.EQ': {
        '700.HK-PDD.US': 0.86,
        'QQQ.US-XLE.US': 0.635,
        'SPY.US-XLE.US': 0.7,
        'SPY.UP-XLE.US': 0.7,
        'XLE.US-XLY.US': 0.8,
        'IWM.US-XLE.US': 0.8,
        'MRVL.US-SOXL.US': 0.91,
        'MU.US-DRAM.US': 0.93,
        'NVDA.US-SOXX.US': 0.93,
        'DRAM.US-SOXX.US': 0.9,
        'AAPL.US-SOXX.US': 0.8,
    }
}

calypso_corr_name = {
    'EQ.EQ': 'Corr_Equity_Equity',
    'EQ.FX': 'Corr_Equity_FX',
    'IDX.IDX': 'Corr_EqIndex_EqIndex',
    'IDX.FX': 'Corr_EqIndex_FX',
    'EQ.IDX': 'Corr_Equity_EqIndex',
    'FX.FX': 'Corr_FX_FX'
}

corr_risk_name = {
    'EQ': 'EQStock',
    'IDX': 'EQIndex',
    'FX': 'FX'
}

beidou_to_calypso = {
    'FB.US': 'META.US',
    'KOSPI2': 'KOSPI200',
    'SQ.US': 'XYZ.US',
    'GOLD.US': 'B.US',
    'YY.US': 'JOYY.US',
    '3939.HK': '2979.HK'
}
calypso_to_beidou = {v: k for k, v in beidou_to_calypso.items()}


def collapse_calypso_ticker(t):
    return beidou_to_calypso.get(t, t) \
        .replace('KOSPI200', 'KOSPI2') \
        .replace('.C1', '.CH') \
        .replace('.C2', '.CH') \
        .replace('.UP', '.US') \
        .replace('.UW', '.US') \
        .replace('.UN', '.US')


def calypso_to_bbg(u):
    c = collapse_calypso_ticker(u)
    if '.' in c:
        return f"{c.replace('.', ' ')} Equity"
    elif '/' in c:
        return f"{c.replace('/', '')} Curncy"
    else:
        return f'{c} Index'


def get_pair_list(p_raw, pad_ow=None, alpha_ow=None, use_future_data=False):
    bbg_pair_raw = {}
    for i, u in enumerate(p_raw):
        bp = [{'Calypso': collapse_calypso_ticker(u), 'BBG': calypso_to_bbg(u)}]
        bbg_pair_raw[i] = bp

    bbg_pairs_raw = [list(p) for p in itertools.product(bbg_pair_raw[0], bbg_pair_raw[1]) if p[0]['BBG'] != p[1]['BBG']]

    return [pp for p in bbg_pairs_raw for pp in get_pair_list_one(p, pad_ow, alpha_ow, use_future_data)]


def get_pair_list_one(bbg_pair, pad_ow=None, alpha_ow=None, use_future_data=False):
    enriched_pair = copy.deepcopy(bbg_pair)
    for i in [0, 1]:
        if 'Equity' in enriched_pair[i]['BBG']:
            enriched_pair[i]['SubRegion'] = sub_region_dict[enriched_pair[i]['BBG'].split(' ')[1]]
            enriched_pair[i]['Region'] = region_dict.get(enriched_pair[i]['SubRegion'], 'Unknown')
            enriched_pair[i]['Currency'] = currency_map.get(enriched_pair[i]['SubRegion'], 'Unknown')
        elif 'Index' in enriched_pair[i]['BBG']:
            enriched_pair[i]['SubRegion'] = sub_region_dict[enriched_pair[i]['BBG'].split(' ')[0]]
            enriched_pair[i]['Region'] = region_dict.get(enriched_pair[i]['SubRegion'], 'Unknown')
            enriched_pair[i]['Currency'] = currency_map.get(enriched_pair[i]['SubRegion'], 'Unknown')
        elif 'Curncy' in enriched_pair[i]['BBG']:
            enriched_pair[i]['SubRegion'] = 'FX'
            enriched_pair[i]['Region'] = 'FX'
        else:
            raise NotImplementedError

    if enriched_pair[0]['Region'] == 'NAM' and sort_pair:
        enriched_pair = [enriched_pair[1], enriched_pair[0]]

    if enriched_pair[1]['Region'] in proxy_info.get(enriched_pair[0]['Calypso'], {}):
        proxy_underlying = proxy_info[enriched_pair[0]['Calypso']][enriched_pair[1]['Region']]
        enriched_pair[0]['BBG'] = proxy_underlying[0]
        enriched_pair[0]['FXConv'] = proxy_underlying[1]
    elif enriched_pair[0]['Region'] in proxy_info.get(enriched_pair[1]['Calypso'], {}):
        proxy_underlying = proxy_info[enriched_pair[1]['Calypso']][enriched_pair[0]['Region']]
        enriched_pair[1]['BBG'] = proxy_underlying[0]
        enriched_pair[1]['FXConv'] = proxy_underlying[1]

    for i in [0, 1]:
        if 'Equity' in enriched_pair[i]['BBG']:
            enriched_pair[i]['SubRegion'] = sub_region_dict[enriched_pair[i]['BBG'].split(' ')[1]]
            enriched_pair[i]['Region'] = region_dict.get(enriched_pair[i]['SubRegion'], 'Unknown')
            enriched_pair[i]['Currency'] = currency_map.get(enriched_pair[i]['SubRegion'], 'Unknown')
        elif 'Index' in enriched_pair[i]['BBG']:
            enriched_pair[i]['SubRegion'] = sub_region_dict[enriched_pair[i]['BBG'].split(' ')[0]]
            enriched_pair[i]['Region'] = region_dict.get(enriched_pair[i]['SubRegion'], 'Unknown')
            enriched_pair[i]['Currency'] = currency_map.get(enriched_pair[i]['SubRegion'], 'Unknown')
        elif 'Curncy' in enriched_pair[i]['BBG']:
            enriched_pair[i]['SubRegion'] = 'FX'
            enriched_pair[i]['Region'] = 'FX'
        else:
            raise NotImplementedError

        force_proxy_info = force_proxy.get(enriched_pair[i]['Calypso'])
        if force_proxy_info is not None:
            enriched_pair[i]['BBG'] = force_proxy_info[0]
            if force_proxy_info[1] is not None:
                enriched_pair[i]['FXConv'] = force_proxy_info[1]
            enriched_pair[i]['Currency'] = force_proxy_info[2]

    if enriched_pair[0]['Region'] != 'Unknown' and enriched_pair[1]['Region'] == 'FX':
        # quanto corr
        fx_fix = fx_fix_map[(enriched_pair[0]['SubRegion'], 'PX_LAST')]
        enriched_pair[1]['BBG'] = enriched_pair[1]['BBG'].replace('Curncy', f'{fx_fix} Curncy')
        fxc_1 = f"USD{enriched_pair[1]['BBG'][:3]} {fx_fix} Curncy" if enriched_pair[1]['BBG'][:3] != 'USD' else None
        fxc_2 = f"USD{enriched_pair[1]['BBG'][3:6]} {fx_fix} Curncy" if enriched_pair[1]['BBG'][3:6] != 'USD' else None
        enriched_pair[1]['BBG_FX'] = [f for f in [fxc_1, fxc_2] if f is not None]
        enriched_pair[0]['BBGField'] = enriched_pair[1]['BBGField'] = 'PX_LAST'
        if 'KRW' in enriched_pair[1]['Calypso']:
            enriched_pair[0]['Pad'] = enriched_pair[1]['Pad'] = pad_ow or 3
            enriched_pair[0]['Alpha'] = enriched_pair[1]['Alpha'] = alpha_ow or 95
        else:
            enriched_pair[0]['Pad'] = enriched_pair[1]['Pad'] = pad_ow or 3
            enriched_pair[0]['Alpha'] = enriched_pair[1]['Alpha'] = alpha_ow or 95

        pair_list = [enriched_pair]
    elif sorted([p['Calypso'] for p in enriched_pair]) == ['SPX', 'SX5E'] and use_future_data:
        # intraday corr: no data now
        enriched_pair[0]['BBGField'] = enriched_pair[1]['BBGField'] = '_T170'
        enriched_pair[0]['Pad'] = enriched_pair[1]['Pad'] = pad_ow or 3
        enriched_pair[0]['Alpha'] = enriched_pair[1]['Alpha'] = alpha_ow or 90
        pair_list = [enriched_pair]
    elif len({'SPX', 'SX5E'}.intersection({p['Calypso'] for p in enriched_pair})) > 0 \
            and len({'NKY', 'KOSPI2', 'SHSZ300', 'SH000905', 'HSCEI', 'HSI', 'HSTECH'}.intersection({p['Calypso'] for p in enriched_pair})) > 0 \
            and use_future_data:
        if enriched_pair[0]['Calypso'] in ['SPX', 'SX5E']:
            future_i = 0
        else:
            future_i = 1
        enriched_pair[1 - future_i]['BBGField'] = 'PX_LAST'

        if enriched_pair[1 - future_i]['Calypso'] == 'NKY' and enriched_pair[future_i]['Calypso'] == 'SX5E':
            enriched_pair[future_i]['BBGField'] = '_T153'
        else:
            enriched_pair[future_i]['BBGField'] = f"_{fx_fix_map[(enriched_pair[1 - future_i]['SubRegion'], 'PX_LAST')]}"
        enriched_pair[0]['Pad'] = enriched_pair[1]['Pad'] = pad_ow or 3
        enriched_pair[0]['Alpha'] = enriched_pair[1]['Alpha'] = alpha_ow or 95
        pair_list = [enriched_pair]
    elif enriched_pair[0]['SubRegion'] == enriched_pair[1]['SubRegion'] != 'Unknown':
        enriched_pair[0]['BBGField'] = enriched_pair[1]['BBGField'] = 'PX_LAST'
        enriched_pair[0]['Pad'] = enriched_pair[1]['Pad'] = pad_ow or 1
        if '.' in enriched_pair[0]['Calypso'] and '.' in enriched_pair[1]['Calypso']:
            # EQ, EQ
            enriched_pair[0]['Alpha'] = enriched_pair[1]['Alpha'] = alpha_ow or 90
        else:
            enriched_pair[0]['Alpha'] = enriched_pair[1]['Alpha'] = alpha_ow or 93
        pair_list = [enriched_pair]
    elif enriched_pair[0]['Region'] == enriched_pair[1]['Region'] != 'Unknown':
        enriched_pair[0]['BBGField'] = enriched_pair[1]['BBGField'] = 'PX_LAST'
        enriched_pair[0]['Pad'] = enriched_pair[1]['Pad'] = pad_ow or 3
        if '.' in enriched_pair[0]['Calypso'] and '.' in enriched_pair[1]['Calypso']:
            # EQ, EQ
            enriched_pair[0]['Alpha'] = enriched_pair[1]['Alpha'] = alpha_ow or 95
        else:
            enriched_pair[0]['Alpha'] = enriched_pair[1]['Alpha'] = alpha_ow or 93
        pair_list = [enriched_pair]
    elif sorted([p['Region'] for p in enriched_pair]) == ['APAC', 'EMEA']:
        enriched_pair[[p['Region'] for p in enriched_pair].index('APAC')]['BBGField'] = 'PX_LAST'
        enriched_pair[[p['Region'] for p in enriched_pair].index('EMEA')]['BBGField'] = 'PX_OPEN'

        if '.' in enriched_pair[0]['Calypso'] and '.' in enriched_pair[1]['Calypso']:
            # EQ, EQ
            enriched_pair[0]['Pad'] = enriched_pair[1]['Pad'] = pad_ow or 5
            enriched_pair[0]['Alpha'] = enriched_pair[1]['Alpha'] = alpha_ow or 99
        else:
            enriched_pair[0]['Pad'] = enriched_pair[1]['Pad'] = pad_ow or 3
            enriched_pair[0]['Alpha'] = enriched_pair[1]['Alpha'] = alpha_ow or 93
        pair_list = [enriched_pair]
    elif sorted([p['Region'] for p in enriched_pair]) == ['APAC', 'NAM']:
        # apac vs us
        enriched_pair[[p['Region'] for p in enriched_pair].index('APAC')]['BBGField'] = 'PX_LAST'
        enriched_pair[[p['Region'] for p in enriched_pair].index('NAM')]['BBGField'] = 'PX_OPEN'
        if '.' in enriched_pair[0]['Calypso'] and '.' in enriched_pair[1]['Calypso']:
            # EQ, EQ
            enriched_pair[0]['Pad'] = enriched_pair[1]['Pad'] = pad_ow or 5
            enriched_pair[0]['Alpha'] = enriched_pair[1]['Alpha'] = alpha_ow or 99
        else:
            enriched_pair[0]['Pad'] = enriched_pair[1]['Pad'] = pad_ow or 3
            enriched_pair[0]['Alpha'] = enriched_pair[1]['Alpha'] = alpha_ow or 93
        pair_list = [enriched_pair]
    elif sorted([p['Region'] for p in enriched_pair]) == ['EMEA', 'NAM']:
        if '.' in enriched_pair[0]['Calypso'] and '.' in enriched_pair[1]['Calypso']:
            # EQ, EQ
            pair_list = [[dict(enriched_pair[0], **{'Pad': pad_ow or 5, 'Alpha': alpha_ow or 99, 'BBGField': f1}),
                          dict(enriched_pair[1], **{'Pad': pad_ow or 5, 'Alpha': alpha_ow or 99, 'BBGField': f2})]
                         for f1 in ['PX_OPEN', 'PX_LAST'] for f2 in ['PX_OPEN', 'PX_LAST']]
        else:
            pair_list = [[dict(enriched_pair[0], **{'Pad': pad_ow or 5, 'Alpha': alpha_ow or 90, 'BBGField': f1}),
                          dict(enriched_pair[1], **{'Pad': pad_ow or 5, 'Alpha': alpha_ow or 90, 'BBGField': f2})]
                         for f1 in ['PX_OPEN', 'PX_LAST'] for f2 in ['PX_OPEN', 'PX_LAST']]
    else:
        pair_list = []

    for i in range(len(pair_list)):
        for j in [0, 1]:
            if 'FXConv' in pair_list[i][j]:
                fx_pair_raw = pair_list[i][j]['FXConv']
                fx_fix = fx_fix_map[(pair_list[i][j]['SubRegion'], pair_list[i][j]['BBGField'])]
                pair_list[i][j]['FXConv'] = f'{fx_pair_raw} {fx_fix} Curncy'
            pair_list[i][j]['Symbol'] = '='.join([str(pair_list[i][j].get(k, 'None')) for k in ['BBG', 'BBGField', 'FXConv', 'Pad']])

    return pair_list


def convert_beidou_underlying(ut, u):
    if ut == 'FX':
        fx_pair = (u[:3], u[3:])
        return '/'.join(fx_pair)
    else:
        return beidou_to_calypso.get(u, u)


def lambda_bump(base_corr, l, up=True):
    if up:
        bumped_corr = (1. - base_corr) * l + base_corr
    else:
        bumped_corr = (-1. - base_corr) * l + base_corr
    return bumped_corr


def get_lambda(el, hl):
    max_l = 0.5
    return max(max_l * (1. - hl / 504), 0.)


def penalty_bump(base_corr, el, hl, up=True):
    l = get_lambda(el, hl)
    return l, lambda_bump(base_corr, l, up)


def calc_corr(log_return_one, rolling_window, alpha, el, up):
    rolling_corr = log_return_one.iloc[:, 0].rolling(rolling_window).corr(log_return_one.iloc[:, 1]).dropna()
    date_str = f'{rolling_corr.index[0].date()}-{rolling_corr.index[-1].date()}-{rolling_corr.shape[0]}'
    history_length = rolling_corr.shape[0]
    if history_length > 0:
        base_corr = np.percentile(rolling_corr, alpha)
        l, corr = penalty_bump(base_corr, el, history_length, up)
        if abs(corr) < 0.001:
            return l, 0.001, rolling_corr[-1], date_str
        elif corr >= 0.98:
            return l, 0.98 - 0.001, rolling_corr[-1], date_str
        elif corr <= -0.98:
            return l, -0.98 + 0.001, rolling_corr[-1], date_str
        else:
            return l, corr, rolling_corr[-1], date_str
    else:
        return 0., 0., 0., date_str


def calc_corr_in_batch(pair_list, log_returns, rolling_window, el, run_mode, to_sort_pair):
    return [calc_corr_in_parallel(
        log_returns[[pp['Symbol'] for pp in p]].dropna(),
        rolling_window, p[0]['Alpha'], el, get_corr_direction(run_mode, p), to_sort_pair)
        for i, p in enumerate(pair_list)]


def calc_corr_in_parallel(log_return_one, rolling_window, corr_alpha, el, up_str, to_sort_pair):
    pair_str = get_pair_str(list(log_return_one.columns), to_sort_pair)
    log_return_one = log_return_one[[abs(v[0]) > 1e-8 or abs(v[1]) > 1e-8 for v in log_return_one.values]]
    log_return_one = log_return_one.iloc[-el:, :]
    try:
        if up_str == 0:
            return [(pair_str, f'{pair_str}-{corr_alpha}-0-NA', 0., 0.)]
        else:
            up = cmdline.get_bool(up_str)
            alpha = corr_alpha if up else 100 - corr_alpha
            l, corr, realized_corr, date_str = calc_corr(log_return_one, rolling_window, alpha, el, up)
            l_str = int(l * 100)

        if ' Curncy=' in log_return_one.columns[1]:
            log_return_one_reverse_fx = log_return_one.copy(deep=True)
            log_return_one_reverse_fx.iloc[:, 1] = -log_return_one_reverse_fx.iloc[:, 1]
            l_reverse_fx, corr_reverse_fx, realized_corr_reverse_fx, date_str = calc_corr(log_return_one_reverse_fx, rolling_window, 100 - alpha, el, not up)
            fx_pair = log_return_one.columns[1]
            pair_str_reverse_fx = get_pair_str([log_return_one.columns[0], f'{fx_pair[3:6]}{fx_pair[:3]}{fx_pair[6:]}'], to_sort_pair)

            if (up and -corr_reverse_fx > corr) or (not up and -corr_reverse_fx < corr):
                corr = -corr_reverse_fx
                l_str = int(l_reverse_fx * 100)
                realized_corr = -realized_corr_reverse_fx

            return [(pair_str, f'{pair_str}-{alpha}-{l_str}-{date_str}', realized_corr, corr), (pair_str_reverse_fx, f'{pair_str_reverse_fx}-{100 - alpha}-{l_str}-{date_str}', -realized_corr, -corr)]
        else:
            return [(pair_str, f'{pair_str}-{alpha}-{l_str}-{date_str}', realized_corr, corr)]
    except Exception:
        return [(pair_str, f'{pair_str}-{corr_alpha}-NA-NA', 0., 0.)]


def get_pair_str(p, sort=True):
    if sort:
        if p[0] <= p[1]:
            return f'{p[0]}-{p[1]}'
        else:
            return f'{p[1]}-{p[0]}'
    else:
        return f'{p[0]}-{p[1]}'


def get_corr_direction(run_mode, pair):
    if run_mode in ['EQ.EQ', 'EQ.IDX', 'IDX.IDX']:
        return 'True'
    elif run_mode in ['EQ.FX', 'IDX.FX']:
        if pair[0]['Currency'] == 'Unknown':
            raise ValueError(f'Unknown currency {pair[0]}')
        elif pair[0]['Currency'].replace('CNH', 'CNY') == pair[1]['BBG'][:3].replace('CNH', 'CNY'):
            return 'False'
        elif pair[0]['Currency'].replace('CNH', 'CNY') == pair[1]['BBG'][3:6].replace('CNH', 'CNY'):
            return 'True'
        else:
            return 0
    else:
        raise NotImplementedError


def get_corr(rl, cl, raw_corr, corr_lambda, default_corr, exclude_tickers, pad_ow, alpha_ow, use_future_data):
    if collapse_calypso_ticker(rl) == collapse_calypso_ticker(cl):
        return 'NA', 1., 1.
    elif rl in exclude_tickers or cl in exclude_tickers:
        return 'NA', 0., default_corr.get(get_pair_str([rl, cl], sort_pair), 0.)
    else:
        calc_pair_list = [(get_pair_str([pp['Symbol'] for pp in p], sort_pair), p) for p in get_pair_list((rl, cl), pad_ow, alpha_ow, use_future_data)]
        corr_list = [(s, raw_corr.get(s)) for s, p in calc_pair_list]
        corr_list = [v for v in corr_list if v[1] is not None]
        if len(corr_list) > 0:
            corr_direction_up = cmdline.get_bool(get_corr_direction(mode, calc_pair_list[0][1]))
            if corr_direction_up:
                bbg_pair_str, (bbg_pair_str_alpha, realized_corr, base_corr) = max(corr_list, key=lambda v: v[1][-1])
            else:
                bbg_pair_str, (bbg_pair_str_alpha, realized_corr, base_corr) = min(corr_list, key=lambda v: v[1][-1])
            return bbg_pair_str_alpha, realized_corr, lambda_bump(base_corr, corr_lambda, corr_direction_up)
        else:
            return 'NA', 0., default_corr.get(get_pair_str([rl, cl], sort_pair), 0.)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-rolling_win', default=63)  # Business days
    parser.add_argument('-look_back', default=63)  # Months
    parser.add_argument('-pad')  # Business days
    parser.add_argument('-alpha')
    parser.add_argument('-rolling_freq', default='True')
    parser.add_argument('-date', default='T-1')
    parser.add_argument('-mkt_date', default='T-1')
    parser.add_argument('-book',
                        default='CSICM-EQD-ESPH-EXO-H,CSICM-EQD-ESPH-EXO-M,CSICM-EQD-ESPH-EXO-U,CSICM-EQD-ESPH-EXO-K,CSICM-EQD-ESPH-EXO-G,CSICM-EQD-ESPH-EXO-I,CSICM-EQD-ESPH-EXO-J,CSICM-EQD-ESPH-EXO-N')  # 'CSICM-EQD-ESPH-EXO-H,CSICM-EQD-ESPH-EXO-M,CSICM-EQD-ESPH-EXO-U,CSICM-EQD-ESPH-EXO-K,CSICM-EQD-ESPH-EXO-I'
    parser.add_argument('-email')
    parser.add_argument('-new_und_1')  # e.g. 'TSP.US,AI.US'
    parser.add_argument('-new_und_2')  # e.g. 'CHF/USD,GBP/HKD'
    parser.add_argument('-proxy', default='True')
    parser.add_argument('-override', default='True')
    parser.add_argument('-update_rfq', default='False')
    parser.add_argument('-parallel', default='True')
    parser.add_argument('-use_future_data', default='False')
    parser.add_argument('-corr_lambda', default=0)
    parser.add_argument('-has_calypso_vol', default='True')
    parser.add_argument('-recalc_1')
    parser.add_argument('-recalc_2')
    parser.add_argument('-mode', default='EQ.EQ')  # EQ.EQ,EQ.FX,IDX.IDX,IDX.FX
    parser.add_argument('-attachment', default='False')
    parser.add_argument('-dump', default='True')
    parser.add_argument('-load_risk', default='False')
    parser.add_argument('-load_folder')

    args = parser.parse_args()
    log.info(args)

    receiver = cmdline.get_default_receiver(args.email, ['EQDExoticTrading@citicclsa.com'])

    mode = args.mode
    is_square_mode = mode.split('.')[0] == mode.split('.')[1]

    if mode not in ['EQ.EQ', 'EQ.IDX', 'IDX.IDX', 'EQ.FX', 'IDX.FX']:
        raise NotImplementedError(f'Unsupported {mode=}')

    try:
        to_dump = cmdline.get_bool(args.dump)
        if args.load_folder is not None:
            dump_folder = os.path.join('//vhkddp12/EQD_EXO_desk$/corr_mark', args.load_folder)
            run_date = mkt_date = pd.to_datetime(args.load_folder.split('_')[0]).date()

            if os.path.exists(os.path.join(dump_folder, 'CorrResult.csv')):
                corr_results_loaded = pd.read_csv(os.path.join(dump_folder, 'CorrResult.csv'))
            else:
                corr_results_loaded = None

            if os.path.exists(os.path.join(dump_folder, 'CalcPair1.csv')) and os.path.exists(os.path.join(dump_folder, 'CalcPair2.csv')):
                calc_pairs_loaded = (pd.read_csv(os.path.join(dump_folder, 'CalcPair1.csv')), pd.read_csv(os.path.join(dump_folder, 'CalcPair2.csv')))
            else:
                calc_pairs_loaded = None
        else:
            run_date = cmdline.get_date(args.date)
            mkt_date = cmdline.get_date(args.mkt_date)
            corr_results_loaded = None
            calc_pairs_loaded = None

            if to_dump:
                uuid_str = uuid.uuid4()
                dump_folder = f'//vhkddp12/EQD_EXO_desk$/corr_mark/{datetime.now():%Y%m%d%H%M}_{run_date}_{mode}_{getpass.getuser()}_{uuid_str}'
            else:
                dump_folder = None

        with timer('preparing calypso connection'):
            date_from = run_date - relativedelta(months=args.look_back + 2)
            expected_length = args.look_back * 21
            pad_override = int(args.pad) if args.pad is not None else None
            alpha_override = float(args.alpha) if args.alpha is not None else None
            has_calypso_vol = cmdline.get_bool(args.has_calypso_vol)
            new_und_list_1 = set(args.new_und_1.split(',')) if args.new_und_1 is not None else {}
            new_und_list_1 = {und for und in new_und_list_1 if und not in exclude_list}

            if is_square_mode:
                new_und_list_2 = new_und_list_1
            else:
                new_und_list_2 = set(args.new_und_2.split(',')) if args.new_und_2 is not None else {}
                new_und_list_2 = {und for und in new_und_list_2 if und not in exclude_list}

            new_und_mapping = {0: new_und_list_1, 1: new_und_list_2}

            if args.recalc_1 is not None:
                recalc_1 = cmdline.get_bool(args.recalc_1)
            else:
                recalc_1 = len(new_und_list_1) == 0

            if args.recalc_2 is not None:
                recalc_2 = cmdline.get_bool(args.recalc_2)
            else:
                recalc_2 = len(new_und_list_2) == 0

            recalc_mapping = {0: recalc_1, 1: recalc_2}

            connection = get_pulse_connection('prod')
            if has_calypso_vol:
                calypso_vol_surface_list = load_mkt_data_from_pulseeqd(mkt_date, 'EQUITY_VOL_SURFACE', connection)
                calypso_vol_surface_list = {u.replace(' ', '.') for u in calypso_vol_surface_list['Underlying']}
            else:
                calypso_vol_surface_list = set()
            calypso_corr = load_mkt_data_from_pulseeqd(mkt_date, 'CORRELATION_MATRIX', connection)
            calypso_corr = calypso_corr[calypso_corr['CurveName'] == calypso_corr_name[mode]]
            calypso_corr['Correlation'] = [float(c) for c in calypso_corr['Correlation']]

            for i in [1, 2]:
                calypso_corr[f'Axis{i}Value'] = calypso_corr.apply(
                    lambda row: row[f'Axis{i}Value'].replace(' ', '.') if row[f'Axis{i}Type'] in ['Equity', 'EqIndex'] else row[f'Axis{i}Value'], axis=1)

            if is_square_mode:
                if set(calypso_corr['Axis1Value']) != set(calypso_corr['Axis2Value']):
                    raise ValueError('Axis not matching')
                sort_pair = True
            else:
                sort_pair = False
            calypso_corr_dict = {get_pair_str(k, sort_pair): v for k, v in calypso_corr.set_index(['Axis1Value', 'Axis2Value'])['Correlation'].to_dict().items()}

        db_engine = script_util.get_db_engine()
        with db_engine.connect() as sql_con:
            with timer('preparing bbg_spots, bbg_returns, bbg_pairs'):
                ref_data = pd.read_sql_table(tableConst.t_config_eq, con=sql_con, schema=schemaConst.ref)

                data_holder = {'calc_axis_all': {}, 'calc_axis': {}, 'result_axis': {}}
                und_change_df = pd.DataFrame()
                bbg_spots_combined = pd.DataFrame()
                bbg_codes_combined = set()
                for i, mode_axis in enumerate(mode.split('.')):
                    calypso_und = list(dict.fromkeys(calypso_corr[f'Axis{i + 1}Value']))

                    if mode_axis in ['EQ', 'IDX']:
                        if mode_axis == 'EQ':
                            beidou_eq = {beidou_to_calypso.get(u, u) for u in ref_data[ref_data['asset_type'] == 'Stock']['underlying']}
                            beidou_eq = {u for u in beidou_eq if u in calypso_und or ('.C1' not in u and '.C2' not in u and '.CH' not in u)}
                        else:
                            beidou_eq = {beidou_to_calypso.get(u, u) for u in ref_data[ref_data['asset_type'] != 'Stock']['underlying']}
                        beidou_eq = {u for u in beidou_eq if u not in exclude_list}

                        if mode == 'EQ.IDX' and mode_axis == 'EQ':
                            base_eq = set(calypso_und).intersection(beidou_eq)
                            calc_axis_all = sorted(base_eq.union(new_und_mapping[i]))
                            result_axis = sorted(set(calypso_und).union(new_und_mapping[i]))
                        else:
                            base_eq = set(calypso_und).union(beidou_eq)
                            calc_axis_all = sorted(beidou_eq.union(new_und_mapping[i]))
                            result_axis = sorted(base_eq.union(new_und_mapping[i]))

                        if recalc_mapping[i]:
                            calc_axis = calc_axis_all
                        else:
                            calc_axis = sorted(new_und_mapping[i])

                        if mode in ['EQ.FX', 'IDX.FX']:
                            data_holder['calc_axis_all'][i] = calc_axis
                        else:
                            data_holder['calc_axis_all'][i] = calc_axis_all
                        data_holder['calc_axis'][i] = calc_axis
                        data_holder['result_axis'][i] = result_axis
                    elif mode_axis == 'FX':
                        if i != 1:
                            raise ValueError
                        total_axis = sorted({pp for p in sorted(set(calypso_und).union(new_und_list_2)) for pp in [p, f"{p.split('/')[1]}/{p.split('/')[0]}"]})
                        context_ref_data = load_ref_data(ref_date=run_date)
                        calc_axis = [p for p in total_axis if context_ref_data.has_fx(p.replace('/', ''))]

                        data_holder['calc_axis_all'][i] = calc_axis
                        if recalc_2:
                            data_holder['calc_axis'][i] = calc_axis
                        else:
                            data_holder['calc_axis'][i] = sorted({pp for p in new_und_list_2 for pp in [p, f"{p.split('/')[1]}/{p.split('/')[0]}"]})

                        add_axis = sorted([u for u in total_axis if u not in calypso_und])
                        original_axis = [u for u in calypso_und if u in total_axis]
                        result_axis = original_axis + add_axis

                        data_holder['result_axis'][i] = result_axis
                    else:
                        raise NotImplementedError

                    if i == 0 or not is_square_mode:
                        und_add_axis = pd.DataFrame([u for u in result_axis if u not in calypso_und], columns=[f'Add_{mode_axis}'])
                        und_remove_axis = pd.DataFrame([u for u in calypso_und if u not in result_axis], columns=[f'Remove_{mode_axis}'])
                        und_change_df = und_change_df.join(und_add_axis, how='outer').join(und_remove_axis, how='outer')

                    data_holder['calc_axis_all'][i] = [a for a in data_holder['calc_axis_all'][i] if a not in exclude_list]
                    data_holder['calc_axis'][i] = [a for a in data_holder['calc_axis'][i] if a not in exclude_list]

                if dump_folder is not None and not os.path.exists(dump_folder):
                    os.makedirs(dump_folder)

                if calc_pairs_loaded is not None:
                    with timer('loading calc_pairs_all'):
                        calc_pairs_all = list(zip([row.dropna().to_dict() for i, row in calc_pairs_loaded[0].iterrows()], [row.dropna().to_dict() for i, row in calc_pairs_loaded[1].iterrows()]))
                else:
                    with timer('preparing calc_pairs_raw'):
                        calc_pairs_raw = list(itertools.product(data_holder['calc_axis'][0], data_holder['calc_axis_all'][1])) \
                                         + list(itertools.product(data_holder['calc_axis_all'][0], data_holder['calc_axis'][1]))
                        calc_pairs_raw = {p if p[0] < p[1] or not is_square_mode else (p[1], p[0]) for p in calc_pairs_raw if p[0] != p[1]}
                    with timer(f'calc_pairs_raw: {len(calc_pairs_raw)}, preparing calc_pairs_all'):
                        calc_pairs_all_dict = {get_pair_str([u['Symbol'] for u in pp], sort_pair): pp for p in calc_pairs_raw
                                               for pp in get_pair_list(p, pad_override, alpha_override, cmdline.get_bool(args.use_future_data))}
                        calc_pairs_all = list(calc_pairs_all_dict.values())
                        if to_dump:
                            calc_pairs_all_df_1 = pd.DataFrame([v[0] for k, v in calc_pairs_all_dict.items()])
                            calc_pairs_all_df_1.to_csv(os.path.join(dump_folder, 'CalcPair1.csv'), index=False)
                            calc_pairs_all_df_2 = pd.DataFrame([v[1] for k, v in calc_pairs_all_dict.items()])
                            calc_pairs_all_df_2.to_csv(os.path.join(dump_folder, 'CalcPair2.csv'), index=False)

                if corr_results_loaded is not None:
                    calculated_pairs = set(corr_results_loaded['pair_str'].values)
                    calc_pairs_remaining = [p for p in calc_pairs_all if get_pair_str([pp['Symbol'] for pp in p], sort_pair) not in calculated_pairs]
                    corr_results = [[tuple(row)] for row in corr_results_loaded.values]
                else:
                    calc_pairs_remaining = calc_pairs_all
                    corr_results = []
                log.info(f'Calc Pairs: Remaining {len(calc_pairs_remaining)}, Total {len(calc_pairs_all)}, Skip {len(calc_pairs_all) - len(calc_pairs_remaining)}')

                calc_batch_size = 200e3
                calc_batch_num = int(len(calc_pairs_remaining) / calc_batch_size) + 1
                calc_pairs_list = np.array_split(calc_pairs_remaining, calc_batch_num)
                log.info(f'Calc Pairs: Batch {calc_batch_num}, Each {len(calc_pairs_list[0])}')

                for calc_i, calc_pairs in enumerate(calc_pairs_list):
                    with timer(f'calculating pairs {calc_i + 1}/{calc_batch_num}'):
                        with pdblp.bopen(timeout=10000) as bbg_con:
                            log.info('Loading BBG Spot')
                            fx_conv_codes = sorted({p[i]['FXConv'] for p in calc_pairs for i in [0, 1] if 'FXConv' in p[i]})
                            if len(fx_conv_codes) > 0:
                                fx_conv_spots = bbg_con.bdh(tickers=fx_conv_codes, flds='PX_LAST', start_date=f'{date_from:%Y%m%d}',
                                                            end_date=f'{mkt_date:%Y%m%d}').fillna(method='ffill')
                            else:
                                fx_conv_spots = None

                            bbg_codes = [[p[i].get(k, 'None') for k in ['BBG', 'BBGField', 'FXConv']] for p in calc_pairs for i in [0, 1] if p[i]['Region'] != 'FX']
                            bbg_codes = {k: sorted({v[0] for v in vl}) for k, vl in itertools.groupby(
                                sorted(bbg_codes, key=lambda b: (b[1], b[2])), key=lambda b: (b[1], b[2]))}

                            bbg_spots_combined = pd.DataFrame(index=[d.date() for d in pd.bdate_range(date_from, mkt_date)])
                            for (fld, fxc), bc in bbg_codes.items():
                                if fld.startswith('_'):
                                    for b in bc:
                                        future_spot = pd.read_csv(os.path.join(trader_tool_path, f"future_data/{b.split(' ')[0]}{fld}.csv"))
                                        future_spot['date'] = [pd.to_datetime(d) for d in future_spot['date']]
                                        future_spot['lr'] = future_spot['log return'].cumsum()
                                        future_spot[f'{b}={fld}={fxc}'] = [math.exp(r) for r in future_spot['lr']]
                                        future_spot = future_spot.drop(['log return', 'lr'], axis=1).set_index('date')
                                        bbg_spots_combined = bbg_spots_combined.join(future_spot, how='outer')
                                else:
                                    try:
                                        bbg_spots = script_util.bdh_in_batch(
                                            bbg_con,
                                            tickers=bc,
                                            flds=fld,
                                            start_date=f'{date_from:%Y%m%d}',
                                            end_date=f'{mkt_date:%Y%m%d}',
                                            elms=[('adjustmentNormal', True),
                                                  ('adjustmentAbnormal', True),
                                                  ('adjustmentSplit', True)]
                                        ).fillna(method='ffill')
                                    except Exception as ex:
                                        raise ValueError('\n' + '\n'.join(bc))
                                    bbg_spots.columns = [f"{'='.join(c)}={fxc}" for c in bbg_spots.columns]
                                    if fxc != 'None':
                                        fx_conv_spot_one = fx_conv_spots[fxc]['PX_LAST']
                                        bbg_spots = bbg_spots.divide(fx_conv_spot_one, axis='index')
                                    bbg_spots_combined = bbg_spots_combined.join(bbg_spots, how='outer')

                            bbg_fx_single_side_codes = sorted({f for p in calc_pairs for i in [0, 1] for f in p[i].get('BBG_FX', [])})
                            if len(bbg_fx_single_side_codes) > 0:
                                bbg_fx_single_side_spots = bbg_con.bdh(
                                    tickers=bbg_fx_single_side_codes,
                                    flds='PX_LAST',
                                    start_date=f'{date_from:%Y%m%d}',
                                    end_date=f'{mkt_date:%Y%m%d}',
                                    elms=[('adjustmentNormal', True),
                                          ('adjustmentAbnormal', True),
                                          ('adjustmentSplit', True)]
                                ).fillna(method='ffill')

                                bbg_fx_codes = sorted({p[i]['BBG'] for p in calc_pairs for i in [0, 1] if p[i]['Region'] == 'FX'})
                                bbg_fx_spots = pd.DataFrame(index=bbg_fx_single_side_spots.index)
                                for c in bbg_fx_codes:
                                    fxs_1 = bbg_fx_single_side_spots[f'USD{c[3:6]} {c[7:]}']['PX_LAST'] if c[3:6] != 'USD' else 1
                                    fxs_2 = bbg_fx_single_side_spots[f'USD{c[:3]} {c[7:]}']['PX_LAST'] if c[:3] != 'USD' else 1
                                    bbg_fx_spots[f'{c}=PX_LAST=None'] = fxs_1 / fxs_2

                                bbg_spots_combined = bbg_spots_combined.join(bbg_fx_spots, how='outer')

                        bbg_spots_combined = bbg_spots_combined.sort_index().fillna(method='ffill')

                        bbg_return_codes = [('='.join([str(p[i].get(k, 'None')) for k in ['BBG', 'BBGField', 'FXConv']]), p[i]['Pad']) for p in calc_pairs for i in [0, 1]]
                        bbg_return_codes = {k: sorted({v[0] for v in vl}) for k, vl in itertools.groupby(sorted(bbg_return_codes, key=lambda b: b[1]), key=lambda b: b[1])}

                        bbg_return_combined = pd.DataFrame()
                        for pad, cs in bbg_return_codes.items():
                            bbg_returns = bbg_spots_combined[cs].rolling(pad + 1).apply(lambda x: math.log(x[-1] / x[0]))
                            bbg_returns.columns = [f'{c}={pad}' for c in bbg_returns.columns]
                            bbg_return_combined = bbg_return_combined.join(bbg_returns, how='outer')

                        und_change_df = und_change_df.dropna(how='all', axis=1)

                        total_number_of_pairs = len(calc_pairs)
                        cpu_count = multiprocessing.cpu_count()
                        log_interval = max(round_halfup(total_number_of_pairs / 20), 1)
                        log.info(f'Total {total_number_of_pairs}, Interval {log_interval}, CPU {cpu_count}')

                        if cmdline.get_bool(args.parallel) and total_number_of_pairs > 100:
                            number_of_pools = 3
                            with timer(f'Processing in parallel {len(calc_pairs)=} {cpu_count=} {number_of_pools=}'):
                                with Pool(number_of_pools) as pool:
                                    bbg_pairs_partition = np.array_split(calc_pairs, number_of_pools)
                                    async_results = [pool.apply_async(calc_corr_in_batch, (
                                        pair_list, bbg_return_combined, int(args.rolling_win), expected_length, mode, sort_pair
                                    )) for pair_list in bbg_pairs_partition]
                                    corr_results_each_calc = [r for r_list in async_results for r in r_list.get()]
                        else:
                            with timer(f'Processing in sequence {len(calc_pairs)=}'):
                                corr_results_each_calc = [calc_corr_in_parallel(
                                    bbg_return_combined[[pp['Symbol'] for pp in p]].dropna(), int(args.rolling_win), p[0]['Alpha'],
                                    expected_length, get_corr_direction(mode, p), sort_pair)
                                    for i, p in enumerate(calc_pairs)]

                        corr_results.extend(corr_results_each_calc)

                        if to_dump:
                            corr_df = pd.DataFrame([row for l in corr_results for row in l], columns=['pair_str', 'pair', 'realized_corr', 'corr'])
                            corr_df.to_csv(os.path.join(dump_folder, 'CorrResult.csv'), index=False)

            with timer(f'process corr_results {len(corr_results)=}'):
                corr_dict = dict([(row[0], (row[1], round_halfup(row[2], dp=3), round_halfup(row[3], dp=3))) for l in corr_results for row in l])
                if recalc_1 and recalc_2:
                    default_corr_dict = {k: v for k, v in calypso_corr_dict.items() if k.split('-')[0] in exclude_list or k.split('-')[1] in exclude_list}
                else:
                    default_corr_dict = calypso_corr_dict
                result_df = pd.DataFrame({cl: [get_corr(rl, cl, corr_dict, float(args.corr_lambda), default_corr_dict, exclude_list,
                                                        pad_override, alpha_override, cmdline.get_bool(args.use_future_data))
                                               for rl in data_holder['result_axis'][0]]
                                          for cl in data_holder['result_axis'][1]},
                                         index=data_holder['result_axis'][0])

                result_key_df = result_df.applymap(lambda v: v[0])
                result_realized_corr_df = result_df.applymap(lambda v: v[1])
                result_corr_df = result_df.applymap(lambda v: v[2])

                override_corr = pd.DataFrame()
                if cmdline.get_bool(args.override):
                    if mode in override_info:
                        override_dict = {get_pair_str([k.split('-')[0], k.split('-')[1]], sort_pair): v for k, v in override_info[mode].items()}
                        override_corr = pd.DataFrame([override_info[mode]], index=['Override']).T
                        override_corr['Realized'] = [result_corr_df.loc[p.split('-')[0], p.split('-')[1]]
                                                     if p.split('-')[0] in result_corr_df.index and p.split('-')[1] in result_corr_df.columns
                                                     else np.nan
                                                     for p in override_corr.index]

                        override_corr['BBG_Pair'] = [result_key_df.loc[p.split('-')[0], p.split('-')[1]]
                                                     if p.split('-')[0] in result_key_df.index and p.split('-')[1] in result_key_df.columns
                                                     else np.nan
                                                     for p in override_corr.index]

                        override_corr['Diff'] = override_corr['Realized'] - override_corr['Override']
                        override_corr = override_corr.dropna().sort_values('Diff', ascending=False)

                        for i, row in override_corr.iterrows():
                            result_corr_df.loc[i.split('-')[0], i.split('-')[1]] = row['Override']
                            result_key_df.loc[i.split('-')[0], i.split('-')[1]] = 'Override'
                            if i.split('-')[1] in result_corr_df.index and i.split('-')[0] in result_corr_df.columns:
                                result_corr_df.loc[i.split('-')[1], i.split('-')[0]] = row['Override']
                                result_key_df.loc[i.split('-')[1], i.split('-')[0]] = 'Override'

                            if mode in ['EQ.FX', 'IDX.FX']:
                                fx_pair = i.split('-')[1][:6]
                                fx_pair_reverse = f'{fx_pair[3:]}{fx_pair[:3]}'
                                eq_ticker = i.split('-')[0]
                                pair_override = [(eq_ticker, fx_pair, 1), (eq_ticker, fx_pair_reverse, -1)]
                            elif mode in ['EQ.EQ', 'IDX.IDX']:
                                pair_override = [(i.split('-')[0], i.split('-')[1], 1), (i.split('-')[1], i.split('-')[0], 1)]
                            elif mode in ['EQ.IDX']:
                                pair_override = [(i.split('-')[0], i.split('-')[1], 1)]
                            else:
                                raise NotImplementedError

                            for r in pair_override:
                                result_corr_df.loc[r[0], r[1]] = row['Override'] * r[2]
                                result_key_df.loc[r[0], r[1]] = 'Override'

                        override_corr = override_corr.reset_index().set_index(['index', 'BBG_Pair'])
                    else:
                        override_dict = {}
                else:
                    override_dict = {}

                calc_pairs_rfq = {(row[0]['Calypso'], row[1]['Calypso']) for row in calc_pairs_all}
                rfq_result = {get_pair_str(p, sort_pair):
                                  get_corr(p[0], p[1], corr_dict, float(args.corr_lambda), {}, exclude_list,
                                           pad_override, alpha_override, cmdline.get_bool(args.use_future_data))[2]
                              for p in calc_pairs_rfq}
                rfq_result = [(calypso_to_beidou.get(k.split('-')[0], k.split('-')[0]),
                               calypso_to_beidou.get(k.split('-')[1], k.split('-')[1]),
                               round_halfup(override_dict.get(k, v), dp=3)) for k, v in rfq_result.items()]
                rfq_result = [(u1, u2, v) for (u1, u2, v) in rfq_result if abs(v) > 1e-8]

                if to_dump:
                    rfq_df = pd.DataFrame(rfq_result, columns=['Underlying1', 'Underlying2', 'Corr'])
                    rfq_df.to_csv(os.path.join(dump_folder, 'RFQ.csv'), index=False)

                if cmdline.get_bool(args.update_rfq):
                    total_rfq_i = round_halfup(len(rfq_result) / 50) + 1
                    for rfq_i, rfq_result_one in enumerate(np.array_split(rfq_result, total_rfq_i)):
                        log.info(f'Updating RFQ {rfq_i + 1}/{total_rfq_i}: from {rfq_result_one[0]} to {rfq_result_one[-1]}')
                        update_rfq_msg = script_util.update_corr(
                            date.today(),
                            'RFQ',
                            [row[0] for row in rfq_result_one],
                            [row[1] for row in rfq_result_one],
                            [row[2] for row in rfq_result_one])
                        if update_rfq_msg != 'null':
                            raise ValueError(f'Update RFQ {rfq_i}: {update_rfq_msg}')

                corr_diff = pd.DataFrame(calypso_corr_dict.items(), columns=['pair', 'old'])
                corr_diff['bbg_pair'] = [result_key_df.loc[tuple(p.split('-'))] for p in corr_diff['pair']]
                corr_diff['new'] = [result_corr_df.loc[tuple(p.split('-'))] for p in corr_diff['pair']]
                corr_diff = corr_diff.set_index(['pair', 'bbg_pair']) * 100
                corr_diff['diff'] = corr_diff.apply(lambda row: row['new'] - row['old'] if abs(row['new']) > 1e-6 and abs(row['old']) > 1e-6 else np.nan, axis=1)
                corr_diff = corr_diff[abs(corr_diff['diff']) > 5]
                corr_diff = corr_diff.dropna().sort_values('diff')

        if to_dump:
            email_body = f'<p>Dump Folder: {os.path.abspath(dump_folder)}</p>'
            for label, dump_df in [('new', result_corr_df), ('diff', corr_diff)]:
                dump_df.reset_index().to_excel(os.path.join(dump_folder, f'{label}.xlsx'), index=False)
        else:
            email_body = ''

        if cmdline.get_bool(args.load_risk):
            with timer('Loading Corr01'):
                ref_data = MDAPI.load_ref_data()
                with Session(bind=db_engine) as session:
                    risk_query_builder = RiskQueryBuilder() \
                        .with_as_of_date(mkt_date) \
                        .with_book_name(args.book.split(',')) \
                        .with_risk_specs([(ctx, 'base', None, None, CorrelationT1Measure(Currency('USD'))) for ctx in ['HKClose', 'NYClose']])

                    risk_query = risk_query_builder \
                        .build('select') \
                        .options(selectinload(RiskEod.instrument)) \
                        .options(selectinload(RiskEod.trade))

                risk_raw = session.execute(risk_query).scalars().all()
                risk_flatten = Risk.flatten_all(risk_raw, ref_data=ref_data, include_trade_info=False)
                risk_flatten['Book'] = [t.trade.book.name for t in risk_flatten['trade']]
                risk_flatten = risk_flatten[[set(t.split('-')) == {corr_risk_name[m] for m in mode.split('.')} for t in risk_flatten['UnderlyingSubType']]] \
                    .groupby(['Book', 'Underlying', 'Underlying1', 'Underlying1SubType', 'Underlying2', 'Underlying2SubType'])[['ResultAmount']].sum()

                if risk_flatten.shape[0] > 0:
                    risk_flatten.columns = ['Corr01']
                    risk_flatten = risk_flatten[abs(risk_flatten['Corr01']) > 1].reset_index()

                    if risk_flatten.shape[0] > 0:
                        if not is_square_mode:
                            risk_flatten['underlying_dict'] = risk_flatten.apply(
                                lambda row: {row[f'Underlying{i}SubType']: convert_beidou_underlying(row[f'Underlying{i}SubType'], row[f'Underlying{i}']) for i in [1, 2]}, axis=1)
                            risk_flatten['Pair'] = [get_pair_str([m[corr_risk_name[mode.split('.')[0]]], m[corr_risk_name[mode.split('.')[1]]]], sort_pair)
                                                    for m in risk_flatten['underlying_dict']]
                        else:
                            risk_flatten['underlying_dict'] = np.nan
                            risk_flatten['Pair'] = risk_flatten.apply(lambda row: '-'.join(
                                [convert_beidou_underlying(row[f'Underlying{i}SubType'], row[f'Underlying{i}']) for i in [1, 2]]), axis=1)

                    risk_flatten['BBG_Pair'] = [result_key_df.loc[p.split('-')[0], p.split('-')[1]] for p in risk_flatten['Pair']]
                    risk_flatten['New'] = [result_corr_df.loc[p.split('-')[0], p.split('-')[1]] * 100 for p in risk_flatten['Pair']]


                def try_get_corr(u, d, ctx):
                    try:
                        u2 = u.split('-')[1]
                        if mode.split('.')[-1] == 'FX':
                            u2 = f'{u2[:3]}/{u2[3:]}'

                        return script_util.get_corr(d, ctx, u.split('-')[0], u2) * 100
                    except Exception as ex:
                        return np.nan


                risk_flatten['Current'] = [try_get_corr(u, mkt_date, 'Calypso') for u in risk_flatten['Underlying']]
                risk_flatten['New-Current'] = risk_flatten['New'] - risk_flatten['Current']
                risk_flatten['PNL_Current'] = risk_flatten['New-Current'] * risk_flatten['Corr01']
                risk_flatten['RFQ'] = [try_get_corr(u, date.today(), 'RFQ') for u in risk_flatten['Underlying']]
                risk_flatten['New-RFQ'] = risk_flatten['New'] - risk_flatten['RFQ']

                risk_flatten['Last'] = [result_realized_corr_df.loc[p.split('-')[0], p.split('-')[1]] * 100 for p in risk_flatten['Pair']]
                risk_flatten['Last-New'] = risk_flatten['Last'] - risk_flatten['New']
                risk_flatten['PNL_Last'] = risk_flatten['Last-New'] * risk_flatten['Corr01']

                risk_flatten = risk_flatten[abs(risk_flatten['Corr01']) > 1].set_index(['Book', 'Pair', 'BBG_Pair'])
                risk_flatten.loc[('0_Total', '0_Total', '0_Total'), 'PNL_Current'] = risk_flatten['PNL_Current'].sum()
                risk_flatten.loc[('0_Total', '0_Total', '0_Total'), 'PNL_Last'] = risk_flatten['PNL_Last'].sum()
                risk_flatten.loc[('0_Total', '0_Total', '0_Total'), 'Corr01'] = risk_flatten['Corr01'].sum()
                risk_flatten = risk_flatten.drop(['Underlying', 'Underlying1', 'Underlying1SubType',
                                                  'Underlying2', 'Underlying2SubType', 'underlying_dict'], axis=1).sort_index()

                if to_dump:
                    risk_flatten.reset_index().to_csv(os.path.join(dump_folder, 'Risk.csv'), index=False)
        else:
            risk_flatten = None

        with timer('Sending Email'):
            subject = f'Correlation Calc {mode} {run_date}'
            email_body += f"<p>Underlying change</p>{und_change_df.to_html(na_rep='')}"
            email_body += f"<p>Override</p>{(override_corr * 100).to_html(float_format='{:,.4f}'.format, justify='right')}"
            email_body += f"<p>CorrDiff</p>{corr_diff.iloc[:100].to_html(float_format='{:,.1f}'.format, justify='right')}"
            if risk_flatten is not None:
                email_body += f"<p>PNL</p>{risk_flatten.to_html(float_format='{:,.1f}'.format, justify='right')}"

            with tempfile.TemporaryDirectory() as temp:
                if cmdline.get_bool(args.attachment):
                    attach_list = []
                    for label, attach_df in [('new', result_corr_df), ('diff', corr_diff)]:
                        attach_path = f'{temp}/{subject.replace(" ", "_")}_{label}.xlsx'
                        attach_df.reset_index().to_excel(attach_path, index=False)
                        attach_list.append(attach_path)
                    messaging.send_email(to=receiver, subject=subject, body=email_body, attachments=attach_list)
                else:
                    messaging.send_email(to=receiver, subject=subject, body=email_body)

    except Exception as ex:
        error_msg = html.escape(f"{type(ex)}\n{str(ex)}\n{traceback.format_exc()}").replace('\n', "<br />")
        subject = '[FAIL] Correlation Calc'
        messaging.send_email(to=receiver, subject=subject, body=error_msg)

        raise ex
